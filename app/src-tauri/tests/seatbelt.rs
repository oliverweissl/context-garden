//! The macOS network sandbox (#81) for real: programs run under the app's
//! Seatbelt profile reach only the allowed localhost ports. Runs on the macOS CI
//! runner.
#![cfg(target_os = "macos")]

#[path = "../src/seatbelt.rs"]
#[allow(dead_code)]
mod seatbelt;

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::process::Command;

/// A listener answering "hi" to every connection.
fn greeter() -> u16 {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for mut s in l.incoming().flatten() {
            let _ = s.write_all(b"hi");
        }
    });
    port
}

/// A minimal HTTP CONNECT proxy (what the app's proxy does after its rules allow).
fn connect_proxy() -> u16 {
    let l = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = l.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for client in l.incoming().flatten() {
            std::thread::spawn(move || {
                let mut client = client;
                let mut head = Vec::new();
                let mut b = [0u8; 1];
                while !head.ends_with(b"\r\n\r\n") && client.read(&mut b).unwrap_or(0) == 1 {
                    head.push(b[0]);
                }
                let line = String::from_utf8_lossy(&head).lines().next().unwrap_or("").to_string();
                let target = line.split(' ').nth(1).unwrap_or("").to_string();
                let Ok(up) = TcpStream::connect(&target) else { return };
                let _ = client.write_all(b"HTTP/1.1 200 Connection Established\r\n\r\n");
                let (mut c2, mut u2) = (client.try_clone().unwrap(), up.try_clone().unwrap());
                let mut up = up;
                std::thread::spawn(move || {
                    let _ = std::io::copy(&mut c2, &mut u2);
                });
                let _ = std::io::copy(&mut up, &mut client);
            });
        }
    });
    port
}

const PROBES: &str = r#"
import socket, sys, errno
allowed, other = int(sys.argv[1]), int(sys.argv[2])
def t(name, f):
    try:
        f(); print(name, "ok")
    except socket.gaierror:
        print(name, "NORESOLVE")
    except OSError as e:
        print(name, errno.errorcode.get(e.errno, str(e)))
def tcp(host, port):
    s = socket.create_connection((host, port), timeout=3); data = s.recv(2); s.close()
    if data != b"hi": raise OSError(errno.EPROTO, "no greeting")
def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2); s.sendto(b"x", ("1.1.1.1", 53)); s.recvfrom(10)
def bind():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
t("allowed", lambda: tcp("127.0.0.1", allowed))
t("other", lambda: tcp("127.0.0.1", other))
t("remote", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
t("remote_on_allowed_port", lambda: socket.create_connection(("1.1.1.1", allowed), timeout=3))
t("udp", udp)
t("resolve", lambda: socket.getaddrinfo("example.com", 443))
t("bind", bind)
t("unix", lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
"#;

fn sandboxed(ports: &[u16], program: &[&str]) -> std::process::Output {
    let wrap = seatbelt::wrap(ports);
    Command::new(&wrap[0]).args(&wrap[1..]).args(program).output().unwrap()
}

fn result<'a>(out: &'a str, name: &str) -> &'a str {
    out.lines().find_map(|l| l.strip_prefix(&format!("{name} "))).unwrap_or_else(|| panic!("{name} missing in {out}"))
}

#[test]
fn the_probe_works_on_this_mac() {
    seatbelt::probe().unwrap();
}

#[test]
fn only_the_allowed_localhost_ports_are_reachable() {
    let (allowed, other) = (greeter(), greeter());
    let a = allowed.to_string();
    let o = other.to_string();
    let out = sandboxed(&[allowed], &["python3", "-c", PROBES, &a, &o]);
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    assert_eq!(result(&text, "allowed"), "ok", "{text}");
    for name in ["other", "remote", "remote_on_allowed_port", "udp", "bind"] {
        assert_eq!(result(&text, name), "EPERM", "{name}: {text}");
    }
    assert_eq!(result(&text, "resolve"), "NORESOLVE", "{text}");
    assert_eq!(result(&text, "unix"), "ok", "{text}");
}

#[test]
fn curl_reaches_the_network_only_through_the_proxy_and_children_inherit_it() {
    let proxy = connect_proxy();
    let via = sandboxed(&[proxy], &["/usr/bin/env", &format!("HTTPS_PROXY=http://127.0.0.1:{proxy}"), "curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}", "https://example.com"]);
    assert_eq!(String::from_utf8_lossy(&via.stdout), "200", "{}", String::from_utf8_lossy(&via.stderr));
    let direct = sandboxed(&[proxy], &["/bin/bash", "-c", "bash -c 'curl -sS -m 8 -o /dev/null https://example.com'"]);
    assert!(!direct.status.success(), "a grandchild ignoring the proxy is blocked");
}

#[test]
fn exit_codes_pass_through() {
    let out = sandboxed(&[], &["/bin/sh", "-c", "echo out; exit 7"]);
    assert_eq!(out.status.code(), Some(7));
    assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "out");
}

#[test]
fn a_nested_sandbox_cannot_start() {
    // Documented limit: programs that start their own sandbox-exec fail inside.
    let out = sandboxed(&[], &[seatbelt::SANDBOX_EXEC, "-p", "(version 1)(allow default)", "/usr/bin/true"]);
    assert!(!out.status.success());
    assert!(String::from_utf8_lossy(&out.stderr).contains("Operation not permitted"), "{}", String::from_utf8_lossy(&out.stderr));
}
