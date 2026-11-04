export type AccountWindow = { label: string; used: number }

export type Account = {
  key: string
  name: string
  email: string | null
  freshness: string
  windows: AccountWindow[]
  error: string | null
}

export type RunInfo = { status: string; pending: string | null; fault: string | null }

export type Snapshot = {
  current: string | null
  managed: boolean
  accounts: Account[]
  run: RunInfo | null
  error: string | null
}

declare module 'claude-code' {
  interface PluginState {
    'ai-sub-accounts': { snapshot: Snapshot | null; message: string | null }
  }
}
