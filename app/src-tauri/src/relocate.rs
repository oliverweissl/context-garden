//! Repos that were renamed or moved on disk inside the garden folder.
//!
//! A garden remembers each repo by absolute path, so renaming a folder or moving
//! it into another group folder made the app think it was deleted. A repo that
//! is missing at its path is looked for among the repos the garden folder holds
//! now: the same remote URL (or, for a repo without one, the same folder name)
//! and not already claimed by another repo of the garden.

use crate::sources::{parse_remote, repos_in_depth};
use crate::store::{Garden, Repo};
use crate::util::git;
use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// How deep under the garden root a moved repo is searched for.
const DEPTH: usize = 5;

/// `host/path` of a remote URL, lowercased, whatever the protocol.
fn identity(url: &str) -> Option<String> {
    parse_remote(url).map(|(h, p)| format!("{h}/{p}").to_lowercase())
}

fn folder(path: &str) -> String {
    Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

/// Point missing repos of `g` at where they moved to. Returns how many were found.
pub fn relocate(g: &mut Garden) -> usize {
    let lost: Vec<usize> = (0..g.repos.len())
        .filter(|&i| {
            let r = &g.repos[i];
            r.ready() && r.source.is_none() && !r.on_disk()
        })
        .collect();
    if lost.is_empty() {
        return 0;
    }
    let claimed: HashSet<PathBuf> = g
        .repos
        .iter()
        .filter(|r| r.on_disk())
        .map(|r| std::fs::canonicalize(&r.path).unwrap_or_else(|_| PathBuf::from(&r.path)))
        .collect();
    // (path, remote identity) of every repo in the garden folder no other repo owns.
    let mut free: Vec<(PathBuf, Option<String>)> = repos_in_depth(Path::new(&g.root), DEPTH)
        .into_iter()
        .filter(|p| !claimed.contains(&std::fs::canonicalize(p).unwrap_or_else(|_| p.clone())))
        .map(|p| {
            let remote = git(&["config", "--get", "remote.origin.url"], &p).and_then(|u| identity(&u));
            (p, remote)
        })
        .collect();
    let mut found = 0;
    for i in lost {
        let want = g.repos[i].url.as_deref().and_then(identity);
        let pick = match &want {
            Some(w) => free.iter().position(|(_, r)| r.as_ref() == Some(w)),
            None => {
                // No remote to go by: a single free folder of the same name.
                let name = folder(&g.repos[i].path);
                let same: Vec<usize> = (0..free.len()).filter(|&j| folder(&free[j].0.to_string_lossy()) == name).collect();
                (same.len() == 1).then(|| same[0])
            }
        };
        let Some(j) = pick else { continue };
        let (path, _) = free.remove(j);
        let new_path = path.to_string_lossy().to_string();
        let r: &mut Repo = &mut g.repos[i];
        // A name that was just the folder's follows its rename.
        if r.name == folder(&r.path) {
            r.name = folder(&new_path);
        }
        r.path = new_path;
        found += 1;
    }
    found
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::process::Command;

    fn repo(name: &str, path: &Path, url: Option<&str>) -> Repo {
        Repo {
            name: name.into(),
            path: path.to_string_lossy().into(),
            url: url.map(String::from),
            source: None,
            group: None,
            status: "cloned".into(),
            error: None,
        }
    }

    fn init(dir: &Path, remote: Option<&str>) {
        std::fs::create_dir_all(dir).unwrap();
        let run = |args: &[&str]| assert!(Command::new("git").args(args).current_dir(dir).output().unwrap().status.success());
        run(&["init", "-q"]);
        if let Some(r) = remote {
            run(&["remote", "add", "origin", r]);
        }
        std::fs::write(dir.join("README.md"), "x").unwrap();
    }

    fn garden(root: &Path, repos: Vec<Repo>) -> Garden {
        serde_json::from_value(serde_json::json!({
            "id": "g", "name": "g", "root": root, "layout": "nested", "protocol": "ssh",
            "created_at": 0, "repos": repos,
        }))
        .unwrap()
    }

    #[test]
    fn renamed_and_moved_repos_are_found_again() {
        let root = std::env::temp_dir().join(format!("cg-reloc-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        // api was renamed; web moved into a group folder; both cloned over ssh, remembered as https.
        init(&root.join("api-renamed"), Some("git@github.com:acme/api.git"));
        init(&root.join("team/web"), Some("git@github.com:acme/web.git"));
        init(&root.join("stays"), Some("git@github.com:acme/stays.git"));
        let mut g = garden(&root, vec![
            repo("api", &root.join("api"), Some("https://github.com/acme/api")),
            repo("web", &root.join("web"), Some("https://github.com/acme/web.git")),
            repo("stays", &root.join("stays"), Some("git@github.com:acme/stays.git")),
            repo("gone", &root.join("gone"), Some("git@github.com:acme/gone.git")),
        ]);
        assert_eq!(relocate(&mut g), 2);
        assert_eq!(g.repos[0].path, root.join("api-renamed").to_string_lossy());
        assert_eq!(g.repos[0].name, "api-renamed");
        assert_eq!(g.repos[1].path, root.join("team/web").to_string_lossy());
        assert_eq!(g.repos[2].path, root.join("stays").to_string_lossy());
        assert_eq!(g.repos[3].path, root.join("gone").to_string_lossy()); // really deleted: stays missing
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn a_local_repo_without_remote_matches_only_by_unique_name() {
        let root = std::env::temp_dir().join(format!("cg-reloc-local-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        init(&root.join("a/tool"), None);
        init(&root.join("b/tool"), None);
        init(&root.join("c/solo"), None);
        let mut g = garden(&root, vec![
            repo("tool", &root.join("tool"), None),
            repo("solo", &root.join("solo"), None),
        ]);
        assert_eq!(relocate(&mut g), 1); // two "tool" folders: ambiguous, left alone
        assert_eq!(g.repos[1].path, root.join("c/solo").to_string_lossy());
        assert_eq!(g.repos[0].path, root.join("tool").to_string_lossy());
        let _ = std::fs::remove_dir_all(&root);
    }
}
