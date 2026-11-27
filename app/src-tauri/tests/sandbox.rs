//! The Linux network sandbox (#81), run through the real app binary in helper
//! mode. A mode this machine does not support is skipped with a note (namespace
//! mode needs unprivileged user namespaces, which Ubuntu ≥ 23.10 grants only
//! with the AppArmor profile the .deb/.rpm installs; the fallback needs Landlock
//! ABI ≥ 4, Linux 6.7).
#![cfg(target_os = "linux")]

use std::io::{Read, Write};
use std::net::TcpListener;
use std::process::Command;

/// The binary under test; CG_SANDBOX_BIN points at another copy, e.g. one an
/// AppArmor profile covers, to run namespace mode on Ubuntu ≥ 23.10.
fn bin() -> String {
    std::env::var("CG_SANDBOX_BIN").unwrap_or_else(|_| env!("CARGO_BIN_EXE_context-garden-app").into())
}

fn supported(mode: &str) -> bool {
    let out = Command::new(bin()).args(["--cg-sandbox", mode, "probe"]).output().unwrap();
    if !out.status.success() {
        eprintln!("skipping {mode} mode: {}", String::from_utf8_lossy(&out.stderr).trim());
    }
    out.status.success()
}

/// A host listener that answers "hi" to every connection.
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

/// Python run inside the sandbox: each probe prints "name ok" or "name <errno name>".
const PROBES: &str = r#"
import socket, sys, errno, ctypes
allowed, other = int(sys.argv[1]), int(sys.argv[2])
def t(name, f):
    try:
        f(); print(name, "ok")
    except OSError as e:
        print(name, errno.errorcode.get(e.errno, str(e)))
def tcp(host, port):
    s = socket.create_connection((host, port), timeout=3); data = s.recv(2); s.close()
    if data != b"hi": raise OSError(errno.EPROTO, "no greeting")
def udp():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(2); s.sendto(b"x", ("1.1.1.1", 53)); s.recvfrom(10)
def bind():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(1)
def uring():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(425, 4, ctypes.create_string_buffer(120)) < 0: raise OSError(ctypes.get_errno(), "io_uring")
t("allowed", lambda: tcp("127.0.0.1", allowed))
t("other", lambda: tcp("127.0.0.1", other))
t("remote", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
t("udp", udp)
t("raw", lambda: socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP))
t("packet", lambda: socket.socket(socket.AF_PACKET, socket.SOCK_RAW))
t("sctp", lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM, 132))
t("bind", bind)
t("io_uring", uring)
t("unix", lambda: socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
"#;

fn run(mode: &str) -> String {
    let (allowed, other) = (greeter(), greeter());
    let out = Command::new(bin())
        .args(["--cg-sandbox", mode, &allowed.to_string(), "--", "python3", "-c", PROBES, &allowed.to_string(), &other.to_string()])
        .output()
        .unwrap();
    assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
    String::from_utf8_lossy(&out.stdout).to_string()
}

fn result<'a>(out: &'a str, name: &str) -> &'a str {
    out.lines().find_map(|l| l.strip_prefix(&format!("{name} "))).unwrap_or_else(|| panic!("{name} missing in {out}"))
}

#[test]
fn landlock_fallback_leaves_only_the_allowed_ports() {
    if !supported("landlock") {
        return;
    }
    let out = run("landlock");
    assert_eq!(result(&out, "allowed"), "ok", "{out}");
    for name in ["other", "remote", "udp", "raw", "sctp", "bind"] {
        assert_eq!(result(&out, name), "EACCES", "{name}: {out}");
    }
    assert_eq!(result(&out, "packet"), "EAFNOSUPPORT", "{out}");
    assert_eq!(result(&out, "io_uring"), "ENOSYS", "{out}");
    assert_eq!(result(&out, "unix"), "ok", "{out}");
}

#[test]
fn namespace_mode_reaches_only_the_forwarded_ports() {
    if !supported("namespace") {
        return;
    }
    let out = run("namespace");
    assert_eq!(result(&out, "allowed"), "ok", "the bridge forwards the allowed port: {out}");
    assert_eq!(result(&out, "other"), "ECONNREFUSED", "other host ports do not exist inside: {out}");
    assert_eq!(result(&out, "remote"), "ENETUNREACH", "{out}");
    assert_ne!(result(&out, "udp"), "ok", "{out}");
    assert_eq!(result(&out, "bind"), "ok", "the namespace has its own loopback: {out}");
}

#[test]
fn the_program_never_runs_without_its_sandbox() {
    // Bad arguments, and a mode this machine lacks, end the helper before the program runs.
    let marker = std::env::temp_dir().join(format!("cg-sandbox-ran-{}", std::process::id()));
    let touch = format!("touch {}", marker.display());
    for mode in ["bogus", "namespace", "landlock"] {
        if mode != "bogus" && supported(mode) {
            continue;
        }
        let out = Command::new(bin()).args(["--cg-sandbox", mode, "1", "--", "sh", "-c", &touch]).output().unwrap();
        assert_eq!(out.status.code(), Some(126), "{mode}");
        assert!(String::from_utf8_lossy(&out.stderr).contains("the agent was not started"), "{mode}");
        assert!(!marker.exists(), "{mode}: the program ran");
    }
}

#[test]
fn exit_codes_and_output_pass_through() {
    let mode = if supported("namespace") { "namespace" } else if supported("landlock") { "landlock" } else { return };
    let mut child = Command::new(bin())
        .args(["--cg-sandbox", mode, "", "--", "sh", "-c", "read x; echo got $x; exit 7"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(b"line\n").unwrap();
    let mut out = String::new();
    child.stdout.take().unwrap().read_to_string(&mut out).unwrap();
    assert_eq!(out.trim(), "got line");
    assert_eq!(child.wait().unwrap().code(), Some(7));
}
