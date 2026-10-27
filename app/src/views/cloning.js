import { api } from "../api.js";
import { h, toast, errText } from "../ui.js";

/** On disk and usable. */
export const READY = new Set(["cloned", "linked", "local"]);
const ACTIVE = new Set(["pending", "cloning"]);

const PLAN_LABEL = {
  pending: "to clone", cloning: "cloning…", cloned: "cloned", linked: "linked", local: "already on disk", failed: "failed",
};
const label = (r) => (r.status === "pending" && r.source ? "to link" : PLAN_LABEL[r.status] || r.status);
const groupName = (key) => key || "Single repositories";

/** Repo indices by the group / org / folder they came from; single repos last. */
function byGroup(repos) {
  const map = new Map();
  repos.forEach((r, i) => {
    const key = r.group || "";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(i);
  });
  return [...map].sort(([a], [b]) => (a === "") - (b === ""));
}

function bar() {
  const fill = h("span", { class: "fill" });
  const bad = h("span", { class: "fill bad" });
  return { el: h("span", { class: "bar" }, fill, bad), fill, bad };
}

function setBar(b, done, failed, total) {
  b.fill.style.width = `${total ? (100 * done) / total : 0}%`;
  b.bad.style.width = `${total ? (100 * failed) / total : 0}%`;
}

/** Plan preview: every repo, grouped; groups fold open on click. */
export function planList(repos) {
  const groups = byGroup(repos);
  return h("div", { class: "cl" }, groups.map(([key, idx]) => {
    const todo = idx.filter((i) => repos[i].status === "pending").length;
    return h("details", { class: "cl-group", open: groups.length === 1 },
      h("summary", { class: "cl-head" },
        h("span", { class: "cl-name ellipsis" }, groupName(key)),
        h("span", { class: "faint small" }, `${idx.length} repos · ${todo} to fetch`)),
      h("ul", { class: "repo-list" }, idx.map((i) => {
        const r = repos[i];
        return h("li", { class: `st-${r.status}` },
          h("span", { class: "mono ellipsis" }, r.path),
          h("span", { class: "small", title: r.error || "" }, label(r), r.error ? `: ${r.error}` : ""));
      })));
  }));
}

/** Keep what the user looks at in place while rows above it come and go. */
function keepScroll(scroller, mutate) {
  if (!scroller || !scroller.scrollTop) return mutate();
  const top = scroller.getBoundingClientRect().top;
  const anchor = [...scroller.querySelectorAll(".cl-head")].find((e) => e.getBoundingClientRect().bottom > top);
  const before = anchor?.getBoundingClientRect().top;
  mutate();
  if (anchor?.isConnected) scroller.scrollTop += anchor.getBoundingClientRect().top - before;
}

/**
 * Live clone progress for a garden: an overall bar, then one section per group
 * with its own bar. Repos still to do are listed; finished ones fold into the
 * group's count, and a group with nothing left collapses to one line. Failures
 * stay listed with their reason and can be retried. Rows update in place.
 */
export function cloneBoard(garden, { onReady } = {}) {
  let repos = garden.repos;
  const sections = new Map();
  const rows = new Map(); // index -> { li, where }
  const title = h("h3", {});
  const total = bar();
  const counts = h("span", { class: "muted small" });
  const retryAll = h("button", { class: "btn", hidden: true, onclick: () => retry([]) }, "Retry failed");
  const body = h("div", { class: "cl" });
  const el = h("div", { class: "card" },
    h("div", { class: "row between" }, title, h("div", { class: "row" }, counts, retryAll)),
    total.el,
    body);

  async function retry(paths) {
    try {
      const g = await api.retry(garden.id, paths);
      setRepos(g.repos);
    } catch (e) {
      toast(errText(e), "error");
    }
  }

  function section(key) {
    if (sections.has(key)) return sections.get(key);
    const b = bar();
    const s = {
      bar: b,
      count: h("span", { class: "faint small" }),
      queue: h("ul", { class: "repo-list cl-queue" }),
      failedList: h("ul", { class: "repo-list" }),
      failedSummary: h("summary", { class: "small bad" }),
      retry: h("button", { class: "btn ghost", onclick: () => retry(s.failedPaths()) }, "Retry"),
    };
    s.failedPaths = () => (sections.get(key).idx || []).filter((i) => repos[i].status === "failed").map((i) => repos[i].path);
    s.failed = h("details", { class: "cl-failed" },
      s.failedSummary, h("div", { class: "row end" }, s.retry), s.failedList);
    s.el = h("div", { class: "cl-group" },
      h("div", { class: "cl-head" }, h("span", { class: "cl-name ellipsis" }, groupName(key)), b.el, s.count),
      s.queue, s.failed);
    sections.set(key, s);
    body.append(s.el);
    return s;
  }

  function row(i) {
    const r = repos[i];
    const status = h("span", { class: "small" });
    const why = h("div", { class: "faint small mono why" });
    const li = h("li", {},
      h("div", { class: "ellipsis" }, h("span", { class: "mono" }, r.name), why),
      h("div", { class: "row" }, status,
        h("button", { class: "icon", title: "Retry", hidden: true, onclick: () => retry([repos[i].path]) }, "↻")));
    const entry = { li, status, why, where: null };
    rows.set(i, entry);
    return entry;
  }

  /** Put row i where its status says: queue, failed list, or nowhere (done). */
  function place(i, s) {
    const r = repos[i];
    const where = ACTIVE.has(r.status) ? "queue" : r.status === "failed" ? "failed" : null;
    let entry = rows.get(i);
    if (!entry && !where) return;
    entry ||= row(i);
    entry.li.className = `st-${r.status}`;
    entry.status.textContent = r.status === "cloning" ? (r.source ? "linking…" : "cloning…") : label(r);
    entry.why.textContent = r.status === "failed" ? r.error || "" : "";
    entry.li.querySelector(".icon").hidden = r.status !== "failed";
    if (entry.where === where) return;
    const box = entry.li.parentElement;
    if (box && entry.li.offsetTop < box.scrollTop) box.scrollTop -= entry.li.offsetHeight;
    entry.li.remove();
    entry.where = where;
    if (where === "queue") s.queue.append(entry.li);
    else if (where === "failed") s.failedList.append(entry.li);
    else rows.delete(i);
  }

  function sync() {
    keepScroll(el.closest("main"), () => {
      let done = 0, failed = 0, active = 0;
      for (const [key, idx] of byGroup(repos)) {
        const s = section(key);
        s.idx = idx;
        for (const i of idx) place(i, s);
        const d = idx.filter((i) => READY.has(repos[i].status)).length;
        const f = idx.filter((i) => repos[i].status === "failed").length;
        const a = idx.length - d - f;
        done += d; failed += f; active += a;
        setBar(s.bar, d, f, idx.length);
        s.count.textContent = `${d}/${idx.length}${f ? ` · ${f} failed` : ""}`;
        s.queue.hidden = !a;
        s.failed.hidden = !f;
        s.failedSummary.textContent = `${f} failed: show why`;
        s.retry.disabled = !!active;
        s.el.classList.toggle("done", !a && !f);
      }
      const n = repos.length;
      setBar(total, done, failed, n);
      counts.textContent = `${done}/${n} in${failed ? ` · ${failed} failed` : ""}`;
      title.textContent = active ? `Fetching ${n - active} of ${n}…` : failed ? `Done, ${failed} failed` : "All repositories are in";
      retryAll.hidden = !failed;
      retryAll.disabled = !!active;
      for (const s of sections.values()) s.retry.disabled = !!active;
      el.querySelectorAll(".cl-failed .icon").forEach((b) => (b.disabled = !!active));
    });
  }

  function setRepos(next) {
    repos = next;
    garden.repos = next;
    sync();
  }

  let offProgress = () => {};
  let offReady = () => {};
  /** Follow backend events until disposed. Call once the board is shown. */
  async function watch() {
    offProgress = await api.on("garden-progress", ({ payload }) => {
      if (payload.garden !== garden.id) return;
      repos[payload.index] = payload.repo;
      sync();
    });
    offReady = await api.on("garden-ready", async ({ payload }) => {
      if (payload !== garden.id) return;
      const g = await api.garden(garden.id);
      setRepos(g.repos);
      onReady?.(g);
    });
    // The run may have ended before we subscribed.
    const g = await api.garden(garden.id);
    setRepos(g.repos);
    if (!g.repos.some((r) => ACTIVE.has(r.status))) onReady?.(g);
  }

  sync();
  return { el, watch, setRepos, dispose: () => { offProgress(); offReady(); } };
}
