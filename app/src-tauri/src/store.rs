//! App state on disk: gardens.json and profiles.json in the app data dir.
//! The app never deletes repositories; removing a garden only forgets it.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use crate::util::atomic_write;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Repo {
    pub name: String,
    /// Absolute path on disk.
    pub path: String,
    #[serde(default)]
    pub url: Option<String>,
    /// Folder on disk this repo is a symlink to (simulated garden), if any.
    #[serde(default)]
    pub source: Option<String>,
    /// Group / org / folder it was pulled from, if any.
    #[serde(default)]
    pub group: Option<String>,
    /// pending | cloning | cloned | linked | local | failed
    pub status: String,
    #[serde(default)]
    pub error: Option<String>,
}

impl Repo {
    /// On disk and usable: cloned, linked, or there already.
    pub fn ready(&self) -> bool {
        matches!(self.status.as_str(), "cloned" | "linked" | "local")
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Garden {
    pub id: String,
    pub name: String,
    pub root: String,
    /// nested | flat
    pub layout: String,
    /// ssh | https
    pub protocol: String,
    #[serde(default)]
    pub repo_inputs: Vec<String>,
    #[serde(default)]
    pub group_inputs: Vec<String>,
    pub repos: Vec<Repo>,
    /// skill -> on | manual | off; empty until skills were chosen.
    #[serde(default)]
    pub skills: BTreeMap<String, String>,
    pub created_at: u64,
    /// Terminals open last time and recently closed ones, newest first, to resume.
    #[serde(default)]
    pub terminals: Vec<SavedTerminal>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SavedTerminal {
    pub key: String,
    pub title: String,
    pub cwd: String,
    #[serde(default)]
    pub cwd_label: String,
    #[serde(default)]
    pub agent: Option<String>,
    #[serde(default)]
    pub profile: Option<String>,
    /// Agent session id (Claude Code), so this pane resumes its own conversation.
    #[serde(default)]
    pub session: Option<String>,
    /// Startup flags the agent was launched with, e.g. ["--chrome"].
    #[serde(default)]
    pub flags: Vec<String>,
    /// Still open when the app last saw it.
    #[serde(default)]
    pub open: bool,
    #[serde(default)]
    pub used_at: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Profile {
    pub id: String,
    pub name: String,
    /// claude | codex
    pub agent: String,
    /// Config dir the agent CLI keeps its login, settings and transcripts in.
    pub dir: String,
    /// Badge shown on this account's terminals: a short symbol and a color.
    #[serde(default)]
    pub icon: String,
    #[serde(default)]
    pub color: String,
}

#[derive(Default, Serialize, Deserialize)]
struct GardenFile {
    #[serde(default)]
    gardens: Vec<Garden>,
}

#[derive(Default, Serialize, Deserialize)]
struct ProfileFile {
    #[serde(default)]
    profiles: Vec<Profile>,
}

pub struct Store {
    pub dir: PathBuf,
    lock: Mutex<()>,
}

fn read_json<T: Default + for<'de> Deserialize<'de>>(path: &Path) -> Result<T, String> {
    match std::fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text)
            .map_err(|e| format!("{} is not valid JSON ({e}); fix or move it", path.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(T::default()),
        Err(e) => Err(e.to_string()),
    }
}

impl Store {
    pub fn new(dir: PathBuf) -> Self {
        Store { dir, lock: Mutex::new(()) }
    }

    fn gardens_path(&self) -> PathBuf {
        self.dir.join("gardens.json")
    }

    fn profiles_path(&self) -> PathBuf {
        self.dir.join("profiles.json")
    }

    pub fn gardens(&self) -> Result<Vec<Garden>, String> {
        let _g = self.lock.lock().unwrap();
        Ok(read_json::<GardenFile>(&self.gardens_path())?.gardens)
    }

    pub fn garden(&self, id: &str) -> Result<Garden, String> {
        self.gardens()?
            .into_iter()
            .find(|g| g.id == id)
            .ok_or_else(|| format!("no garden '{id}'"))
    }

    /// Read-modify-write under the lock.
    pub fn update_gardens<R>(
        &self,
        f: impl FnOnce(&mut Vec<Garden>) -> Result<R, String>,
    ) -> Result<R, String> {
        let _g = self.lock.lock().unwrap();
        let mut file: GardenFile = read_json(&self.gardens_path())?;
        let out = f(&mut file.gardens)?;
        let text = serde_json::to_string_pretty(&file).map_err(|e| e.to_string())?;
        atomic_write(&self.gardens_path(), &(text + "\n"))?;
        Ok(out)
    }

    pub fn update_garden<R>(
        &self,
        id: &str,
        f: impl FnOnce(&mut Garden) -> Result<R, String>,
    ) -> Result<R, String> {
        self.update_gardens(|all| {
            let g = all
                .iter_mut()
                .find(|g| g.id == id)
                .ok_or_else(|| format!("no garden '{id}'"))?;
            f(g)
        })
    }

    pub fn profiles(&self) -> Result<Vec<Profile>, String> {
        let _g = self.lock.lock().unwrap();
        Ok(read_json::<ProfileFile>(&self.profiles_path())?.profiles)
    }

    pub fn update_profiles<R>(
        &self,
        f: impl FnOnce(&mut Vec<Profile>) -> Result<R, String>,
    ) -> Result<R, String> {
        let _g = self.lock.lock().unwrap();
        let mut file: ProfileFile = read_json(&self.profiles_path())?;
        let out = f(&mut file.profiles)?;
        let text = serde_json::to_string_pretty(&file).map_err(|e| e.to_string())?;
        atomic_write(&self.profiles_path(), &(text + "\n"))?;
        Ok(out)
    }
}
