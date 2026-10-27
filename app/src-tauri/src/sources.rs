//! Turn the user's repo / group inputs into a clone plan, then clone it.
//!
//! Group listing goes through the `gh` / `glab` CLIs so the app never handles
//! tokens itself: whatever account those CLIs are logged in with is used.

use serde::Serialize;
use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use crate::store::Repo;
use crate::util::{expand_tilde, run, which};

#[derive(Serialize)]
pub struct GroupInfo {
    pub input: String,
    pub provider: String,
    pub repos: usize,
}

#[derive(Serialize)]
pub struct Plan {
    pub repos: Vec<Repo>,
    pub groups: Vec<GroupInfo>,
    pub warnings: Vec<String>,
    /// The inputs as classified: single repos / folders vs groups and orgs.
    pub repo_inputs: Vec<String>,
    pub group_inputs: Vec<String>,
}

#[derive(Debug, PartialEq)]
enum Kind {
    Repo,
    Group,
}

/// One input field takes repos, local folders, GitHub orgs and GitLab groups;
/// tell them apart. GitLab paths are ambiguous (group/sub vs group/project), so
/// those are looked up with `glab` when it is installed.
fn classify(input: &str, warnings: &mut Vec<String>) -> Kind {
    if expand_tilde(input).is_dir() || input.ends_with(".git") {
        return Kind::Repo;
    }
    if !input.contains("://") && input.contains('@') && input.contains(':') {
        return Kind::Repo; // scp-style git@host:path
    }
    let Some((host, path)) = parse_remote(input).or_else(|| parse_remote(&format!("https://{input}"))) else {
        return Kind::Repo; // reported as unusable later
    };
    let depth = path.split('/').count();
    if depth == 1 {
        return Kind::Group; // github.com/org, gitlab.com/group
    }
    if is_github(&host) {
        return Kind::Repo;
    }
    if which("glab").is_none() {
        warnings.push(format!(
            "{input}: treated as a repository; install `glab` so GitLab subgroups are recognised"
        ));
        return Kind::Repo;
    }
    let encoded = path.replace('/', "%2F");
    let mut args = vec!["api"];
    if host != "gitlab.com" {
        args.extend(["--hostname", host.as_str()]);
    }
    let project = format!("projects/{encoded}");
    if run("glab", &[args.as_slice(), &[project.as_str()]].concat(), None).is_ok() {
        return Kind::Repo;
    }
    let group = format!("groups/{encoded}");
    if run("glab", &[args.as_slice(), &[group.as_str()]].concat(), None).is_ok() {
        return Kind::Group;
    }
    Kind::Repo
}

/// (host, path) from https://host/a/b(.git), git@host:a/b.git, ssh://git@host/a/b, host/a/b.
pub fn parse_remote(input: &str) -> Option<(String, String)> {
    let s = input.trim().trim_end_matches('/');
    let rest = if let Some(r) = s.split_once("://").map(|(_, r)| r) {
        r.to_string()
    } else if let Some((user_host, path)) = s.split_once(':').filter(|(h, _)| h.contains('@')) {
        format!("{user_host}/{path}")
    } else {
        s.to_string()
    };
    let rest = rest.rsplit_once('@').map(|(_, r)| r.to_string()).unwrap_or(rest);
    let (host, path) = rest.split_once('/')?;
    let host = host.split(':').next().unwrap_or(host); // drop :port
    let path = path.trim_end_matches(".git").trim_matches('/');
    if host.is_empty() || path.is_empty() || !host.contains('.') && host != "localhost" {
        return None;
    }
    Some((host.to_lowercase(), path.to_string()))
}

fn is_github(host: &str) -> bool {
    host == "github.com" || host.starts_with("github.")
}

fn last_segment(path: &str) -> String {
    path.rsplit('/').next().unwrap_or(path).trim_end_matches(".git").to_string()
}

struct Remote {
    name: String,
    /// Path inside the garden for the nested layout.
    nested: String,
    full_path: String,
    https: String,
    ssh: String,
}

/// Stream of concatenated JSON arrays (`--paginate` prints one per page).
fn json_items(text: &str) -> Result<Vec<serde_json::Value>, String> {
    let mut items = Vec::new();
    for value in serde_json::Deserializer::from_str(text).into_iter::<serde_json::Value>() {
        match value.map_err(|e| format!("unexpected API output: {e}"))? {
            serde_json::Value::Array(a) => items.extend(a),
            other => items.push(other),
        }
    }
    Ok(items)
}

fn str_field(v: &serde_json::Value, key: &str) -> String {
    v.get(key).and_then(|x| x.as_str()).unwrap_or_default().to_string()
}

fn github_group(host: &str, owner: &str) -> Result<Vec<Remote>, String> {
    if which("gh").is_none() {
        return Err("listing a GitHub org needs the GitHub CLI `gh` (then `gh auth login`)".into());
    }
    let host_args: Vec<&str> = if host == "github.com" { vec![] } else { vec!["--hostname", host] };
    let fetch = |kind: &str| {
        let endpoint = format!("{kind}/{owner}/repos?per_page=100");
        let mut args = vec!["api", "--paginate"];
        args.extend(&host_args);
        args.push(&endpoint);
        run("gh", &args, None)
    };
    let text = fetch("orgs").or_else(|_| fetch("users"))?;
    Ok(json_items(&text)?
        .iter()
        .map(|r| {
            let full = str_field(r, "full_name");
            Remote {
                name: str_field(r, "name"),
                nested: full.clone(),
                full_path: full,
                https: str_field(r, "clone_url"),
                ssh: str_field(r, "ssh_url"),
            }
        })
        .filter(|r| !r.name.is_empty())
        .collect())
}

fn gitlab_group(host: &str, group: &str) -> Result<Vec<Remote>, String> {
    if which("glab").is_none() {
        return Err("listing a GitLab group needs the GitLab CLI `glab` (then `glab auth login`)".into());
    }
    let encoded = group.replace('/', "%2F");
    let endpoint = format!("groups/{encoded}/projects?include_subgroups=true&per_page=100");
    let mut args = vec!["api", "--paginate"];
    if host != "gitlab.com" {
        args.extend(["--hostname", host]);
    }
    args.push(&endpoint);
    let text = run("glab", &args, None)?;
    // Nested paths start at the group itself: platform/backend + .../svc/api -> backend/svc/api.
    let parent_len = group.rfind('/').map(|i| i + 1).unwrap_or(0);
    Ok(json_items(&text)?
        .iter()
        .map(|p| {
            let full = str_field(p, "path_with_namespace");
            Remote {
                name: str_field(p, "path"),
                nested: full.get(parent_len..).unwrap_or(&full).to_string(),
                full_path: full,
                https: str_field(p, "http_url_to_repo"),
                ssh: str_field(p, "ssh_url_to_repo"),
            }
        })
        .filter(|r| !r.name.is_empty())
        .collect())
}

fn target_status(target: &Path) -> (String, Option<String>) {
    if !target.exists() {
        ("pending".into(), None)
    } else if target.join(".git").exists() {
        ("local".into(), None) // already cloned earlier: reuse, never overwrite
    } else if std::fs::read_dir(target).map(|mut d| d.next().is_none()).unwrap_or(false) {
        ("pending".into(), None)
    } else {
        ("failed".into(), Some("target folder exists and is not a git repo".into()))
    }
}

/// Like `target_status`, for a symlink to `source` that should sit at `target`.
fn link_status(target: &Path, source: &Path) -> (String, Option<String>) {
    match std::fs::symlink_metadata(target) {
        Err(_) => ("pending".into(), None),
        Ok(m) if m.file_type().is_symlink() => {
            if target.canonicalize().ok().as_deref() == Some(source) {
                ("linked".into(), None)
            } else {
                ("failed".into(), Some("a link to another folder already sits there".into()))
            }
        }
        Ok(_) => ("failed".into(), Some("target folder already exists".into())),
    }
}

/// Git repos inside a plain folder, up to 3 levels deep (not descending into repos).
fn repos_in(dir: &Path) -> Vec<PathBuf> {
    fn walk(dir: &Path, depth: usize, out: &mut Vec<PathBuf>) {
        let Ok(entries) = std::fs::read_dir(dir) else { return };
        let mut dirs: Vec<PathBuf> = entries
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.is_dir() && !p.file_name().is_some_and(|n| n.to_string_lossy().starts_with('.')))
            .collect();
        dirs.sort();
        for d in dirs {
            if d.join(".git").exists() {
                out.push(d);
            } else if depth > 1 {
                walk(&d, depth - 1, out);
            }
        }
    }
    let mut out = Vec::new();
    walk(dir, 3, &mut out);
    out
}

fn file_name(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into()).unwrap_or_default()
}

/// What makes two repos the same: the folder a link points to, the remote, or the path.
fn repo_key(r: &Repo) -> String {
    r.source
        .clone()
        .or_else(|| r.url.as_deref().and_then(parse_remote).map(|(h, p)| format!("{h}/{p}").to_lowercase()))
        .unwrap_or_else(|| r.path.clone())
}

/// Plan the repos for `inputs`. `existing` are the repos already in the garden
/// when adding to one: those are skipped and their folders count as taken.
pub fn plan(root: &str, flat: bool, protocol: &str, inputs: &[String], existing: &[Repo]) -> Result<Plan, String> {
    let root = expand_tilde(root);
    let root_real = root.canonicalize().unwrap_or_else(|_| root.clone());
    let mut repos: Vec<Repo> = Vec::new();
    let mut groups = Vec::new();
    let mut warnings = Vec::new();
    let (mut repo_inputs, mut group_inputs) = (Vec::new(), Vec::new());
    for input in inputs.iter().map(|s| s.trim()).filter(|s| !s.is_empty()) {
        match classify(input, &mut warnings) {
            Kind::Repo => repo_inputs.push(input.to_string()),
            Kind::Group => group_inputs.push(input.to_string()),
        }
    }
    let known: HashSet<String> = existing.iter().map(repo_key).collect();
    let mut skipped = 0;
    // (repo index, preferred destination, fallback name) for collision handling.
    let mut dests: Vec<(usize, String, String)> = Vec::new();
    let mut push = |repos: &mut Vec<Repo>, repo: Repo, dest: Option<(String, String)>| {
        if known.contains(&repo_key(&repo)) {
            skipped += 1;
            return;
        }
        if let Some((dest, full)) = dest {
            dests.push((repos.len(), dest, full));
        }
        repos.push(repo);
    };

    // Folders on disk are linked into the garden (a simulated garden); a plain
    // folder holding several repos becomes a group of links.
    let mut folder_groups = Vec::new();
    for input in &repo_inputs {
        let local = expand_tilde(input);
        if local.is_dir() {
            let real = local.canonicalize().unwrap_or(local);
            let inside = if real.join(".git").exists() { vec![] } else { repos_in(&real) };
            if inside.is_empty() {
                if !real.join(".git").exists() {
                    warnings.push(format!("{input}: not a git repository (added anyway)"));
                }
                let name = file_name(&real);
                let link = Repo {
                    name: name.clone(),
                    path: real.to_string_lossy().into(),
                    url: None,
                    source: Some(real.to_string_lossy().into()),
                    group: None,
                    status: "local".into(),
                    error: None,
                };
                if real.starts_with(&root_real) {
                    // Already inside the garden folder: use it where it is.
                    push(&mut repos, Repo { source: None, ..link }, None);
                } else {
                    let full = real.parent().map(|p| format!("{}-{name}", file_name(p))).unwrap_or(name.clone());
                    push(&mut repos, link, Some((name, full)));
                }
                continue;
            }
            let base = file_name(&real);
            for repo in &inside {
                let rel = repo.strip_prefix(&real).unwrap_or(repo).to_string_lossy().replace('\\', "/");
                let name = file_name(repo);
                let nested = format!("{base}/{rel}");
                let dest = if flat { name.clone() } else { nested.clone() };
                let link = Repo {
                    name,
                    path: String::new(),
                    url: None,
                    source: Some(repo.to_string_lossy().into()),
                    group: Some(input.clone()),
                    status: "pending".into(),
                    error: None,
                };
                push(&mut repos, link, Some((dest, nested.replace('/', "-"))));
            }
            groups.push(GroupInfo { input: input.clone(), provider: "folder".into(), repos: inside.len() });
            folder_groups.push(input.clone());
            continue;
        }
        let Some((_, path)) = parse_remote(input) else {
            warnings.push(format!("{input}: neither an existing folder nor a git URL, skipped"));
            continue;
        };
        let name = last_segment(&path);
        let repo = Repo {
            name: name.clone(),
            path: String::new(),
            url: Some(input.to_string()),
            source: None,
            group: None,
            status: "pending".into(),
            error: None,
        };
        push(&mut repos, repo, Some((name, path.replace('/', "-"))));
    }
    repo_inputs.retain(|i| !folder_groups.contains(i));
    group_inputs.extend(folder_groups);

    for input in group_inputs.iter().filter(|i| !expand_tilde(i).is_dir()) {
        let Some((host, path)) = parse_remote(input).or_else(|| parse_remote(&format!("https://{input}")))
        else {
            warnings.push(format!("{input}: not a group URL like https://gitlab.com/group, skipped"));
            continue;
        };
        let github = is_github(&host);
        let listed = if github {
            let owner = path.split('/').next().unwrap_or(&path);
            github_group(&host, owner)
        } else {
            gitlab_group(&host, &path)
        };
        let listed = match listed {
            Ok(l) => l,
            Err(e) => {
                warnings.push(format!("{input}: {e}"));
                continue;
            }
        };
        groups.push(GroupInfo {
            input: input.to_string(),
            provider: if github { "github" } else { "gitlab" }.into(),
            repos: listed.len(),
        });
        for r in listed {
            let url = if protocol == "https" { &r.https } else { &r.ssh };
            let dest = if flat { r.name.clone() } else { r.nested.clone() };
            let repo = Repo {
                name: r.name,
                path: String::new(),
                url: Some(url.clone()),
                source: None,
                group: Some(path.clone()),
                status: "pending".into(),
                error: None,
            };
            push(&mut repos, repo, Some((dest, r.full_path.replace('/', "-"))));
        }
    }
    if skipped > 0 {
        warnings.push(format!("{skipped} already in this garden, skipped"));
    }

    // Same destination twice (flat layout, a repo also inside a group, or a
    // folder the garden already has): the later ones take their fallback name.
    let mut seen: HashSet<String> = existing
        .iter()
        .filter_map(|r| Path::new(&r.path).strip_prefix(&root).ok())
        .map(|rel| rel.to_string_lossy().replace('\\', "/").to_lowercase())
        .collect();
    for (idx, dest, fallback) in dests {
        let dest = if seen.insert(dest.to_lowercase()) { dest } else { fallback };
        seen.insert(dest.to_lowercase());
        let target: PathBuf = dest.split('/').fold(root.clone(), |p, seg| p.join(seg));
        let repo = &mut repos[idx];
        let (status, error) = match &repo.source {
            Some(src) => link_status(&target, Path::new(src)),
            None => target_status(&target),
        };
        repo.path = target.to_string_lossy().into();
        repo.status = status;
        repo.error = error;
    }

    let mut unique = HashSet::new();
    repos.retain(|r| unique.insert(r.path.clone()));
    Ok(Plan { repos, groups, warnings, repo_inputs, group_inputs })
}

#[cfg(unix)]
fn link_dir(source: &Path, target: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(source, target)
}

#[cfg(windows)]
fn link_dir(source: &Path, target: &Path) -> std::io::Result<()> {
    std::os::windows::fs::symlink_dir(source, target)
}

/// Clone (or link) every pending repo, 4 at a time. `report(index, repo)` runs after each.
pub fn clone_all(repos: Vec<Repo>, report: impl Fn(usize, &Repo) + Send + Sync + 'static) {
    let queue: Arc<Mutex<Vec<(usize, Repo)>>> = Arc::new(Mutex::new(
        repos.into_iter().enumerate().filter(|(_, r)| r.status == "pending").rev().collect(),
    ));
    let report = Arc::new(report);
    let workers: Vec<_> = (0..4)
        .map(|_| {
            let queue = queue.clone();
            let report = report.clone();
            std::thread::spawn(move || loop {
                let Some((idx, mut repo)) = queue.lock().unwrap().pop() else { break };
                repo.status = "cloning".into();
                report(idx, &repo);
                let target = Path::new(&repo.path);
                if let Some(parent) = target.parent() {
                    let _ = std::fs::create_dir_all(parent);
                }
                let result = match &repo.source {
                    Some(src) => link_dir(Path::new(src), target).map(|_| "linked").map_err(|e| {
                        if cfg!(windows) {
                            format!("could not link: {e} (turn on Windows Developer Mode to allow symlinks)")
                        } else {
                            format!("could not link: {e}")
                        }
                    }),
                    None => {
                        let url = repo.url.clone().unwrap_or_default();
                        run("git", &["clone", "--quiet", "--", &url, &repo.path], None).map(|_| "cloned")
                    }
                };
                match result {
                    Ok(status) => {
                        repo.status = status.into();
                        repo.error = None;
                    }
                    Err(e) => {
                        repo.status = "failed".into();
                        repo.error = Some(e);
                    }
                }
                report(idx, &repo);
            })
        })
        .collect();
    for w in workers {
        let _ = w.join();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_common_remote_forms() {
        let want = Some(("gitlab.example.com".to_string(), "platform/backend/api".to_string()));
        assert_eq!(parse_remote("https://gitlab.example.com/platform/backend/api.git"), want);
        assert_eq!(parse_remote("git@gitlab.example.com:platform/backend/api.git"), want);
        assert_eq!(parse_remote("ssh://git@gitlab.example.com:2222/platform/backend/api"), want);
        assert_eq!(parse_remote("gitlab.example.com/platform/backend/api/"), want);
        assert_eq!(parse_remote("not a url"), None);
    }

    #[test]
    fn classifies_without_network_where_unambiguous() {
        let mut w = Vec::new();
        assert_eq!(classify("https://github.com/acme", &mut w), Kind::Group);
        assert_eq!(classify("https://github.com/acme/api", &mut w), Kind::Repo);
        assert_eq!(classify("git@gitlab.com:acme/platform/api.git", &mut w), Kind::Repo);
        assert_eq!(classify("gitlab.example.com/platform", &mut w), Kind::Group);
        assert_eq!(classify(std::env::temp_dir().to_str().unwrap(), &mut w), Kind::Repo);
    }

    #[test]
    fn flat_layout_disambiguates_same_names() {
        let tmp = std::env::temp_dir().join(format!("cg-plan-{}", std::process::id()));
        let inputs = vec![
            "https://github.com/a/api.git".to_string(),
            "https://github.com/b/api.git".to_string(),
        ];
        let p = plan(tmp.to_str().unwrap(), true, "ssh", &inputs, &[]).unwrap();
        let names: Vec<_> = p.repos.iter().map(|r| Path::new(&r.path).file_name().unwrap().to_owned()).collect();
        assert_eq!(names, vec!["api", "b-api"]);
    }
}

#[cfg(test)]
mod clone_tests {
    use super::*;

    #[test]
    fn clones_pending_and_reports_failures() {
        let tmp = std::env::temp_dir().join(format!("cg-clone-{}", std::process::id()));
        let origin = tmp.join("origin");
        std::fs::create_dir_all(&origin).unwrap();
        run("git", &["init", "-q"], Some(&origin)).unwrap();
        run("git", &["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x"], Some(&origin)).unwrap();
        let repo = |name: &str, url: &str| Repo {
            name: name.into(),
            path: tmp.join("garden/group").join(name).to_string_lossy().into(),
            url: Some(url.into()),
            source: None,
            group: None,
            status: "pending".into(),
            error: None,
        };
        let results = Arc::new(Mutex::new(Vec::new()));
        let sink = results.clone();
        clone_all(
            vec![repo("ok", origin.to_str().unwrap()), repo("missing", tmp.join("nope").to_str().unwrap())],
            move |i, r| sink.lock().unwrap().push((i, r.status.clone())),
        );
        let mut done: Vec<_> = results.lock().unwrap().iter().filter(|(_, s)| s != "cloning").cloned().collect();
        done.sort();
        assert_eq!(done, vec![(0, "cloned".to_string()), (1, "failed".to_string())]);
        assert!(tmp.join("garden/group/ok/.git").exists());
        let _ = std::fs::remove_dir_all(&tmp);
    }

    #[test]
    fn folder_of_repos_becomes_linked_group() {
        let tmp = std::env::temp_dir().join(format!("cg-link-{}", std::process::id()));
        for name in ["a", "nested/b"] {
            std::fs::create_dir_all(tmp.join("code").join(name).join(".git")).unwrap();
        }
        let garden = tmp.join("garden");
        let inputs = vec![tmp.join("code").to_string_lossy().to_string()];
        let p = plan(garden.to_str().unwrap(), false, "ssh", &inputs, &[]).unwrap();
        assert_eq!(p.group_inputs, inputs);
        assert_eq!(p.repos.iter().map(|r| r.status.as_str()).collect::<Vec<_>>(), vec!["pending", "pending"]);
        assert!(Path::new(&p.repos[1].path).ends_with("code/nested/b"));

        let results = Arc::new(Mutex::new(Vec::new()));
        let sink = results.clone();
        clone_all(p.repos.clone(), move |i, r| sink.lock().unwrap().push((i, r.status.clone())));
        assert!(results.lock().unwrap().iter().all(|(_, s)| s != "failed"));
        assert!(garden.join("code/a").is_symlink());

        // Planning the same folder again for that garden skips what it has.
        let mut have = p.repos;
        for r in &mut have {
            r.status = "linked".into();
        }
        let again = plan(garden.to_str().unwrap(), false, "ssh", &inputs, &have).unwrap();
        assert!(again.repos.is_empty());
        let _ = std::fs::remove_dir_all(&tmp);
    }
}
