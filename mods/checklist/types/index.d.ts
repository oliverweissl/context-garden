export type Task = { text: string; isDone: boolean; comment: string }

/** One checklist the agent handed the person, keyed by its tasks' text. */
export type Checklist = { id: string; title: string; tasks: Task[] }

declare module 'claude-code' {
  interface PluginState {
    checklist: { current: Shaped<Checklist | null> }
  }
}
