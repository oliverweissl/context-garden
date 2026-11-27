//! Linux enforcement of the network security layer (#81, design section 3.1):
//! agent terminals start through this helper, which leaves the app's proxy as
//! their only way out.
//!
//! - **Namespace mode** (preferred): the agent runs in its own user and network
//!   namespace. It has only a loopback interface, so TCP, UDP, DNS, ICMP and raw
//!   sockets have nowhere to go. A small bridge process, forked before the
//!   namespace is made and so still on the host's network, accepts on
//!   127.0.0.1:<port> *inside* the namespace and forwards to the same port on the
//!   host: the terminal's proxy listener and the app's control endpoint. It is
//!   not dumpable, so the agent cannot take it over with ptrace. Ubuntu ≥ 23.10
//!   allows user namespaces only to programs with an AppArmor profile; the
//!   .deb/.rpm installs one for the app (packaging/linux/apparmor).
//! - **Landlock fallback** (only after the user agreed, see lib.rs): Landlock
//!   allows TCP connect only to those ports and no TCP bind; seccomp refuses
//!   every socket except Unix, netlink and plain TCP, refuses TCP Fast Open
//!   sends (a connect without connect()) and io_uring. Its hole: a TCP
//!   connection to *any* address on one of those port numbers is not stopped.
//!
//! Both are inherited by every child and cannot be lifted (no_new_privs).
//! Anything that fails to apply ends the helper with an error instead of
//! running the agent without the sandbox.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::os::fd::{AsRawFd, FromRawFd, RawFd};
use std::os::unix::process::CommandExt;

/// argv[1] of the helper.
pub const FLAG: &str = "--cg-sandbox";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mode {
    Namespace,
    Landlock,
}

impl Mode {
    fn name(self) -> &'static str {
        match self {
            Mode::Namespace => "namespace",
            Mode::Landlock => "landlock",
        }
    }
    fn parse(s: &str) -> Option<Mode> {
        match s {
            "namespace" => Some(Mode::Namespace),
            "landlock" => Some(Mode::Landlock),
            _ => None,
        }
    }
}

/// What this machine supports, found once by running the helper in probe mode.
#[derive(Clone, Debug)]
pub struct Support {
    /// Ok, or why namespace mode does not work here.
    pub namespace: Result<(), String>,
    /// Ok, or why the Landlock fallback does not work here.
    pub landlock: Result<(), String>,
}

impl Support {
    /// The mode to use: namespace when it works, else the fallback (the caller
    /// asks for the user's agreement), else why neither works.
    pub fn best(&self) -> Result<Mode, String> {
        match (&self.namespace, &self.landlock) {
            (Ok(()), _) => Ok(Mode::Namespace),
            (Err(_), Ok(())) => Ok(Mode::Landlock),
            (Err(n), Err(l)) => Err(format!("namespace mode: {n}; Landlock fallback: {l}")),
        }
    }
}

/// Test both modes in throwaway children of the app binary (the probe has to be
/// the same binary, since an AppArmor profile is tied to its path).
pub fn detect() -> Support {
    let probe = |mode: Mode| -> Result<(), String> {
        let exe = std::env::current_exe().map_err(|e| e.to_string())?;
        let out = std::process::Command::new(exe)
            .args([FLAG, mode.name(), "probe"])
            .stdin(std::process::Stdio::null())
            .output()
            .map_err(|e| e.to_string())?;
        if out.status.success() {
            Ok(())
        } else {
            let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
            Err(if err.is_empty() { format!("probe failed ({})", out.status) } else { err })
        }
    };
    Support { namespace: probe(Mode::Namespace), landlock: probe(Mode::Landlock) }
}

/// The argv prefix that starts a program in `mode`, reaching only `ports` on
/// the host's 127.0.0.1.
pub fn wrap(mode: Mode, ports: &[u16]) -> Result<Vec<String>, String> {
    let exe = std::env::current_exe().map_err(|e| format!("network sandbox: {e}"))?;
    let ports: Vec<String> = ports.iter().map(|p| p.to_string()).collect();
    Ok(vec![exe.to_string_lossy().into(), FLAG.into(), mode.name().into(), ports.join(","), "--".into()])
}

/// The helper's entry point, called first in main(): Some(exit code) when this
/// process was started as the helper (it does not return when it starts the
/// program), None for a normal app start.
pub fn helper() -> Option<i32> {
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) != Some(FLAG) {
        return None;
    }
    let fail = |e: String| {
        eprintln!("Context Garden: the network sandbox could not be set up, so the agent was not started: {e}");
        Some(126)
    };
    let Some(mode) = args.get(2).and_then(|m| Mode::parse(m)) else { return fail("unknown mode".into()) };
    if args.get(3).map(String::as_str) == Some("probe") {
        return Some(match probe(mode) {
            Ok(()) => 0,
            Err(e) => {
                eprintln!("{e}");
                1
            }
        });
    }
    let ports: Result<Vec<u16>, _> = args.get(3).map(|p| p.split(',').filter(|s| !s.is_empty()).map(str::parse).collect()).unwrap_or(Ok(vec![]));
    let Ok(ports) = ports else { return fail("bad port list".into()) };
    if args.get(4).map(String::as_str) != Some("--") || args.len() < 6 {
        return fail("no program to run".into());
    }
    let program = &args[5..];
    let applied = match mode {
        Mode::Namespace => enter_namespace(&ports),
        Mode::Landlock => landlock(&ports, true).and_then(|_| seccomp()),
    };
    if let Err(e) = applied {
        return fail(e);
    }
    let err = std::process::Command::new(&program[0]).args(&program[1..]).exec();
    fail(format!("{}: {err}", program[0]))
}

/// Apply a mode in this (throwaway) process and check that it holds.
fn probe(mode: Mode) -> Result<(), String> {
    match mode {
        Mode::Namespace => {
            unshare_net()?;
            // Only loopback is left: the outside is unreachable.
            let s = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("loopback in the namespace: {e}"))?;
            let port = s.local_addr().map_err(|e| e.to_string())?.port();
            TcpStream::connect(("127.0.0.1", port)).map_err(|e| format!("loopback in the namespace: {e}"))?;
            Ok(())
        }
        Mode::Landlock => {
            landlock(&[], true)?;
            seccomp()?;
            // A UDP socket must now be refused.
            match std::net::UdpSocket::bind("127.0.0.1:0") {
                Ok(_) => Err("seccomp did not take effect".into()),
                Err(_) => Ok(()),
            }
        }
    }
}

fn os_err(what: &str) -> String {
    format!("{what}: {}", std::io::Error::last_os_error())
}

fn write_file(path: &str, data: &str) -> Result<(), String> {
    std::fs::write(path, data).map_err(|e| format!("{path}: {e}"))
}

/// New user + network namespace with loopback up; uid/gid stay the same.
fn unshare_net() -> Result<(), String> {
    unshare_inner().map_err(|e| {
        // Ubuntu ≥ 23.10 lets the unshare succeed but takes every capability in
        // the namespace away, so it fails one step later.
        if std::fs::read_to_string("/proc/sys/kernel/apparmor_restrict_unprivileged_userns").is_ok_and(|v| v.trim() == "1") {
            format!("{e} (AppArmor allows user namespaces only to programs with a profile; the .deb and .rpm install one for the app)")
        } else {
            e
        }
    })
}

fn unshare_inner() -> Result<(), String> {
    let (uid, gid) = unsafe { (libc::geteuid(), libc::getegid()) };
    if unsafe { libc::unshare(libc::CLONE_NEWUSER | libc::CLONE_NEWNET) } != 0 {
        return Err(os_err("user namespace"));
    }
    write_file("/proc/self/setgroups", "deny")?;
    write_file("/proc/self/uid_map", &format!("{uid} {uid} 1"))?;
    write_file("/proc/self/gid_map", &format!("{gid} {gid} 1"))?;
    // Bring lo up (we hold CAP_NET_ADMIN in the new namespace until exec).
    unsafe {
        let fd = libc::socket(libc::AF_INET, libc::SOCK_DGRAM | libc::SOCK_CLOEXEC, 0);
        if fd < 0 {
            return Err(os_err("socket"));
        }
        let mut req: libc::ifreq = std::mem::zeroed();
        for (i, b) in b"lo\0".iter().enumerate() {
            req.ifr_name[i] = *b as libc::c_char;
        }
        let ok = libc::ioctl(fd, libc::SIOCGIFFLAGS as _, &mut req) == 0 && {
            req.ifr_ifru.ifru_flags |= libc::IFF_UP as libc::c_short;
            libc::ioctl(fd, libc::SIOCSIFFLAGS as _, &mut req) == 0
        };
        let err = os_err("loopback up");
        libc::close(fd);
        if !ok {
            return Err(err);
        }
    }
    Ok(())
}

/// Namespace mode: fork the bridge (it stays on the host network), then move
/// into the namespace, listen on the ports there and hand the listeners over.
fn enter_namespace(ports: &[u16]) -> Result<(), String> {
    let mut pair = [0 as RawFd; 2];
    if unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_STREAM | libc::SOCK_CLOEXEC, 0, pair.as_mut_ptr()) } != 0 {
        return Err(os_err("socketpair"));
    }
    let parent = unsafe { libc::getpid() };
    match unsafe { libc::fork() } {
        -1 => return Err(os_err("fork")),
        0 => {
            unsafe { libc::close(pair[0]) };
            bridge(pair[1], parent, ports.len());
        }
        _ => unsafe {
            libc::close(pair[1]);
        },
    }
    unshare_net()?;
    ptrace_domain()?;
    let mut fds = Vec::new();
    for p in ports {
        let l = TcpListener::bind(("127.0.0.1", *p)).map_err(|e| format!("listen on 127.0.0.1:{p} in the namespace: {e}"))?;
        fds.push(l);
    }
    let raw: Vec<RawFd> = fds.iter().map(|l| l.as_raw_fd()).collect();
    send_fds(pair[0], &raw)?;
    // The bridge has the listeners now; wait for it to confirm, so the program
    // never starts before its way out exists.
    let mut ok = [0u8; 1];
    let mut s = unsafe { std::os::unix::net::UnixStream::from_raw_fd(pair[0]) };
    s.read_exact(&mut ok).map_err(|e| format!("bridge: {e}"))?;
    drop(fds);
    Ok(())
}

/// The bridge process: never returns. Ends when the program (its parent) ends.
fn bridge(sock: RawFd, parent: libc::pid_t, n: usize) -> ! {
    unsafe {
        libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL);
        if libc::getppid() != parent {
            libc::_exit(0);
        }
        // Not ptrace-able or readable through /proc by the program, and out of
        // the terminal's session so Ctrl-C and hangups do not reach it.
        libc::prctl(libc::PR_SET_DUMPABLE, 0);
        libc::setsid();
        let null = libc::open(c"/dev/null".as_ptr(), libc::O_RDWR);
        for fd in 0..3 {
            libc::dup2(null, fd);
        }
    }
    // Keep nothing else of the terminal's open (the pty, pipes).
    let open: Vec<RawFd> = std::fs::read_dir("/proc/self/fd").map(|d| d.flatten().filter_map(|e| e.file_name().to_str()?.parse().ok()).collect()).unwrap_or_default();
    for fd in open.into_iter().filter(|fd| *fd > 2 && *fd != sock) {
        unsafe { libc::close(fd) };
    }
    let Ok(fds) = recv_fds(sock, n) else { unsafe { libc::_exit(1) } };
    let mut s = unsafe { std::os::unix::net::UnixStream::from_raw_fd(sock) };
    let mut threads = Vec::new();
    for fd in fds {
        let listener = unsafe { TcpListener::from_raw_fd(fd) };
        let Ok(port) = listener.local_addr().map(|a| a.port()) else { continue };
        threads.push(std::thread::spawn(move || {
            for inside in listener.incoming().flatten() {
                std::thread::spawn(move || {
                    if let Ok(outside) = TcpStream::connect(("127.0.0.1", port)) {
                        splice(inside, outside);
                    }
                });
            }
        }));
    }
    let _ = s.write_all(b"1");
    for t in threads {
        let _ = t.join();
    }
    unsafe { libc::_exit(0) }
}

/// Copy both ways until either side closes.
fn splice(a: TcpStream, b: TcpStream) {
    let (Ok(mut a2), Ok(mut b2)) = (a.try_clone(), b.try_clone()) else { return };
    let (mut a, mut b) = (a, b);
    let t = std::thread::spawn(move || {
        let _ = std::io::copy(&mut a2, &mut b2);
        let _ = b2.shutdown(std::net::Shutdown::Write);
    });
    let _ = std::io::copy(&mut b, &mut a);
    let _ = a.shutdown(std::net::Shutdown::Write);
    let _ = t.join();
}

fn send_fds(sock: RawFd, fds: &[RawFd]) -> Result<(), String> {
    if fds.is_empty() {
        return if unsafe { libc::write(sock, b"0".as_ptr().cast(), 1) } == 1 { Ok(()) } else { Err(os_err("bridge")) };
    }
    unsafe {
        let size = std::mem::size_of_val(fds) as u32;
        let mut cbuf = vec![0u8; libc::CMSG_SPACE(size) as usize];
        let mut byte = [0u8; 1];
        let mut iov = libc::iovec { iov_base: byte.as_mut_ptr().cast(), iov_len: 1 };
        let mut msg: libc::msghdr = std::mem::zeroed();
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = cbuf.as_mut_ptr().cast();
        msg.msg_controllen = cbuf.len() as _;
        let c = libc::CMSG_FIRSTHDR(&msg);
        (*c).cmsg_level = libc::SOL_SOCKET;
        (*c).cmsg_type = libc::SCM_RIGHTS;
        (*c).cmsg_len = libc::CMSG_LEN(size) as _;
        std::ptr::copy_nonoverlapping(fds.as_ptr().cast::<u8>(), libc::CMSG_DATA(c), size as usize);
        if libc::sendmsg(sock, &msg, 0) != 1 {
            return Err(os_err("hand over listeners"));
        }
    }
    Ok(())
}

fn recv_fds(sock: RawFd, n: usize) -> Result<Vec<RawFd>, String> {
    unsafe {
        let size = (n * std::mem::size_of::<RawFd>()) as u32;
        let mut cbuf = vec![0u8; libc::CMSG_SPACE(size.max(4)) as usize];
        let mut byte = [0u8; 1];
        let mut iov = libc::iovec { iov_base: byte.as_mut_ptr().cast(), iov_len: 1 };
        let mut msg: libc::msghdr = std::mem::zeroed();
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = cbuf.as_mut_ptr().cast();
        msg.msg_controllen = cbuf.len() as _;
        if libc::recvmsg(sock, &mut msg, libc::MSG_CMSG_CLOEXEC) != 1 {
            return Err(os_err("receive listeners"));
        }
        let c = libc::CMSG_FIRSTHDR(&msg);
        if c.is_null() || n == 0 {
            return Ok(vec![]);
        }
        let mut out = vec![0 as RawFd; n];
        std::ptr::copy_nonoverlapping(libc::CMSG_DATA(c), out.as_mut_ptr().cast::<u8>(), size as usize);
        Ok(out)
    }
}

// Landlock (linux/landlock.h). The syscall numbers are the same on every architecture.
const SYS_LANDLOCK_CREATE_RULESET: libc::c_long = 444;
const SYS_LANDLOCK_ADD_RULE: libc::c_long = 445;
const SYS_LANDLOCK_RESTRICT_SELF: libc::c_long = 446;
const LANDLOCK_CREATE_RULESET_VERSION: u32 = 1;
const LANDLOCK_RULE_NET_PORT: u32 = 2;
const LANDLOCK_ACCESS_NET_BIND_TCP: u64 = 1;
const LANDLOCK_ACCESS_NET_CONNECT_TCP: u64 = 2;

#[repr(C)]
struct RulesetAttr {
    handled_access_fs: u64,
    handled_access_net: u64,
}

#[repr(C, packed)]
struct NetPortAttr {
    allowed_access: u64,
    port: u64,
}

/// TCP connect only to `ports`, and (with `no_bind`) no TCP bind. Needs Landlock ABI ≥ 4 (Linux 6.7).
fn landlock(ports: &[u16], no_bind: bool) -> Result<(), String> {
    unsafe {
        let abi = libc::syscall(SYS_LANDLOCK_CREATE_RULESET, std::ptr::null::<u8>(), 0usize, LANDLOCK_CREATE_RULESET_VERSION);
        if abi < 0 {
            return Err(os_err("Landlock is not available"));
        }
        if abi < 4 {
            return Err(format!("Landlock ABI {abi} has no network rules (needs 4, Linux 6.7)"));
        }
        let handled = LANDLOCK_ACCESS_NET_CONNECT_TCP | if no_bind { LANDLOCK_ACCESS_NET_BIND_TCP } else { 0 };
        let attr = RulesetAttr { handled_access_fs: 0, handled_access_net: handled };
        let fd = libc::syscall(SYS_LANDLOCK_CREATE_RULESET, &attr, std::mem::size_of::<RulesetAttr>(), 0u32);
        if fd < 0 {
            return Err(os_err("Landlock ruleset"));
        }
        let fd = fd as RawFd;
        for p in ports {
            let rule = NetPortAttr { allowed_access: LANDLOCK_ACCESS_NET_CONNECT_TCP, port: *p as u64 };
            if libc::syscall(SYS_LANDLOCK_ADD_RULE, fd, LANDLOCK_RULE_NET_PORT, &rule, 0u32) != 0 {
                libc::close(fd);
                return Err(os_err("Landlock rule"));
            }
        }
        if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
            libc::close(fd);
            return Err(os_err("no_new_privs"));
        }
        let r = libc::syscall(SYS_LANDLOCK_RESTRICT_SELF, fd, 0u32);
        libc::close(fd);
        if r != 0 {
            return Err(os_err("Landlock restrict"));
        }
    }
    Ok(())
}

/// Namespace mode: a Landlock domain that restricts nothing a user could do
/// anyway (making block devices), for Landlock's ptrace rule: a process in a
/// domain cannot trace or read the memory of processes outside it, such as the
/// app or the bridge, even where Yama allows tracing (ptrace_scope 0). Skipped
/// where the kernel has no Landlock.
fn ptrace_domain() -> Result<(), String> {
    const LANDLOCK_ACCESS_FS_MAKE_BLOCK: u64 = 1 << 11;
    unsafe {
        if libc::syscall(SYS_LANDLOCK_CREATE_RULESET, std::ptr::null::<u8>(), 0usize, LANDLOCK_CREATE_RULESET_VERSION) < 0 {
            return Ok(());
        }
        let attr = RulesetAttr { handled_access_fs: LANDLOCK_ACCESS_FS_MAKE_BLOCK, handled_access_net: 0 };
        let fd = libc::syscall(SYS_LANDLOCK_CREATE_RULESET, &attr, 8usize, 0u32);
        if fd < 0 {
            return Err(os_err("Landlock ruleset"));
        }
        let r = libc::syscall(SYS_LANDLOCK_RESTRICT_SELF, fd as RawFd, 0u32);
        libc::close(fd as RawFd);
        if r != 0 {
            return Err(os_err("Landlock restrict"));
        }
    }
    Ok(())
}

#[cfg(target_arch = "x86_64")]
const AUDIT_ARCH: u32 = 0xC000_003E;
#[cfg(target_arch = "aarch64")]
const AUDIT_ARCH: u32 = 0xC000_00B7;

/// A classic BPF program with forward jumps to labels.
struct Bpf {
    code: Vec<(u16, u32, Option<&'static str>, Option<&'static str>)>,
    labels: Vec<(&'static str, usize)>,
}

impl Bpf {
    fn op(&mut self, code: u16, k: u32) {
        self.code.push((code, k, None, None));
    }
    fn jump(&mut self, code: u16, k: u32, t: Option<&'static str>, f: Option<&'static str>) {
        self.code.push((code, k, t, f));
    }
    fn label(&mut self, name: &'static str) {
        self.labels.push((name, self.code.len()));
    }
    fn build(&self) -> Result<Vec<libc::sock_filter>, String> {
        let at = |i: usize, l: Option<&str>| -> Result<u8, String> {
            let Some(l) = l else { return Ok(0) };
            let target = self.labels.iter().find(|(n, _)| *n == l).ok_or(format!("bpf label {l}"))?.1;
            u8::try_from(target - i - 1).map_err(|_| "bpf jump too far".to_string())
        };
        self.code
            .iter()
            .enumerate()
            .map(|(i, (code, k, t, f))| Ok(libc::sock_filter { code: *code, jt: at(i, *t)?, jf: at(i, *f)?, k: *k }))
            .collect()
    }
}

const LD_W_ABS: u16 = 0x20; // BPF_LD | BPF_W | BPF_ABS
const JEQ: u16 = 0x15; // BPF_JMP | BPF_JEQ | BPF_K
const JGE: u16 = 0x35; // BPF_JMP | BPF_JGE | BPF_K
const JSET: u16 = 0x45; // BPF_JMP | BPF_JSET | BPF_K
const AND: u16 = 0x54; // BPF_ALU | BPF_AND | BPF_K
const RET: u16 = 0x06; // BPF_RET | BPF_K
const ALLOW: u32 = 0x7fff_0000;
fn errno(e: i32) -> u32 {
    0x0005_0000 | (e as u32 & 0xffff)
}
const ARG0: u32 = 16; // seccomp_data.args[i] (low 32 bits, little-endian)
const fn arg(i: u32) -> u32 {
    ARG0 + 8 * i
}
const MSG_FASTOPEN: u32 = 0x2000_0000;

/// The seccomp filter of the Landlock fallback (see the module comment).
fn filter() -> Result<Vec<libc::sock_filter>, String> {
    let mut b = Bpf { code: vec![], labels: vec![] };
    b.op(LD_W_ABS, 4); // arch
    b.jump(JEQ, AUDIT_ARCH, Some("native"), None);
    b.op(RET, errno(libc::ENOSYS)); // other ABIs (i386 on x86_64) get nothing
    b.label("native");
    b.op(LD_W_ABS, 0); // syscall number
    #[cfg(target_arch = "x86_64")]
    {
        b.jump(JGE, 0x4000_0000, Some("enosys"), None); // x32 ABI
    }
    b.jump(JEQ, libc::SYS_socket as u32, Some("socket"), None);
    b.jump(JEQ, libc::SYS_sendto as u32, Some("flags3"), None);
    b.jump(JEQ, libc::SYS_sendmmsg as u32, Some("flags3"), None);
    b.jump(JEQ, libc::SYS_sendmsg as u32, Some("flags2"), None);
    b.jump(JEQ, libc::SYS_io_uring_setup as u32, Some("enosys"), None);
    b.jump(JEQ, libc::SYS_io_uring_enter as u32, Some("enosys"), None);
    b.jump(JEQ, libc::SYS_io_uring_register as u32, Some("enosys"), None);
    b.op(RET, ALLOW);
    b.label("socket");
    b.op(LD_W_ABS, arg(0)); // domain
    b.jump(JEQ, libc::AF_UNIX as u32, Some("allow"), None);
    b.jump(JEQ, libc::AF_NETLINK as u32, Some("allow"), None);
    b.jump(JEQ, libc::AF_INET as u32, Some("inet"), None);
    b.jump(JEQ, libc::AF_INET6 as u32, Some("inet"), None);
    b.op(RET, errno(libc::EAFNOSUPPORT));
    b.label("inet");
    b.op(LD_W_ABS, arg(1)); // type, without SOCK_NONBLOCK / SOCK_CLOEXEC
    b.op(AND, 0xf);
    b.jump(JEQ, libc::SOCK_STREAM as u32, None, Some("eacces"));
    b.op(LD_W_ABS, arg(2)); // protocol: TCP only (no SCTP, MPTCP)
    b.jump(JEQ, 0, Some("allow"), None);
    b.jump(JEQ, libc::IPPROTO_TCP as u32, Some("allow"), Some("eacces"));
    b.label("flags3");
    b.op(LD_W_ABS, arg(3));
    b.jump(JSET, MSG_FASTOPEN, Some("eacces"), Some("allow"));
    b.label("flags2");
    b.op(LD_W_ABS, arg(2));
    b.jump(JSET, MSG_FASTOPEN, Some("eacces"), Some("allow"));
    b.label("allow");
    b.op(RET, ALLOW);
    b.label("eacces");
    b.op(RET, errno(libc::EACCES));
    b.label("enosys");
    b.op(RET, errno(libc::ENOSYS));
    b.build()
}

fn seccomp() -> Result<(), String> {
    let prog = filter()?;
    let fprog = libc::sock_fprog { len: prog.len() as u16, filter: prog.as_ptr() as *mut _ };
    unsafe {
        if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
            return Err(os_err("no_new_privs"));
        }
        if libc::prctl(libc::PR_SET_SECCOMP, libc::SECCOMP_MODE_FILTER, &fprog as *const libc::sock_fprog) != 0 {
            return Err(os_err("seccomp"));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_filter_assembles() {
        let f = filter().unwrap();
        assert!(f.len() > 20);
        assert_eq!(f.last().unwrap().k, errno(libc::ENOSYS));
    }

    #[test]
    fn namespace_first_then_the_fallback_then_why_not() {
        let s = |n: bool, l: bool| Support {
            namespace: if n { Ok(()) } else { Err("no userns".into()) },
            landlock: if l { Ok(()) } else { Err("no landlock".into()) },
        };
        assert_eq!(s(true, true).best(), Ok(Mode::Namespace));
        assert_eq!(s(true, false).best(), Ok(Mode::Namespace));
        assert_eq!(s(false, true).best(), Ok(Mode::Landlock));
        let e = s(false, false).best().unwrap_err();
        assert!(e.contains("no userns") && e.contains("no landlock"), "{e}");
    }

    #[test]
    fn wrap_names_mode_and_ports() {
        let w = wrap(Mode::Landlock, &[123, 456]).unwrap();
        assert_eq!(&w[1..], &[FLAG, "landlock", "123,456", "--"]);
    }
}
