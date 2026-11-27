import { api } from "../api.js";
import { h, modal, toast, errText, badge } from "../ui.js";

/**
 * After the app was installed or updated (#90): when Claude/Codex accounts do not have
 * this app's release of the context-garden skills, ask once whether to install it.
 * "Not now" is remembered per app version; the "Skills outdated" button stays.
 */

const NOT_NOW = "cg.releaseSkills.notNow";

export async function askReleaseSkills() {
  let r = null;
  try {
    r = await api.releaseSkills();
  } catch {
    return; // offline or GitHub unreachable: ask on a later start
  }
  if (!r || !r.behind.length) return;
  try {
    if (localStorage.getItem(NOT_NOW) === r.version) return;
  } catch { /* private mode: ask */ }

  const status = h("p", { class: "muted small" });
  const yes = h("button", { class: "btn primary" }, "Update");
  const later = h("button", { class: "btn" }, "Not now");
  const m = modal(`Skills for ContextGarden ${r.version}`, h("div", {},
    h("p", {}, `These accounts do not have the context-garden skills of this release (${r.tag}). Update them now?`),
    ...r.behind.map((p) => h("div", { class: "plugin-line" }, badge(p), h("span", { class: "ellipsis" }, p.name), h("span", { class: "faint small" }, p.agent))),
    status,
    h("div", { class: "row end" }, later, yes)));

  later.onclick = () => {
    try { localStorage.setItem(NOT_NOW, r.version); } catch { /* private mode */ }
    m.close();
  };
  yes.onclick = async () => {
    yes.disabled = later.disabled = true;
    yes.textContent = "Updating…";
    const done = [];
    const failed = [];
    const notes = [];
    for (const p of r.behind) {
      status.textContent = `Updating ${p.name}…`;
      try {
        const note = await api.pinPlugin(p.id);
        if (note) notes.push(`${p.name}: ${note}`);
        done.push(p.name);
      } catch (e) {
        failed.push(`${p.name}: ${errText(e)}`);
      }
    }
    m.close();
    if (failed.length) toast(`Could not update ${failed.join("; ")}`, "error");
    if (done.length) {
      toast(`Skills ${r.tag} installed for ${done.join(", ")}. Restart open agent terminals of these accounts (close and resume them) to load them.${notes.length ? " " + notes.join("; ") : ""}`);
    }
  };
}
