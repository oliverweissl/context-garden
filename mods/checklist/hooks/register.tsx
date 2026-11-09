import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Checklist } from '../types'
import { findChecklist, reference, results, summary } from './parse'

const PANE = 'checklist'
// Change the tag when Checklist's fields change: it declines what an earlier
// version stored, which outlives a reload.
const current = atom({ plugin: 'checklist', key: 'current' } as const, null, {
  shape: 'checklist-1',
})

function open($: EngineInterface, list: Checklist) {
  return $.ui.open({
    id: PANE,
    title: `${list.title} (${summary(list)})`,
    focus: true,
    closeOnEscape: true,
  })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'checklist',
      description: 'Open the last checklist the agent handed you',
    })

    return next(e)
  })

  on('command.run', { command: 'checklist' }, async $ => {
    const list = await read($, current)
    if (!list) return { text: 'No checklist yet.' }
    await open($, list)

    return { text: 'Checklist opened.' }
  })

  // A main-loop answer that ends with a checklist for the person opens it.
  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    if (e.agentId || e.reason !== 'answer') return done

    const found = findChecklist(e.answer)
    if (!found) return done

    const kept = await read($, current)
    if (kept?.id !== found.id) await update($, current, () => found)
    void open($, kept?.id === found.id ? kept : found)

    return done
  })

  // A band above the prompt, so the list is one press away when the pane is
  // closed or the terminal too narrow to seat it unasked.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const list = await read($, current)
    if (!list || e.props.hasSurvey) return next(e)
    const isUp = (await $.ui.panes()).some(pane => pane.id === PANE && pane.isPlaced)
    if (isUp) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)

    return (
      <Box>
        <Text dimColor>
          Checklist: {list.title} ({summary(list)}){' '}
        </Text>
        <Button key="checklist:open" label="Open" onPress={() => open($, list)} />
        <Text> </Text>
        <Button
          key="checklist:dismiss"
          label="Dismiss"
          dimColor
          onPress={() => update($, current, () => null)}
        />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Input, Text } = $.ui.resolve(e)
    const list = await read($, current)
    if (!list) return <Text dimColor>No checklist yet.</Text>

    const setTask = (i: number, change: Partial<Checklist['tasks'][number]>) =>
      update($, current, l =>
        l && l.id === list.id
          ? { ...l, tasks: l.tasks.map((t, j) => (j === i ? { ...t, ...change } : t)) }
          : l,
      )

    const send = async () => {
      const latest = await read($, current)
      if (!latest) return
      await $.ui.close({ id: PANE })
      await update($, current, () => null)
      await $.prompt.submit({ text: results(latest) })
    }

    const ask = async () => {
      const latest = await read($, current)
      if (!latest) return
      await $.ui.close({ id: PANE })
      await $.prompt.fill({ text: reference(latest), mode: 'replace' })
    }

    return (
      <Box flexDirection="column">
        {list.tasks.map((task, i) => (
          <Box flexDirection="column" marginBottom={1}>
            <Button
              key={`task:${i}`}
              plain
              autoFocus={i === 0 ? true : undefined}
              label={`[${task.isDone ? 'x' : ' '}] ${i + 1}. ${task.text}`}
              dimColor={task.isDone}
              onPress={() => setTask(i, { isDone: !task.isDone })}
            />
            <Input
              key={`comment:${i}`}
              placeholder="comment (optional)"
              value={task.comment}
              submitLabel="next"
              onInput={value => setTask(i, { comment: value })}
              onSubmit={value => setTask(i, { comment: value })}
            />
          </Box>
        ))}
        <Text dimColor>{summary(list)}</Text>
        <Box>
          <Button key="checklist:send" variant="primary" label="Send to agent" onPress={send} />
          <Text> </Text>
          <Button key="checklist:ask" label="Ask about this" onPress={ask} />
        </Box>
      </Box>
    )
  })
}
