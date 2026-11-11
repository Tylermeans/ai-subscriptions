import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Account, RunInfo, Snapshot } from '../types'

// Footer label, /account picker and a band while a switch waits. ai-sub does the
// switching: `ai-sub switch` queues a handoff that the session's own Stop hook
// completes after a checkpoint, so this module only reads state and asks.

const PANE = 'ai-sub-accounts'
const snapshot = atom({ plugin: 'ai-sub-accounts', key: 'snapshot' } as const, null)
const message = atom({ plugin: 'ai-sub-accounts', key: 'message' } as const, null)
const SHORT: Record<string, string> = { five_hour: '5h', seven_day: '7d' }

type DashboardProfile = {
  profile: string
  provider: string
  name: string
  email: string | null
  freshness: string
  sample: { windows: { label: string; minutes?: number | null; used: number }[]; error: string | null } | null
}

async function executable($: EngineInterface) {
  return (await $.env.get('AI_SUB_EXECUTABLE')) ?? 'ai-sub'
}

function firstLine(text: string) {
  return (text.trim().split('\n')[0] ?? '').replace(/^ai-sub: /, '')
}

function shortError(error: string | null) {
  if (!error) return null
  if (error.includes('does not match')) return 'signed in to a different account'
  return error.length > 60 ? `${error.slice(0, 57)}...` : error
}

// Codex reports windows by slot name (codex/primary); their length says more.
function windowLabel(label: string, minutes?: number | null) {
  if (SHORT[label] || !minutes) return label
  if (minutes % 1440 === 0) return `${minutes / 1440}d`
  return minutes % 60 === 0 ? `${minutes / 60}h` : label
}

function usage(account: Account) {
  if (account.freshness !== 'fresh' || !account.windows.length) return 'usage not current'
  return account.windows.map(w => `${SHORT[w.label] ?? w.label} ${Math.round(w.used)}%`).join(' ')
}

function shortName(key: string | null) {
  return key?.split('/')[1] ?? key ?? ''
}

async function load($: EngineInterface) {
  const current = (await $.env.get('AI_SUB_PROFILE')) ?? null
  const runDir = await $.env.get('AI_SUB_RUN_DIR')
  const provider = current?.split('/')[0] ?? 'claude'
  let accounts: Account[] = []
  let others: Account[] = []
  let error: string | null = null
  try {
    const result = await $.process.run([await executable($), 'dashboard', '--json'], { timeoutMs: 15000 })
    if (result.exitCode === 0) {
      const data = JSON.parse(result.stdout) as { profiles: DashboardProfile[] }
      const account = (p: DashboardProfile): Account => ({
        key: p.profile,
        name: p.name,
        email: p.email,
        freshness: p.freshness,
        windows: (p.sample?.windows ?? []).map(w => ({ label: windowLabel(w.label, w.minutes), used: w.used })),
        error: p.sample?.error ?? null,
      })
      accounts = data.profiles.filter(p => p.provider === provider).map(account)
      // Other providers' accounts are shown, but a session can only move within its own provider.
      others = data.profiles.filter(p => p.provider !== provider).map(account)
    } else {
      error = firstLine(result.stderr) || 'ai-sub dashboard failed.'
    }
  } catch {
    error = 'ai-sub could not be run.'
  }
  let run: RunInfo | null = null
  if (runDir) {
    try {
      const state = JSON.parse(await $.fs.read(`${runDir}/run.json`))
      run = { status: String(state.status ?? ''), pending: state.pending?.target ?? null, fault: state.fault ?? null }
    } catch {
      run = null
    }
  }
  const latest: Snapshot = { current, managed: Boolean(runDir), accounts, others, run, error }
  await update($, snapshot, () => latest)
  return latest
}

async function choose($: EngineInterface, key: string) {
  const snap = await read($, snapshot)
  if (key === snap?.current) {
    await update($, message, () => `This session already uses ${key}.`)
    return `This session already uses ${key}.`
  }
  await update($, message, () => `Checking ${key}...`)
  let said: string
  try {
    const result = await $.process.run([await executable($), 'switch', key, '--json'], { timeoutMs: 120000 })
    if (result.exitCode !== 0) {
      said = firstLine(result.stderr) || 'ai-sub refused the switch.'
    } else {
      const out = JSON.parse(result.stdout) as { message: string; prompt: string }
      said = out.message
      await $.ui.close({ id: PANE })
      $.ui.toast(said)
      // A turn of its own once idle; the session's hooks turn it into the checkpoint.
      void $.prompt.submit({ text: out.prompt })
    }
  } catch {
    said = 'ai-sub could not be run.'
  }
  await update($, message, () => said)
  await load($)
  return said
}

async function cancel($: EngineInterface) {
  let said = 'ai-sub could not be run.'
  try {
    const result = await $.process.run([await executable($), 'switch', '--cancel', '--json'])
    said = result.exitCode === 0 ? (JSON.parse(result.stdout) as { message: string }).message : firstLine(result.stderr)
  } catch {
    // keep the default text
  }
  $.ui.toast(said)
  await load($)
  return said
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'account',
      description: 'Show subscription accounts and continue this session on another one',
      argumentHint: '[name | cancel]',
    })
    void load($)
    $.clock.every(20000, () => void load($))

    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    void load($)

    return next(e)
  })

  on('command.run', { command: 'account' }, async ($, e) => {
    const arg = e.args.trim()
    const snap = await load($)
    if (!arg) {
      await update($, message, () => null)
      await $.ui.open({
        id: PANE,
        title: 'Accounts',
        focus: true,
        closeOnEscape: true,
        rows: snap.accounts.length + snap.others.length + 3,
      })

      return {}
    }
    if (arg === 'cancel') return { text: await cancel($) }
    const provider = snap.current?.split('/')[0] ?? 'claude'

    return { text: await choose($, arg.includes('/') ? arg : `${provider}/${arg}`) }
  })

  on('ui.render', { component: 'SessionMode' }, async ($, e, next) => {
    const snap = await read($, snapshot)
    if (!snap?.current) return next(e)
    const me = snap.accounts.find(a => a.key === snap.current)
    let label = shortName(snap.current)
    if (me && me.freshness === 'fresh' && me.windows.length) label += ` ${usage(me)}`
    if (snap.run?.pending) label += ` → ${shortName(snap.run.pending)}`

    return next({ ...e, props: { ...e.props, modes: [...e.props.modes, `${label} · /account`] } })
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const snap = await read($, snapshot)
    const pending = snap?.run?.pending
    if (e.props.hasSurvey || !pending) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)

    return (
      <Box>
        <Text color="warning">Switching to {pending} once this session saves a checkpoint </Text>
        <Button key="cancel-switch" label="Cancel" onPress={() => void cancel($)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const snap = await read($, snapshot)
    const said = await read($, message)
    if (!snap) return <Text dimColor>Reading accounts...</Text>
    const focus = snap.accounts.findIndex(a => a.key !== snap.current)
    let hint = 'Pick an account to continue this task there. Esc closes.'
    if (!snap.managed) hint = 'Switching needs a session started through ai-sub with auto on.'
    else if (snap.run?.fault) hint = `Switching is suspended here: ${snap.run.fault} Restart this session.`

    return (
      <Box flexDirection="column">
        {snap.accounts.map((account, i) => {
          const notes = [account.email ?? 'no email', usage(account)]
          if (account.key === snap.current) notes.push('current')
          const problem = shortError(account.error)
          if (problem) notes.push(problem)

          return (
            <Button
              key={account.key}
              plain
              {...(i < 9 ? { hotkey: String(i + 1) } : {})}
              {...(i === focus ? { autoFocus: true as const } : {})}
              onPress={() => void choose($, account.key)}
            >
              {account.name} <Text dimColor>{notes.join(' · ')}</Text>
            </Button>
          )
        })}
        {snap.others.map(account => {
          const notes = [account.email ?? 'no email', usage(account)]
          const problem = shortError(account.error)
          if (problem) notes.push(problem)
          notes.push('not switchable from this session')

          return (
            <Text key={account.key} dimColor>
              {account.key} {notes.join(' · ')}
            </Text>
          )
        })}
        {snap.error && <Text color="error">{snap.error}</Text>}
        <Text dimColor>{said ?? hint}</Text>
      </Box>
    )
  })
}
