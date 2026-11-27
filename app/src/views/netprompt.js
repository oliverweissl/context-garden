import { api } from "../api.js";
import { h, modal, toast, errText } from "../ui.js";
import { paneHere } from "../terminals.js";

/**
 * The network security layer's question (#81, design section 5): a connection the
 * open garden's lists do not cover waits in the app's proxy until the user picks
 * one of four answers. Unanswered, it is declined after the timeout (60 s). Only
 * the window holding the terminal asks; an answer or timeout closes it everywhere.
 */

const open = new Map(); // prompt id -> modal

/** host -> suggestions for the entry: the host, its domain with subdomains, or the address. */
export function suggestions(host, ips = []) {
  const out = [host];
  const labels = host.split(".");
  if (!/^[\d.]+$/.test(host) && !host.includes(":") && labels.length > 2) out.push(`*.${labels.slice(-2).join(".")}`);
  if (labels.length === 2) out.push(`*.${host}`);
  for (const ip of ips.slice(0, 2)) if (ip !== host) out.push(ip);
  return out;
}

async function show(p) {
  let cfg;
  try {
    cfg = await api.netConfig();
  } catch (e) {
    return toast(errText(e), "error");
  }
  const ids = cfg.gardens[p.garden] ?? ["agent-endpoints"];
  const listsOf = (kind) => cfg.lists.filter((l) => l.kind === kind && ids.includes(l.id));
  const NEW = "__new__";
  const picker = (kind) => h("select", {},
    ...listsOf(kind).map((l) => h("option", { value: l.id }, l.name)),
    h("option", { value: NEW }, kind === "allow" ? "New allow list…" : "New deny list…"));
  const allowPick = picker("allow");
  const denyPick = picker("deny");
  const entry = h("input", { type: "text", value: p.host, spellcheck: false, class: "mono" });
  const chips = suggestions(p.host, p.ips).map((s) => h("button", { class: "chip toggle", onclick: () => { entry.value = s; } }, s));
  const left = h("span", { class: "faint small" });
  const buttons = [];
  let end = Date.now() + p.timeout_secs * 1000;
  const countdown = () => {
    const s = Math.max(0, Math.round((end - Date.now()) / 1000));
    left.textContent = `Declined in ${s} s if not answered`;
  };
  countdown();
  const tick = setInterval(countdown, 250);

  /** A list id to add to; creates the list (and adds it to this garden) for "New …". */
  const target = async (pick, kind) => {
    if (pick.value !== NEW) return pick.value;
    const name = `${kind === "allow" ? "Allowed" : "Blocked"} in this garden`;
    const next = await api.netSaveList({ id: "", name, kind, entries: [], builtin: false });
    const created = next.lists[next.lists.length - 1];
    await api.netSetGardenLists(p.garden, [...ids, created.id]);
    return created.id;
  };
  const answer = async (choice) => {
    buttons.forEach((b) => { b.disabled = true; });
    try {
      let list = null;
      if (choice === "accept_add") list = await target(allowPick, "allow");
      if (choice === "decline_add") list = await target(denyPick, "deny");
      await api.netAnswer(p.id, choice, list, entry.value);
      m.close();
    } catch (e) {
      buttons.forEach((b) => { b.disabled = false; });
      toast(errText(e), "error");
    }
  };
  const btn = (label, choice, cls) => {
    const b = h("button", { class: `btn ${cls}`, onclick: () => answer(choice) }, label);
    buttons.push(b);
    return b;
  };
  const m = modal(`Network: ${p.host}`, h("div", { class: "net-prompt" },
    h("p", {}, h("b", {}, p.terminal), " wants to connect to ", h("code", {}, `${p.host}:${p.port}`),
      p.ips.length ? h("span", { class: "faint small" }, ` (${p.ips.join(", ")})`) : null),
    h("p", { class: "muted small" }, "No list of this garden covers it."),
    h("div", { class: "row" }, h("span", { class: "small" }, "Entry to add:"), entry),
    h("div", { class: "chips" }, ...chips),
    h("div", { class: "net-choices" },
      h("div", { class: "row" }, btn("Accept once", "accept_once", ""), btn("Accept and add to", "accept_add", "primary"), allowPick),
      h("div", { class: "row" }, btn("Decline once", "decline_once", ""), btn("Decline and add to", "decline_add", "danger"), denyPick)),
    left), { onClose: () => { clearInterval(tick); open.delete(p.id); } });
  open.set(p.id, m);
  end = Date.now() + p.timeout_secs * 1000;
}

/** Listen for the proxy's questions; call once per window. */
export function initNetPrompt() {
  api.on("net-prompt", ({ payload: p }) => {
    if (paneHere(p.key)) show(p);
  });
  api.on("net-prompt-done", ({ payload: d }) => {
    const m = open.get(d.id);
    if (!m) return;
    m.close();
    if (d.allowed === null) toast("No answer in time: the connection was declined once");
  });
}
