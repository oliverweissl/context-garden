//! Network rules (#81, docs/network-security-design.md section 4): named allow and
//! deny lists of hostnames, domains (`*.example.com`) and IP ranges (CIDR),
//! combined per garden. Deny wins; then allow; anything else is asked about.
//! This module only decides; enforcement (sandbox + proxy) comes separately.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::net::IpAddr;

/// Id of the built-in "Agent endpoints" allow list (design section 6).
pub const BUILTIN: &str = "agent-endpoints";

/// Hosts the agent CLIs need to run, measured with claude 2.1.332 and
/// codex-cli 0.150.1 (design section 6). Visible and editable in Settings.
pub const AGENT_HOSTS: [&str; 10] = [
    "api.anthropic.com",
    "mcp-proxy.anthropic.com",
    "claude.ai",
    "platform.claude.com",
    "downloads.claude.ai",
    "http-intake.logs.us5.datadoghq.com",
    "chatgpt.com",
    "auth.openai.com",
    "api.openai.com",
    "ab.chatgpt.com",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Kind {
    Allow,
    Deny,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct List {
    pub id: String,
    pub name: String,
    pub kind: Kind,
    /// As the user wrote them, normalized: `host`, `*.domain` or `CIDR`.
    pub entries: Vec<String>,
    #[serde(default)]
    pub builtin: bool,
}

/// network.json: the switch, the lists, and which lists each garden uses.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct NetConfig {
    /// Off by default (requirement 2).
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub lists: Vec<List>,
    /// garden id -> list ids. A garden not listed uses the built-in list only.
    #[serde(default)]
    pub gardens: BTreeMap<String, Vec<String>>,
    /// Linux: the user agreed to the Landlock fallback and its hole (design 3.1).
    /// Cleared when namespace mode works again, so a later fallback asks anew.
    #[serde(default)]
    pub fallback_agreed: bool,
}

impl Default for NetConfig {
    fn default() -> Self {
        let mut c = NetConfig { enabled: false, lists: vec![], gardens: BTreeMap::new(), fallback_agreed: false };
        c.ensure_builtin();
        c
    }
}

pub fn builtin_list() -> List {
    List {
        id: BUILTIN.into(),
        name: "Agent endpoints".into(),
        kind: Kind::Allow,
        entries: AGENT_HOSTS.iter().map(|h| h.to_string()).collect(),
        builtin: true,
    }
}

impl NetConfig {
    /// The built-in list exists (it cannot be deleted, only edited).
    pub fn ensure_builtin(&mut self) {
        if !self.lists.iter().any(|l| l.id == BUILTIN) {
            self.lists.insert(0, builtin_list());
        }
    }

    /// The ids of the lists a garden uses.
    pub fn garden_ids(&self, garden: &str) -> Vec<String> {
        self.gardens.get(garden).cloned().unwrap_or_else(|| vec![BUILTIN.to_string()])
    }

    /// The lists a garden uses, in the order they are stored.
    pub fn garden_lists(&self, garden: &str) -> Vec<&List> {
        let ids = self.garden_ids(garden);
        self.lists.iter().filter(|l| ids.contains(&l.id)).collect()
    }

    /// Add or replace a list after checking it. A new list (empty id) gets one.
    pub fn save_list(&mut self, mut list: List) -> Result<List, String> {
        list.name = list.name.trim().to_string();
        if list.name.is_empty() {
            return Err("a list needs a name".into());
        }
        list.entries = normalize_entries(&list.entries)?;
        match self.lists.iter_mut().find(|l| !list.id.is_empty() && l.id == list.id) {
            Some(old) => {
                if old.builtin {
                    // The built-in list stays an allow list and keeps its name.
                    list.kind = Kind::Allow;
                    list.name = old.name.clone();
                }
                list.builtin = old.builtin;
                *old = list.clone();
            }
            None => {
                list.id = new_id(&list.name, &self.lists);
                list.builtin = false;
                self.lists.push(list.clone());
            }
        }
        Ok(list)
    }

    pub fn delete_list(&mut self, id: &str) -> Result<(), String> {
        if id == BUILTIN {
            return Err("the built-in list cannot be deleted; edit or reset it instead".into());
        }
        let before = self.lists.len();
        self.lists.retain(|l| l.id != id);
        if self.lists.len() == before {
            return Err("that list no longer exists".into());
        }
        for ids in self.gardens.values_mut() {
            ids.retain(|i| i != id);
        }
        Ok(())
    }

    pub fn reset_builtin(&mut self) {
        match self.lists.iter_mut().find(|l| l.id == BUILTIN) {
            Some(l) => *l = builtin_list(),
            None => self.ensure_builtin(),
        }
    }

    pub fn set_garden(&mut self, garden: &str, ids: Vec<String>) -> Result<(), String> {
        if let Some(bad) = ids.iter().find(|i| !self.lists.iter().any(|l| &l.id == *i)) {
            return Err(format!("no list '{bad}'"));
        }
        let mut uniq: Vec<String> = Vec::new();
        for i in ids {
            if !uniq.contains(&i) {
                uniq.push(i);
            }
        }
        self.gardens.insert(garden.to_string(), uniq);
        Ok(())
    }

    /// Add one entry to a list (the prompt's "add to list"); no-op if present.
    pub fn add_entry(&mut self, id: &str, entry: &str) -> Result<String, String> {
        let entry = parse_entry(entry)?.to_string();
        let list = self.lists.iter_mut().find(|l| l.id == id).ok_or("that list no longer exists")?;
        if !list.entries.contains(&entry) {
            list.entries.push(entry.clone());
        }
        Ok(entry)
    }
}

fn new_id(name: &str, lists: &[List]) -> String {
    let base: String = name
        .to_lowercase()
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '-' })
        .collect::<String>()
        .split('-')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("-");
    let base = if base.is_empty() { "list".to_string() } else { base };
    let mut id = base.clone();
    let mut n = 2;
    while lists.iter().any(|l| l.id == id) || id == BUILTIN {
        id = format!("{base}-{n}");
        n += 1;
    }
    id
}

/// One rule. Display form is what is stored.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Entry {
    /// Exactly this hostname.
    Host(String),
    /// This domain and every subdomain (`*.example.com`).
    Domain(String),
    /// An address range; a single address is /32 or /128.
    Cidr(IpAddr, u8),
}

impl std::fmt::Display for Entry {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Entry::Host(h) => write!(f, "{h}"),
            Entry::Domain(d) => write!(f, "*.{d}"),
            Entry::Cidr(ip, bits) => write!(f, "{ip}/{bits}"),
        }
    }
}

fn valid_hostname(h: &str) -> bool {
    !h.is_empty()
        && h.len() <= 253
        && h.split('.').all(|label| {
            !label.is_empty()
                && label.len() <= 63
                && !label.starts_with('-')
                && !label.ends_with('-')
                && label.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        })
}

/// Parse one entry: a hostname, `*.domain`, an IP address or a CIDR range.
/// URLs, paths and ports are refused: rules match hosts and addresses only.
pub fn parse_entry(raw: &str) -> Result<Entry, String> {
    let s = raw.trim().to_lowercase();
    if s.is_empty() {
        return Err("empty entry".into());
    }
    if s.contains("://") || s.contains('/') && s.parse::<IpAddr>().is_err() && !looks_like_cidr(&s) {
        return Err(format!("{raw}: rules match hostnames, domains and IP ranges, not URLs or paths"));
    }
    if let Some((addr, bits)) = s.split_once('/') {
        let ip: IpAddr = addr.parse().map_err(|_| format!("{raw}: not an IP range (CIDR)"))?;
        let max = if ip.is_ipv4() { 32 } else { 128 };
        let bits: u8 = bits.parse().ok().filter(|b| *b <= max).ok_or_else(|| format!("{raw}: prefix must be 0-{max}"))?;
        return Ok(Entry::Cidr(mask(ip, bits), bits));
    }
    let bare = s.trim_start_matches('[').trim_end_matches(']');
    if let Ok(ip) = bare.parse::<IpAddr>() {
        return Ok(Entry::Cidr(ip, if ip.is_ipv4() { 32 } else { 128 }));
    }
    if s.contains(':') {
        return Err(format!("{raw}: no ports; rules match the host only"));
    }
    let s = s.trim_end_matches('.');
    if let Some(domain) = s.strip_prefix("*.") {
        return if valid_hostname(domain) { Ok(Entry::Domain(domain.into())) } else { Err(format!("{raw}: not a domain")) };
    }
    if valid_hostname(s) {
        Ok(Entry::Host(s.into()))
    } else {
        Err(format!("{raw}: not a hostname, *.domain or IP range"))
    }
}

fn looks_like_cidr(s: &str) -> bool {
    s.split_once('/').is_some_and(|(a, b)| a.parse::<IpAddr>().is_ok() && b.chars().all(|c| c.is_ascii_digit()))
}

/// Parse, normalize and de-duplicate a list's entries; blank lines are dropped.
pub fn normalize_entries(raw: &[String]) -> Result<Vec<String>, String> {
    let mut out: Vec<String> = Vec::new();
    let mut errors = Vec::new();
    for r in raw.iter().filter(|r| !r.trim().is_empty()) {
        match parse_entry(r) {
            Ok(e) => {
                let e = e.to_string();
                if !out.contains(&e) {
                    out.push(e);
                }
            }
            Err(e) => errors.push(e),
        }
    }
    if errors.is_empty() {
        Ok(out)
    } else {
        Err(errors.join("; "))
    }
}

fn mask(ip: IpAddr, bits: u8) -> IpAddr {
    match ip {
        IpAddr::V4(v4) => {
            let m = if bits == 0 { 0 } else { u32::MAX << (32 - bits) };
            IpAddr::V4((u32::from(v4) & m).into())
        }
        IpAddr::V6(v6) => {
            let m = if bits == 0 { 0 } else { u128::MAX << (128 - bits) };
            IpAddr::V6((u128::from(v6) & m).into())
        }
    }
}

/// IPv4-mapped IPv6 (::ffff:a.b.c.d) counts as the IPv4 address.
fn canonical(ip: IpAddr) -> IpAddr {
    match ip {
        IpAddr::V6(v6) => v6.to_ipv4_mapped().map(IpAddr::V4).unwrap_or(ip),
        v4 => v4,
    }
}

impl Entry {
    fn matches_host(&self, host: &str) -> bool {
        match self {
            Entry::Host(h) => h == host,
            Entry::Domain(d) => host == d || host.strip_suffix(d.as_str()).is_some_and(|rest| rest.ends_with('.')),
            Entry::Cidr(..) => false,
        }
    }

    fn matches_ip(&self, ip: IpAddr) -> bool {
        match self {
            Entry::Cidr(net, bits) => {
                let ip = canonical(ip);
                ip.is_ipv4() == net.is_ipv4() && mask(ip, *bits) == *net
            }
            _ => false,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "decision", rename_all = "lowercase")]
pub enum Decision {
    /// A list allows it (named).
    Allow { list: String },
    /// A deny list matched (named): deny always wins.
    Deny { list: String },
    /// No list covers it: ask the user.
    Ask,
}

/// The decision for a connection to `host` (a name or an IP literal) whose
/// resolved addresses are `ips` (design section 4):
/// 1. any deny list matches the host or any address -> deny;
/// 2. an allow list matches the host, or every address is in allow ranges -> allow;
/// 3. otherwise ask.
pub fn decide(lists: &[&List], host: &str, ips: &[IpAddr]) -> Decision {
    let host = host.trim().trim_end_matches('.').trim_start_matches('[').trim_end_matches(']').to_lowercase();
    let mut ips: Vec<IpAddr> = ips.iter().copied().map(canonical).collect();
    let literal = host.parse::<IpAddr>().ok().map(canonical);
    if let Some(ip) = literal {
        if !ips.contains(&ip) {
            ips.push(ip);
        }
    }
    let parsed: Vec<(&List, Vec<Entry>)> = lists
        .iter()
        .map(|l| (*l, l.entries.iter().filter_map(|e| parse_entry(e).ok()).collect()))
        .collect();
    let hit = |entries: &[Entry]| {
        literal.is_none() && entries.iter().any(|e| e.matches_host(&host))
            || ips.iter().any(|ip| entries.iter().any(|e| e.matches_ip(*ip)))
    };
    if let Some((l, _)) = parsed.iter().find(|(l, es)| l.kind == Kind::Deny && hit(es)) {
        return Decision::Deny { list: l.name.clone() };
    }
    let allows = |entries: &[Entry]| {
        literal.is_none() && entries.iter().any(|e| e.matches_host(&host))
            || !ips.is_empty() && ips.iter().all(|ip| entries.iter().any(|e| e.matches_ip(*ip)))
    };
    let allow: Vec<Entry> = parsed.iter().filter(|(l, _)| l.kind == Kind::Allow).flat_map(|(_, es)| es.clone()).collect();
    if allows(&allow) {
        let name = parsed
            .iter()
            .filter(|(l, _)| l.kind == Kind::Allow)
            .find(|(_, es)| literal.is_none() && es.iter().any(|e| e.matches_host(&host)) || ips.iter().any(|ip| es.iter().any(|e| e.matches_ip(*ip))))
            .map(|(l, _)| l.name.clone())
            .unwrap_or_default();
        return Decision::Allow { list: name };
    }
    Decision::Ask
}

#[cfg(test)]
mod tests {
    use super::*;

    fn list(name: &str, kind: Kind, entries: &[&str]) -> List {
        List { id: name.into(), name: name.into(), kind, entries: entries.iter().map(|e| e.to_string()).collect(), builtin: false }
    }
    fn ip(s: &str) -> IpAddr {
        s.parse().unwrap()
    }

    #[test]
    fn entries_parse_and_normalize() {
        assert_eq!(parse_entry(" API.GitHub.com. ").unwrap(), Entry::Host("api.github.com".into()));
        assert_eq!(parse_entry("*.github.com").unwrap().to_string(), "*.github.com");
        assert_eq!(parse_entry("10.1.2.3/8").unwrap().to_string(), "10.0.0.0/8");
        assert_eq!(parse_entry("192.168.1.5").unwrap().to_string(), "192.168.1.5/32");
        assert_eq!(parse_entry("2001:db8::1/32").unwrap().to_string(), "2001:db8::/32");
        assert_eq!(parse_entry("[::1]").unwrap().to_string(), "::1/128");
        for bad in ["https://github.com", "github.com/path", "github.com:443", "10.0.0.0/33", "bad host", "*.", "-x.com", ""] {
            assert!(parse_entry(bad).is_err(), "{bad}");
        }
        let e = normalize_entries(&["a.com".into(), "".into(), "A.com".into(), "*.b.com".into()]).unwrap();
        assert_eq!(e, vec!["a.com", "*.b.com"]);
        assert!(normalize_entries(&["ok.com".into(), "http://x".into()]).unwrap_err().contains("not URLs"));
    }

    #[test]
    fn hosts_and_domains_match() {
        let allow = list("A", Kind::Allow, &["api.github.com", "*.example.org"]);
        let l = [&allow];
        assert_eq!(decide(&l, "API.github.com", &[]), Decision::Allow { list: "A".into() });
        assert_eq!(decide(&l, "github.com", &[]), Decision::Ask);
        assert_eq!(decide(&l, "example.org", &[]), Decision::Allow { list: "A".into() });
        assert_eq!(decide(&l, "a.b.example.org", &[]), Decision::Allow { list: "A".into() });
        assert_eq!(decide(&l, "badexample.org", &[]), Decision::Ask);
    }

    #[test]
    fn deny_wins_and_cidr_checks_resolved_addresses() {
        let allow = list("Allow", Kind::Allow, &["*.corp.example", "10.0.0.0/8"]);
        let deny = list("Private", Kind::Deny, &["127.0.0.0/8", "169.254.0.0/16", "secret.corp.example"]);
        let l = [&allow, &deny];
        assert_eq!(decide(&l, "git.corp.example", &[ip("10.1.1.1")]), Decision::Allow { list: "Allow".into() });
        // An allowed name that resolves into a denied range (rebinding) is denied.
        assert_eq!(decide(&l, "git.corp.example", &[ip("127.0.0.1")]), Decision::Deny { list: "Private".into() });
        assert_eq!(decide(&l, "secret.corp.example", &[ip("10.1.1.1")]), Decision::Deny { list: "Private".into() });
        // IP literals and IPv4-mapped IPv6.
        assert_eq!(decide(&l, "10.9.9.9", &[]), Decision::Allow { list: "Allow".into() });
        assert_eq!(decide(&l, "::ffff:127.0.0.1", &[]), Decision::Deny { list: "Private".into() });
        // A name not in any list is allowed only if all its addresses are in allow ranges.
        assert_eq!(decide(&l, "other.net", &[ip("10.2.2.2")]), Decision::Allow { list: "Allow".into() });
        assert_eq!(decide(&l, "other.net", &[ip("10.2.2.2"), ip("8.8.8.8")]), Decision::Ask);
        assert_eq!(decide(&l, "other.net", &[]), Decision::Ask);
        let v6 = list("V6", Kind::Allow, &["2001:db8::/32"]);
        assert_eq!(decide(&[&v6], "x", &[ip("2001:db8:1::5")]), Decision::Allow { list: "V6".into() });
    }

    #[test]
    fn lists_combine_per_garden_and_builtin_stays() {
        let mut c = NetConfig::default();
        assert!(!c.enabled);
        assert_eq!(c.garden_ids("g"), vec![BUILTIN]);
        assert_eq!(decide(&c.garden_lists("g"), "api.anthropic.com", &[]), Decision::Allow { list: "Agent endpoints".into() });
        let work = c.save_list(List { id: "".into(), name: " Work ".into(), kind: Kind::Allow, entries: vec!["*.github.com".into()], builtin: false }).unwrap();
        assert_eq!((work.id.as_str(), work.name.as_str()), ("work", "Work"));
        let block = c.save_list(List { id: "".into(), name: "Work".into(), kind: Kind::Deny, entries: vec!["gist.github.com".into()], builtin: false }).unwrap();
        assert_eq!(block.id, "work-2");
        c.set_garden("g", vec![BUILTIN.into(), "work".into(), "work-2".into(), "work".into()]).unwrap();
        assert_eq!(c.garden_ids("g").len(), 3);
        let g = c.garden_lists("g");
        assert_eq!(decide(&g, "api.github.com", &[]), Decision::Allow { list: "Work".into() });
        assert_eq!(decide(&g, "gist.github.com", &[]), Decision::Deny { list: "Work".into() });
        assert_eq!(decide(&c.garden_lists("other"), "api.github.com", &[]), Decision::Ask);
        assert!(c.set_garden("g", vec!["nope".into()]).is_err());
        // The built-in list: editable, keeps kind and name, cannot be deleted, can be reset.
        let edited = c.save_list(List { id: BUILTIN.into(), name: "x".into(), kind: Kind::Deny, entries: vec!["api.anthropic.com".into()], builtin: false }).unwrap();
        assert_eq!((edited.kind, edited.name.as_str(), edited.builtin), (Kind::Allow, "Agent endpoints", true));
        assert!(c.delete_list(BUILTIN).is_err());
        c.reset_builtin();
        assert_eq!(c.lists[0].entries.len(), AGENT_HOSTS.len());
        // Deleting a list drops it from gardens; adding an entry normalizes it.
        c.delete_list("work").unwrap();
        assert_eq!(c.garden_ids("g"), vec![BUILTIN.to_string(), "work-2".into()]);
        assert_eq!(c.add_entry("work-2", "Evil.COM").unwrap(), "evil.com");
        assert!(c.add_entry("work-2", "http://x").is_err());
    }
}
