//! Skill modes per repo, written exactly like `hooks/garden.py preset ... --scope local`:
//! `skillOverrides` in <repo>/.claude/settings.local.json (on -> "on",
//! manual -> "user-invocable-only", off -> "off"). Other keys are kept as they are.
//!
//! Accounts span all gardens, skills belong to one garden: app accounts get the
//! plugin installed but disabled account-wide, and the same file enables it
//! (`enabledPlugins`, local scope beats user scope) in the garden's folders only.

use serde::Serialize;
use std::collections::BTreeMap;
use std::path::Path;

use crate::store::Garden;
use crate::util::{atomic_write, expand_tilde, now_secs};

pub const PLUGIN: &str = "context-garden@context-garden";

pub const SKILLS: [&str; 6] = ["seedbank", "pruner", "compost", "mycelium", "greenhouse", "weeder"];

/// Mirrors PRESETS in hooks/garden.py.
pub const PRESETS: [(&str, &str, &[&str]); 5] = [
    ("solo", "One repository, everyday coding and debugging", &["seedbank", "pruner", "compost"]),
    (
        "platform",
        "Several repos that call each other (services, shared packages)",
        &["greenhouse", "seedbank", "pruner", "compost"],
    ),
    (
        "swarm",
        "Many subagents searching, investigating or editing in parallel",
        &["mycelium", "seedbank", "pruner", "compost"],
    ),
    (
        "platform-swarm",
        "Many agents working across a multi-repo platform",
        &["greenhouse", "mycelium", "seedbank", "pruner", "compost"],
    ),
    ("logs", "Long builds, test suites, CI or HPC logs; nothing else", &["compost"]),
];

#[derive(Serialize)]
pub struct PresetInfo {
    pub name: String,
    pub description: String,
    pub on: Vec<String>,
}

#[derive(Serialize)]
pub struct Recommendation {
    pub preset: String,
    pub reason: String,
    pub modes: BTreeMap<String, String>,
    pub presets: Vec<PresetInfo>,
}

pub fn preset_modes(name: &str) -> BTreeMap<String, String> {
    let on: &[&str] = PRESETS.iter().find(|p| p.0 == name).map(|p| p.2).unwrap_or(&[]);
    SKILLS
        .iter()
        .map(|s| (s.to_string(), if on.contains(s) { "on" } else { "manual" }.to_string()))
        .collect()
}

pub fn recommend(garden: &Garden) -> Recommendation {
    let n = garden.repos.iter().filter(|r| r.status != "failed").count();
    let (preset, reason) = match n {
        0 | 1 => ("solo", "One repository: cached facts, sliced reads and compact logs.".to_string()),
        2..=9 => (
            "platform",
            format!("{n} repositories: greenhouse shares what each repo provides across all of them."),
        ),
        _ => (
            "platform-swarm",
            format!("{n} repositories: big enough that agents fan out; mycelium keeps their findings shared."),
        ),
    };
    Recommendation {
        preset: preset.into(),
        reason,
        modes: preset_modes(preset),
        presets: PRESETS
            .iter()
            .map(|(name, description, on)| PresetInfo {
                name: name.to_string(),
                description: description.to_string(),
                on: on.iter().map(|s| s.to_string()).collect(),
            })
            .collect(),
    }
}

fn override_value(mode: &str) -> Result<&'static str, String> {
    match mode {
        "on" => Ok("on"),
        "manual" => Ok("user-invocable-only"),
        "off" => Ok("off"),
        other => Err(format!("unknown mode '{other}'")),
    }
}

fn write_repo_modes(repo: &Path, modes: &BTreeMap<String, String>) -> Result<(), String> {
    let path = repo.join(".claude").join("settings.local.json");
    let mut settings: serde_json::Value = match std::fs::read_to_string(&path) {
        Ok(text) if text.trim().is_empty() => serde_json::json!({}),
        // Never overwrite a file we cannot parse: it is the user's.
        Ok(text) => serde_json::from_str(&text)
            .map_err(|e| format!("{} is not valid JSON ({e}); left untouched", path.display()))?,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => serde_json::json!({}),
        Err(e) => return Err(e.to_string()),
    };
    let obj = settings
        .as_object_mut()
        .ok_or_else(|| format!("{} is not a JSON object; left untouched", path.display()))?;
    let overrides = obj
        .entry("skillOverrides")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .ok_or("skillOverrides is not an object")?;
    for (skill, mode) in modes {
        overrides.insert(skill.clone(), serde_json::Value::from(override_value(mode)?));
    }
    let enabled = modes.values().any(|m| m != "off");
    obj.entry("enabledPlugins")
        .or_insert_with(|| serde_json::json!({}))
        .as_object_mut()
        .ok_or("enabledPlugins is not an object")?
        .insert(PLUGIN.into(), serde_json::Value::from(enabled));
    atomic_write(&path, &(serde_json::to_string_pretty(&settings).unwrap() + "\n"))
}

/// <garden root>/.greenhouse: every repo below the root finds it by ancestor
/// search; repos added from elsewhere get $GREENHOUSE_BASE in garden terminals.
pub fn ensure_greenhouse_base(garden: &Garden) -> Result<(), String> {
    let base = expand_tilde(&garden.root).join(".greenhouse");
    let marker = base.join("greenhouse.json");
    if marker.exists() {
        return Ok(());
    }
    std::fs::create_dir_all(&base).map_err(|e| e.to_string())?;
    let meta = serde_json::json!({"version": 1, "name": garden.name, "created_at": now_secs()});
    atomic_write(&marker, &(serde_json::to_string_pretty(&meta).unwrap() + "\n"))?;
    let ignore = base.join(".gitignore");
    if !ignore.exists() {
        atomic_write(&ignore, ".lock\n*.tmp\n")?;
    }
    Ok(())
}

/// Make sure a folder a terminal starts in carries the garden's skill choice
/// (it may predate the choice, or be the root of a garden set up earlier).
pub fn ensure_folder(garden: &Garden, dir: &Path) -> Result<(), String> {
    if garden.skills.is_empty() {
        return Ok(());
    }
    write_repo_modes(dir, &garden.skills)
}

/// `CONTEXT_GARDEN_MODE_<SKILL>` for each chosen mode; the skills read it first.
pub fn mode_env(garden: &Garden) -> Vec<(String, String)> {
    garden
        .skills
        .iter()
        .filter(|(skill, mode)| SKILLS.contains(&skill.as_str()) && override_value(mode).is_ok())
        .map(|(skill, mode)| (format!("CONTEXT_GARDEN_MODE_{}", skill.to_uppercase()), mode.clone()))
        .collect()
}

/// Writes the modes into every repo and the garden root (terminals start there
/// too). Returns one error line per folder that could not be updated.
pub fn apply(garden: &Garden, modes: &BTreeMap<String, String>) -> Vec<String> {
    let mut errors = Vec::new();
    if let Err(e) = write_repo_modes(&expand_tilde(&garden.root), modes) {
        errors.push(format!("garden root: {e}"));
    }
    for repo in garden.repos.iter().filter(|r| r.ready()) {
        if let Err(e) = write_repo_modes(Path::new(&repo.path), modes) {
            errors.push(format!("{}: {e}", repo.name));
        }
    }
    if modes.get("greenhouse").map(String::as_str) == Some("on") {
        if let Err(e) = ensure_greenhouse_base(garden) {
            errors.push(format!("greenhouse base: {e}"));
        }
    }
    errors
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::Repo;

    #[test]
    fn apply_keeps_other_settings_and_refuses_broken_json() {
        let tmp = std::env::temp_dir().join(format!("cg-skills-{}", std::process::id()));
        let (good, broken) = (tmp.join("good"), tmp.join("broken"));
        for dir in [&good, &broken] {
            std::fs::create_dir_all(dir.join(".claude")).unwrap();
        }
        std::fs::write(good.join(".claude/settings.local.json"), r#"{"model": "x"}"#).unwrap();
        std::fs::write(broken.join(".claude/settings.local.json"), "{oops").unwrap();
        let repo = |p: &Path| Repo {
            name: p.file_name().unwrap().to_string_lossy().into(),
            path: p.to_string_lossy().into(),
            url: None,
            group: None,
            source: None,
            status: "local".into(),
            error: None,
        };
        let garden = Garden {
            id: "t".into(),
            name: "t".into(),
            root: tmp.to_string_lossy().into(),
            layout: "nested".into(),
            protocol: "ssh".into(),
            repo_inputs: vec![],
            group_inputs: vec![],
            repos: vec![repo(&good), repo(&broken)],
            skills: BTreeMap::new(),
            created_at: 0,
            terminals: Vec::new(),
        };
        let errors = apply(&garden, &preset_modes("platform"));
        assert_eq!(errors.len(), 1, "{errors:?}");
        let written: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(good.join(".claude/settings.local.json")).unwrap()).unwrap();
        assert_eq!(written["model"], "x");
        assert_eq!(written["skillOverrides"]["greenhouse"], "on");
        assert_eq!(written["skillOverrides"]["weeder"], "user-invocable-only");
        assert_eq!(written["enabledPlugins"][PLUGIN], true);
        assert!(tmp.join(".claude/settings.local.json").is_file(), "garden root gets the modes too");
        assert_eq!(std::fs::read_to_string(broken.join(".claude/settings.local.json")).unwrap(), "{oops");
        assert!(tmp.join(".greenhouse/greenhouse.json").is_file());
        let garden = Garden { skills: preset_modes("logs"), ..garden };
        let env = mode_env(&garden);
        assert!(env.contains(&("CONTEXT_GARDEN_MODE_COMPOST".into(), "on".into())), "{env:?}");
        assert!(env.contains(&("CONTEXT_GARDEN_MODE_GREENHOUSE".into(), "manual".into())), "{env:?}");
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
