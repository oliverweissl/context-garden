import { api } from "../api.js";
import { h, toast, errText, setCrumbs } from "../ui.js";
import { skillPicker } from "./skills.js";
import { cloneBoard, planList } from "./cloning.js";

const lines = (text) => text.split("\n").map((s) => s.trim()).filter(Boolean);
export const slug = (name) => name.trim().toLowerCase().replace(/[^a-z0-9._]+/g, "-").replace(/^[-._]+|[-._]+$/g, "");

export const SOURCES_HINT = "https://gitlab.com/acme/platform        (group, with subgroups)\n"
  + "https://github.com/acme                 (org)\n"
  + "git@github.com:acme/api.git             (repo)\n"
  + "~/code/existing-repo                    (folder on disk, linked)\n"
  + "~/code                                  (folder of repos, each linked)";
export const LINK_HELP = "Folders on disk are linked into the garden (symlinks), never copied or moved: "
  + "a simulated garden over checkouts you already have. A folder holding several repos links each of them.";

/** Plan summary card. `again` re-runs the lookup (failed group listings are warnings). */
export function planCard(plan, again) {
  const toFetch = plan.repos.filter((r) => r.status === "pending").length;
  return h("div", { class: "card" },
    h("div", { class: "row between" },
      h("h3", {}, `${plan.repos.length} repositories`),
      h("span", { class: "muted small" }, `${plan.groups.length} groups · ${toFetch} to fetch`)),
    plan.groups.map((g) => h("div", { class: "small muted" }, `${g.provider}: ${g.input} → ${g.repos} repos`)),
    plan.warnings.length
      ? h("div", { class: "row between" },
        h("div", {}, plan.warnings.map((w) => h("div", { class: "small warn" }, w))),
        h("button", { class: "btn", onclick: again }, "Look up again"))
      : null,
    planList(plan.repos));
}

export async function renderPlant(app) {
  setCrumbs(h("span", {}, "Start a garden"));
  const defaultRoot = await api.defaultRoot();
  const sep = defaultRoot.includes("\\") ? "\\" : "/";

  // The garden folder is always <location>/<garden name>, so it is easy to find.
  const name = h("input", { placeholder: "e.g. payments-platform" });
  const parentDir = h("input", { value: defaultRoot, class: "mono" });
  const folder = h("div", { class: "faint small mono ellipsis" });
  const rootPath = () => parentDir.value.trim().replace(/[\\/]+$/, "") + sep + (slug(name.value) || "…");
  const showFolder = () => (folder.textContent = `Garden folder: ${rootPath()}`);
  showFolder();
  const browse = h("button", {
    class: "btn",
    onclick: async () => {
      const dir = await api.pickFolder(parentDir.value.trim() || defaultRoot);
      if (dir) { parentDir.value = dir; parentDir.dispatchEvent(new Event("input")); }
    },
  }, "Browse");
  const sources = h("textarea", { rows: 6, class: "mono", placeholder: SOURCES_HINT });
  const flat = h("input", { type: "checkbox" });
  const flatHelp = "Off: repos from groups keep their structure, e.g. platform/backend/api. "
    + "On: every repo sits directly in the garden folder; clashing names get their group as prefix.";
  const protocol = h("select", {}, h("option", { value: "ssh" }, "SSH"), h("option", { value: "https" }, "HTTPS"));

  const preview = h("div");
  const go = h("button", { class: "btn primary" }, "Find repositories");
  let plan = null;
  let stopWatching = null;

  const args = () => ({
    root: rootPath(), flat: flat.checked, protocol: protocol.value, inputs: lines(sources.value),
  });

  async function lookup() {
    go.disabled = true;
    go.textContent = "Looking up…";
    plan = null;
    try {
      plan = await api.plan(args());
    } catch (e) {
      toast(errText(e), "error");
    }
    go.disabled = false;
    if (!plan) return (go.textContent = "Find repositories");
    preview.replaceChildren(planCard(plan, lookup));
    go.textContent = plan.repos.length ? "Plant garden" : "Find repositories";
    if (!plan.repos.length) plan = null;
  }

  go.onclick = async () => {
    if (!name.value.trim()) return toast("Give the garden a name", "error");
    if (!plan) return lookup();
    go.disabled = true;
    try {
      const a = args();
      const garden = await api.create({
        name: name.value, root: a.root, layout: a.flat ? "flat" : "nested", protocol: a.protocol,
        repoInputs: plan.repo_inputs, groupInputs: plan.group_inputs, repos: plan.repos,
      });
      stopWatching = await growing(app, garden);
    } catch (e) {
      toast(errText(e), "error");
      go.disabled = false;
    }
  };
  // Changing inputs invalidates the looked-up plan.
  for (const el of [name, parentDir, sources, flat, protocol]) {
    el.addEventListener("input", () => { showFolder(); plan = null; go.textContent = "Find repositories"; preview.replaceChildren(); });
  }

  app.append(
    h("section", { class: "hero" }, h("p", { class: "kicker" }, "new garden"), h("h1", {}, "Start a garden")),
    h("div", { class: "card form" },
      h("label", {}, h("span", {}, "Name"), name),
      h("label", {}, h("span", {}, "Location"), h("div", { class: "row" }, parentDir, browse), folder),
      h("label", {}, h("span", {}, "Sources", h("em", {}, "one per line: repos, folders, GitLab groups, GitHub orgs"),
        h("span", { class: "info", title: LINK_HELP }, "i")), sources),
      h("div", { class: "row between" },
        h("label", { class: "check", title: flatHelp }, flat, "Enable flat layout",
          h("span", { class: "info", title: flatHelp }, "i")),
        h("label", { class: "inline" }, h("span", {}, "Clone over"), protocol)),
      h("div", { class: "row end" }, go)),
    preview);
  return () => stopWatching?.();
}

/** Clone progress, then the skill step. */
async function growing(app, garden) {
  const step = h("div");
  let picked = false;
  const board = cloneBoard(garden, {
    onReady: async (g) => {
      if (picked) return;
      picked = true;
      step.replaceChildren(await skillPicker(g, () => (location.hash = `#/garden/${encodeURIComponent(g.id)}`)));
    },
  });
  app.replaceChildren(
    h("section", { class: "hero" }, h("p", { class: "kicker" }, "planting"), h("h1", {}, garden.name)),
    board.el,
    step);
  await board.watch();
  return board.dispose;
}
