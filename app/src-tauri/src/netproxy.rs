//! The network security layer's decision point (#81, docs/network-security-design.md
//! sections 2 and 5): one loopback proxy listener per agent terminal, speaking
//! HTTP CONNECT, plain HTTP and SOCKS5 (remote DNS). Each connection is checked
//! against the terminal's garden's lists (netrules); one they do not cover is held
//! while the app asks the user, coalesced per (garden, host), and declined after
//! the timeout (60 s). The proxy resolves names itself and dials the addresses it
//! checked, so a rebinding between check and dial is not possible.
//!
//! On Linux a listener serves only processes of the user running the app (the
//! owner of the connecting socket, from /proc/net/tcp): other users of the
//! machine cannot use it as a relay.
//!
//! Enforcement (the OS sandbox that leaves this proxy as the only way out) is a
//! later step; until then programs that ignore the proxy variables bypass it.

use crate::netrules::{decide, Decision, List};
use serde::Serialize;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{IpAddr, Shutdown, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

/// How long a connection waits for the user's answer (owner decision 2).
pub const PROMPT_TIMEOUT: Duration = Duration::from_secs(60);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const MAX_HEAD: usize = 32 * 1024;

/// What the window shows (event `net-prompt`).
#[derive(Clone, Debug, Serialize)]
pub struct Prompt {
    pub id: u64,
    /// Terminal key: the window holding this terminal asks.
    pub key: String,
    pub garden: String,
    pub terminal: String,
    pub host: String,
    pub port: u16,
    pub ips: Vec<String>,
    pub timeout_secs: u64,
}

/// What the proxy needs from the rest of the app (tests use a fake).
pub trait Env: Send + Sync {
    /// The lists a garden combines right now.
    fn lists(&self, garden: &str) -> Vec<List>;
    /// Show a prompt.
    fn ask(&self, prompt: &Prompt);
    /// A prompt is over: answered (`allowed`) or timed out (None).
    fn done(&self, id: u64, allowed: Option<bool>);
}

type Slot = Arc<(Mutex<Option<bool>>, Condvar)>;

struct Pending {
    garden: String,
    host: String,
    slot: Slot,
}

struct Listener {
    port: u16,
    pty: u32,
    stop: Arc<AtomicBool>,
    /// (garden, terminal title), updated when the terminal is resumed.
    who: Arc<Mutex<(String, String)>>,
}

#[derive(Default)]
struct Inner {
    listeners: HashMap<String, Listener>,
    pending: HashMap<u64, Pending>,
    next: u64,
}

#[derive(Clone)]
pub struct NetProxy {
    inner: Arc<Mutex<Inner>>,
    env: Arc<dyn Env>,
    timeout: Duration,
}

impl NetProxy {
    pub fn new(env: Arc<dyn Env>, timeout: Duration) -> Self {
        NetProxy { inner: Arc::default(), env, timeout }
    }

    /// The loopback port of terminal `key`'s listener, started if needed.
    pub fn listen(&self, key: &str, garden: &str, title: &str) -> Result<u16, String> {
        let mut g = self.inner.lock().unwrap();
        if let Some(l) = g.listeners.get(key) {
            *l.who.lock().unwrap() = (garden.to_string(), title.to_string());
            return Ok(l.port);
        }
        let socket = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("network proxy: {e}"))?;
        let port = socket.local_addr().map_err(|e| e.to_string())?.port();
        let stop = Arc::new(AtomicBool::new(false));
        let who = Arc::new(Mutex::new((garden.to_string(), title.to_string())));
        g.listeners.insert(key.to_string(), Listener { port, pty: 0, stop: stop.clone(), who: who.clone() });
        let me = self.clone();
        let key = key.to_string();
        std::thread::spawn(move || {
            for stream in socket.incoming() {
                if stop.load(Ordering::SeqCst) {
                    break;
                }
                let Ok(stream) = stream else { continue };
                let (me, key, who) = (me.clone(), key.clone(), who.clone());
                std::thread::spawn(move || {
                    let (garden, title) = who.lock().unwrap().clone();
                    me.serve(stream, &key, &garden, &title);
                });
            }
        });
        Ok(port)
    }

    /// The pty a listener's terminal runs in, so closing the terminal stops it.
    pub fn set_pty(&self, key: &str, pty: u32) {
        if let Some(l) = self.inner.lock().unwrap().listeners.get_mut(key) {
            l.pty = pty;
        }
    }

    /// Stop the listener of the terminal running in `pty`.
    pub fn close_pty(&self, pty: u32) {
        let mut g = self.inner.lock().unwrap();
        let keys: Vec<String> = g.listeners.iter().filter(|(_, l)| l.pty == pty && pty != 0).map(|(k, _)| k.clone()).collect();
        for k in keys {
            if let Some(l) = g.listeners.remove(&k) {
                l.stop.store(true, Ordering::SeqCst);
                let _ = TcpStream::connect(("127.0.0.1", l.port)); // wake its accept()
            }
        }
    }

    /// The user's answer to prompt `id`. Err when it is over already.
    pub fn answer(&self, id: u64, allow: bool) -> Result<(), String> {
        {
            // Set under the lock, so a timeout racing with this answer sees it.
            let mut g = self.inner.lock().unwrap();
            let p = g.pending.remove(&id).ok_or("that question was already answered or timed out")?;
            *p.slot.0.lock().unwrap() = Some(allow);
            p.slot.1.notify_all();
        }
        self.env.done(id, Some(allow));
        Ok(())
    }

    /// Allow or decline one connection to `host` (resolved to `ips`) for a garden.
    fn check(&self, key: &str, garden: &str, title: &str, host: &str, port: u16, ips: &[IpAddr]) -> Result<(), String> {
        let lists = self.env.lists(garden);
        let refs: Vec<&List> = lists.iter().collect();
        match decide(&refs, host, ips) {
            Decision::Allow { .. } => Ok(()),
            Decision::Deny { list } => Err(format!("denied by the list \"{list}\"")),
            Decision::Ask => {
                if self.ask(key, garden, title, host, port, ips) {
                    Ok(())
                } else {
                    Err("declined".into())
                }
            }
        }
    }

    /// Hold until the user answers; one prompt per (garden, host) at a time.
    fn ask(&self, key: &str, garden: &str, title: &str, host: &str, port: u16, ips: &[IpAddr]) -> bool {
        let (id, slot, new) = {
            let mut g = self.inner.lock().unwrap();
            match g.pending.iter().find(|(_, p)| p.garden == garden && p.host == host) {
                Some((id, p)) => (*id, p.slot.clone(), false),
                None => {
                    g.next += 1;
                    let id = g.next;
                    let slot: Slot = Arc::default();
                    g.pending.insert(id, Pending { garden: garden.into(), host: host.into(), slot: slot.clone() });
                    (id, slot, true)
                }
            }
        };
        if new {
            self.env.ask(&Prompt {
                id,
                key: key.into(),
                garden: garden.into(),
                terminal: title.into(),
                host: host.into(),
                port,
                ips: ips.iter().map(|i| i.to_string()).collect(),
                timeout_secs: self.timeout.as_secs(),
            });
        }
        let (lock, cv) = &*slot;
        let (answer, _) = cv.wait_timeout_while(lock.lock().unwrap(), self.timeout, |a| a.is_none()).unwrap();
        if let Some(a) = *answer {
            return a;
        }
        drop(answer);
        // Timed out: decline once (fail closed), and end the prompt for everyone waiting on it.
        let timed_out = {
            let mut g = self.inner.lock().unwrap();
            let gone = g.pending.remove(&id).is_some();
            if gone {
                *lock.lock().unwrap() = Some(false);
                cv.notify_all();
            }
            gone
        };
        if timed_out {
            self.env.done(id, None);
        }
        let decided = lock.lock().unwrap().unwrap_or(false);
        decided
    }

    fn serve(&self, mut client: TcpStream, key: &str, garden: &str, title: &str) {
        if !same_user(&client) {
            eprintln!("network proxy ({title}): refused a connection from another user");
            return;
        }
        let _ = client.set_read_timeout(Some(Duration::from_secs(30)));
        let mut first = [0u8; 1];
        if client.peek(&mut first).ok() != Some(1) {
            return;
        }
        let result = if first[0] == 5 { self.socks5(&mut client, key, garden, title) } else { self.http(&mut client, key, garden, title) };
        match result {
            Ok((upstream, leftover)) => {
                let _ = client.set_read_timeout(None);
                pipe(client, upstream, leftover);
            }
            Err(e) => eprintln!("network proxy ({title}): {e}"),
        }
    }

    /// Resolve, check, dial. Err(reason) when declined or unreachable.
    fn open(&self, key: &str, garden: &str, title: &str, host: &str, port: u16) -> Result<TcpStream, (bool, String)> {
        let addrs: Vec<SocketAddr> = (host, port).to_socket_addrs().map(|a| a.collect()).unwrap_or_default();
        let ips: Vec<IpAddr> = addrs.iter().map(|a| a.ip()).collect();
        self.check(key, garden, title, host, port, &ips).map_err(|e| (true, e))?;
        let mut last = format!("{host} did not resolve");
        for a in &addrs {
            match TcpStream::connect_timeout(a, CONNECT_TIMEOUT) {
                Ok(s) => return Ok(s),
                Err(e) => last = format!("{a}: {e}"),
            }
        }
        Err((false, last))
    }

    fn http(&self, client: &mut TcpStream, key: &str, garden: &str, title: &str) -> Result<(TcpStream, Vec<u8>), String> {
        let (head, rest) = read_head(client)?;
        let text = String::from_utf8_lossy(&head).to_string();
        let mut lines = text.split("\r\n");
        let request = lines.next().unwrap_or("");
        let mut parts = request.split(' ');
        let (method, target, version) = (parts.next().unwrap_or(""), parts.next().unwrap_or(""), parts.next().unwrap_or("HTTP/1.1"));
        let refuse = |client: &mut TcpStream, code: &str, why: &str| {
            let body = format!("Context Garden: {why}\n");
            let _ = write!(client, "HTTP/1.1 {code}\r\nContent-Type: text/plain\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
            why.to_string()
        };
        if method.eq_ignore_ascii_case("CONNECT") {
            let Some((host, port)) = split_host_port(target, 443) else { return Err(refuse(client, "400 Bad Request", "bad CONNECT target")) };
            return match self.open(key, garden, title, &host, port) {
                Ok(up) => {
                    client.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n").map_err(|e| e.to_string())?;
                    Ok((up, rest))
                }
                Err((true, why)) => Err(refuse(client, "403 Forbidden", &format!("{host} {why}"))),
                Err((false, why)) => Err(refuse(client, "502 Bad Gateway", &why)),
            };
        }
        // Plain HTTP: absolute-form target, forwarded in origin-form, one request per connection.
        let Some(after) = target.strip_prefix("http://") else { return Err(refuse(client, "400 Bad Request", "only http:// URLs or CONNECT")) };
        let (authority, path) = after.split_at(after.find('/').unwrap_or(after.len()));
        let path = if path.is_empty() { "/" } else { path };
        let Some((host, port)) = split_host_port(authority, 80) else { return Err(refuse(client, "400 Bad Request", "bad URL")) };
        match self.open(key, garden, title, &host, port) {
            Ok(mut up) => {
                let mut out = format!("{method} {path} {version}\r\n");
                for l in lines.filter(|l| !l.is_empty()) {
                    let name = l.split(':').next().unwrap_or("").trim().to_ascii_lowercase();
                    if !matches!(name.as_str(), "proxy-connection" | "proxy-authorization" | "connection" | "keep-alive") {
                        out.push_str(l);
                        out.push_str("\r\n");
                    }
                }
                out.push_str("Connection: close\r\n\r\n");
                up.write_all(out.as_bytes()).map_err(|e| e.to_string())?;
                Ok((up, rest))
            }
            Err((true, why)) => Err(refuse(client, "403 Forbidden", &format!("{host} {why}"))),
            Err((false, why)) => Err(refuse(client, "502 Bad Gateway", &why)),
        }
    }

    fn socks5(&self, client: &mut TcpStream, key: &str, garden: &str, title: &str) -> Result<(TcpStream, Vec<u8>), String> {
        let io = |e: std::io::Error| e.to_string();
        let mut hello = [0u8; 2];
        client.read_exact(&mut hello).map_err(io)?;
        let mut methods = vec![0u8; hello[1] as usize];
        client.read_exact(&mut methods).map_err(io)?;
        if !methods.contains(&0) {
            let _ = client.write_all(&[5, 0xff]);
            return Err("SOCKS client offers no unauthenticated method".into());
        }
        client.write_all(&[5, 0]).map_err(io)?;
        let mut req = [0u8; 4];
        client.read_exact(&mut req).map_err(io)?;
        let reply = |client: &mut TcpStream, code: u8| {
            let _ = client.write_all(&[5, code, 0, 1, 0, 0, 0, 0, 0, 0]);
        };
        let host = match req[3] {
            1 => {
                let mut a = [0u8; 4];
                client.read_exact(&mut a).map_err(io)?;
                IpAddr::from(a).to_string()
            }
            3 => {
                let mut n = [0u8; 1];
                client.read_exact(&mut n).map_err(io)?;
                let mut name = vec![0u8; n[0] as usize];
                client.read_exact(&mut name).map_err(io)?;
                String::from_utf8_lossy(&name).to_string()
            }
            4 => {
                let mut a = [0u8; 16];
                client.read_exact(&mut a).map_err(io)?;
                IpAddr::from(a).to_string()
            }
            _ => {
                reply(client, 8);
                return Err("SOCKS: unknown address type".into());
            }
        };
        let mut p = [0u8; 2];
        client.read_exact(&mut p).map_err(io)?;
        let port = u16::from_be_bytes(p);
        if req[1] != 1 {
            reply(client, 7);
            return Err("SOCKS: only CONNECT".into());
        }
        match self.open(key, garden, title, &host, port) {
            Ok(up) => {
                reply(client, 0);
                Ok((up, vec![]))
            }
            Err((true, why)) => {
                reply(client, 2); // connection not allowed by ruleset
                Err(format!("{host} {why}"))
            }
            Err((false, why)) => {
                reply(client, 4);
                Err(why)
            }
        }
    }
}

/// Whether the process at the other end of a loopback connection runs as this
/// user: its socket's owner in /proc/net/tcp. Unknown counts as no.
#[cfg(target_os = "linux")]
fn same_user(s: &TcpStream) -> bool {
    use std::net::SocketAddr::V4;
    let (Ok(V4(peer)), Ok(V4(local))) = (s.peer_addr(), s.local_addr()) else { return false };
    // Addresses as the kernel prints them: the 32-bit value in host order, in hex.
    let hex = |a: std::net::SocketAddrV4| format!("{:08X}:{:04X}", u32::from_ne_bytes(a.ip().octets()), a.port());
    let (me, them) = (hex(local), hex(peer));
    let uid = unsafe { libc::geteuid() };
    let Ok(table) = std::fs::read_to_string("/proc/net/tcp") else { return false };
    table.lines().skip(1).any(|l| {
        let f: Vec<&str> = l.split_whitespace().collect();
        f.len() > 7 && f[1] == them && f[2] == me && f[7].parse() == Ok(uid)
    })
}

#[cfg(not(target_os = "linux"))]
fn same_user(_: &TcpStream) -> bool {
    true
}

/// Headers up to the blank line, and whatever came after them.
fn read_head(s: &mut TcpStream) -> Result<(Vec<u8>, Vec<u8>), String> {
    let mut buf = Vec::new();
    let mut chunk = [0u8; 4096];
    loop {
        if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
            let rest = buf.split_off(i + 4);
            buf.truncate(i);
            return Ok((buf, rest));
        }
        if buf.len() > MAX_HEAD {
            return Err("request head too large".into());
        }
        let n = s.read(&mut chunk).map_err(|e| e.to_string())?;
        if n == 0 {
            return Err("closed before the request head ended".into());
        }
        buf.extend_from_slice(&chunk[..n]);
    }
}

/// `host:port`, `[v6]:port` or a bare host (default port).
fn split_host_port(s: &str, default: u16) -> Option<(String, u16)> {
    if let Some(rest) = s.strip_prefix('[') {
        let (h, after) = rest.split_once(']')?;
        let port = match after.strip_prefix(':') {
            Some(p) => p.parse().ok()?,
            None if after.is_empty() => default,
            None => return None,
        };
        return Some((h.to_string(), port));
    }
    match s.rsplit_once(':') {
        Some((h, p)) if !h.contains(':') => Some((h.to_string(), p.parse().ok()?)),
        Some(_) => None,
        None if !s.is_empty() => Some((s.to_string(), default)),
        None => None,
    }
}

/// Copy both ways until either side closes.
fn pipe(client: TcpStream, upstream: TcpStream, leftover: Vec<u8>) {
    let (Ok(mut c2), Ok(mut u2)) = (client.try_clone(), upstream.try_clone()) else { return };
    let (mut client, mut upstream) = (client, upstream);
    if !leftover.is_empty() && upstream.write_all(&leftover).is_err() {
        return;
    }
    let up = std::thread::spawn(move || {
        let _ = std::io::copy(&mut c2, &mut u2);
        let _ = u2.shutdown(Shutdown::Write);
    });
    let _ = std::io::copy(&mut upstream, &mut client);
    let _ = client.shutdown(Shutdown::Write);
    let _ = up.join();
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::netrules::Kind;
    use std::sync::mpsc::{channel, Receiver, Sender};

    struct Fake {
        lists: Mutex<Vec<List>>,
        asked: Mutex<Sender<Prompt>>,
        done: Mutex<Vec<(u64, Option<bool>)>>,
    }
    impl Env for Fake {
        fn lists(&self, _: &str) -> Vec<List> {
            self.lists.lock().unwrap().clone()
        }
        fn ask(&self, p: &Prompt) {
            let _ = self.asked.lock().unwrap().send(p.clone());
        }
        fn done(&self, id: u64, a: Option<bool>) {
            self.done.lock().unwrap().push((id, a));
        }
    }

    fn list(kind: Kind, entries: &[&str]) -> List {
        List { id: format!("{kind:?}"), name: format!("{kind:?}"), kind, entries: entries.iter().map(|s| s.to_string()).collect(), builtin: false }
    }

    fn setup(lists: Vec<List>, timeout: Duration) -> (NetProxy, Arc<Fake>, Receiver<Prompt>, u16) {
        let (tx, rx) = channel();
        let fake = Arc::new(Fake { lists: Mutex::new(lists), asked: Mutex::new(tx), done: Mutex::new(vec![]) });
        let proxy = NetProxy::new(fake.clone(), timeout);
        let port = proxy.listen("k", "g", "Term").unwrap();
        (proxy, fake, rx, port)
    }

    /// A one-shot server answering "hello" to anything.
    fn origin() -> u16 {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        std::thread::spawn(move || {
            for s in l.incoming().flatten() {
                let mut s = s;
                let mut b = [0u8; 1024];
                let _ = s.read(&mut b);
                let _ = s.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello");
            }
        });
        port
    }

    fn connect_via(proxy: u16, target: &str) -> String {
        let mut s = TcpStream::connect(("127.0.0.1", proxy)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
        write!(s, "CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n").unwrap();
        let mut head = [0u8; 512];
        let n = s.read(&mut head).unwrap();
        let head = String::from_utf8_lossy(&head[..n]).to_string();
        if !head.starts_with("HTTP/1.1 200") {
            let mut rest = String::new();
            let _ = s.read_to_string(&mut rest);
            return head + &rest;
        }
        s.write_all(b"GET / HTTP/1.1\r\n\r\n").unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        out
    }

    #[test]
    fn allowed_connects_and_denied_gets_403() {
        let o = origin();
        let (_p, fake, rx, port) = setup(vec![list(Kind::Allow, &["127.0.0.0/8"])], Duration::from_secs(5));
        assert!(connect_via(port, &format!("127.0.0.1:{o}")).ends_with("hello"));
        *fake.lists.lock().unwrap() = vec![list(Kind::Allow, &["127.0.0.0/8"]), list(Kind::Deny, &["127.0.0.1"])];
        let r = connect_via(port, &format!("127.0.0.1:{o}"));
        assert!(r.starts_with("HTTP/1.1 403") && r.contains("Deny"), "{r}");
        assert!(rx.try_recv().is_err(), "covered connections never prompt");
    }

    #[test]
    fn uncovered_connections_wait_for_one_shared_answer() {
        let o = origin();
        let (p, fake, rx, port) = setup(vec![], Duration::from_secs(10));
        let target = format!("127.0.0.1:{o}");
        let t1 = { let t = target.clone(); std::thread::spawn(move || connect_via(port, &t)) };
        let prompt = rx.recv_timeout(Duration::from_secs(5)).unwrap();
        assert_eq!((prompt.host.as_str(), prompt.port, prompt.garden.as_str(), prompt.key.as_str()), ("127.0.0.1", o, "g", "k"));
        assert_eq!(prompt.timeout_secs, 10);
        // A second connection to the same host joins the open prompt.
        let t2 = { let t = target.clone(); std::thread::spawn(move || connect_via(port, &t)) };
        std::thread::sleep(Duration::from_millis(300));
        assert!(rx.try_recv().is_err(), "no second prompt");
        p.answer(prompt.id, true).unwrap();
        assert!(t1.join().unwrap().ends_with("hello"));
        assert!(t2.join().unwrap().ends_with("hello"));
        assert!(p.answer(prompt.id, true).is_err(), "answered once");
        assert_eq!(fake.done.lock().unwrap().as_slice(), &[(prompt.id, Some(true))]);
        // Declined once: refused, and the next connection asks again.
        let t3 = { let t = target.clone(); std::thread::spawn(move || connect_via(port, &t)) };
        let again = rx.recv_timeout(Duration::from_secs(5)).unwrap();
        assert_ne!(again.id, prompt.id);
        p.answer(again.id, false).unwrap();
        assert!(t3.join().unwrap().starts_with("HTTP/1.1 403"));
    }

    #[test]
    fn unanswered_prompts_decline_after_the_timeout() {
        let o = origin();
        let (_p, fake, rx, port) = setup(vec![], Duration::from_millis(400));
        let r = connect_via(port, &format!("127.0.0.1:{o}"));
        assert!(r.starts_with("HTTP/1.1 403") && r.contains("declined"), "{r}");
        let prompt = rx.try_recv().unwrap();
        assert_eq!(fake.done.lock().unwrap().as_slice(), &[(prompt.id, None)]);
    }

    #[test]
    fn socks5_and_plain_http_go_through_the_rules() {
        let o = origin();
        let (_p, fake, _rx, port) = setup(vec![list(Kind::Allow, &["127.0.0.0/8", "::1"])], Duration::from_secs(5));
        // SOCKS5 with a domain name (remote DNS).
        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
        s.write_all(&[5, 1, 0]).unwrap();
        let mut b = [0u8; 2];
        s.read_exact(&mut b).unwrap();
        assert_eq!(b, [5, 0]);
        let name = b"localhost";
        let mut req = vec![5, 1, 0, 3, name.len() as u8];
        req.extend_from_slice(name);
        req.extend_from_slice(&o.to_be_bytes());
        s.write_all(&req).unwrap();
        let mut rep = [0u8; 10];
        s.read_exact(&mut rep).unwrap();
        assert_eq!(rep[1], 0, "SOCKS connect allowed");
        s.write_all(b"GET / HTTP/1.1\r\n\r\n").unwrap();
        let mut out = String::new();
        let _ = s.read_to_string(&mut out);
        assert!(out.ends_with("hello"));
        // Plain HTTP, then denied over SOCKS with reply 2.
        let mut h = TcpStream::connect(("127.0.0.1", port)).unwrap();
        write!(h, "GET http://127.0.0.1:{o}/x HTTP/1.1\r\nHost: 127.0.0.1\r\nProxy-Connection: keep-alive\r\n\r\n").unwrap();
        let mut out = String::new();
        let _ = h.read_to_string(&mut out);
        assert!(out.ends_with("hello"), "{out}");
        *fake.lists.lock().unwrap() = vec![list(Kind::Deny, &["127.0.0.0/8", "::1"])];
        let mut s = TcpStream::connect(("127.0.0.1", port)).unwrap();
        s.write_all(&[5, 1, 0]).unwrap();
        s.read_exact(&mut b).unwrap();
        let mut req = vec![5, 1, 0, 1, 127, 0, 0, 1];
        req.extend_from_slice(&o.to_be_bytes());
        s.write_all(&req).unwrap();
        s.read_exact(&mut rep).unwrap();
        assert_eq!(rep[1], 2, "not allowed by ruleset");
    }

    #[test]
    fn listeners_are_per_terminal_and_close_with_it() {
        let (p, _f, _rx, port) = setup(vec![], Duration::from_secs(1));
        assert_eq!(p.listen("k", "g2", "Renamed").unwrap(), port, "a resumed terminal keeps its port");
        let other = p.listen("k2", "g", "Other").unwrap();
        assert_ne!(other, port);
        p.set_pty("k2", 7);
        p.close_pty(7);
        std::thread::sleep(Duration::from_millis(200));
        let gone = TcpStream::connect(("127.0.0.1", other)).and_then(|mut s| {
            s.set_read_timeout(Some(Duration::from_millis(500)))?;
            s.write_all(b"CONNECT a:1 HTTP/1.1\r\n\r\n")?;
            let mut b = [0u8; 1];
            s.read(&mut b)
        });
        assert!(!matches!(gone, Ok(n) if n > 0), "closed listener answers nothing");
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn connections_of_this_user_are_served() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let _c = TcpStream::connect(l.local_addr().unwrap()).unwrap();
        let (s, _) = l.accept().unwrap();
        assert!(same_user(&s), "found in /proc/net/tcp with this uid");
    }

    #[test]
    fn host_port_forms() {
        assert_eq!(split_host_port("a.com:443", 1), Some(("a.com".into(), 443)));
        assert_eq!(split_host_port("[::1]:8080", 1), Some(("::1".into(), 8080)));
        assert_eq!(split_host_port("[::1]", 80), Some(("::1".into(), 80)));
        assert_eq!(split_host_port("a.com", 80), Some(("a.com".into(), 80)));
        assert_eq!(split_host_port("::1:80", 80), None);
        assert_eq!(split_host_port("a.com:x", 80), None);
    }
}
