import { invoke as tauriInvoke, Channel } from "@tauri-apps/api/core";
import { listen as tauriListen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";

// `npm run dev` in a plain browser: fake backend for UI work (dropped from builds).
const inTauri = "__TAURI_INTERNALS__" in window;
const mock = !inTauri && import.meta.env.DEV ? await import("./mock.js") : null;
const invoke = mock ? mock.mockInvoke : tauriInvoke;
const listen = mock ? async () => () => {} : tauriListen;

export const api = {
  defaultRoot: () => invoke("default_root"),
  gardens: () => invoke("list_gardens"),
  garden: (id) => invoke("get_garden", { id }),
  plan: (args) => invoke("plan_garden", { garden: null, ...args }),
  create: (args) => invoke("create_garden", args),
  addToGarden: (id, plan) => invoke("add_to_garden", {
    id, repoInputs: plan.repo_inputs, groupInputs: plan.group_inputs, repos: plan.repos,
  }),
  retry: (id, paths = []) => invoke("retry_repos", { id, paths }),
  forget: (id) => invoke("forget_garden", { id }),
  rename: (id, name) => invoke("rename_garden", { id, name }),
  recommend: (id) => invoke("recommend_skills", { id }),
  applySkills: (id, modes) => invoke("apply_skills", { id, modes }),
  inspectPlugins: (source) => invoke("inspect_plugin_source", { source }),
  addPlugins: (id, info, plugins) => invoke("add_garden_plugins", {
    id, source: info.source, marketplace: info.marketplace, plugins,
  }),
  removePlugin: (id, plugin) => invoke("remove_garden_plugin", { id, plugin }),
  stats: (id) => invoke("garden_stats", { id }),
  agents: () => invoke("list_agents"),
  profiles: () => invoke("list_profiles"),
  addProfile: (name, agent, dir, icon, color) => invoke("add_profile", { name, agent, dir: dir || null, icon, color }),
  /** Installed context-garden version in an account, or null. */
  pluginVersion: (id) => invoke("profile_plugin_version", { id }),
  installPlugin: (id) => invoke("install_profile_plugin", { id }),
  removeProfile: (id) => invoke("remove_profile", { id }),
  copy: (text) => (mock ? navigator.clipboard.writeText(text) : invoke("clipboard_write", { text })),
  pasteText: () => (mock ? navigator.clipboard.readText() : invoke("clipboard_read")),
  pickFolder: (defaultPath) => open({ directory: true, defaultPath }),
  on: listen,

  /** Start a terminal; onOutput gets Uint8Array chunks, onExit the exit code. */
  spawn: ({ garden, cwd, agent, profile, session, resume, flags, cols, rows }, onOutput, onExit) =>
    pty("terminal_spawn", {
      garden, cwd, agent: agent || null, profile: profile || null, session: session || null, resume: !!resume,
      flags: flags || [], cols, rows,
    }, onOutput, onExit),
  saveTerminals: (id, terminals) => invoke("save_terminals", { id, terminals }),
  /** Terminal running an account's sign-in. */
  login: ({ profile, cols, rows }, onOutput, onExit) =>
    pty("profile_login", { id: profile, cols, rows }, onOutput, onExit),
  write: (id, data) => invoke("terminal_write", { id, data }),
  resize: (id, cols, rows) => invoke("terminal_resize", { id, cols, rows }),
  kill: (id) => invoke("terminal_kill", { id }),
};

function pty(cmd, args, onOutput, onExit) {
  const channel = mock ? {} : new Channel();
  channel.onmessage = (msg) => {
    if (msg.event === "output") {
      const bin = atob(msg.data);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      onOutput(bytes);
    } else {
      onExit(msg.data);
    }
  };
  return invoke(cmd, { ...args, onEvent: channel });
}
