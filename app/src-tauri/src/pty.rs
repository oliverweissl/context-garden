//! Terminals: one pseudo-terminal per pane (ConPTY on Windows), output streamed
//! to the pane over a Tauri channel as base64 chunks.

use base64::Engine;
use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};
use serde::Serialize;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use tauri::ipc::Channel;

#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data", rename_all = "camelCase")]
pub enum PtyEvent {
    Output(String),
    Exit(Option<u32>),
}

struct Session {
    writer: Box<dyn Write + Send>,
    master: Box<dyn MasterPty + Send>,
    child: Box<dyn Child + Send + Sync>,
}

#[derive(Default)]
pub struct Ptys {
    next: AtomicU32,
    sessions: Arc<Mutex<HashMap<u32, Session>>>,
}

/// What a pane runs. `command` None = the user's plain shell.
pub struct Launch {
    pub command: Option<String>,
    pub cwd: String,
    pub env: Vec<(String, String)>,
    pub cols: u16,
    pub rows: u16,
}

fn default_shell() -> String {
    if cfg!(windows) {
        "powershell.exe".into()
    } else {
        std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".into())
    }
}

/// Agents run through the login shell so its PATH, aliases and npm shims
/// (claude.cmd on Windows) resolve the same way they do in a normal terminal.
fn build_command(launch: &Launch) -> CommandBuilder {
    let shell = default_shell();
    let mut cmd = CommandBuilder::new(&shell);
    match (&launch.command, cfg!(windows)) {
        (None, true) => cmd.arg("-NoLogo"),
        (None, false) => cmd.arg("-l"),
        (Some(c), true) => cmd.args(["-NoLogo", "-Command", c.as_str()]),
        (Some(c), false) => cmd.args(["-lc", c.as_str()]),
    }
    cmd.cwd(&launch.cwd);
    if let Ok(path) = std::env::var("PATH") {
        cmd.env("PATH", path);
    }
    cmd.env("TERM", "xterm-256color");
    cmd.env("COLORTERM", "truecolor");
    for (k, v) in &launch.env {
        cmd.env(k, v);
    }
    cmd
}

impl Ptys {
    pub fn spawn(&self, launch: Launch, channel: Channel<PtyEvent>) -> Result<u32, String> {
        let pair = native_pty_system()
            .openpty(PtySize { rows: launch.rows.max(2), cols: launch.cols.max(10), pixel_width: 0, pixel_height: 0 })
            .map_err(|e| e.to_string())?;
        let child = pair.slave.spawn_command(build_command(&launch)).map_err(|e| e.to_string())?;
        drop(pair.slave);
        let mut reader = pair.master.try_clone_reader().map_err(|e| e.to_string())?;
        let writer = pair.master.take_writer().map_err(|e| e.to_string())?;
        let id = self.next.fetch_add(1, Ordering::SeqCst) + 1;
        self.sessions
            .lock()
            .unwrap()
            .insert(id, Session { writer, master: pair.master, child });

        let out = channel.clone();
        std::thread::spawn(move || {
            let b64 = base64::engine::general_purpose::STANDARD;
            let mut buf = [0u8; 16 * 1024];
            while let Ok(n) = reader.read(&mut buf) {
                if n == 0 || out.send(PtyEvent::Output(b64.encode(&buf[..n]))).is_err() {
                    break;
                }
            }
        });

        // The master does not report EOF on every platform (macOS keeps the
        // read blocked), so the child's exit is polled instead.
        let sessions = self.sessions.clone();
        std::thread::spawn(move || loop {
            std::thread::sleep(std::time::Duration::from_millis(250));
            let mut map = sessions.lock().unwrap();
            let Some(s) = map.get_mut(&id) else { break };
            match s.child.try_wait() {
                Ok(None) => continue,
                result => {
                    let code = result.ok().flatten().map(|st| st.exit_code());
                    map.remove(&id); // drops the master: closes the pty
                    let _ = channel.send(PtyEvent::Exit(code));
                    break;
                }
            }
        });
        Ok(id)
    }

    pub fn write(&self, id: u32, data: &str) -> Result<(), String> {
        let mut sessions = self.sessions.lock().unwrap();
        let s = sessions.get_mut(&id).ok_or("terminal is closed")?;
        s.writer.write_all(data.as_bytes()).map_err(|e| e.to_string())?;
        s.writer.flush().map_err(|e| e.to_string())
    }

    pub fn resize(&self, id: u32, cols: u16, rows: u16) -> Result<(), String> {
        let sessions = self.sessions.lock().unwrap();
        let s = sessions.get(&id).ok_or("terminal is closed")?;
        s.master
            .resize(PtySize { rows: rows.max(2), cols: cols.max(10), pixel_width: 0, pixel_height: 0 })
            .map_err(|e| e.to_string())
    }

    pub fn kill(&self, id: u32) {
        if let Some(s) = self.sessions.lock().unwrap().get_mut(&id) {
            let _ = s.child.kill();
        }
    }

    pub fn kill_all(&self) {
        for s in self.sessions.lock().unwrap().values_mut() {
            let _ = s.child.kill();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    #[cfg(unix)]
    fn agent_command_runs_in_cwd_with_env() {
        let cwd = std::env::temp_dir().canonicalize().unwrap();
        let launch = Launch {
            command: Some("printf '%s|%s' \"$PWD\" \"$CG_TEST\"".into()),
            cwd: cwd.to_string_lossy().into(),
            env: vec![("CG_TEST".into(), "ok".into())],
            cols: 80,
            rows: 24,
        };
        let pair = native_pty_system()
            .openpty(PtySize { rows: 24, cols: 80, pixel_width: 0, pixel_height: 0 })
            .unwrap();
        let mut child = pair.slave.spawn_command(build_command(&launch)).unwrap();
        drop(pair.slave);
        let mut reader = pair.master.try_clone_reader().unwrap();
        // macOS never signals EOF on the master, so read until the marker shows up.
        let want = format!("{}|ok", cwd.display());
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let mut buf = [0u8; 4096];
            while let Ok(n) = reader.read(&mut buf) {
                if n == 0 || tx.send(buf[..n].to_vec()).is_err() {
                    break;
                }
            }
        });
        let mut out = String::new();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while !out.contains(&want) && std::time::Instant::now() < deadline {
            if let Ok(chunk) = rx.recv_timeout(std::time::Duration::from_millis(200)) {
                out.push_str(&String::from_utf8_lossy(&chunk));
            }
        }
        let _ = child.kill();
        assert!(out.contains(&want), "{out:?}");
    }
}
