import { api, PROFILES_CHANGED } from "../api.js";
import { h, modal, toast, errText, badge } from "../ui.js";
import { staleTerminals, STALE_CHANGED } from "../terminals.js";

/**
 * "Skills outdated" next to Settings (#64): accounts whose context-garden
 * plugin is behind main, or behind this app's release when the account is
 * pinned to one (the same check as Settings → Accounts), and agent
 * terminals still running older plugins than their account has (the yellow
 * pane dot). One click opens the list; Update runs the usual account update.
 */

const CHECK_EVERY = 30 * 60 * 1000; // asking GitHub for main is a network call per account
let behind = []; // [{ profile, version }]
let button = null;

async function checkAccounts() {
  let profiles = [];
  try {
    profiles = await api.profiles();
  } catch {
    return;
  }
  const found = await Promise.all(profiles.filter((p) => ["claude", "codex"].includes(p.agent)).map(async (profile) => {
    // Not installed (null) is Settings → Accounts' business, not an update.
    const version = await api.pluginVersion(profile.id).catch(() => null);
    if (!version) return null;
    // Offline or GitHub unreachable: no hint.
    return (await api.pluginOutdated(profile.id).catch(() => false)) ? { profile, version } : null;
  }));
  behind = found.filter(Boolean);
  draw();
}

function draw() {
  const stale = staleTerminals();
  const n = behind.length + stale.length;
  if (!n) {
    button?.remove();
    button = null;
    return;
  }
  const parts = [behind.length && `${behind.length} account${behind.length > 1 ? "s" : ""} behind`,
    stale.length && `${stale.length} terminal${stale.length > 1 ? "s" : ""} on old skills`].filter(Boolean);
  if (!button) {
    button = h("button", { class: "btn update", onclick: openList });
    document.getElementById("settings").before(button);
  }
  button.textContent = `Skills outdated (${n})`;
  button.title = `context-garden: ${parts.join(", ")}. Click to update.`;
}

function openList() {
  const body = h("div", { class: "outdated" });
  const m = modal("context-garden skills", body);
  const update = async (btn, items) => {
    btn.disabled = true;
    btn.textContent = "Updating…";
    const failed = [];
    for (const { profile } of items) {
      try {
        await api.updatePlugin(profile.id);
      } catch (e) {
        failed.push(`${profile.name}: ${errText(e)}`);
      }
    }
    if (failed.length) toast(failed.join("; "), "error");
    else toast(`Skills updated for ${items.map((x) => x.profile.name).join(", ")}; resume their terminals to load them`);
    await checkAccounts();
    fill();
  };
  const fill = () => {
    const stale = staleTerminals();
    if (!behind.length && !stale.length) return m.close();
    body.replaceChildren(...[
      behind.length ? h("p", { class: "muted small" }, "These accounts have older skills (than main, or than this app's release for accounts pinned to one):") : null,
      ...behind.map((x) => h("div", { class: "plugin-line" },
        badge(x.profile), h("span", { class: "ellipsis" }, x.profile.name),
        h("span", { class: "warn small" }, `${x.version} → newer available`),
        h("span", { class: "grow" }),
        h("button", { class: "btn small-btn", onclick: (e) => update(e.target, [x]) }, "Update"))),
      behind.length > 1
        ? h("div", { class: "row end" }, h("button", { class: "btn primary", onclick: (e) => update(e.target, behind) }, "Update all"))
        : null,
      stale.length ? h("p", { class: "muted small" }, "These terminals still run older skills than their account has. Close and resume them (↺ Recent) to load the new ones:") : null,
      ...stale.map((t) => h("div", { class: "plugin-line" },
        h("a", { href: `#/garden/${t.garden}`, onclick: () => m.close(), title: t.why }, t.title),
        h("span", { class: "faint small ellipsis" }, t.why)))].filter(Boolean));
  };
  fill();
}

/** Start watching; call once at startup. */
export function initOutdated() {
  checkAccounts();
  setInterval(checkAccounts, CHECK_EVERY);
  window.addEventListener(PROFILES_CHANGED, checkAccounts);
  window.addEventListener(STALE_CHANGED, draw);
}
