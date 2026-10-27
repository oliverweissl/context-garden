import { api } from "../api.js";
import { h, toast, errText, SKILL_COLORS } from "../ui.js";
import { gardenArt } from "../art.js";

export async function renderHome(app) {
  let gardens = [];
  try {
    gardens = await api.gardens();
  } catch (e) {
    toast(errText(e), "error");
  }
  const cards = gardens.map((g) => {
    const groups = g.group_inputs.length;
    const failed = g.repos.filter((r) => r.status === "failed").length;
    const on = Object.entries(g.skills).filter(([, m]) => m === "on").map(([s]) => s);
    return h("a", { class: "card garden-card", href: `#/garden/${encodeURIComponent(g.id)}` },
      gardenArt(g.id, g.repos.length),
      h("h3", {}, g.name),
      h("div", { class: "muted mono small ellipsis" }, g.root),
      h("div", { class: "counts" },
        h("span", {}, h("b", {}, g.repos.length), g.repos.length === 1 ? " repo" : " repos"),
        groups ? h("span", {}, h("b", {}, groups), groups === 1 ? " group" : " groups") : null,
        failed ? h("span", { class: "bad" }, h("b", {}, failed), " failed") : null),
      h("div", { class: "dots" },
        on.length
          ? on.map((s) => h("span", { class: "dot", title: s, style: `background:${SKILL_COLORS[s]}` }))
          : h("span", { class: "faint small" }, "no skills chosen")));
  });

  app.append(
    h("section", { class: "hero" },
      h("p", { class: "kicker" }, "v0.4"),
      h("h1", {}, "Your Gardens")),
    h("div", { class: "grid" },
      ...cards,
      h("a", { class: "card add-card", href: "#/new" },
        h("span", { class: "plus" }, "+"),
        h("span", {}, "Start a garden"))));
}
