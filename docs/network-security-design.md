# Network security layer: design (#81)

Status: **proposal for review**. Nothing is implemented yet. Implementation starts after this design is confirmed.

## 1. Requirements (from the owner, not reinterpreted)

1. Everything agents run is inside a sandbox, and **all** network traffic goes through the app's security layer. Where the OS offers enforcement that programs cannot bypass, use it. A proxy that programs can ignore is not enough.
2. The feature is **off by default** and can be switched on and off.
3. Rules match **hostnames, domains and IP ranges (CIDR)**. No URL/path matching, no preset groups.
4. Users define **named lists** (allow lists / deny lists) that can be combined. Rules apply **per open garden**.
5. A connection the open garden's rules do not cover opens a **popup with four choices**: accept once / accept and add to allow list / decline once / decline and add to deny list.
6. Lists are editable in Settings.
7. Full feature, delivered as several reviewable PRs.

## 2. Architecture

```
 agent terminal (PTY)                                   ContextGarden app (outside the sandbox)
 ┌───────────────────────────────────────────┐          ┌───────────────────────────────────────────┐
 │ OS sandbox: no direct network at all      │          │ netproxy: one listener per terminal        │
 │   claude / codex / shell / git / npm ...  │  only    │   HTTP CONNECT + plain HTTP + SOCKS5h      │
 │   HTTPS_PROXY / HTTP_PROXY / ALL_PROXY ───┼─────────▶│   -> rules of the terminal's garden         │
 │   = http://127.0.0.1:<port>               │  path    │   -> allow: dial out / deny: refuse          │
 └───────────────────────────────────────────┘          │   -> unknown: hold, ask the window (popup)   │
                                                        └───────────────────────────────────────────┘
```

- **One proxy listener per agent terminal.** The listener knows which terminal, and therefore which garden, a connection comes from, so no credentials are needed in the proxy URL. Each listener is a Unix socket where the OS sandbox can pass one in (Linux netns, see 3.1), otherwise a loopback TCP port.
- **The OS sandbox is the enforcement and the proxy is the decision point.** Inside the sandbox, the only route out is that terminal's proxy. A program that ignores the proxy variables gets no network: it fails closed.
- The proxy resolves DNS itself (CONNECT by hostname, SOCKS5 with remote DNS). The sandbox needs no DNS, and every decision sees the hostname and the resolved addresses.
- With the feature **off** (the default), nothing changes: no sandbox, no proxy variables, today's behaviour.
- Switching it on or off, or changing a garden's lists, applies to agent terminals **started or resumed afterwards**. A running terminal cannot be moved into or out of a sandbox. The UI marks such terminals, like the existing "older skills" marker.
- Scope is network only. The sandbox gives the agent the same filesystem view as today; filesystem restriction is not part of #81.

## 3. Per-OS enforcement

What I could test: a Linux machine (Ubuntu 24.04.4, kernel 7.0.0, AppArmor, no root). macOS and Windows were **not available**, so those sections are based on documentation and how other tools (Codex, Claude Code's sandbox) do it. Each one gets a spike at the start of its PR (section 7) before code is written.

### 3.1 Linux

| Option | How | Tested here | Can programs bypass it? |
|---|---|---|---|
| **A. Network namespace** (bubblewrap `--unshare-net`, or the app doing `unshare(CLONE_NEWUSER\|CLONE_NEWNET)` itself) | The sandbox has only its own loopback. The proxy's Unix socket is bind-mounted in, and a tiny bridge inside (the app binary in a helper mode, so no socat dependency) listens on 127.0.0.1:<port> and forwards to it. | **Blocked on stock Ubuntu ≥ 23.10.** Both `unshare -rn` and `bwrap --unshare-net` fail (`write /proc/self/uid_map: Operation not permitted`, `bwrap: loopback: Failed RTM_NEWADDR`), because `kernel.apparmor_restrict_unprivileged_userns = 1`. Codex's own Linux sandbox fails here the same way. Several installed apps (1password, github-desktop, chrome) ship an AppArmor profile that grants `userns`; the app can do the same. | No: there is no interface to bypass. TCP, UDP, DNS, ICMP and raw sockets all have nowhere to go. **Strongest option.** |
| **B. Landlock + seccomp** (no namespace, no root) | Landlock (ABI ≥ 4) allows TCP `connect` only to the terminal's proxy port and denies TCP `bind`. seccomp-bpf denies creating UDP, raw and packet sockets (AF_INET/AF_INET6 with SOCK_DGRAM/SOCK_RAW, AF_PACKET) and denies `io_uring_setup`, so io_uring cannot be used to create sockets. Applied in the PTY child before `exec`, with `no_new_privs`; inherited by every descendant and cannot be lifted. | **Landlock tested** (ABI 8 available): direct TCP to 1.1.1.1:443 over IPv4 and IPv6 → `EACCES`; TCP to the local proxy port → allowed. **UDP DNS to 1.1.1.1:53 still went through**, so Landlock alone is not enough; the seccomp part is designed but not tested yet. | Small residual hole: Landlock matches **ports, not addresses**, so a TCP connection to *any* host on the proxy's port number is allowed. A random high port makes this impractical but not impossible. Kernels < 6.7 lack Landlock networking. |
| C. seccomp user-notify supervisor | The app inspects each `connect` sockaddr. | Not tested. | Not a security boundary: the kernel docs note TOCTOU on the user-memory sockaddr. **Rejected.** |
| D. nftables/iptables per uid, cgroup eBPF `connect4/6` | Kernel filtering by uid/cgroup. | `nft` present but needs root. | Strong, but needs root at runtime or a privileged service. **Rejected** for a desktop app. |
| E. Proxy variables only | Set `HTTPS_PROXY` etc. | n/a | Yes, trivially. Not enough (requirement 1). |

**Recommendation for Linux:** A (network namespace) as the primary. The `.deb`/`.rpm` installs an AppArmor profile granting `userns` to the app's sandbox helper only, the same pattern as the apps above. Where A is unavailable (AppImage without the profile, or a hardened kernel), fall back to **B** and show a one-line notice in Settings: "Linux: Landlock mode, a connection to another host on the proxy port is not blocked". If neither A nor B is available (kernel < 6.7 and no userns), **refuse to start agent terminals while the feature is on**, with an error that says why. It does not silently run unprotected.

### 3.2 macOS

| Option | How | Tested | Bypass |
|---|---|---|---|
| **A. Seatbelt** (`sandbox-exec` with an SBPL profile) | `(deny network*)`, then allow `network-outbound` only to `localhost:<proxy port>` (and the Unix socket path). The shell in the PTY starts under `sandbox-exec -p <profile>`, and every child inherits it. Codex and Claude Code's sandbox use this mechanism. | **Not tested** (no Mac available). | Kernel-enforced (Sandbox.kext), inherited and cannot be lifted by the child. `sandbox-exec` is marked deprecated, but it ships and works on current macOS and is what the other agent tools rely on. |
| B. NetworkExtension content filter (`NEFilterDataProvider`) | System extension that sees every flow with its process. | Not tested. | Strongest and per-process, but needs an Apple Developer ID, Apple-granted entitlements, notarization and the user approving a system extension. The app is **not code-signed yet** (README), so this is not feasible now. |

**Recommendation for macOS:** A (Seatbelt). Revisit B if the app is ever signed and notarized.

### 3.3 Windows

| Option | How | Tested | Bypass / cost |
|---|---|---|---|
| **A. Dedicated sandbox user + Windows Firewall (WFP) outbound rule** | One-time **elevated** setup creates a local user (for example `cg-sandbox`) and an outbound block rule for that user's SID, except to 127.0.0.1:<proxy port range>. Agent terminals start as that user (ConPTY + `CreateProcessWithLogonW` / a restricted token). Codex's Windows sandbox uses the same pattern (sandbox users plus firewall rules, set up once with elevation). | **Not tested** (no Windows available). | Kernel WFP enforcement per user, not bypassable by the child. Costs: one UAC prompt at setup, and files the agents work on must be readable and writable by that user (ACLs set during setup for the garden folders). |
| B. AppContainer / LPAC without network capabilities | Process has no network except a loopback exemption. | Not tested. | Strong for network, but AppContainer also cuts **filesystem** access to user files and needs admin for the loopback exemption. Agents would break. Not recommended. |
| C. Firewall rules per program path | Block `node.exe`, `git.exe`, ... | n/a | Bypassable by any binary not on the list. Rejected. |

**Recommendation for Windows:** A. It is the riskiest part of the plan (ConPTY under another user, file ACLs), so its PR starts with a spike on a real Windows machine. If A turns out not to be feasible, the feature stays **unavailable on Windows** (Settings shows why) rather than silently falling back to E (proxy variables only).

## 4. Rules

- **Entries.** Each entry is one of:
  - `host`: exact hostname, case-insensitive (e.g. `api.github.com`)
  - `*.domain`: the domain and all its subdomains (e.g. `*.github.com` matches `github.com` and `a.b.github.com`)
  - `CIDR`: IPv4 or IPv6 range (e.g. `10.0.0.0/8`, `2001:db8::/32`); a single IP is `/32` or `/128`

  No ports, no URLs, no paths (requirement 3).
- **Lists:** `{ id, name, kind: "allow" | "deny", entries: [...] }`, stored app-wide in the app store next to the gardens. Users create, rename, edit and delete lists in **Settings → Network**.
- **Per garden:** each garden selects any number of lists (combined), in the garden's settings.
- **Evaluation for a connection** (hostname H if known, resolved addresses IPs):
  1. If any **deny** list of the garden matches H, or any of IPs → **declined**.
  2. Else, if any **allow** list matches H (host or domain entry), or **all** IPs are inside allow CIDRs → **allowed**.
  3. Else → **prompt** (section 5).

  Deny always wins. CIDR deny is checked against the addresses the proxy resolved, so an allowed hostname that resolves into a denied range (for example a DNS rebinding to `127.0.0.0/8` or `169.254.0.0/16`) is still declined.
- Decisions are made once per connection (CONNECT or SOCKS request, or plain-HTTP request), not per byte.
- Limitation (stated in the UI help): for HTTPS the proxy sees the hostname from CONNECT/SOCKS, not the TLS SNI, and does no TLS interception. A program can name an allowed host and then speak TLS to the same IP for another name (domain fronting on shared CDNs). Matching is on what the proxy dials, which is what can be enforced without breaking TLS.

## 5. The prompt

- **Where it waits:** in the proxy. The request is **held open**: the CONNECT gets no response yet, and SOCKS gets no reply yet. The app sends a `net-prompt` event to the windows, the same mechanism as the existing `control-request`. The window that shows the terminal's garden opens the popup; if none does, any window opens it, and the first answer wins.
- **Popup content:** terminal title and garden, host (and port, shown for information only), the resolved IPs, and the four choices:
  - **Accept once:** this connection only
  - **Accept and add to allow list:** with a list picker (the garden's allow lists, or "new list…"); the entry is pre-filled with the exact host and can be changed to `*.domain` or a CIDR before saving
  - **Decline once**
  - **Decline and add to deny list:** same picker
- **Coalescing:** while a prompt for (garden, host) is open, further connections to the same host from that garden wait on the same prompt instead of opening new ones. "Once" answers apply to all connections waiting at that moment.
- **Timeout: 60 s, then decline once.** A toast says so, with a link to the lists. Fail-closed. Most clients' own connect timeouts are longer than 60 s, so they see a refusal, not a hang. Open question: is 60 s right?
- Declines answer with HTTP `403` (CONNECT/HTTP) or SOCKS reply `0x02` (connection not allowed by ruleset). Programs then fail with a clear error.

## 6. Agent API endpoints

The agent CLIs (`claude`, `codex`) run **inside** the sandbox too: requirement 1 says everything agents run, and their tool calls are child processes of the CLI. So their own API traffic (`api.anthropic.com`, `chatgpt.com` / `api.openai.com`, login and telemetry hosts) goes through the proxy like everything else. Both CLIs honour `HTTPS_PROXY`. This still has to be verified for each agent in the Linux PR; a CLI that does not would simply fail to connect, which is fail-closed and visible.

Because there are **no preset groups**, the app does **not** pre-allow these hosts. The first start of an agent in a garden with no matching list opens the popup for its API host, and "accept and add to allow list" once puts it in a list the user can reuse in every garden. A garden with no allow list at all is usable after a few clicks.

Open question for the owner: is this first-run prompting acceptable, or may the app add (visibly, editable, removable) the agent's own API host to a list the user names when they turn the feature on?

## 7. PR breakdown

1. **This design doc.**
2. **Rules engine + storage + Settings UI.** `netrules.rs` (entries, list model, evaluation, unit tests incl. CIDR v4/v6, `*.domain`, deny-wins, rebinding). Settings → Network: global on/off (default off), list editor. Per-garden list selection. No enforcement yet; the switch is labelled as such until PR 4.
3. **Proxy + prompt.** `netproxy.rs`: per-terminal listener, HTTP CONNECT / plain HTTP / SOCKS5h, DNS in the proxy, hold-and-ask, coalescing, 60 s timeout. `net-prompt` event, popup with the four choices, "add to list" writes through PR 2's store. Tested with a local client and server.
4. **Linux enforcement.** Network-namespace mode (sandbox helper, Unix-socket bridge, AppArmor profile in the `.deb`/`.rpm`) and Landlock + seccomp fallback. Refuse to start when neither is available. Integration tests: inside the sandbox `curl` with and without the proxy, UDP DNS and raw sockets all fail except via the proxy. Verify that `claude` and `codex` work through the proxy.
5. **macOS enforcement.** Seatbelt profile and launcher; the same integration tests on a macOS runner (the existing `app-macos` workflow).
6. **Windows enforcement.** Starts with the spike (sandbox user + firewall rule + ConPTY). Either it is implemented with tests on the `app-windows` runner, or the feature is reported as unavailable on Windows.
7. **Docs and polish.** README/app README section, "terminals started before the change" marker, help text with the limitations (section 4, Linux fallback).

## 8. Open questions for the owner

1. Agent API hosts: prompt on first use (current design), or let the user put them in a list when turning the feature on? (section 6)
2. Prompt timeout of 60 s, then decline once: OK? (section 5)
3. Linux fallback B has the "other host on the proxy port" hole. Accept it as a fallback with a visible notice, or require namespace mode (and refuse to start otherwise)?
4. Windows needs a one-time elevated setup (UAC) and a separate sandbox user. Acceptable?
