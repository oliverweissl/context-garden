import { api } from "../api.js";
import { h, toast, errText } from "../ui.js";

// Network rules (#81, docs/network-security-design.md): named allow / deny lists of
// hostnames, *.domains and IP ranges, combined per garden. This step stores and
// evaluates them; the sandbox and proxy that enforce them come next.

const KIND = { allow: "Allow list", deny: "Deny list" };
const HELP = "One entry per line: a hostname (api.github.com), a domain with all its subdomains "
  + "(*.github.com) or an IP range in CIDR form (10.0.0.0/8, 2001:db8::/32). No URLs, paths or ports. "
  + "Deny lists win over allow lists.";

/** Settings → Network: the switch and every list. */
export async function networkPanel() {
  const root = h("div", { class: "network" });
  let cfg = await api.netConfig();
  const apply = (next) => { cfg = next; draw(); };
  const run = (p, done) => p.then((c) => { apply(c); if (done) toast(done); }).catch((e) => toast(errText(e), "error"));

  function listCard(list) {
    const name = h("input", { type: "text", value: list.name, disabled: list.builtin, placeholder: "List name" });
    const kind = h("select", { disabled: list.builtin },
      ...Object.entries(KIND).map(([k, label]) => h("option", { value: k, selected: list.kind === k }, label)));
    const entries = h("textarea", { rows: Math.min(12, Math.max(3, list.entries.length + 1)), spellcheck: false, class: "mono" });
    entries.value = list.entries.join("\n");
    const save = () => run(api.netSaveList({
      id: list.id, name: name.value, kind: kind.value, builtin: list.builtin,
      entries: entries.value.split("\n"),
    }), `Saved ${name.value || "list"}`);
    return h("div", { class: "card net-list" },
      h("div", { class: "row" }, name, kind,
        list.builtin ? h("span", { class: "chip", title: "Allowed by default so the agents work; edit or reset it, it cannot be deleted" }, "built-in") : null),
      list.builtin ? h("p", { class: "faint small" }, "The hosts Claude Code and Codex need to run. Selected in every garden unless the garden turns it off.") : null,
      entries,
      h("div", { class: "row end" },
        list.builtin
          ? h("button", { class: "btn", onclick: () => run(api.netResetBuiltin(), "Agent endpoints reset to the defaults") }, "Reset to defaults")
          : h("button", { class: "btn danger", onclick: () => run(api.netDeleteList(list.id), `Deleted ${list.name}`) }, "Delete"),
        h("button", { class: "btn primary", onclick: save }, "Save")));
  }

  function draw() {
    const box = h("input", {
      type: "checkbox", checked: cfg.enabled,
      onchange: () => run(api.netSetEnabled(box.checked)),
    });
    const add = (kind) => run(api.netSaveList({ id: "", name: kind === "allow" ? "New allow list" : "New deny list", kind, entries: [], builtin: false }));
    root.replaceChildren(
      h("label", { class: "check" }, box, "Network security layer"),
      h("p", { class: "faint small" },
        "Off by default. When on, agent terminals started or resumed afterwards reach the network through the app's "
        + "proxy, which applies the open garden's lists and asks about anything they do not cover (declined after 60 s "
        + "without an answer). Not enforced yet: until the sandbox step, a program that ignores the proxy settings "
        + "(HTTPS_PROXY and friends) is not stopped."),
      h("p", { class: "muted small" }, HELP),
      ...cfg.lists.map(listCard),
      h("div", { class: "row" },
        h("button", { class: "btn", onclick: () => add("allow") }, "+ Allow list"),
        h("button", { class: "btn", onclick: () => add("deny") }, "+ Deny list")));
  }
  draw();
  return root;
}

/** Garden settings → Network: which lists this garden combines, and a host check. */
export async function gardenNetworkPanel(garden) {
  const root = h("div", { class: "network" });
  let cfg = await api.netConfig();
  const chosen = () => cfg.gardens[garden.id] ?? ["agent-endpoints"];

  function draw() {
    const ids = chosen();
    const boxes = cfg.lists.map((l) => {
      const box = h("input", {
        type: "checkbox", checked: ids.includes(l.id),
        onchange: () => {
          const next = cfg.lists.filter((x) => (x.id === l.id ? box.checked : ids.includes(x.id))).map((x) => x.id);
          api.netSetGardenLists(garden.id, next).then((c) => { cfg = c; draw(); })
            .catch((e) => { box.checked = !box.checked; toast(errText(e), "error"); });
        },
      });
      return h("label", { class: "check" }, box, l.name,
        h("span", { class: `chip${l.kind === "deny" ? " warn" : ""}` }, l.kind),
        h("span", { class: "faint small" }, `${l.entries.length} entr${l.entries.length === 1 ? "y" : "ies"}`));
    });
    const host = h("input", { type: "text", placeholder: "Check a host or IP, e.g. api.github.com" });
    const out = h("span", { class: "small" });
    const check = () => api.netCheck(garden.id, host.value)
      .then((d) => {
        out.className = `small ${d.decision === "deny" ? "warn" : d.decision === "allow" ? "ok" : "muted"}`;
        out.textContent = d.decision === "ask" ? "not covered: the app would ask" : `${d.decision}ed by ${d.list}`;
      })
      .catch((e) => { out.className = "small warn"; out.textContent = errText(e); });
    host.addEventListener("keydown", (e) => { if (e.key === "Enter") check(); });
    root.replaceChildren(
      h("p", { class: "muted small" }, `Lists combined for ${garden.name}. Deny wins, then allow; anything else is asked about. `
        + "Edit the lists in the app settings (Network tab)."),
      ...(cfg.lists.length ? boxes : [h("p", { class: "faint small" }, "No lists yet.")]),
      h("div", { class: "row" }, host, h("button", { class: "btn", onclick: check }, "Check"), out),
      cfg.enabled ? null : h("p", { class: "faint small" }, "The network security layer is off (app settings → Network)."));
  }
  draw();
  return root;
}
