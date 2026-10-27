//! Other people's Claude Code plugins (skills, agents, MCP servers) for one
//! garden, e.g. github.com/rse/ase. Same model as context-garden itself: each
//! Claude account gets the plugin installed but disabled, and the garden's
//! folders enable it (`enabledPlugins`, see skills.rs).

use serde::Serialize;
use std::path::{Path, PathBuf};

use crate::util::{expand_tilde, now_secs, run, run_env, which};

#[derive(Serialize)]
pub struct PluginInfo {
    pub name: String,
    pub description: String,
    /// Commands its MCP servers start that are not installed here.
    pub missing: Vec<String>,
}

#[derive(Serialize)]
pub struct SourceInfo {
    pub source: String,
    pub marketplace: String,
    pub plugins: Vec<PluginInfo>,
}

/// What `claude plugin marketplace add` takes: GitHub links become owner/repo;
/// other git URLs and existing folders pass as they are.
pub fn normalize(input: &str) -> Result<String, String> {
    let raw = input.trim().trim_end_matches('/');
    if raw.is_empty() {
        return Err("enter a GitHub repository, git URL or folder".into());
    }
    if raw.starts_with('-') || raw.chars().any(char::is_whitespace) {
        return Err(format!("'{raw}' is not a repository"));
    }
    let github = raw
        .strip_prefix("https://github.com/")
        .or_else(|| raw.strip_prefix("http://github.com/"))
        .or_else(|| raw.strip_prefix("github.com/"))
        .or_else(|| raw.strip_prefix("git@github.com:"));
    if let Some(rest) = github {
        let mut parts = rest.split('/');
        let (Some(owner), Some(repo)) = (parts.next(), parts.next()) else {
            return Err(format!("'{raw}' names no repository"));
        };
        return Ok(format!("{owner}/{}", repo.trim_end_matches(".git")));
    }
    let shorthand = raw.split('/').count() == 2
        && raw.chars().all(|c| c.is_ascii_alphanumeric() || "-_./".contains(c))
        && !raw.starts_with('.');
    if shorthand || raw.contains("://") || raw.starts_with("git@") {
        return Ok(raw.to_string());
    }
    let dir = expand_tilde(raw);
    if dir.is_dir() {
        return Ok(dir.to_string_lossy().into());
    }
    Err(format!("'{raw}' is neither owner/repo, a git URL nor a folder"))
}

fn clone_url(source: &str) -> String {
    if source.contains("://") || source.starts_with("git@") || Path::new(source).is_absolute() {
        source.to_string()
    } else {
        format!("https://github.com/{source}.git")
    }
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// Read a marketplace's catalog from a shallow clone, before anything is installed.
pub fn inspect(input: &str) -> Result<SourceInfo, String> {
    let source = normalize(input)?;
    let tmp = std::env::temp_dir().join(format!("cg-plugin-{}-{}", std::process::id(), now_secs()));
    let local = Path::new(&source).is_absolute();
    let root: PathBuf = if local {
        PathBuf::from(&source)
    } else {
        let target = tmp.to_string_lossy().to_string();
        run("git", &["clone", "--depth", "1", "--quiet", &clone_url(&source), &target], None)
            .map_err(|e| format!("could not fetch {source}: {e}"))?;
        tmp.clone()
    };
    let result = read_catalog(&root, &source);
    if !local {
        let _ = std::fs::remove_dir_all(&tmp);
    }
    result
}

fn read_catalog(root: &Path, source: &str) -> Result<SourceInfo, String> {
    let Some(catalog) = read_json(&root.join(".claude-plugin/marketplace.json")) else {
        return Err(if root.join(".claude-plugin/plugin.json").is_file() {
            format!("{source} is a single plugin without a marketplace (.claude-plugin/marketplace.json); Claude Code cannot add it by itself")
        } else {
            format!("{source} has no .claude-plugin/marketplace.json, so it is not a Claude Code plugin marketplace")
        });
    };
    let marketplace = catalog["name"].as_str().ok_or("marketplace.json has no name")?.to_string();
    let plugins = catalog["plugins"]
        .as_array()
        .map(|list| {
            list.iter()
                .filter_map(|p| {
                    let name = p["name"].as_str()?.to_string();
                    // Local plugin folders tell which MCP commands they need.
                    let manifest = p["source"]
                        .as_str()
                        .and_then(|dir| read_json(&root.join(dir).join(".claude-plugin/plugin.json")));
                    let missing = manifest
                        .as_ref()
                        .and_then(|m| m["mcpServers"].as_object())
                        .map(|servers| {
                            servers
                                .values()
                                .filter_map(|s| s["command"].as_str())
                                .filter(|c| !c.contains("${") && which(c).is_none())
                                .map(String::from)
                                .collect()
                        })
                        .unwrap_or_default();
                    let description = p["description"]
                        .as_str()
                        .or_else(|| manifest.as_ref().and_then(|m| m["description"].as_str()))
                        .unwrap_or("")
                        .to_string();
                    Some(PluginInfo { name, description, missing })
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if plugins.is_empty() {
        return Err(format!("{source} lists no plugins"));
    }
    Ok(SourceInfo { source: source.to_string(), marketplace, plugins })
}

fn known(config_dir: &Path, file: &str, key: &str) -> bool {
    read_json(&config_dir.join("plugins").join(file))
        .is_some_and(|v| v.get("plugins").unwrap_or(&v).get(key).is_some())
}

/// Install `id` (<plugin>@<marketplace>) into one Claude account, disabled
/// account-wide so it only runs where a garden enables it.
pub fn install_claude(config_dir: &str, source: &str, id: &str) -> Result<(), String> {
    if which("claude").is_none() {
        return Err("`claude` is not installed".into());
    }
    let dir = expand_tilde(config_dir);
    let marketplace = id.split_once('@').map(|(_, m)| m).ok_or("plugin id needs <plugin>@<marketplace>")?;
    let env = [("CLAUDE_CONFIG_DIR", config_dir)];
    if !known(&dir, "known_marketplaces.json", marketplace) {
        run_env("claude", &["plugin", "marketplace", "add", source], None, &env)?;
    }
    if !known(&dir, "installed_plugins.json", id) {
        run_env("claude", &["plugin", "install", id, "--scope", "user"], None, &env)?;
    }
    run_env("claude", &["plugin", "disable", id, "--scope", "user"], None, &env).map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sources_normalize() {
        assert_eq!(normalize("https://github.com/rse/ase").unwrap(), "rse/ase");
        assert_eq!(normalize("https://github.com/rse/ase.git/").unwrap(), "rse/ase");
        assert_eq!(normalize("https://github.com/rse/ase/tree/main/plugin").unwrap(), "rse/ase");
        assert_eq!(normalize("git@github.com:rse/ase.git").unwrap(), "rse/ase");
        assert_eq!(normalize("rse/ase").unwrap(), "rse/ase");
        assert_eq!(normalize("https://gitlab.com/a/b.git").unwrap(), "https://gitlab.com/a/b.git");
        assert!(normalize("--upload-pack=x").is_err());
        assert!(normalize("two words").is_err());
        assert!(normalize("").is_err());
    }

    /// Network: `cargo test -- --ignored`.
    #[test]
    #[ignore]
    fn inspects_a_github_marketplace() {
        let info = inspect("https://github.com/rse/ase").unwrap();
        assert_eq!((info.source.as_str(), info.marketplace.as_str()), ("rse/ase", "ase"));
        assert!(info.plugins.iter().any(|p| p.name == "ase"));
    }

    #[test]
    fn catalog_reports_plugins_and_missing_commands() {
        let root = std::env::temp_dir().join(format!("cg-catalog-{}", std::process::id()));
        std::fs::create_dir_all(root.join(".claude-plugin")).unwrap();
        std::fs::create_dir_all(root.join("plugin/.claude-plugin")).unwrap();
        std::fs::write(
            root.join(".claude-plugin/marketplace.json"),
            r#"{"name": "m", "plugins": [{"name": "p", "source": "./plugin/", "description": "d"}]}"#,
        )
        .unwrap();
        std::fs::write(
            root.join("plugin/.claude-plugin/plugin.json"),
            r#"{"name": "p", "mcpServers": {"x": {"command": "cg-no-such-command"}}}"#,
        )
        .unwrap();
        let info = read_catalog(&root, "o/r").unwrap();
        assert_eq!(info.marketplace, "m");
        assert_eq!(info.plugins[0].name, "p");
        assert_eq!(info.plugins[0].missing, vec!["cg-no-such-command"]);
        std::fs::remove_dir_all(&root).unwrap();
        assert!(read_catalog(&root, "o/r").is_err());
    }
}
