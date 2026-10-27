//! ContextGarden desktop app: Tauri commands the web UI calls.

mod plugins;
mod pty;
mod skills;
mod sources;
mod stats;
mod store;
mod updates;
mod util;

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};
use std::path::Path;
use std::sync::Mutex;
use tauri::ipc::Channel;
use tauri::{AppHandle, Emitter, Manager, RunEvent, State};

use pty::{Launch, PtyEvent, Ptys};
use skills::PLUGIN;
use store::{Garden, GardenPlugin, Profile, Repo, SavedTerminal, Store};
use util::{expand_tilde, home_dir, now_secs, run_env, slugify, which};

/// (id, label, binary, env var that selects its config dir / account).
const AGENTS: [(&str, &str, &str, Option<&str>); 3] = [
    ("claude", "Claude Code", "claude", Some("CLAUDE_CONFIG_DIR")),
    ("codex", "Codex", "codex", Some("CODEX_HOME")),
    ("gemini", "Gemini CLI", "gemini", None),
];

#[derive(Serialize)]
struct AgentInfo {
    id: &'static str,
    label: &'static str,
    found: bool,
    profiles: bool,
}

#[derive(Clone, Serialize)]
struct Progress {
    garden: String,
    index: usize,
    repo: Repo,
}

#[tauri::command]
fn default_root() -> String {
    home_dir().join("gardens").to_string_lossy().into()
}

#[tauri::command]
fn list_gardens(store: State<Store>) -> Result<Vec<Garden>, String> {
    store.gardens()
}

#[tauri::command]
fn get_garden(store: State<Store>, id: String) -> Result<Garden, String> {
    store.garden(&id)
}

/// Plan a new garden, or (with `garden`) more repos for an existing one, which
/// then decides root, layout and protocol.
#[tauri::command]
async fn plan_garden(
    store: State<'_, Store>,
    garden: Option<String>,
    root: String,
    flat: bool,
    protocol: String,
    inputs: Vec<String>,
) -> Result<sources::Plan, String> {
    match garden {
        Some(id) => {
            let g = store.garden(&id)?;
            sources::plan(&g.root, g.layout == "flat", &g.protocol, &inputs, &g.repos)
        }
        None => sources::plan(&root, flat, &protocol, &inputs, &[]),
    }
}

/// Gardens with a clone run in progress; a second run would clone twice.
#[derive(Default)]
struct Cloning(Mutex<HashSet<String>>);

/// Change a garden with `prepare`, then clone/link its pending repos in the
/// background, reporting each over `garden-progress` and the end as `garden-ready`.
fn clone_in_background(
    app: &AppHandle,
    id: &str,
    prepare: impl FnOnce(&mut Garden) -> Result<(), String>,
) -> Result<Garden, String> {
    let cloning = app.state::<Cloning>();
    let mut busy = cloning.0.lock().unwrap();
    if busy.contains(id) {
        return Err("still cloning this garden; try again when it is done".into());
    }
    let garden = app.state::<Store>().update_garden(id, |g| {
        prepare(g)?;
        Ok(g.clone())
    })?;
    busy.insert(id.to_string());
    drop(busy);

    let app = app.clone();
    let id = garden.id.clone();
    let repos = garden.repos.clone();
    std::thread::spawn(move || {
        let report_app = app.clone();
        let report_id = id.clone();
        sources::clone_all(repos, move |index, repo| {
            let store = report_app.state::<Store>();
            let _ = store.update_garden(&report_id, |g| {
                if let Some(slot) = g.repos.get_mut(index) {
                    *slot = repo.clone();
                }
                Ok(())
            });
            let _ = report_app.emit(
                "garden-progress",
                Progress { garden: report_id.clone(), index, repo: repo.clone() },
            );
        });
        // Repos that came in after the skills were chosen (added, retried) need
        // the same settings, or the skills read "off" in them.
        if let Ok(g) = app.state::<Store>().garden(&id) {
            if !g.skills.is_empty() {
                for e in skills::apply(&g, &g.skills) {
                    eprintln!("skills: {e}");
                }
            }
        }
        app.state::<Cloning>().0.lock().unwrap().remove(&id);
        let _ = app.emit("garden-ready", id);
    });
    Ok(garden)
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
fn create_garden(
    app: AppHandle,
    store: State<Store>,
    name: String,
    root: String,
    layout: String,
    protocol: String,
    repo_inputs: Vec<String>,
    group_inputs: Vec<String>,
    repos: Vec<Repo>,
) -> Result<Garden, String> {
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err("give the garden a name".into());
    }
    let root_path = expand_tilde(&root);
    std::fs::create_dir_all(&root_path).map_err(|e| format!("{}: {e}", root_path.display()))?;
    let garden = store.update_gardens(|all| {
        let base = slugify(&name);
        let mut id = base.clone();
        let mut n = 1;
        while all.iter().any(|g| g.id == id) {
            n += 1;
            id = format!("{base}-{n}");
        }
        let g = Garden {
            id,
            name,
            root: root_path.to_string_lossy().into(),
            layout,
            protocol,
            repo_inputs,
            group_inputs,
            repos,
            skills: BTreeMap::new(),
            created_at: now_secs(),
            terminals: Vec::new(),
            plugins: Vec::new(),
        };
        all.push(g.clone());
        Ok(g)
    })?;
    clone_in_background(&app, &garden.id, |_| Ok(()))
}

/// Add repos (planned with `plan_garden` for this garden) and clone them.
#[tauri::command]
fn add_to_garden(
    app: AppHandle,
    id: String,
    repo_inputs: Vec<String>,
    group_inputs: Vec<String>,
    repos: Vec<Repo>,
) -> Result<Garden, String> {
    clone_in_background(&app, &id, |g| {
        for r in repos {
            if !g.repos.iter().any(|x| x.path == r.path) {
                g.repos.push(r);
            }
        }
        for (have, new) in [(&mut g.repo_inputs, repo_inputs), (&mut g.group_inputs, group_inputs)] {
            for i in new {
                if !have.contains(&i) {
                    have.push(i);
                }
            }
        }
        Ok(())
    })
}

/// Try failed repos again: those at `paths`, or every failed one when empty.
#[tauri::command]
fn retry_repos(app: AppHandle, id: String, paths: Vec<String>) -> Result<Garden, String> {
    clone_in_background(&app, &id, |g| {
        for r in g.repos.iter_mut().filter(|r| r.status == "failed") {
            if paths.is_empty() || paths.contains(&r.path) {
                r.status = "pending".into();
                r.error = None;
            }
        }
        Ok(())
    })
}

/// Forget a garden. Files on disk are never touched.
#[tauri::command]
fn forget_garden(store: State<Store>, id: String) -> Result<(), String> {
    store.update_gardens(|all| {
        all.retain(|g| g.id != id);
        Ok(())
    })
}

#[tauri::command]
fn rename_garden(store: State<Store>, id: String, name: String) -> Result<(), String> {
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err("name cannot be empty".into());
    }
    store.update_garden(&id, |g| {
        g.name = name;
        Ok(())
    })
}

#[tauri::command]
fn recommend_skills(store: State<Store>, id: String) -> Result<skills::Recommendation, String> {
    Ok(skills::recommend(&store.garden(&id)?))
}

#[tauri::command]
fn apply_skills(
    store: State<Store>,
    id: String,
    modes: BTreeMap<String, String>,
) -> Result<Vec<String>, String> {
    let garden = store.garden(&id)?;
    let errors = skills::apply(&garden, &modes);
    store.update_garden(&id, |g| {
        g.skills = modes;
        Ok(())
    })?;
    Ok(errors)
}

#[tauri::command]
async fn garden_stats(store: State<'_, Store>, id: String) -> Result<stats::GardenStats, String> {
    let garden = store.garden(&id)?;
    let profiles = store.profiles()?;
    Ok(stats::collect(&garden, &profiles))
}

#[tauri::command]
fn list_agents() -> Vec<AgentInfo> {
    AGENTS
        .iter()
        .map(|(id, label, bin, env)| AgentInfo {
            id,
            label,
            found: which(bin).is_some(),
            profiles: env.is_some(),
        })
        .collect()
}

#[tauri::command]
fn list_profiles(store: State<Store>) -> Result<Vec<Profile>, String> {
    store.profiles()
}

/// Add a named account. Its config dir defaults to <app data>/profiles/<id>;
/// the agent CLI stores the login there itself on first launch.
#[tauri::command]
fn add_profile(
    store: State<Store>,
    name: String,
    agent: String,
    dir: Option<String>,
    icon: String,
    color: String,
) -> Result<Profile, String> {
    let name = name.trim().to_string();
    if name.is_empty() {
        return Err("give the account a name".into());
    }
    if !AGENTS.iter().any(|a| a.0 == agent && a.3.is_some()) {
        return Err(format!("'{agent}' does not support separate accounts"));
    }
    let profiles_dir = store.dir.join("profiles");
    store.update_profiles(|all| {
        let base = format!("{agent}-{}", slugify(&name));
        let mut id = base.clone();
        let mut n = 1;
        while all.iter().any(|p| p.id == id) {
            n += 1;
            id = format!("{base}-{n}");
        }
        let dir = match dir.map(|d| d.trim().to_string()).filter(|d| !d.is_empty()) {
            Some(d) => expand_tilde(&d),
            None => profiles_dir.join(&id),
        };
        std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        // A short name or an emoji (the UI caps it at 4 visible characters).
        let icon: String = icon.trim().chars().take(16).collect();
        let p = Profile { id, name, agent, dir: dir.to_string_lossy().into(), icon, color };
        all.push(p.clone());
        Ok(p)
    })
}

/// The context-garden plugin as the README installs it.
const MARKETPLACE: (&str, &str) = ("context-garden", "oliverweissl/context-garden");

fn plugin_json(dir: &Path, file: &str, key: &str) -> bool {
    std::fs::read_to_string(dir.join("plugins").join(file))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .is_some_and(|v| v.get("plugins").unwrap_or(&v).get(key).is_some())
}

/// Codex keeps marketplaces and plugins in `config.toml` sections.
fn codex_config_has(dir: &Path, section: &str) -> bool {
    std::fs::read_to_string(dir.join("config.toml")).is_ok_and(|t| t.lines().any(|l| l.trim() == section))
}

#[tauri::command]
fn clipboard_write(text: String) -> Result<(), String> {
    util::clipboard_write(&text)
}

#[tauri::command]
fn clipboard_read() -> Result<String, String> {
    util::clipboard_read()
}

/// Installed version of the context-garden plugin in an account, or None.
/// A fresh config dir has no plugins at all, so its terminals have none of the skills.
#[tauri::command]
fn profile_plugin_version(store: State<Store>, id: String) -> Result<Option<String>, String> {
    let p = store.profiles()?.into_iter().find(|p| p.id == id).ok_or("that account no longer exists")?;
    let dir = expand_tilde(&p.dir);
    Ok(match p.agent.as_str() {
        "claude" => std::fs::read_to_string(dir.join("plugins").join("installed_plugins.json"))
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .and_then(|v| v["plugins"][PLUGIN][0]["version"].as_str().map(String::from)),
        "codex" if codex_config_has(&dir, &format!("[plugins.\"{PLUGIN}\"]")) => {
            // plugins/cache/<marketplace>/<plugin>/<version>
            let cache = dir.join("plugins/cache/context-garden/context-garden");
            let mut versions: Vec<String> = std::fs::read_dir(cache)
                .map(|d| d.flatten().map(|e| e.file_name().to_string_lossy().into()).collect())
                .unwrap_or_default();
            versions.sort();
            Some(versions.pop().unwrap_or_else(|| "installed".into()))
        }
        "codex" => None,
        _ => Some("n/a".into()),
    })
}

/// The commit of main an account's skills were installed from, if known.
fn installed_commit(dir: &Path) -> Option<String> {
    std::fs::read_to_string(dir.join("plugins").join("installed_plugins.json"))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| v["plugins"][PLUGIN][0]["gitCommitSha"].as_str().map(String::from))
}

/// Whether main has moved past the skills an account has. Claude accounts
/// only: they record the commit they installed. Unknown counts as current.
#[tauri::command]
async fn profile_plugin_outdated(store: State<'_, Store>, id: String) -> Result<bool, String> {
    let p = store.profiles()?.into_iter().find(|p| p.id == id).ok_or("that account no longer exists")?;
    if p.agent != "claude" {
        return Ok(false);
    }
    let Some(installed) = installed_commit(&expand_tilde(&p.dir)) else { return Ok(false) };
    Ok(updates::main_commit()? != installed)
}

/// Pull the latest skills from main into an account; it stays switched off
/// account-wide (gardens switch it on).
#[tauri::command]
async fn update_profile_plugin(store: State<'_, Store>, id: String) -> Result<(), String> {
    let p = store.profiles()?.into_iter().find(|p| p.id == id).ok_or("that account no longer exists")?;
    if p.agent != "claude" {
        return Err("only Claude accounts can update here; reinstall the skills for Codex".into());
    }
    if which("claude").is_none() {
        return Err("`claude` is not installed".into());
    }
    let env = [("CLAUDE_CONFIG_DIR", p.dir.as_str())];
    run_env("claude", &["plugin", "marketplace", "update", MARKETPLACE.0], None, &env)?;
    run_env("claude", &["plugin", "update", PLUGIN, "--scope", "user"], None, &env)?;
    // Already off is fine; the update must not leave it on everywhere.
    let _ = run_env("claude", &["plugin", "disable", PLUGIN, "--scope", "user"], None, &env);
    Ok(())
}

/// A release newer than this app, if one is tagged.
#[tauri::command]
async fn app_update(app: AppHandle) -> Result<Option<updates::AppRelease>, String> {
    updates::newer_release(&app.package_info().version.to_string())
}

#[tauri::command]
fn open_repo_page(url: String) -> Result<(), String> {
    updates::open_repo_page(&url)
}

/// Install the plugin into an account with the agent's own plugin commands,
/// run against that account's config dir. Claude: disabled account-wide and
/// enabled per garden folder (see skills.rs). Codex reads the same marketplace
/// and enables it account-wide; outside a garden every skill's mode is off,
/// so the skills decline there.
#[tauri::command]
async fn install_profile_plugin(store: State<'_, Store>, id: String) -> Result<(), String> {
    let p = store.profiles()?.into_iter().find(|p| p.id == id).ok_or("that account no longer exists")?;
    let dir = expand_tilde(&p.dir);
    match p.agent.as_str() {
        "claude" => {
            if which("claude").is_none() {
                return Err("`claude` is not installed".into());
            }
            let env = [("CLAUDE_CONFIG_DIR", p.dir.as_str())];
            if !plugin_json(&dir, "known_marketplaces.json", MARKETPLACE.0) {
                run_env("claude", &["plugin", "marketplace", "add", MARKETPLACE.1], None, &env)?;
            }
            run_env("claude", &["plugin", "install", PLUGIN, "--scope", "user"], None, &env)?;
            run_env("claude", &["plugin", "disable", PLUGIN, "--scope", "user"], None, &env)?;
            // Plugins gardens added from elsewhere, so this account has them too.
            for gp in store.gardens()?.iter().flat_map(|g| g.plugins.iter()) {
                if let Err(e) = plugins::install_claude(&p.dir, &gp.source, &gp.id) {
                    eprintln!("{} for {}: {e}", gp.id, p.name);
                }
            }
        }
        "codex" => {
            if which("codex").is_none() {
                return Err("`codex` is not installed".into());
            }
            let env = [("CODEX_HOME", p.dir.as_str())];
            if !codex_config_has(&dir, &format!("[marketplaces.{}]", MARKETPLACE.0)) {
                run_env("codex", &["plugin", "marketplace", "add", MARKETPLACE.1], None, &env)?;
            }
            run_env("codex", &["plugin", "add", PLUGIN], None, &env)?;
        }
        other => return Err(format!("'{other}' has no plugin support")),
    }
    Ok(())
}

/// What a plugin marketplace offers, read before anything is installed.
#[tauri::command]
async fn inspect_plugin_source(source: String) -> Result<plugins::SourceInfo, String> {
    plugins::inspect(&source)
}

#[derive(Deserialize)]
struct PluginPick {
    name: String,
    #[serde(default)]
    description: String,
}

/// Add plugins from a marketplace to a garden: installed (disabled) into every
/// Claude account, enabled in the garden's folders. Returns per-account problems.
#[tauri::command]
async fn add_garden_plugins(
    store: State<'_, Store>,
    id: String,
    source: String,
    marketplace: String,
    plugins: Vec<PluginPick>,
) -> Result<Vec<String>, String> {
    let source = plugins::normalize(&source)?;
    if marketplace.is_empty() || plugins.is_empty() {
        return Err("pick at least one plugin".into());
    }
    let accounts: Vec<Profile> = store.profiles()?.into_iter().filter(|p| p.agent == "claude").collect();
    if accounts.is_empty() {
        return Err("add a Claude account first: plugins are installed into accounts".into());
    }
    let added: Vec<GardenPlugin> = plugins
        .into_iter()
        .map(|p| GardenPlugin { id: format!("{}@{marketplace}", p.name), source: source.clone(), description: p.description })
        .collect();
    let mut problems = Vec::new();
    for gp in &added {
        for account in &accounts {
            if let Err(e) = plugins::install_claude(&account.dir, &gp.source, &gp.id) {
                problems.push(format!("{} in {}: {e}", gp.id, account.name));
            }
        }
    }
    let garden = store.update_garden(&id, |g| {
        for gp in added {
            g.plugins.retain(|p| p.id != gp.id);
            g.plugins.push(gp);
        }
        Ok(g.clone())
    })?;
    problems.extend(skills::apply(&garden, &garden.skills));
    Ok(problems)
}

/// Stop using a plugin in this garden. Accounts keep it installed (disabled).
#[tauri::command]
fn remove_garden_plugin(store: State<Store>, id: String, plugin: String) -> Result<Vec<String>, String> {
    let garden = store.update_garden(&id, |g| {
        g.plugins.retain(|p| p.id != plugin);
        Ok(g.clone())
    })?;
    Ok(skills::switch_plugin(&garden, &plugin, false))
}

/// Forget an account. Its config dir (and the login inside) stays on disk.
#[tauri::command]
fn remove_profile(store: State<Store>, id: String) -> Result<(), String> {
    store.update_profiles(|all| {
        all.retain(|p| p.id != id);
        Ok(())
    })
}

/// Session ids go into a shell command line: only UUID characters pass.
fn valid_session(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.chars().all(|c| c.is_ascii_hexdigit() || c == '-')
}

/// Claude Code writes a session's transcript only once it has a message; a
/// session that never got one cannot be resumed and is started fresh instead.
fn claude_session_exists(config_dir: &Path, session: &str) -> bool {
    let Ok(projects) = std::fs::read_dir(config_dir.join("projects")) else { return false };
    projects.flatten().any(|p| p.path().join(format!("{session}.jsonl")).is_file())
}

/// The first thing the user typed in a Claude session, to tell saved terminals
/// apart. Slash commands, pasted command output and caveats are skipped.
fn claude_first_prompt(config_dir: &Path, session: &str) -> Option<String> {
    use std::io::{BufRead, BufReader};
    let projects = std::fs::read_dir(config_dir.join("projects")).ok()?;
    let file = projects.flatten().map(|p| p.path().join(format!("{session}.jsonl"))).find(|f| f.is_file())?;
    let reader = BufReader::new(std::fs::File::open(file).ok()?);
    for line in reader.lines().map_while(Result::ok).take(400) {
        let Ok(v) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
        if v["type"] != "user" || v["isMeta"] == true {
            continue;
        }
        let content = &v["message"]["content"];
        let text = content.as_str().map(String::from).or_else(|| {
            content.as_array()?.iter().find(|c| c["type"] == "text")?["text"].as_str().map(String::from)
        });
        let Some(mut text) = text else { continue };
        // Pasted text is wrapped in <pasted_content id=…> tags: keep the text, drop the tags.
        while let Some(start) = text.find("<pasted_content") {
            let end = text[start..].find('>').map_or(text.len(), |e| start + e + 1);
            text.replace_range(start..end, " ");
        }
        let text = text.replace("</pasted_content>", " ").split_whitespace().collect::<Vec<_>>().join(" ");
        if text.is_empty() || text.starts_with('<') || text.starts_with("Caveat:") {
            continue;
        }
        return Some(if text.chars().count() > 90 { format!("{}…", text.chars().take(90).collect::<String>()) } else { text });
    }
    None
}

/// First prompt of each saved Claude terminal of a garden, by terminal key.
#[tauri::command]
fn terminal_previews(store: State<Store>, id: String) -> Result<BTreeMap<String, String>, String> {
    let profiles = store.profiles()?;
    let default_dir = std::env::var_os("CLAUDE_CONFIG_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| home_dir().join(".claude"));
    Ok(store
        .garden(&id)?
        .terminals
        .iter()
        .filter_map(|t| {
            let session = t.session.as_deref().filter(|s| valid_session(s))?;
            let dir = match t.profile.as_deref() {
                Some(pid) => {
                    let p = profiles.iter().find(|p| p.id == pid && p.agent == "claude")?;
                    expand_tilde(&p.dir)
                }
                None if t.agent.as_deref() == Some("claude") => default_dir.clone(),
                None => return None,
            };
            Some((t.key.clone(), claude_first_prompt(&dir, session)?))
        })
        .collect())
}

/// One argument for the shell that runs agents (`sh -lc` / `powershell -Command`),
/// quoted so it stays a single literal argument whatever it contains.
fn shell_quote(arg: &str) -> String {
    if !arg.is_empty() && arg.chars().all(|c| c.is_ascii_alphanumeric() || "-_.=:/@,+%".contains(c)) {
        arg.to_string()
    } else if cfg!(windows) {
        format!("'{}'", arg.replace('\'', "''"))
    } else {
        format!("'{}'", arg.replace('\'', r"'\''"))
    }
}

/// Session flags the app sets itself (resume / session id); users may not pass them.
const RESERVED_FLAGS: [&str; 7] = ["--resume", "-r", "--session-id", "--continue", "-c", "--fork-session", "resume"];

fn check_flags(flags: &[String]) -> Result<(), String> {
    match flags.iter().find(|f| RESERVED_FLAGS.contains(&f.split('=').next().unwrap_or(f))) {
        Some(f) => Err(format!("{f} is set by the app (terminals resume on their own)")),
        None => Ok(()),
    }
}

/// The agent's command line: a new session (Claude: with our id, so it can be
/// resumed exactly) or the pane's previous one, then the user's startup flags.
fn agent_command(
    agent: &str,
    bin: &str,
    config_dir: &Path,
    session: Option<&str>,
    resume: bool,
    flags: &[String],
) -> String {
    let mut cmd = match (agent, session) {
        ("claude", Some(id)) if resume && claude_session_exists(config_dir, id) => format!("{bin} --resume {id}"),
        ("claude", Some(id)) => format!("{bin} --session-id {id}"),
        ("codex", _) if resume => format!("{bin} resume --last"),
        _ => bin.to_string(),
    };
    for f in flags {
        cmd.push(' ');
        cmd.push_str(&shell_quote(f));
    }
    cmd
}

#[tauri::command]
#[allow(clippy::too_many_arguments)]
fn terminal_spawn(
    store: State<Store>,
    ptys: State<Ptys>,
    garden: String,
    cwd: String,
    agent: Option<String>,
    profile: Option<String>,
    session: Option<String>,
    resume: bool,
    flags: Option<Vec<String>>,
    cols: u16,
    rows: u16,
    on_event: Channel<PtyEvent>,
) -> Result<u32, String> {
    let g = store.garden(&garden)?;
    let allowed = cwd == g.root || g.repos.iter().any(|r| r.path == cwd);
    if !allowed || !Path::new(&cwd).is_dir() {
        return Err(format!("{cwd} is not a folder of this garden"));
    }
    // Claude reads skill modes and the plugin switch from the folder it starts in.
    if let Err(e) = skills::ensure_folder(&g, Path::new(&cwd)) {
        eprintln!("skills for {cwd}: {e}");
    }
    let session = session.filter(|s| !s.is_empty());
    if session.as_deref().is_some_and(|s| !valid_session(s)) {
        return Err("invalid session id".into());
    }
    let flags: Vec<String> = flags.unwrap_or_default().into_iter().filter(|f| !f.is_empty()).collect();
    check_flags(&flags)?;
    let mut env = vec![("CONTEXT_GARDEN".to_string(), g.name.clone())];
    // The skills resolve their mode from the git root they run in; group folders,
    // linked repos and repos without settings would read "off". The garden's
    // choice holds for the whole session this way.
    env.extend(skills::mode_env(&g));
    let base = Path::new(&g.root).join(".greenhouse");
    if base.join("greenhouse.json").is_file() {
        env.push(("GREENHOUSE_BASE".into(), base.to_string_lossy().into()));
    }
    let mut command = None;
    if let Some(profile_id) = profile.filter(|p| !p.is_empty()) {
        let p = store
            .profiles()?
            .into_iter()
            .find(|p| p.id == profile_id)
            .ok_or("that account no longer exists")?;
        let (_, _, bin, var) = AGENTS.iter().find(|a| a.0 == p.agent).ok_or("unknown agent")?;
        env.push((var.unwrap().to_string(), p.dir.clone()));
        command = Some(agent_command(&p.agent, bin, &expand_tilde(&p.dir), session.as_deref(), resume, &flags));
    } else if let Some(agent) = agent.filter(|a| !a.is_empty()) {
        let (_, _, bin, _) = AGENTS.iter().find(|a| a.0 == agent).ok_or("unknown agent")?;
        let config = std::env::var_os("CLAUDE_CONFIG_DIR")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| home_dir().join(".claude"));
        command = Some(agent_command(&agent, bin, &config, session.as_deref(), resume, &flags));
    }
    ptys.spawn(Launch { command, cwd, env, cols, rows }, on_event)
}

/// Remember a garden's terminals (open ones and recently closed), newest first.
#[tauri::command]
fn save_terminals(store: State<Store>, id: String, mut terminals: Vec<SavedTerminal>) -> Result<(), String> {
    terminals.truncate(24);
    store.update_garden(&id, |g| {
        g.terminals = terminals;
        Ok(())
    })
}

/// Terminal that runs an account's agent login (browser sign-in) in its config dir.
#[tauri::command]
fn profile_login(
    store: State<Store>,
    ptys: State<Ptys>,
    id: String,
    cols: u16,
    rows: u16,
    on_event: Channel<PtyEvent>,
) -> Result<u32, String> {
    let p = store.profiles()?.into_iter().find(|p| p.id == id).ok_or("that account no longer exists")?;
    let (_, _, bin, var) = AGENTS.iter().find(|a| a.0 == p.agent).ok_or("unknown agent")?;
    if which(bin).is_none() {
        return Err(format!("`{bin}` is not installed"));
    }
    // Codex has a login command; Claude Code asks to sign in on first start in a fresh config dir.
    let command = if p.agent == "codex" { format!("{bin} login") } else { bin.to_string() };
    let env = vec![(var.unwrap().to_string(), p.dir.clone())];
    let cwd = home_dir().to_string_lossy().into();
    ptys.spawn(Launch { command: Some(command), cwd, env, cols, rows }, on_event)
}

#[tauri::command]
fn terminal_write(ptys: State<Ptys>, id: u32, data: String) -> Result<(), String> {
    ptys.write(id, &data)
}

#[tauri::command]
fn terminal_resize(ptys: State<Ptys>, id: u32, cols: u16, rows: u16) -> Result<(), String> {
    ptys.resize(id, cols, rows)
}

#[tauri::command]
fn terminal_kill(ptys: State<Ptys>, id: u32) {
    ptys.kill(id)
}

pub fn run() {
    util::init_env();
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&dir)?;
            app.manage(Store::new(dir));
            Ok(())
        })
        .manage(Ptys::default())
        .manage(Cloning::default())
        .invoke_handler(tauri::generate_handler![
            add_garden_plugins,
            app_update,
            open_repo_page,
            profile_plugin_outdated,
            update_profile_plugin,
            clipboard_read,
            inspect_plugin_source,
            remove_garden_plugin,
            terminal_previews,
            clipboard_write,
            default_root,
            list_gardens,
            get_garden,
            plan_garden,
            create_garden,
            add_to_garden,
            retry_repos,
            profile_login,
            profile_plugin_version,
            install_profile_plugin,
            forget_garden,
            rename_garden,
            recommend_skills,
            apply_skills,
            garden_stats,
            list_agents,
            list_profiles,
            add_profile,
            remove_profile,
            terminal_spawn,
            save_terminals,
            terminal_write,
            terminal_resize,
            terminal_kill,
        ])
        .build(tauri::generate_context!())
        .expect("failed to start ContextGarden");
    app.run(|app, event| {
        if let RunEvent::Exit = event {
            app.state::<Ptys>().kill_all();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_prompt_skips_commands_and_unwraps_pastes() {
        let dir = std::env::temp_dir().join(format!("cg-prompt-{}", std::process::id()));
        let project = dir.join("projects/-x");
        std::fs::create_dir_all(&project).unwrap();
        let lines = [
            r#"{"type":"user","message":{"content":"<command-name>/chrome</command-name>"}}"#,
            r#"{"type":"user","isMeta":true,"message":{"content":"Base directory for this skill"}}"#,
            r#"{"type":"user","message":{"content":[{"type":"text","text":"\n<pasted_content id=\"a1\">\n# Task: check   the nightly\n</pasted_content>"}]}}"#,
        ];
        std::fs::write(project.join("abc-1.jsonl"), lines.join("\n")).unwrap();
        let got = claude_first_prompt(&dir, "abc-1");
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(got.as_deref(), Some("# Task: check the nightly"));
    }

    #[test]
    fn session_ids_are_checked_and_resumed_only_with_a_transcript() {
        assert!(valid_session("0f8e2c1a-1b2c-4d5e-8f90-123456789abc"));
        assert!(!valid_session("x; rm -rf ~"));
        let dir = std::env::temp_dir().join(format!("cg-session-{}", std::process::id()));
        let id = "0f8e2c1a-1b2c-4d5e-8f90-123456789abc";
        assert_eq!(agent_command("claude", "claude", &dir, Some(id), true, &[]), format!("claude --session-id {id}"));
        std::fs::create_dir_all(dir.join("projects/-tmp-x")).unwrap();
        std::fs::write(dir.join(format!("projects/-tmp-x/{id}.jsonl")), "{}").unwrap();
        assert_eq!(agent_command("claude", "claude", &dir, Some(id), true, &[]), format!("claude --resume {id}"));
        assert_eq!(agent_command("codex", "codex", &dir, None, true, &[]), "codex resume --last");
        assert_eq!(agent_command("codex", "codex", &dir, None, false, &[]), "codex");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    #[cfg(unix)]
    fn startup_flags_are_quoted_and_session_flags_refused() {
        let flags = vec!["--chrome".to_string(), "--append-system-prompt".into(), "be brief; rm -rf ~ 'x'".into()];
        let cmd = agent_command("claude", "claude", Path::new("/nonexistent"), None, false, &flags);
        assert_eq!(cmd, r"claude --chrome --append-system-prompt 'be brief; rm -rf ~ '\''x'\'''");
        // The shell hands the agent exactly the three arguments, nothing runs.
        let args = cmd.strip_prefix("claude ").unwrap();
        let out = std::process::Command::new("sh").args(["-c", &format!("printf '%s|' {args}")]).output().unwrap();
        assert_eq!(String::from_utf8_lossy(&out.stdout), "--chrome|--append-system-prompt|be brief; rm -rf ~ 'x'|");
        assert!(check_flags(&["--resume=abc".into()]).is_err());
        assert!(check_flags(&["-c".into()]).is_err());
        assert!(check_flags(&["--model".into(), "opus".into()]).is_ok());
    }
}
