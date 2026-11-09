import { expect, test } from 'claude-code/testing'

import { findChecklist, reference, results } from '../hooks/parse'

const STEPS = [
  'Done. Before the deploy, please do these by hand:',
  '',
  '1. Rotate the **staging** token',
  '2. Run `make migrate` on the replica',
  '   - wait for the lag to drop',
  '3. Tell the on-call team',
  '',
  'Then ping me.',
].join('\n')

test('a cued numbered list is a checklist, sub-bullets join their item', async () => {
  const list = findChecklist(STEPS)
  expect(list?.title).toBe('Done. Before the deploy, please do these by hand')
  expect(list?.tasks.map(t => t.text)).toEqual([
    'Rotate the staging token',
    'Run `make migrate` on the replica wait for the lag to drop',
    'Tell the on-call team',
  ])
})

test('task items count from two; ticked ones stay ticked', async () => {
  const list = findChecklist('TODO\n- [ ] add the secret\n- [x] open the port')
  expect(list?.tasks.map(t => t.isDone)).toEqual([false, true])
})

test('summaries, plain bullets and code are not checklists', async () => {
  expect(findChecklist('Changes:\n1. a\n2. b\n3. c')).toBe(null)
  expect(findChecklist('You should:\n- a\n- b\n- c')).toBe(null)
  expect(findChecklist('Run this:\n```\n1. a\n2. b\n3. c\n```')).toBe(null)
})

test('the same list keeps its id', async () => {
  expect(findChecklist(STEPS)?.id).toBe(findChecklist(`Intro\n\n${STEPS}`)?.id)
})

test('results carry state and comments; the reference ends at the question', async () => {
  const list = findChecklist(STEPS)!
  list.tasks[0]!.isDone = true
  list.tasks[1]!.comment = 'replica is read-only'
  expect(results(list)).toContain('1. [x] Rotate the staging token')
  expect(results(list)).toContain('(not done)\n   Comment: replica is read-only')
  expect(results(list)).toContain('(1/3 done)')
  expect(reference(list).endsWith('Question: ')).toBe(true)
})

test('an answer with a checklist fills the pane with tasks, send and ask', async ($, on) => {
  on('command.register', () => ({ value: { command: 'checklist' } }))
  on('ui.open', () => ({ value: { isPlaced: false, reason: 'narrow' } }))
  on('ui.panes', () => ({ value: [] }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('turn.complete', () => ({ text: '' }))

  await $.session.start({ cwd: '/', surface: 'terminal', isInteractive: true })
  await $.turn.complete({
    answer: STEPS,
    durationMs: 1,
    isAborted: false,
    turnId: 't1',
    reason: 'answer',
  })

  const pane = await $.ui.mount({
    plugin: 'checklist',
    component: 'Pane',
    requestId: 'checklist',
    surface: 'terminal',
    props: {
      title: 'Checklist',
      isFocused: true,
      bodyColumns: 80,
      placement: 'inline',
      scroll: { offset: 0, bodyRows: 30 },
      view: {},
    },
  })
  expect((await pane.find({ type: 'Button', key: 'task:0' }))?.text).toContain(
    '[ ] 1. Rotate the staging token',
  )
  expect(await pane.find({ type: 'Button', key: 'checklist:send' })).toBeTruthy()
  expect(await pane.find({ type: 'Button', key: 'checklist:ask' })).toBeTruthy()
  await pane.unmount()
})
