//! What each account has installed, keeping it up to date, and where a
//! running agent is working. Read from the agent's own files in its config
//! dir (Claude Code: CLAUDE_CONFIG_DIR, Codex: CODEX_HOME); never its login.

use serde::Serialize;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

use crate::util::{git, home_dir, run_env, which};

/// The config dir an agent reads: the account's, or the agent's default.
pub fn config_dir(agent: &str, account_dir: Option<&str>) -> PathBuf {
    if let Some(d) = account_dir {
        return crate::util::expand_tilde(d);
    }
    let (var, dot) = if agent == "codex" { ("CODEX_HOME", ".codex") } else { ("CLAUDE_CONFIG_DIR", ".claude") };
    std::env::var_os(var).map(PathBuf::from).unwrap_or_else(|| home_dir().join(dot))
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

#[derive(Serialize, Clone, Debug, PartialEq)]
pub struct Installed {
    /// <plugin>@<marketplace>
    pub id: String,
    pub version: String,
    /// The version in the account's copy of its marketplace, when that differs.
    pub latest: Option<String>,
    /// Changes whenever the installed copy changes: a terminal started under
    /// an older stamp runs the old plugin until it restarts.
    pub stamp: String,
}

/// Version a marketplace catalog (a checkout) lists for one plugin.
fn catalog_version(root: &Path, plugin: &str) -> Option<String> {
    let catalog = read_json(&root.join(".claude-plugin/marketplace.json"))?;
    let entry = catalog["plugins"].as_array()?.iter().find(|p| p["name"] == plugin)?;
    if let Some(v) = entry["version"].as_str() {
        return Some(v.to_string());
    }
    let dir = root.join(entry["source"].as_str()?);
    [".claude-plugin/plugin.json", ".codex-plugin/plugin.json"]
        .iter()
        .find_map(|f| read_json(&dir.join(f))?["version"].as_str().map(String::from))
}

/// Dotted versions compare by number, anything else as text.
fn version_key(v: &str) -> Vec<u64> {
    v.trim_start_matches('v').split(['.', '-', '+']).map(|p| p.parse().unwrap_or(0)).collect()
}

fn claude_marketplace_root(dir: &Path, marketplace: &str) -> Option<PathBuf> {
    let known = read_json(&dir.join("plugins/known_marketplaces.json"))?;
    known[marketplace]["installLocation"].as_str().map(PathBuf::from)
}

fn codex_marketplace_root(dir: &Path, marketplace: &str) -> PathBuf {
    dir.join(".tmp/marketplaces").join(marketplace)
}

/// Codex lists installed plugins as `[plugins."<id>"]` sections of config.toml.
fn codex_plugin_ids(dir: &Path) -> Vec<String> {
    std::fs::read_to_string(dir.join("config.toml"))
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.trim().strip_prefix("[plugins.\"")?.strip_suffix("\"]").map(String::from))
        .collect()
}

/// Plugins installed in a config dir, sorted by id.
pub fn installed(agent: &str, dir: &Path) -> Vec<Installed> {
    let mut out: Vec<Installed> = match agent {
        "claude" => {
            let Some(file) = read_json(&dir.join("plugins/installed_plugins.json")) else { return vec![] };
            let Some(map) = file["plugins"].as_object() else { return vec![] };
            map.iter()
                .filter_map(|(id, entries)| {
                    let e = entries.as_array()?.first()?;
                    let version = e["version"].as_str().unwrap_or("?").to_string();
                    let stamp = format!("{version}|{}|{}", e["gitCommitSha"].as_str().unwrap_or(""), e["lastUpdated"].as_str().unwrap_or(""));
                    let (name, marketplace) = id.split_once('@')?;
                    let latest = claude_marketplace_root(dir, marketplace).and_then(|r| catalog_version(&r, name));
                    Some(Installed { id: id.clone(), latest: latest.filter(|l| *l != version), version, stamp })
                })
                .collect()
        }
        "codex" => codex_plugin_ids(dir)
            .into_iter()
            .filter_map(|id| {
                let (name, marketplace) = id.split_once('@')?;
                // plugins/cache/<marketplace>/<plugin>/<version>
                let mut versions: Vec<String> = std::fs::read_dir(dir.join("plugins/cache").join(marketplace).join(name))
                    .map(|d| d.flatten().filter(|e| e.path().is_dir()).map(|e| e.file_name().to_string_lossy().into()).collect())
                    .unwrap_or_default();
                versions.sort_by_key(|v| version_key(v));
                let version = versions.pop().unwrap_or_else(|| "installed".into());
                let latest = catalog_version(&codex_marketplace_root(dir, marketplace), name);
                Some(Installed { stamp: version.clone(), latest: latest.filter(|l| *l != version), version, id })
            })
            .collect(),
        _ => vec![],
    };
    out.sort_by(|a, b| a.id.cmp(&b.id));
    out
}

/// The commit of the account's context-garden copy, to compare with main.
pub fn installed_commit(agent: &str, dir: &Path, plugin: &str) -> Option<String> {
    match agent {
        "claude" => read_json(&dir.join("plugins/installed_plugins.json"))?["plugins"][plugin][0]["gitCommitSha"]
            .as_str()
            .map(String::from),
        "codex" => {
            let marketplace = plugin.split_once('@')?.1;
            git(&["rev-parse", "HEAD"], &codex_marketplace_root(dir, marketplace))
        }
        _ => None,
    }
}

fn claude_enabled(dir: &Path, plugin: &str) -> bool {
    read_json(&dir.join("settings.json")).is_some_and(|s| s["enabledPlugins"][plugin] == true)
}

/// Refresh the plugin's marketplace and install its newest version, with the
/// agent's own plugin commands. Claude keeps whether it was switched on
/// account-wide (gardens switch theirs on per folder).
pub fn update(agent: &str, dir: &Path, plugin: &str) -> Result<(), String> {
    let marketplace = plugin.split_once('@').map(|(_, m)| m).ok_or("plugin id needs <plugin>@<marketplace>")?;
    let dir_text = dir.to_string_lossy();
    match agent {
        "claude" => {
            if which("claude").is_none() {
                return Err("`claude` is not installed".into());
            }
            let env = [("CLAUDE_CONFIG_DIR", dir_text.as_ref())];
            let was_on = claude_enabled(dir, plugin);
            run_env("claude", &["plugin", "marketplace", "update", marketplace], None, &env)?;
            run_env("claude", &["plugin", "update", plugin, "--scope", "user"], None, &env)?;
            if !was_on {
                // Already off is fine; the update must not leave it on everywhere.
                let _ = run_env("claude", &["plugin", "disable", plugin, "--scope", "user"], None, &env);
            }
            Ok(())
        }
        "codex" => {
            if which("codex").is_none() {
                return Err("`codex` is not installed".into());
            }
            let env = [("CODEX_HOME", dir_text.as_ref())];
            // Only git marketplaces have a snapshot to refresh.
            if codex_marketplace_root(dir, marketplace).join(".git").exists() {
                run_env("codex", &["plugin", "marketplace", "upgrade", marketplace], None, &env)?;
            }
            run_env("codex", &["plugin", "add", plugin], None, &env).map(|_| ())
        }
        other => Err(format!("'{other}' has no plugin support")),
    }
}

/// Last `max` bytes of a file, from the first full line on.
fn tail(path: &Path, max: u64) -> Option<String> {
    let mut f = std::fs::File::open(path).ok()?;
    let len = f.metadata().ok()?.len();
    let start = len.saturating_sub(max);
    f.seek(SeekFrom::Start(start)).ok()?;
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).ok()?;
    let text = String::from_utf8_lossy(&buf).into_owned();
    Some(if start > 0 { text.split_once('\n').map_or(String::new(), |(_, rest)| rest.to_string()) } else { text })
}

/// The folder a shell command works in: `cd <dir>`, else its first path argument.
fn command_dir(command: &str) -> Option<String> {
    let words: Vec<&str> = command.split_whitespace().map(|w| w.trim_matches(|c| "'\";&|()".contains(c))).collect();
    if let Some(i) = words.iter().position(|w| *w == "cd") {
        return words.get(i + 1).filter(|w| !w.is_empty()).map(|w| w.to_string());
    }
    words.iter().skip(1).find(|w| w.starts_with('/') || w.starts_with("~/")).map(|w| w.to_string())
}

fn absolute(path: &str, cwd: &str) -> PathBuf {
    if let Some(rest) = path.strip_prefix("~/") {
        home_dir().join(rest)
    } else {
        Path::new(cwd).join(path)
    }
}

/// Where a Claude session last worked: the path of its newest file tool call
/// or `cd`, else the folder the session is in.
pub fn claude_location(dir: &Path, session: &str) -> Option<PathBuf> {
    let projects = std::fs::read_dir(dir.join("projects")).ok()?;
    let file = projects.flatten().map(|p| p.path().join(format!("{session}.jsonl"))).find(|f| f.is_file())?;
    let text = tail(&file, 512 * 1024)?;
    let mut fallback = None;
    for line in text.lines().rev() {
        let Ok(v) = serde_json::from_str::<serde_json::Value>(line) else { continue };
        let cwd = v["cwd"].as_str().unwrap_or("");
        if fallback.is_none() && !cwd.is_empty() {
            fallback = Some(PathBuf::from(cwd));
        }
        let Some(items) = v["message"]["content"].as_array() else { continue };
        for item in items.iter().rev().filter(|c| c["type"] == "tool_use") {
            let input = &item["input"];
            let path = ["file_path", "notebook_path", "path"]
                .iter()
                .find_map(|k| input[*k].as_str().map(String::from))
                .or_else(|| command_dir(input["command"].as_str()?));
            if let Some(p) = path {
                return Some(absolute(&p, cwd));
            }
        }
    }
    fallback
}

/// Where a Codex session last worked: the `workdir` of its newest shell call
/// in the newest rollout started in `cwd` since `since` (Codex picks its own
/// session ids, so the pane's rollout is found by folder and time).
pub fn codex_location(dir: &Path, cwd: &str, since: u64) -> Option<PathBuf> {
    let since = std::time::UNIX_EPOCH + std::time::Duration::from_secs(since);
    let mut rollouts = Vec::new();
    // sessions/YYYY/MM/DD/rollout-*.jsonl: only the newest two days matter.
    let mut days: Vec<PathBuf> = Vec::new();
    for year in dirs_sorted(&dir.join("sessions")).into_iter().rev().take(2) {
        for month in dirs_sorted(&year).into_iter().rev().take(2) {
            days.extend(dirs_sorted(&month).into_iter().rev().take(2));
        }
    }
    for day in days {
        for f in std::fs::read_dir(day).into_iter().flatten().flatten() {
            let Ok(modified) = f.metadata().and_then(|m| m.modified()) else { continue };
            if modified >= since {
                rollouts.push((modified, f.path()));
            }
        }
    }
    rollouts.sort();
    for (_, file) in rollouts.into_iter().rev() {
        let Ok(head) = std::fs::read_to_string(&file) else { continue };
        let first = head.lines().next().unwrap_or("");
        let Ok(meta) = serde_json::from_str::<serde_json::Value>(first) else { continue };
        if meta["payload"]["cwd"].as_str() != Some(cwd) {
            continue;
        }
        for line in head.lines().rev() {
            let Ok(v) = serde_json::from_str::<serde_json::Value>(line) else { continue };
            let p = &v["payload"];
            let args = p["arguments"].as_str().and_then(|a| serde_json::from_str::<serde_json::Value>(a).ok());
            if let Some(w) = args.as_ref().and_then(|a| a["workdir"].as_str()) {
                return Some(absolute(w, cwd));
            }
        }
        return Some(PathBuf::from(cwd));
    }
    None
}

fn dirs_sorted(dir: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::fs::read_dir(dir)
        .map(|d| d.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect())
        .unwrap_or_default();
    out.sort();
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("cg-acc-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn claude_plugins_show_a_newer_catalog_version() {
        let dir = scratch("claude");
        let market = dir.join("plugins/marketplaces/m");
        std::fs::create_dir_all(market.join(".claude-plugin")).unwrap();
        std::fs::write(market.join(".claude-plugin/marketplace.json"), r#"{"name":"m","plugins":[{"name":"p","source":"./","version":"1.1.0"}]}"#).unwrap();
        std::fs::write(
            dir.join("plugins/known_marketplaces.json"),
            serde_json::json!({"m": {"installLocation": market}}).to_string(),
        )
        .unwrap();
        std::fs::write(
            dir.join("plugins/installed_plugins.json"),
            r#"{"plugins":{"p@m":[{"version":"1.0.0","gitCommitSha":"abc","lastUpdated":"t"}]}}"#,
        )
        .unwrap();
        let got = installed("claude", &dir);
        assert_eq!(got.len(), 1);
        assert_eq!((got[0].version.as_str(), got[0].latest.as_deref()), ("1.0.0", Some("1.1.0")));
        assert_eq!(got[0].stamp, "1.0.0|abc|t");
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn codex_plugins_come_from_config_sections_and_cache() {
        let dir = scratch("codex");
        std::fs::write(dir.join("config.toml"), "[plugins.\"p@m\"]\nenabled = true\n").unwrap();
        for v in ["0.9.0", "0.10.0"] {
            std::fs::create_dir_all(dir.join("plugins/cache/m/p").join(v)).unwrap();
        }
        let got = installed("codex", &dir);
        assert_eq!((got[0].id.as_str(), got[0].version.as_str(), got[0].latest.as_ref()), ("p@m", "0.10.0", None));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn claude_location_is_the_newest_tool_path() {
        let dir = scratch("loc");
        std::fs::create_dir_all(dir.join("projects/x")).unwrap();
        let lines = [
            r#"{"type":"user","cwd":"/g","message":{"content":"hi"}}"#,
            r#"{"type":"assistant","cwd":"/g","message":{"content":[{"type":"tool_use","input":{"file_path":"/g/a/x.py"}}]}}"#,
            r#"{"type":"assistant","cwd":"/g","message":{"content":[{"type":"tool_use","input":{"command":"cd b/src && pytest"}}]}}"#,
            r#"{"type":"user","cwd":"/g","message":{"content":[{"type":"tool_result"}]}}"#,
        ];
        std::fs::write(dir.join("projects/x/s1.jsonl"), lines.join("\n")).unwrap();
        assert_eq!(claude_location(&dir, "s1"), Some(PathBuf::from("/g/b/src")));
        assert_eq!(claude_location(&dir, "nope"), None);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn command_dirs() {
        assert_eq!(command_dir("cd 'repo' && ls").as_deref(), Some("repo"));
        assert_eq!(command_dir("grep -rn x /abs/path").as_deref(), Some("/abs/path"));
        assert_eq!(command_dir("ls -la"), None);
    }
}
