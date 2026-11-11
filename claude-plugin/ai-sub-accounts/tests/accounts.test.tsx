import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const PLUGIN = 'ai-sub-accounts'
const DASHBOARD = {
  profiles: [
    { profile: 'claude/refractors', provider: 'claude', name: 'refractors', email: 'r@example.com',
      freshness: 'fresh', sample: { windows: [{ label: 'five_hour', used: 11 }, { label: 'seven_day', used: 86 }], error: null } },
    { profile: 'claude/midwest', provider: 'claude', name: 'midwest', email: 'm@example.com',
      freshness: 'unknown', sample: { windows: [], error: 'The signed-in email does not match this profile.' } },
    { profile: 'codex/current', provider: 'codex', name: 'current', email: 'c@example.com',
      freshness: 'fresh', sample: { windows: [{ label: 'codex/primary', minutes: 10080, used: 43 }], error: null } },
  ],
}

function result(exitCode: number, stdout: string, stderr = '') {
  return { exitCode, stdout, stderr, isStdoutTruncated: false, isStderrTruncated: false }
}

function world(on: On, switchResult: ReturnType<typeof result>) {
  const calls: string[][] = []
  const toasts: string[] = []
  const prompts: string[] = []
  let pending: string | null = null
  mock.env(on, { AI_SUB_PROFILE: 'claude/refractors', AI_SUB_RUN_DIR: '/runs/r1', AI_SUB_EXECUTABLE: '/bin/ai-sub' })
  on('process.run', (_, e) => {
    calls.push([...e.argv])
    if (e.argv[1] === 'dashboard') return { value: result(0, JSON.stringify(DASHBOARD)) }
    if (e.argv[1] === 'switch' && switchResult.exitCode === 0) pending = e.argv[2] ?? null

    return { value: switchResult }
  })
  on('fs.read', () => ({ value: JSON.stringify({ status: 'monitoring', pending: pending && { target: pending }, fault: null }) }))
  on('command.register', () => ({ value: { command: 'account' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.close', () => ({ value: undefined }))
  on('ui.toast', (_, e) => {
    toasts.push(e.text)

    return { value: undefined }
  })
  on('prompt.submit', (_, e) => {
    prompts.push(e.text)

    return { text: e.text }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('ui.render', { component: 'SessionMode' }, ($, e) => {
    const { Text } = $.ui.resolve(e)

    return <Text>{e.props.modes.join(' & ')}</Text>
  })

  return { calls, toasts, prompts }
}

const SWITCHED = result(0, JSON.stringify({
  message: 'Switching to claude/midwest after this session saves a checkpoint.',
  target: 'claude/midwest', prompt: 'Switch this session to claude/midwest: submit the ai-sub handoff checkpoint now.',
}))

test('the footer names the account and its usage beside the other modes', async ($, on) => {
  world(on, SWITCHED)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: PLUGIN, surface, component: 'SessionMode', props: { modes: ['auto mode on'] } })
    const row = await ui.find({ type: 'Text', text: /refractors/ })
    expect(row?.text).toBe('auto mode on & refractors 5h 11% 7d 86% · /account')
    await ui.unmount()
  }
})

test('picking an account in the picker asks ai-sub and starts the checkpoint turn', async ($, on) => {
  const seen = world(on, SWITCHED)
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({
      plugin: PLUGIN, surface, component: 'Pane', requestId: PLUGIN,
      props: { title: 'Accounts', isFocused: true, bodyColumns: 80, placement: 'inline',
               scroll: { offset: 0, bodyRows: 10 }, view: {} },
    })
    expect(await ui.findAll({ type: 'Button' })).toHaveLength(2)
    expect((await ui.find({ type: 'Text', text: /codex\/current/ }))?.text)
      .toBe('codex/current c@example.com · 7d 43% · not switchable from this session')
    expect((await ui.find({ key: 'claude/midwest' }))?.text).toContain('signed in to a different account')
    await ui.press({ key: 'claude/midwest' })
    await ui.unmount()
  }
  expect(seen.calls).toContainEqual(['/bin/ai-sub', 'switch', 'claude/midwest', '--json'])
  expect(seen.prompts).toContain('Switch this session to claude/midwest: submit the ai-sub handoff checkpoint now.')
  expect(seen.toasts).toContain('Switching to claude/midwest after this session saves a checkpoint.')
  const band = await $.ui.mount({
    plugin: PLUGIN, surface: 'terminal', component: 'AbovePrompt',
    props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 10 }, view: {} },
  })
  expect((await band.find({ type: 'Text', text: /Switching to/ }))?.text).toContain('claude/midwest')
  await band.unmount()
})

test('a refused switch is shown in the picker and starts no turn', async ($, on) => {
  const seen = world(on, result(1, '', 'ai-sub: claude/midwest: The signed-in email does not match this profile.\n'))
  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  const ran = await $.command.run({ command: 'account', args: 'midwest', origin: { kind: 'composer' },
                                    presentation: { isFullscreen: false, columns: 100 } })
  expect(ran.text).toBe('claude/midwest: The signed-in email does not match this profile.')
  expect(seen.prompts).toHaveLength(0)
})
