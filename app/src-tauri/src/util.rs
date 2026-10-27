//! Small process / filesystem helpers shared by every module.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

static LOGIN_PATH: OnceLock<Option<String>> = OnceLock::new();

/// PATH as the user's login shell sees it. Apps started from Finder / a desktop
/// launcher get a minimal PATH that misses Homebrew, npm globals, ~/.local/bin,
/// so `claude`, `glab`, `gh` would look "not installed" without this.
pub fn login_path() -> Option<String> {
    LOGIN_PATH.get_or_init(read_login_path).clone()
}

#[cfg(unix)]
fn read_login_path() -> Option<String> {
    let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".into());
    let mut child = Command::new(shell)
        .args(["-ilc", "printf '__CG_PATH__%s__CG_PATH__' \"$PATH\""])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    // A profile that waits for input must not hang startup.
    let start = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if start.elapsed() < Duration::from_secs(4) => {
                std::thread::sleep(Duration::from_millis(25))
            }
            _ => {
                let _ = child.kill();
                return None;
            }
        }
    }
    let out = child.wait_with_output().ok()?;
    let text = String::from_utf8_lossy(&out.stdout);
    let mut parts = text.split("__CG_PATH__");
    parts.next()?;
    parts.next().map(str::to_string).filter(|p| !p.is_empty())
}

#[cfg(windows)]
fn read_login_path() -> Option<String> {
    None // GUI processes on Windows already inherit the user PATH.
}

/// Apply the login PATH to this process once, so children inherit it.
pub fn init_env() {
    if let Some(path) = login_path() {
        std::env::set_var("PATH", path);
    }
}

pub fn which(program: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    let exts: Vec<String> = if cfg!(windows) {
        std::env::var("PATHEXT")
            .unwrap_or_else(|_| ".EXE;.CMD;.BAT".into())
            .split(';')
            .map(|e| e.to_lowercase())
            .collect()
    } else {
        vec![String::new()]
    };
    for dir in std::env::split_paths(&path) {
        for ext in &exts {
            let candidate = dir.join(format!("{program}{ext}"));
            if candidate.is_file() {
                return Some(candidate);
            }
        }
    }
    None
}

/// Run a program, return trimmed stdout on success or the stderr text on failure.
pub fn run(program: &str, args: &[&str], cwd: Option<&Path>) -> Result<String, String> {
    run_env(program, args, cwd, &[])
}

/// `run` with extra environment variables. npm shims on Windows (claude.cmd)
/// only start through cmd.exe, so those go through it.
pub fn run_env(program: &str, args: &[&str], cwd: Option<&Path>, env: &[(&str, &str)]) -> Result<String, String> {
    let shim = cfg!(windows)
        && which(program).is_some_and(|p| p.extension().is_some_and(|e| e.eq_ignore_ascii_case("cmd")));
    let mut cmd = if shim {
        let mut c = Command::new("cmd");
        c.args(["/C", program]);
        c
    } else {
        Command::new(program)
    };
    cmd.envs(env.iter().copied());
    cmd.args(args)
        .stdin(Stdio::null())
        .env("GIT_TERMINAL_PROMPT", "0")
        .env("GH_PROMPT_DISABLED", "1")
        .env("GLAB_NO_PROMPT", "1");
    if let Some(dir) = cwd {
        cmd.current_dir(dir);
    }
    let out = cmd
        .output()
        .map_err(|e| format!("could not run `{program}`: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).trim_end().to_string())
    } else {
        let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
        Err(if err.is_empty() {
            format!("`{program}` exited with {}", out.status)
        } else {
            err
        })
    }
}

pub fn git(args: &[&str], cwd: &Path) -> Option<String> {
    run("git", args, Some(cwd)).ok()
}

pub fn home_dir() -> PathBuf {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
}

pub fn expand_tilde(raw: &str) -> PathBuf {
    let raw = raw.trim();
    match raw.strip_prefix("~") {
        Some(rest) if rest.is_empty() || rest.starts_with('/') || rest.starts_with('\\') => {
            home_dir().join(rest.trim_start_matches(['/', '\\']))
        }
        _ => PathBuf::from(raw),
    }
}

pub fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Temp file in the same directory + rename: readers never see a partial file.
pub fn atomic_write(path: &Path, text: &str) -> Result<(), String> {
    let dir = path.parent().ok_or("path has no parent")?;
    std::fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("file");
    let tmp = dir.join(format!(".{name}.{}.tmp", std::process::id()));
    std::fs::write(&tmp, text).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        e.to_string()
    })
}

pub fn slugify(name: &str) -> String {
    let mut out = String::new();
    for c in name.trim().to_lowercase().chars() {
        if c.is_ascii_alphanumeric() || c == '.' || c == '_' {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    let out = out.trim_matches(['-', '.', '_']).to_string();
    if out.is_empty() {
        "garden".into()
    } else {
        out
    }
}

pub fn estimate_tokens(text: &str) -> u64 {
    (text.len() as u64).div_ceil(4)
}
