//! Terminals: one pseudo-terminal per pane (ConPTY on Windows), output streamed
//! to the pane over a Tauri channel as base64 chunks.

use base64::Engine;
use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};
use serde::Serialize;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;
use tauri::ipc::Channel;

#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data", rename_all = "camelCase")]
pub enum PtyEvent {
    Output(String),
    Exit(Option<u32>),
}

/// Bytes sent to the pane but not yet confirmed as rendered. The reader stops
/// at `MAX_IN_FLIGHT`, so a process that floods output (a runaway `cat`, a
/// looping agent) makes the pty fill up and the process block, instead of
/// the queue in the webview growing without bound until memory runs out.
type Flow = Arc<(Mutex<usize>, Condvar)>;
const MAX_IN_FLIGHT: usize = 1024 * 1024;

struct Session {
    flow: Flow,
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
    /// Argv prefix that starts the shell inside the network sandbox (#81); empty: none.
    pub sandbox: Vec<String>,
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
    let mut cmd = match launch.sandbox.split_first() {
        Some((helper, args)) => {
            let mut c = CommandBuilder::new(helper);
            c.args(args);
            c.arg(&shell);
            c
        }
        None => CommandBuilder::new(&shell),
    };
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
        let flow: Flow = Arc::new((Mutex::new(0), Condvar::new()));
        self.sessions
            .lock()
            .unwrap()
            .insert(id, Session { flow: flow.clone(), writer, master: pair.master, child });

        let out = channel.clone();
        let owner = self.sessions.clone();
        std::thread::spawn(move || {
            let b64 = base64::engine::general_purpose::STANDARD;
            let mut buf = [0u8; 16 * 1024];
            while let Ok(n) = reader.read(&mut buf) {
                if n == 0 {
                    break;
                }
                // Wait for the pane to catch up before reading more.
                let (lock, cv) = &*flow;
                let mut pending = lock.lock().unwrap();
                while *pending >= MAX_IN_FLIGHT {
                    if !owner.lock().unwrap().contains_key(&id) {
                        return;
                    }
                    pending = cv.wait_timeout(pending, Duration::from_millis(200)).unwrap().0;
                }
                *pending += n;
                drop(pending);
                if out.send(PtyEvent::Output(b64.encode(&buf[..n]))).is_err() {
                    // The window that owned this terminal is gone: end the process with it.
                    if let Some(mut s) = owner.lock().unwrap().remove(&id) {
                        let _ = s.child.kill();
                    }
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

    /// The pane rendered `bytes` of output: let the reader go on.
    pub fn ack(&self, id: u32, bytes: usize) {
        let flow = self.sessions.lock().unwrap().get(&id).map(|s| s.flow.clone());
        if let Some(flow) = flow {
            let mut pending = flow.0.lock().unwrap();
            *pending = pending.saturating_sub(bytes);
            flow.1.notify_all();
        }
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

    /// Still running (the exit poll drops finished sessions).
    pub fn alive(&self, id: u32) -> bool {
        self.sessions.lock().unwrap().contains_key(&id)
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

// The test drives a POSIX shell, so it only exists on Unix.
#[cfg(all(test, unix))]
mod tests {
    use super::*;

    #[test]
    fn a_sandboxed_launch_starts_the_shell_through_the_helper() {
        let launch = Launch {
            command: Some("claude".into()),
            cwd: "/".into(),
            env: vec![],
            cols: 80,
            rows: 24,
            sandbox: vec!["/app".into(), "--cg-sandbox".into(), "landlock".into(), "1".into(), "--".into()],
        };
        let argv: Vec<String> = build_command(&launch).get_argv().iter().map(|a| a.to_string_lossy().into()).collect();
        assert_eq!(argv, ["/app", "--cg-sandbox", "landlock", "1", "--", &default_shell(), "-lc", "claude"]);
    }

    #[test]
    fn agent_command_runs_in_cwd_with_env() {
        let cwd = std::env::temp_dir().canonicalize().unwrap();
        let launch = Launch {
            command: Some("printf '%s|%s' \"$PWD\" \"$CG_TEST\"".into()),
            cwd: cwd.to_string_lossy().into(),
            env: vec![("CG_TEST".into(), "ok".into())],
            cols: 80,
            rows: 24,
            sandbox: vec![],
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

#[cfg(all(test, unix))]
mod flow_tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    #[test]
    fn unacked_output_stops_at_the_window_and_resumes_on_ack() {
        let sent = Arc::new(AtomicUsize::new(0));
        let s = sent.clone();
        let channel = Channel::new(move |body| {
            if let tauri::ipc::InvokeResponseBody::Json(j) = body {
                // base64 length * 3/4 ~ bytes
                s.fetch_add(j.len() * 3 / 4, Ordering::SeqCst);
            }
            Ok(())
        });
        let ptys = Ptys::default();
        let cwd = std::env::temp_dir().to_string_lossy().into();
        let id = ptys
            .spawn(Launch { command: Some("yes".into()), cwd, env: vec![], cols: 80, rows: 24, sandbox: vec![] }, channel)
            .unwrap();
        std::thread::sleep(Duration::from_millis(1500));
        let first = sent.load(Ordering::SeqCst);
        std::thread::sleep(Duration::from_millis(500));
        let second = sent.load(Ordering::SeqCst);
        assert_eq!(first, second, "reader kept sending without acks");
        assert!(first < 3 * MAX_IN_FLIGHT, "sent {first}");
        ptys.ack(id, 900_000);
        std::thread::sleep(Duration::from_millis(500));
        assert!(sent.load(Ordering::SeqCst) > second, "did not resume after ack");
        ptys.kill(id);
    }
}
