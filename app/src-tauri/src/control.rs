//! Agent-to-agent control: a manager terminal prompts and reads its workers, and
//! workers report back, through a small local HTTP endpoint the app runs.
//!
//! Every agent terminal gets `CG_CONTROL` (the endpoint), `CG_TOKEN` (its own
//! secret) and `CG_TERMINAL` (its key) in its environment; the plugin's MCP
//! server (`mcp/cg_control.py`) forwards the agent's tool calls here. Who may
//! reach whom is the garden's `links` graph, enforced in `call`. Prompts and
//! screen reads go through the window that owns the terminal, which holds the
//! terminal emulator; it answers over `control_reply`.

use crate::store::Link;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::mpsc::{channel, Sender};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

/// How long a call waits for the window that owns a terminal.
const ASK_TIMEOUT: Duration = Duration::from_secs(8);
const MAX_BODY: usize = 64 * 1024;
const MAX_LINES: usize = 400;

#[derive(Clone, Debug)]
pub struct Term {
    pub garden: String,
    pub title: String,
    pub agent: Option<String>,
    pub account: Option<String>,
    pub cwd: String,
    pub pty: u32,
}

#[derive(Default)]
struct Inner {
    /// secret -> terminal key
    tokens: HashMap<String, String>,
    terms: HashMap<String, Term>,
    pending: HashMap<u64, Sender<Result<String, String>>>,
    next: u64,
}

#[derive(Clone, Default)]
pub struct Control {
    inner: Arc<Mutex<Inner>>,
    addr: Arc<OnceLock<String>>,
}

/// Links as the UI saved them: no self-links, no duplicates, no cycles (a manager
/// and its worker prompting each other in circles would never stop).
pub fn valid_links(links: Vec<Link>) -> Result<Vec<Link>, String> {
    let mut out: Vec<Link> = Vec::new();
    for l in links {
        if l.manager.is_empty() || l.worker.is_empty() || l.manager == l.worker {
            return Err("a terminal cannot manage itself".into());
        }
        if !out.contains(&l) {
            out.push(l);
        }
    }
    // Depth-first from every manager: reaching it again is a cycle.
    fn reaches(links: &[Link], from: &str, target: &str, depth: usize) -> bool {
        depth < links.len() + 1
            && links.iter().filter(|l| l.manager == from).any(|l| l.worker == target || reaches(links, &l.worker, target, depth + 1))
    }
    for l in &out {
        if reaches(&out, &l.worker, &l.manager, 0) {
            return Err("managers cannot form a loop".into());
        }
    }
    Ok(out)
}

/// What a call needs from the rest of the app, so the rules test without one.
pub trait World {
    fn links(&self, garden: &str) -> Vec<Link>;
    fn alive(&self, pty: u32) -> bool;
    /// Ask the window owning terminal `key`: {"kind": "send"|"read", ...}.
    fn ask(&self, key: &str, request: Value) -> Result<String, String>;
}

fn secret() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};
    // RandomState is seeded from the OS per instance: unguessable, no extra crate.
    let mut out = String::new();
    for i in 0..4u8 {
        let mut h = RandomState::new().build_hasher();
        h.write_u8(i);
        h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
        out.push_str(&format!("{:016x}", h.finish()));
    }
    out
}

impl Control {
    pub fn addr(&self) -> Option<&String> {
        self.addr.get()
    }

    /// Record a running agent terminal; returns its secret for the environment.
    pub fn register(&self, key: &str, term: Term) -> String {
        let token = secret();
        let mut g = self.inner.lock().unwrap();
        g.tokens.retain(|_, k| k != key); // a resumed terminal replaces its old secret
        g.tokens.insert(token.clone(), key.to_string());
        g.terms.insert(key.to_string(), term);
        token
    }

    /// The environment variables an agent terminal gets, or none while the endpoint is down.
    pub fn env(&self, key: &str, term: Term) -> Vec<(String, String)> {
        let Some(addr) = self.addr() else { return vec![] };
        let token = self.register(key, term);
        vec![
            ("CG_CONTROL".into(), format!("http://{addr}")),
            ("CG_TOKEN".into(), token),
            ("CG_TERMINAL".into(), key.to_string()),
        ]
    }

    /// The pty a registered terminal runs in, known once it has started.
    pub fn set_pty(&self, key: &str, pty: u32) {
        if let Some(t) = self.inner.lock().unwrap().terms.get_mut(key) {
            t.pty = pty;
        }
    }

    /// The window answered request `id`.
    pub fn reply(&self, id: u64, result: Result<String, String>) {
        if let Some(tx) = self.inner.lock().unwrap().pending.remove(&id) {
            let _ = tx.send(result);
        }
    }

    /// Park a request until a window replies (or time runs out).
    pub fn wait_for(&self, emit: impl FnOnce(u64)) -> Result<String, String> {
        let (tx, rx) = channel();
        let id = {
            let mut g = self.inner.lock().unwrap();
            g.next += 1;
            let id = g.next;
            g.pending.insert(id, tx);
            id
        };
        emit(id);
        let out = rx.recv_timeout(ASK_TIMEOUT);
        self.inner.lock().unwrap().pending.remove(&id);
        out.unwrap_or_else(|_| Err("that terminal is not open in any window".into()))
    }

    /// Run one tool call from the terminal that owns `token`.
    pub fn call(&self, world: &dyn World, token: &str, tool: &str, args: &Value) -> Result<Value, String> {
        let (me_key, me, terms) = {
            let g = self.inner.lock().unwrap();
            let key = g.tokens.get(token).cloned().ok_or("unknown terminal")?;
            let me = g.terms.get(&key).cloned().ok_or("unknown terminal")?;
            (key, me, g.terms.clone())
        };
        let links = world.links(&me.garden);
        let manages = |m: &str, w: &str| links.iter().any(|l| l.manager == m && l.worker == w);
        let same_garden = |t: &&(&String, &Term)| t.1.garden == me.garden && t.0 != &me_key;
        let others: Vec<(&String, &Term)> = terms.iter().collect();
        // A target named by key or by (unique) title.
        let resolve = |name: &str| -> Result<String, String> {
            let hits: Vec<&String> = others
                .iter()
                .filter(|t| same_garden(t))
                .filter(|t| t.0 == name || t.1.title.eq_ignore_ascii_case(name))
                .map(|t| t.0)
                .collect();
            match hits.as_slice() {
                [one] => Ok((*one).clone()),
                [] => Err(format!("no terminal '{name}' in this garden; call list_terminals")),
                _ => Err(format!("'{name}' is ambiguous; use its key")),
            }
        };
        let text = |name: &str| args.get(name).and_then(|v| v.as_str()).map(str::trim).filter(|s| !s.is_empty());
        let describe = |key: &String, t: &Term, role: &str| {
            json!({
                "key": key, "title": t.title, "role": role, "agent": t.agent, "account": t.account,
                "folder": t.cwd, "running": world.alive(t.pty),
            })
        };
        match tool {
            "list_terminals" => {
                let mut out = Vec::new();
                for t in others.iter().filter(|t| same_garden(t)) {
                    if manages(&me_key, t.0) {
                        out.push(describe(t.0, t.1, "worker"));
                    } else if manages(t.0, &me_key) {
                        out.push(describe(t.0, t.1, "manager"));
                    }
                }
                Ok(json!({ "you": describe(&me_key, &me, "you"), "terminals": out }))
            }
            "send_prompt" => {
                let to = resolve(text("to").ok_or("'to' is required")?)?;
                let body = text("text").ok_or("'text' is required")?;
                if !manages(&me_key, &to) {
                    return Err("you do not manage that terminal".into());
                }
                let framed = format!(
                    "[Task from your manager \"{}\"] {body}\n(When done or blocked, tell your manager with the report_to_manager tool.)",
                    me.title
                );
                world.ask(&to, json!({ "kind": "send", "key": to, "text": framed }))?;
                Ok(json!({ "delivered_to": to }))
            }
            "read_output" => {
                let from = resolve(text("from").ok_or("'from' is required")?)?;
                if !manages(&me_key, &from) {
                    return Err("you do not manage that terminal".into());
                }
                let lines = args.get("lines").and_then(|v| v.as_u64()).unwrap_or(60).clamp(1, MAX_LINES as u64);
                let screen = world.ask(&from, json!({ "kind": "read", "key": from, "lines": lines }))?;
                Ok(json!({ "terminal": from, "output": screen }))
            }
            "report_to_manager" => {
                let body = text("text").ok_or("'text' is required")?;
                let bosses: Vec<String> =
                    links.iter().filter(|l| l.worker == me_key).map(|l| l.manager.clone()).collect();
                if bosses.is_empty() {
                    return Err("you have no manager; link this terminal under one in the app first".into());
                }
                let framed = format!("[Report from your worker \"{}\"] {body}", me.title);
                let mut sent = Vec::new();
                let mut failed = Vec::new();
                for b in bosses {
                    match world.ask(&b, json!({ "kind": "send", "key": b, "text": framed })) {
                        Ok(_) => sent.push(b),
                        Err(e) => failed.push(format!("{b}: {e}")),
                    }
                }
                if sent.is_empty() {
                    return Err(failed.join("; "));
                }
                Ok(json!({ "delivered_to": sent, "failed": failed }))
            }
            other => Err(format!("unknown tool '{other}'")),
        }
    }
}

fn respond(stream: &mut TcpStream, status: &str, body: &Value) {
    let text = body.to_string();
    let _ = write!(
        stream,
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{text}",
        text.len()
    );
}

/// One request: POST /call with {"token", "tool", "args"}.
fn serve(mut stream: TcpStream, control: &Control, world: &dyn World) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let mut buf = Vec::new();
    let mut chunk = [0u8; 4096];
    let (head_end, len) = loop {
        let Ok(n) = stream.read(&mut chunk) else { return };
        if n == 0 {
            return;
        }
        buf.extend_from_slice(&chunk[..n]);
        if buf.len() > MAX_BODY + 8192 {
            return respond(&mut stream, "413 Payload Too Large", &json!({ "error": "too large" }));
        }
        if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            let head = String::from_utf8_lossy(&buf[..i]).to_lowercase();
            let len = head
                .lines()
                .find_map(|l| l.strip_prefix("content-length:").and_then(|v| v.trim().parse::<usize>().ok()))
                .unwrap_or(0);
            if !head.starts_with("post /call") || len > MAX_BODY {
                return respond(&mut stream, "400 Bad Request", &json!({ "error": "POST /call only" }));
            }
            break (i + 4, len);
        }
    };
    while buf.len() < head_end + len {
        let Ok(n) = stream.read(&mut chunk) else { return };
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&chunk[..n]);
    }
    let body: Value = serde_json::from_slice(&buf[head_end..]).unwrap_or(Value::Null);
    let token = body.get("token").and_then(|v| v.as_str()).unwrap_or("");
    let tool = body.get("tool").and_then(|v| v.as_str()).unwrap_or("");
    let args = body.get("args").cloned().unwrap_or(Value::Null);
    match control.call(world, token, tool, &args) {
        Ok(v) => respond(&mut stream, "200 OK", &json!({ "ok": true, "result": v })),
        Err(e) => {
            let status = if e == "unknown terminal" { "401 Unauthorized" } else { "200 OK" };
            respond(&mut stream, status, &json!({ "ok": false, "error": e }))
        }
    }
}

/// Start the endpoint on a free localhost port; its address is then in `addr()`.
pub fn start(control: Control, world: Arc<dyn World + Send + Sync>) -> std::io::Result<()> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let _ = control.addr.set(listener.local_addr()?.to_string());
    std::thread::spawn(move || {
        for stream in listener.incoming().flatten() {
            let (c, w) = (control.clone(), world.clone());
            std::thread::spawn(move || serve(stream, &c, w.as_ref()));
        }
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex as M;

    struct Fake {
        links: Vec<Link>,
        asked: M<Vec<Value>>,
    }
    impl World for Fake {
        fn links(&self, _: &str) -> Vec<Link> {
            self.links.clone()
        }
        fn alive(&self, _: u32) -> bool {
            true
        }
        fn ask(&self, _: &str, request: Value) -> Result<String, String> {
            let reply = if request["kind"] == "read" { "screen text".to_string() } else { "ok".into() };
            self.asked.lock().unwrap().push(request);
            Ok(reply)
        }
    }

    fn term(title: &str, pty: u32) -> Term {
        Term { garden: "g".into(), title: title.into(), agent: Some("claude".into()), account: None, cwd: "/g".into(), pty }
    }

    fn setup() -> (Control, Fake, String, String, String) {
        let c = Control::default();
        let boss = c.register("boss", term("Boss", 1));
        let w1 = c.register("w1", term("Worker One", 2));
        let w2 = c.register("w2", term("Worker Two", 3));
        let fake = Fake { links: vec![Link { manager: "boss".into(), worker: "w1".into() }], asked: M::new(vec![]) };
        (c, fake, boss, w1, w2)
    }

    #[test]
    fn a_manager_reaches_only_its_workers() {
        let (c, f, boss, w1, _w2) = setup();
        let sent = c.call(&f, &boss, "send_prompt", &json!({ "to": "Worker One", "text": "build it" })).unwrap();
        assert_eq!(sent["delivered_to"], "w1");
        let asked = f.asked.lock().unwrap();
        assert_eq!(asked[0]["key"], "w1");
        assert!(asked[0]["text"].as_str().unwrap().contains("build it"));
        drop(asked);
        // Not linked: refused. A worker cannot prompt its manager either.
        assert!(c.call(&f, &boss, "send_prompt", &json!({ "to": "w2", "text": "x" })).unwrap_err().contains("do not manage"));
        assert!(c.call(&f, &w1, "send_prompt", &json!({ "to": "boss", "text": "x" })).unwrap_err().contains("do not manage"));
        assert!(c.call(&f, &w1, "read_output", &json!({ "from": "boss" })).is_err());
    }

    #[test]
    fn workers_report_back_and_managers_read() {
        let (c, f, boss, w1, w2) = setup();
        let r = c.call(&f, &w1, "report_to_manager", &json!({ "text": "done" })).unwrap();
        assert_eq!(r["delivered_to"], json!(["boss"]));
        assert!(f.asked.lock().unwrap().last().unwrap()["text"].as_str().unwrap().contains("Worker One"));
        assert!(c.call(&f, &w2, "report_to_manager", &json!({ "text": "x" })).unwrap_err().contains("no manager"));
        let out = c.call(&f, &boss, "read_output", &json!({ "from": "w1", "lines": 9999 })).unwrap();
        assert_eq!(out["output"], "screen text");
        assert_eq!(f.asked.lock().unwrap().last().unwrap()["lines"], MAX_LINES);
    }

    #[test]
    fn listing_shows_only_linked_terminals_and_bad_tokens_fail() {
        let (c, f, boss, w1, w2) = setup();
        let l = c.call(&f, &boss, "list_terminals", &Value::Null).unwrap();
        assert_eq!(l["terminals"].as_array().unwrap().len(), 1);
        assert_eq!(l["terminals"][0]["role"], "worker");
        assert_eq!(c.call(&f, &w1, "list_terminals", &Value::Null).unwrap()["terminals"][0]["role"], "manager");
        assert!(c.call(&f, &w2, "list_terminals", &Value::Null).unwrap()["terminals"].as_array().unwrap().is_empty());
        assert_eq!(c.call(&f, "nope", "list_terminals", &Value::Null).unwrap_err(), "unknown terminal");
    }

    fn post(addr: &str, request_line: &str, body: &str) -> String {
        let mut s = TcpStream::connect(addr).unwrap();
        write!(s, "{request_line} HTTP/1.1\r\nHost: x\r\nContent-Length: {}\r\n\r\n{body}", body.len()).unwrap();
        let mut out = String::new();
        s.read_to_string(&mut out).unwrap();
        out
    }

    #[test]
    fn the_http_endpoint_answers_calls_and_rejects_the_rest() {
        let (c, f, boss, _w1, _w2) = setup();
        let world: Arc<dyn World + Send + Sync> = Arc::new(f);
        start(c.clone(), world).unwrap();
        let addr = c.addr().unwrap().clone();
        let ok = post(&addr, "POST /call", &json!({ "token": boss, "tool": "list_terminals" }).to_string());
        assert!(ok.starts_with("HTTP/1.1 200"), "{ok}");
        assert!(ok.contains("\"ok\":true") && ok.contains("Worker One"), "{ok}");
        let bad = post(&addr, "POST /call", &json!({ "token": "wrong", "tool": "list_terminals" }).to_string());
        assert!(bad.starts_with("HTTP/1.1 401"), "{bad}");
        assert!(post(&addr, "GET /call", "").starts_with("HTTP/1.1 400"));
        assert!(post(&addr, "POST /elsewhere", "{}").starts_with("HTTP/1.1 400"));
    }

    #[test]
    fn links_reject_self_duplicates_and_loops() {
        let l = |m: &str, w: &str| Link { manager: m.into(), worker: w.into() };
        assert_eq!(valid_links(vec![l("a", "b"), l("a", "b"), l("a", "c")]).unwrap().len(), 2);
        assert!(valid_links(vec![l("a", "a")]).is_err());
        assert!(valid_links(vec![l("a", "b"), l("b", "a")]).is_err());
        assert!(valid_links(vec![l("a", "b"), l("b", "c"), l("c", "a")]).is_err());
        assert!(valid_links(vec![l("a", "b"), l("a", "c"), l("b", "d"), l("c", "d")]).is_ok()); // diamond is fine
    }

    #[test]
    fn a_resumed_terminal_replaces_its_secret() {
        let c = Control::default();
        let a = c.register("k", term("T", 1));
        let b = c.register("k", term("T", 2));
        assert_ne!(a, b);
        let f = Fake { links: vec![], asked: M::new(vec![]) };
        assert!(c.call(&f, &a, "list_terminals", &Value::Null).is_err());
        assert!(c.call(&f, &b, "list_terminals", &Value::Null).is_ok());
    }
}
