import { h, modal, toast, errText } from "../ui.js";
import { api } from "../api.js";
import { accountsPanel } from "./accounts.js";
import { checklistSettings } from "../terminals.js";

/** App-wide terminal settings. */
async function terminalsPanel() {
  const settings = await api.settings();
  const box = h("input", {
    type: "checkbox", checked: settings.worktrees,
    onchange: () => api.saveSettings({ ...settings, worktrees: box.checked })
      .then(() => { settings.worktrees = box.checked; })
      .catch((e) => { box.checked = settings.worktrees; toast(errText(e), "error"); }),
  });
  return h("div", {},
    h("label", { class: "check" }, box, "Agents work in their own git worktree"),
    h("p", { class: "faint small" },
      "An agent terminal opened on a repo gets its own worktree in <garden>/.worktrees on a new branch ",
      h("code", {}, "cg/<agent>-<account>-<id>"),
      ", so agents on the same repo never edit the same files. Closing the terminal removes the worktree "
      + "and its branch unless they hold uncommitted changes or commits on no other branch; those are kept."),
    checklistSettings());
}

/** Tabs the current page adds in front of Accounts: [{ name, build: () => Node | Promise<Node> }]. */
let pageTabs = [];
export function setPageSettings(tabs = []) {
  pageTabs = tabs;
}

/** The settings popup: the page's own tabs (a garden's skills and plugins), then Accounts. */
export function openSettings(start) {
  let cleanup = null;
  const body = h("div", { class: "settings-body" });
  const tabs = [...pageTabs, { name: "Accounts", build: async () => {
    const el = h("div");
    cleanup = await accountsPanel(el);
    return el;
  } }, { name: "Terminals", build: terminalsPanel }];
  const bar = h("div", { class: "seg settings-tabs" });
  const m = modal("Settings", h("div", {}, bar, body), { wide: true, onClose: () => cleanup?.() });
  const show = async (tab) => {
    cleanup?.();
    cleanup = null;
    bar.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.textContent === tab.name));
    body.replaceChildren(h("div", { class: "faint small" }, "Loading…"));
    body.replaceChildren(await tab.build(m.close));
  };
  bar.replaceChildren(...tabs.map((t) => h("button", { onclick: () => show(t) }, t.name)));
  show(tabs.find((t) => t.name === start) || tabs[0]);
  return m;
}
