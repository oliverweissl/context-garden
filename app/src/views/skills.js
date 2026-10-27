import { api } from "../api.js";
import { h, toast, errText, SKILL_COLORS, SKILL_BLURBS } from "../ui.js";

const MODES = ["on", "manual", "off"];

/** Skill chooser for a garden. Preselects the recommendation unless the garden already has modes. */
export async function skillPicker(garden, onApplied) {
  const rec = await api.recommend(garden.id);
  const modes = { ...(Object.keys(garden.skills).length ? garden.skills : rec.modes) };

  const rows = h("div", { class: "skill-rows" });
  const drawRows = () => rows.replaceChildren(...Object.keys(rec.modes).map((skill) =>
    h("div", { class: "skill-row" },
      h("span", { class: "dot", style: `background:${SKILL_COLORS[skill]}` }),
      h("div", { class: "skill-name" }, h("b", {}, skill), h("div", { class: "faint small" }, SKILL_BLURBS[skill])),
      h("div", { class: "seg" }, MODES.map((m) =>
        h("button", {
          class: modes[skill] === m ? "active" : "",
          onclick: () => { modes[skill] = m; presetSel.value = ""; drawRows(); },
        }, m))))));

  const presetSel = h("select", {
    onchange: () => {
      const p = rec.presets.find((x) => x.name === presetSel.value);
      if (!p) return;
      for (const s of Object.keys(modes)) modes[s] = p.on.includes(s) ? "on" : "manual";
      drawRows();
    },
  },
  h("option", { value: "" }, "custom"),
  rec.presets.map((p) => h("option", { value: p.name, selected: !Object.keys(garden.skills).length && p.name === rec.preset },
    `${p.name}${p.name === rec.preset ? " (recommended)" : ""} — ${p.description}`)));

  const apply = h("button", {
    class: "btn primary",
    onclick: async () => {
      apply.disabled = true;
      try {
        const errors = await api.applySkills(garden.id, modes);
        if (errors.length) toast(errors.join("\n"), "error");
        else toast("Skills written to every repo's .claude/settings.local.json");
        onApplied?.(modes);
      } catch (e) {
        toast(errText(e), "error");
      } finally {
        apply.disabled = false;
      }
    },
  }, "Apply skills");

  drawRows();
  return h("div", { class: "card skills" },
    h("div", { class: "row between" },
      h("h3", {}, "Context Garden skills"),
      h("label", { class: "inline" }, h("span", { class: "faint small" }, "Preset"), presetSel)),
    h("p", { class: "muted small" }, rec.reason,
      " on = agents use it automatically, manual = only when you call it. Needs the context-garden plugin installed in Claude Code."),
    rows,
    h("div", { class: "row end" }, apply));
}
