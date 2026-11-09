// Dev only: lets `npm run dev` render in a plain browser (no Tauri) with fake
// data, for UI work. Never bundled into the app (see api.js).
const gardens = [
  {
    id: "payments-platform", name: "payments-platform", root: "/Users/me/gardens/payments-platform",
    layout: "nested", protocol: "ssh", repo_inputs: [], group_inputs: ["https://gitlab.com/acme/payments"],
    repos: ["ledger", "gateway", "fraud", "notifications", "api-schema", "web"].map((name, i) => ({
      name, path: `/Users/me/gardens/payments-platform/payments/${name}`, group: "acme/payments",
      status: i === 2 ? "failed" : "cloned", error: i === 2 ? "git@gitlab.com: Permission denied (publickey)." : null,
    })),
    skills: { greenhouse: "on", seedbank: "on", pruner: "on", compost: "on", mycelium: "manual", weeder: "manual" },
    created_at: 0,
    terminals: [
      { key: "a", title: "Claude Code", cwd: "/Users/me/gardens/payments-platform", cwd_label: "garden root", agent: "claude", session: "0f8e2c1a-1b2c-4d5e-8f90-123456789abc", open: true, used_at: 0 },
      { key: "b", title: "Terminal", cwd: "/Users/me/gardens/payments-platform/payments/ledger", cwd_label: "ledger", open: true, used_at: 0 },
      { key: "c", title: "Codex · work", cwd: "/Users/me/gardens/payments-platform", cwd_label: "garden root", agent: null, profile: "claude-work", open: false, used_at: Math.floor(Date.now() / 1000) - 7200 },
    ],
  },
  {
    id: "solo-tool", name: "solo-tool", root: "/Users/me/gardens/solo-tool", layout: "flat", protocol: "ssh",
    repo_inputs: ["~/code/tool"], group_inputs: [],
    repos: [{ name: "tool", path: "/Users/me/code/tool", status: "local" }], skills: {}, created_at: 0,
  },
];

const presets = [
  { name: "solo", description: "One repository", on: ["seedbank", "pruner", "compost"] },
  { name: "platform", description: "Several repos that call each other", on: ["greenhouse", "seedbank", "pruner", "compost"] },
];

const MOCK_AGENT = [
  "⏺ The webhook handler is in place. Before I wire it up, please do these steps yourself:",
  "",
  "  1. Create a signing secret in the payment provider's dashboard",
  "  2. Add it to .env as WEBHOOK_SECRET",
  "  3. Run the migration: npm run db:migrate",
  "  4. Restart the dev server",
  "",
  "  Tell me when you're done.",
  "",
  "> ",
].join("\r\n");

const handlers = {
  terminal_previews: () => ({}),
  default_root: () => "/Users/me/gardens",
  list_gardens: () => gardens,
  get_garden: ({ id }) => gardens.find((g) => g.id === id),
  plan_garden: () => ({
    repos: [
      ...gardens[0].repos.map((r) => ({ ...r, status: "pending", error: null })),
      { name: "tool", path: "/Users/me/gardens/x/tool", source: "/Users/me/code/tool", status: "pending" },
    ],
    groups: [{ input: "https://gitlab.com/acme/payments", provider: "gitlab", repos: 6 }],
    warnings: ["https://github.com/acme-old: gh: Not Found (HTTP 404)"], repo_inputs: [], group_inputs: ["x"],
  }),
  // Fails again right away, reporting before the reply like a quick real failure.
  retry_repos: ({ id }) => {
    const g = gardens.find((x) => x.id === id);
    g.repos.forEach((repo, index) => repo.status === "failed" && emit("garden-progress", { garden: id, index, repo }));
    emit("garden-ready", id);
    return g;
  },
  forget_missing_repos: ({ id }) => gardens.find((g) => g.id === id),
  resync_groups: ({ id }) => {
    const g = gardens.find((x) => x.id === id);
    return {
      repos: [{ name: "payouts", path: `${g.root}/payments/payouts`, group: "acme/payments", status: "pending" }],
      groups: [{ input: "https://gitlab.com/acme/payments", provider: "gitlab", repos: 6 }],
      warnings: [], repo_inputs: [], group_inputs: g.group_inputs,
      gone: g.repos.filter((r) => r.name === "fraud"),
    };
  },
  forget_repos: ({ id, paths }) => {
    const g = gardens.find((x) => x.id === id);
    g.repos = g.repos.filter((r) => !paths.includes(r.path));
    return g;
  },
  add_to_garden: ({ id }) => gardens.find((g) => g.id === id),
  recommend_skills: () => ({ preset: "platform", reason: "6 repositories: greenhouse shares what each repo provides.", modes: gardens[0].skills, presets }),
  apply_skills: () => [],
  garden_stats: ({ id }) => {
    const g = gardens.find((x) => x.id === id);
    return {
      repos: g.repos.map((r, i) => ({
        ...r, branch: "main", changed: i % 3 ? 0 : 2, agent_tokens: 400 + i * 90, seedbank_facts: i,
        files: 120 + i * 40, lines: [42000, 18000, 0, 7600, 2300, 31000][i] ?? 900,
      })),
      files: 1200, lines: 100900,
      languages: { ts: 41000, py: 22000, rs: 15000, md: 8000, json: 6000, yaml: 3000, sql: 2400, sh: 1200, other: 2300 },
      groups: g.group_inputs.length, namespaces: 1, dirty: 2, missing: 0, agent_tokens: 3800, seedbank_facts: 15, greenhouse_cards: 6,
      accounts: [
        { name: "Work", agent: "claude", icon: "◆", color: "#5bb0ff", sessions: 29, input: 90000, output: 260000, cache_read: 6100000, cache_write: 420000 },
        { name: "Personal", agent: "claude", icon: "✿", color: "#ff7a8f", sessions: 8, input: 20000, output: 80000, cache_read: 2300000, cache_write: 160000 },
        { name: "Codex (default)", agent: "codex", icon: "", color: "", sessions: 4, input: 10000, output: 40000, cache_read: 800000, cache_write: 0 },
      ],
      days: 30,
    };
  },
  list_agents: () => [
    { id: "claude", label: "Claude Code", found: true, profiles: true },
    { id: "codex", label: "Codex", found: true, profiles: true },
    { id: "gemini", label: "Gemini CLI", found: false, profiles: false },
  ],
  list_profiles: () => [{ id: "claude-work", name: "work", agent: "claude", dir: "~/.claude-work", icon: "★", color: "#f0b43c" }],
  profile_plugin_version: () => "0.3.1",
  profile_plugin_outdated: () => true,
  account_plugins: () => [
    { id: "context-garden@context-garden", version: "0.4.2", latest: null, stamp: "a" },
    { id: "ase@ase", version: "1.0.0", latest: "1.1.0", stamp: "b" },
  ],
  update_account_plugin: () => null,
  terminal_locations: ({ panes }) => Object.fromEntries(panes.map((p) => [p.key, "ledger"])),
  terminal_spawn: ({ onEvent, agent, profile }) => {
    // Agents print steps for the user, so the checklist chip shows up (checklist.js).
    const text = agent || profile ? MOCK_AGENT : "mock terminal $ ";
    const b64 = btoa(String.fromCharCode(...new TextEncoder().encode(text)));
    setTimeout(() => onEvent.onmessage({ event: "output", data: b64 }), 50);
    return { id: 1, worktree: null };
  },
  worktree_release: () => null,
  worktree_sweep: () => [],
  get_settings: () => ({ worktrees: true }),
  set_settings: () => null,
};

const listeners = new Map();
const emit = (name, payload) => (listeners.get(name) || []).forEach((cb) => cb({ payload }));

export async function mockListen(name, cb) {
  listeners.set(name, [...(listeners.get(name) || []), cb]);
  return () => listeners.set(name, listeners.get(name).filter((x) => x !== cb));
}

export function mockInvoke(cmd, args) {
  const fn = handlers[cmd];
  return fn ? Promise.resolve(fn(args || {})) : Promise.resolve(null);
}
