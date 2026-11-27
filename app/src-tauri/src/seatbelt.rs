//! macOS enforcement of the network security layer (#81, design section 3.2):
//! agent terminals start under `sandbox-exec` with a Seatbelt profile that
//! denies all network access except TCP to the terminal's proxy port and the
//! app's control port on localhost. The kernel applies it to every child
//! process, and it cannot be lifted from inside.
//!
//! - IP: outbound only to `localhost:<port>` for those ports (no address hole:
//!   the filter matches the address too), no bind and no inbound, so no TCP,
//!   UDP or ICMP to anywhere else, and no local servers.
//! - Unix sockets stay allowed (local IPC), except mDNSResponder's, so names
//!   are not resolved (or tunnelled out over DNS) outside the proxy.
//! - Sandboxes cannot be nested: a program inside that starts its own
//!   `sandbox-exec` fails with "Operation not permitted".
//!
//! The module is plain std, so the tests on every OS check the profile text and
//! `tests/seatbelt.rs` runs it for real on macOS.

/// The macOS sandbox launcher; part of every macOS install.
pub const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";

/// The Seatbelt profile that leaves only `ports` on localhost reachable.
pub fn profile(ports: &[u16]) -> String {
    let mut p = String::from(
        "(version 1)\n\
         (allow default)\n\
         (deny network-outbound)\n\
         (deny network-bind (local ip))\n\
         (deny network-inbound (local ip))\n\
         (allow network-outbound (remote unix-socket))\n\
         (deny network-outbound (remote unix-socket (path-literal \"/private/var/run/mDNSResponder\")))\n",
    );
    for port in ports {
        p.push_str(&format!("(allow network-outbound (remote ip \"localhost:{port}\"))\n"));
    }
    p
}

/// The argv prefix that starts a program under the profile.
pub fn wrap(ports: &[u16]) -> Vec<String> {
    vec![SANDBOX_EXEC.into(), "-p".into(), profile(ports)]
}

/// Ok when `sandbox-exec` applies the profile on this Mac; else why not.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
pub fn probe() -> Result<(), String> {
    let out = std::process::Command::new(SANDBOX_EXEC)
        .args(["-p", &profile(&[]), "/usr/bin/true"])
        .stdin(std::process::Stdio::null())
        .output()
        .map_err(|e| format!("{SANDBOX_EXEC}: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
        Err(if err.is_empty() { format!("{SANDBOX_EXEC} failed ({})", out.status) } else { err })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_profile_denies_the_network_and_allows_only_the_given_ports() {
        let p = profile(&[4100, 4200]);
        assert!(p.starts_with("(version 1)\n(allow default)\n(deny network-outbound)\n"), "{p}");
        assert!(p.contains("(deny network-bind (local ip))") && p.contains("(deny network-inbound (local ip))"));
        assert!(p.contains("(remote unix-socket (path-literal \"/private/var/run/mDNSResponder\"))"));
        assert!(p.contains("(allow network-outbound (remote ip \"localhost:4100\"))\n(allow network-outbound (remote ip \"localhost:4200\"))\n"));
        assert_eq!(p.matches("(allow network-outbound (remote ip").count(), 2);
        // The allows come after the denies: in Seatbelt the last matching rule wins.
        assert!(p.find("localhost:4100").unwrap() > p.find("(deny network-outbound)").unwrap());
        let w = wrap(&[1]);
        assert_eq!((w[0].as_str(), w[1].as_str()), (SANDBOX_EXEC, "-p"));
    }
}
