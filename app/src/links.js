// When the user links or unlinks terminals in the Orchestrate view, the running agents
// on both ends are told (through their inbox), so a manager does not keep believing it
// has no workers, and a worker knows who its tasks come from (#85).

const same = (a, b) => a.manager === b.manager && a.worker === b.worker;

const TOOLS = "garden-control tools";
const NOTE = "Just note this; no reply needed.";

/** The notices for going from links `before` to `after`: [{ key, text }], by terminal key. */
export function linkNotices(before, after, titleOf) {
  const added = after.filter((l) => !before.some((b) => same(b, l)));
  const removed = before.filter((l) => !after.some((a) => same(a, l)));
  const out = [];
  for (const l of added) {
    const [m, w] = [titleOf(l.manager), titleOf(l.worker)];
    out.push({ key: l.manager, text: `[Context Garden] You now manage "${w}". Give it tasks with send_prompt, check its screen with read_output, and see all your workers and managers with list_terminals (${TOOLS}). ${NOTE}` });
    out.push({ key: l.worker, text: `[Context Garden] "${m}" now manages you. Its tasks arrive as messages starting '[Task from your manager'; when done or blocked, answer with report_to_manager (${TOOLS}). ${NOTE}` });
  }
  for (const l of removed) {
    const [m, w] = [titleOf(l.manager), titleOf(l.worker)];
    out.push({ key: l.manager, text: `[Context Garden] You no longer manage "${w}": do not send it tasks or read its screen any more. ${NOTE}` });
    out.push({ key: l.worker, text: `[Context Garden] "${m}" no longer manages you: do not report to it any more. ${NOTE}` });
  }
  return out;
}
