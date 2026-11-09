// Finds a checklist addressed to the person in an assistant answer.
//
// A checklist is either a run of 2+ Markdown task items (`- [ ] ...`), or a
// run of 3+ numbered items (`1. ...`, `1) ...`, `Step 1: ...`) introduced by
// a line that asks the person to do something. Plain bullets never count:
// summaries use them all the time.

import type { Checklist } from '../types'

const TASK = /^\s{0,3}[-*+]\s+\[([ xX])\]\s+(.+)$/
const NUMBERED = /^\s{0,3}(?:step\s+)?(\d{1,2})[.):]\s+(.+)$/i
const CUE =
  /\b(you|your|please|steps?|to ?do|checklist|manual(ly)?|follow|run|make sure|before|next)\b/i
const FENCE = /^\s*(```|~~~)/

type Item = { text: string; isDone: boolean }
type Run = { kind: 'task' | 'numbered'; items: Item[]; start: number }

function clean(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/__(.+?)__/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

function hash(text: string): string {
  let h = 5381
  for (let i = 0; i < text.length; i++) h = ((h << 5) + h + text.charCodeAt(i)) | 0
  return (h >>> 0).toString(36)
}

function runs(lines: string[]): Run[] {
  const found: Run[] = []
  let run: Run | null = null
  let inFence = false

  lines.forEach((line, i) => {
    if (FENCE.test(line)) {
      inFence = !inFence
      run = null
      return
    }
    if (inFence) return

    const task = TASK.exec(line)
    const numbered = task ? null : NUMBERED.exec(line)
    const kind = task ? 'task' : numbered ? 'numbered' : null

    if (kind) {
      const item = task
        ? { text: task[2] ?? '', isDone: task[1] !== ' ' }
        : { text: numbered?.[2] ?? '', isDone: false }
      if (!run || run.kind !== kind) {
        run = { kind, items: [], start: i }
        found.push(run)
      }
      run.items.push(item)
      return
    }
    if (!run) return
    if (line.trim() === '') return // a blank line between items keeps the run
    if (/^\s{2,}\S/.test(line)) {
      // An indented line continues the last item (a sub-bullet or a wrapped line).
      const last = run.items[run.items.length - 1]
      if (last) last.text += ' ' + line.trim().replace(/^[-*+]\s+/, '')
      return
    }
    run = null
  })

  return found
}

function cued(lines: string[], start: number): boolean {
  for (let i = start - 1; i >= 0 && i >= start - 3; i--) {
    const line = lines[i] ?? ''
    if (line.trim() !== '') return CUE.test(line)
  }
  return false
}

function heading(lines: string[], start: number): string {
  for (let i = start - 1; i >= 0 && i >= start - 3; i--) {
    const line = clean((lines[i] ?? '').replace(/^#+\s*/, '').replace(/:\s*$/, ''))
    if (line) return line.length > 60 ? line.slice(0, 57) + '...' : line
  }
  return 'Checklist'
}

export function findChecklist(answer: string): Checklist | null {
  const lines = answer.split(/\r?\n/)
  const fits = runs(lines).filter(run =>
    run.kind === 'task'
      ? run.items.length >= 2
      : run.items.length >= 3 && cued(lines, run.start),
  )
  if (fits.length === 0) return null

  // The longest list wins; on a tie, the last one (the answer's closing ask).
  const best = fits.reduce((a, b) => (b.items.length >= a.items.length ? b : a))
  const tasks = best.items.map(item => ({
    text: clean(item.text),
    isDone: item.isDone,
    comment: '',
  }))

  return {
    id: hash(tasks.map(t => t.text).join('\n')),
    title: heading(lines, best.start),
    tasks,
  }
}

export function summary(list: Checklist): string {
  const done = list.tasks.filter(t => t.isDone).length
  return `${done}/${list.tasks.length} done`
}

/** The results the person sends back: every task with its state and comment. */
export function results(list: Checklist): string {
  const rows = list.tasks.map((t, i) => {
    const note = t.comment.trim()
    const state = t.isDone ? '' : ' (not done)'
    return `${i + 1}. [${t.isDone ? 'x' : ' '}] ${t.text}${state}${note ? `\n   Comment: ${note}` : ''}`
  })
  return `Checklist results for "${list.title}" (${summary(list)}):\n${rows.join('\n')}`
}

/** The reference a question about the list starts with, left for the person to finish. */
export function reference(list: Checklist): string {
  const rows = list.tasks.map(
    (t, i) => `${i + 1}. [${t.isDone ? 'x' : ' '}] ${t.text}${t.comment.trim() ? ` (${t.comment.trim()})` : ''}`,
  )
  return `About the checklist "${list.title}" (${summary(list)}):\n${rows.join('\n')}\n\nQuestion: `
}
