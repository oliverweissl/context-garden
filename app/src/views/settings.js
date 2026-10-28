import { h, modal } from "../ui.js";
import { accountsPanel } from "./accounts.js";

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
  } }];
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
