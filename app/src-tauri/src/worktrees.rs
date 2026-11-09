//! Agent worktrees: an agent terminal opened on a repo works in its own git
//! worktree, so agents on the same repo never share a working tree. They live
//! in `<garden>/.worktrees/<repo>/<leaf>` (hidden: the garden scan skips it, and
//! the user's repos and their .gitignore stay untouched) on branch `cg/<leaf>`,
//! `<leaf>` = `<agent>-<account>-<suffix>`. A worktree is removed with its branch
//! only when nothing in it would be lost.

use std::collections::HashSet;
use std::hash::{BuildHasher, Hasher};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use crate::store::Garden;
use crate::util::{git, run, slugify};

pub const DIR: &str = ".worktrees";
const PREFIX: &str = "cg/";
/// The app writes the garden's skill choice here when a terminal starts; it is not the agent's work.
const OURS: &str = ".claude/settings.local.json";

/// Worktrees this app run created or reopened: the startup sweep leaves them alone.
#[derive(Default)]
pub struct Session(pub Mutex<HashSet<PathBuf>>);

/// Lowercase letters and digits joined by single dashes: safe in a ref and a folder name.
pub fn slug(s: &str) -> String {
    slugify(s).split(|c: char| !c.is_ascii_alphanumeric()).filter(|p| !p.is_empty()).collect::<Vec<_>>().join("-")
}

/// `cg/<agent>-<account>-<suffix>`; the agent's default login is account "default".
pub fn branch_name(agent: &str, account: Option<&str>, suffix: &str) -> String {
    format!("{PREFIX}{}-{}-{suffix}", slug(agent), account.map(slug).unwrap_or_else(|| "default".into()))
}

fn suffix() -> String {
    let mut h = std::collections::hash_map::RandomState::new().build_hasher();
    h.write_u128(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| d.as_nanos()));
    format!("{:04x}", h.finish() & 0xffff)
}

/// The folder a repo's agent worktrees go in.
fn repo_dir(root: &Path, repo_name: &str) -> PathBuf {
    root.join(DIR).join(slug(repo_name))
}

/// The branch of a worktree folder of ours: `cg/<folder name>`.
pub fn branch_of(worktree: &Path) -> Option<String> {
    let leaf = worktree.file_name()?.to_str()?;
    Some(format!("{PREFIX}{leaf}"))
}

/// A path inside the garden's worktree folder (not the folder itself).
pub fn owned(root: &Path, path: &Path) -> bool {
    let base = root.join(DIR);
    path.starts_with(&base) && path != base && !path.components().any(|c| c.as_os_str() == "..")
}

/// `dir` is the top of a git working tree with at least one commit.
pub fn is_repo_top(dir: &Path) -> bool {
    let Some(top) = git(&["rev-parse", "--show-toplevel"], dir) else { return false };
    let same = Path::new(&top).canonicalize().ok() == dir.canonicalize().ok();
    same && git(&["rev-parse", "--verify", "--quiet", "HEAD"], dir).is_some()
}

fn branch_exists(repo: &Path, branch: &str) -> bool {
    git(&["rev-parse", "--verify", "--quiet", &format!("refs/heads/{branch}")], repo).is_some()
}

/// New worktree of `repo` on a new branch from its HEAD.
pub fn create(repo: &Path, root: &Path, repo_name: &str, agent: &str, account: Option<&str>) -> Result<PathBuf, String> {
    let dir = repo_dir(root, repo_name);
    for _ in 0..8 {
        let branch = branch_name(agent, account, &suffix());
        let path = dir.join(branch.trim_start_matches(PREFIX));
        if path.exists() || branch_exists(repo, &branch) {
            continue;
        }
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        run("git", &["worktree", "add", "-b", &branch, &path.to_string_lossy(), "HEAD"], Some(repo))?;
        return Ok(path);
    }
    Err("no free worktree name".into())
}

/// A saved terminal's worktree, set up again at the same path (so the agent's
/// session resumes) if it was removed: on its branch if that still exists.
pub fn reopen(repo: &Path, path: &Path) -> Result<(), String> {
    if path.is_dir() {
        return Ok(());
    }
    let branch = branch_of(path).ok_or("not an agent worktree")?;
    let _ = git(&["worktree", "prune"], repo);
    let p = path.to_string_lossy();
    if branch_exists(repo, &branch) {
        run("git", &["worktree", "add", &p, &branch], Some(repo))?;
    } else {
        run("git", &["worktree", "add", "-b", &branch, &p, "HEAD"], Some(repo))?;
    }
    Ok(())
}

/// Why a worktree must stay, or None when removing it and its branch loses
/// nothing: no changes besides the app's own settings file, and no commit
/// (on HEAD or its branch) that is not on another branch, a remote or a tag.
pub fn keep_reason(worktree: &Path, branch: &str) -> Option<String> {
    let status = match run("git", &["status", "--porcelain", "--untracked-files=all"], Some(worktree)) {
        Ok(s) => s,
        Err(e) => return Some(e),
    };
    if status.lines().any(|l| l.trim_end() != format!("?? {OURS}")) {
        return Some("uncommitted changes".into());
    }
    unique_commits(worktree, &["HEAD", &format!("refs/heads/{branch}")], branch)
}

/// Why `branch` must stay: some of `revs` (that exist) hold commits on no
/// other branch, remote or tag. Run from any checkout of the repository.
fn unique_commits(dir: &Path, revs: &[&str], branch: &str) -> Option<String> {
    let exclude = format!("--exclude={branch}");
    for rev in revs {
        if *rev != "HEAD" && !branch_exists(dir, branch) {
            continue;
        }
        match run("git", &["rev-list", "--count", rev, "--not", &exclude, "--branches", "--remotes", "--tags"], Some(dir)) {
            Ok(n) if n.trim() == "0" => {}
            Ok(_) => return Some("commits that are on no other branch".into()),
            Err(e) => return Some(e),
        }
    }
    None
}

/// Remove a worktree and its branch when that is safe; else say why it stays.
pub fn release(repo: &Path, worktree: &Path) -> Result<Option<String>, String> {
    let branch = branch_of(worktree).ok_or("not an agent worktree")?;
    if worktree.is_dir() {
        if let Some(why) = keep_reason(worktree, &branch) {
            return Ok(Some(why));
        }
        // --force: the app's settings file is untracked; everything else was checked above.
        run("git", &["worktree", "remove", "--force", &worktree.to_string_lossy()], Some(repo))?;
        if let Some(parent) = worktree.parent() {
            let _ = std::fs::remove_dir(parent); // only when empty
        }
    } else if let Some(why) = unique_commits(repo, &[&format!("refs/heads/{branch}")], &branch) {
        // The folder is gone (deleted by hand): keep a branch that holds work.
        let _ = git(&["worktree", "prune"], repo);
        return Ok(Some(why));
    }
    let _ = git(&["worktree", "prune"], repo);
    if branch_exists(repo, &branch) {
        run("git", &["branch", "-D", &branch], Some(repo))?;
    }
    Ok(None)
}

/// Prune stale worktree records of every repo of the garden and remove its
/// clean agent worktrees no terminal uses. Returns the ones kept, with why.
pub fn sweep(g: &Garden, session: &Session, saved: &HashSet<PathBuf>) -> Vec<(String, String)> {
    let root = Path::new(&g.root);
    let Ok(base) = root.join(DIR).canonicalize() else { return vec![] };
    let mut kept = vec![];
    for r in g.repos.iter().filter(|r| r.ready()) {
        let repo = Path::new(&r.path);
        if !repo.join(".git").exists() {
            continue;
        }
        let _ = git(&["worktree", "prune"], repo);
        let Some(list) = git(&["worktree", "list", "--porcelain"], repo) else { continue };
        for path in list.lines().filter_map(|l| l.strip_prefix("worktree ")).map(PathBuf::from) {
            let Ok(real) = path.canonicalize() else { continue };
            if !real.starts_with(&base) || saved.contains(&real) {
                continue;
            }
            let live = session.0.lock().unwrap();
            if live.contains(&real) {
                continue;
            }
            match release(repo, &path) {
                Ok(None) => {}
                Ok(Some(why)) => kept.push((path.to_string_lossy().into(), why)),
                Err(e) => kept.push((path.to_string_lossy().into(), e)),
            }
        }
    }
    kept
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sh_git(dir: &Path, args: &[&str]) {
        run("git", args, Some(dir)).unwrap();
    }

    fn temp_repo(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("cg-wt-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let repo = dir.join("repo");
        std::fs::create_dir_all(&repo).unwrap();
        sh_git(&repo, &["init", "-q", "-b", "main"]);
        sh_git(&repo, &["config", "user.email", "t@example.com"]);
        sh_git(&repo, &["config", "user.name", "t"]);
        sh_git(&repo, &["config", "commit.gpgsign", "false"]);
        std::fs::write(repo.join("a.txt"), "a").unwrap();
        sh_git(&repo, &["add", "."]);
        sh_git(&repo, &["commit", "-q", "-m", "init"]);
        repo.canonicalize().unwrap()
    }

    #[test]
    fn branch_names_carry_agent_and_account() {
        assert_eq!(branch_name("claude", Some("Work"), "3f9a"), "cg/claude-work-3f9a");
        assert_eq!(branch_name("codex", None, "0001"), "cg/codex-default-0001");
        assert_eq!(branch_name("claude", Some("Me & co.. ltd"), "beef"), "cg/claude-me-co-ltd-beef");
        assert_ne!(suffix(), suffix());
        assert_eq!(branch_of(Path::new("/g/.worktrees/r/claude-work-3f9a")).as_deref(), Some("cg/claude-work-3f9a"));
        assert!(owned(Path::new("/g"), Path::new("/g/.worktrees/r/x")));
        assert!(!owned(Path::new("/g"), Path::new("/g/.worktrees")));
        assert!(!owned(Path::new("/g"), Path::new("/g/.worktrees/../r")));
        assert!(!owned(Path::new("/g"), Path::new("/g/r")));
    }

    #[test]
    fn clean_worktrees_go_and_unsaved_work_stays() {
        let repo = temp_repo("release");
        let root = repo.parent().unwrap().to_path_buf();
        assert!(is_repo_top(&repo));
        assert!(!is_repo_top(&root));

        // Clean (the app's settings file aside): removed with its branch.
        let wt = create(&repo, &root, "repo", "claude", Some("work")).unwrap();
        let branch = branch_of(&wt).unwrap();
        std::fs::create_dir_all(wt.join(".claude")).unwrap();
        std::fs::write(wt.join(OURS), "{}").unwrap();
        assert_eq!(keep_reason(&wt, &branch), None);
        assert_eq!(release(&repo, &wt).unwrap(), None);
        assert!(!wt.exists() && !branch_exists(&repo, &branch));

        // Uncommitted change: kept.
        let wt = create(&repo, &root, "repo", "claude", None).unwrap();
        let branch = branch_of(&wt).unwrap();
        std::fs::write(wt.join("a.txt"), "changed").unwrap();
        assert_eq!(release(&repo, &wt).unwrap().as_deref(), Some("uncommitted changes"));
        assert!(wt.is_dir());

        // Committed but on no other branch: kept; once merged elsewhere: removed.
        sh_git(&wt, &["commit", "-q", "-am", "work"]);
        assert!(release(&repo, &wt).unwrap().is_some());
        assert!(branch_exists(&repo, &branch));
        sh_git(&repo, &["branch", "keep-it", &branch]);
        assert_eq!(release(&repo, &wt).unwrap(), None);
        assert!(!wt.exists() && !branch_exists(&repo, &branch));

        // A removed worktree comes back at the same path; the sweep removes it
        // unless a terminal uses it.
        let wt = create(&repo, &root, "repo", "codex", None).unwrap();
        std::fs::remove_dir_all(&wt).unwrap();
        reopen(&repo, &wt).unwrap();
        assert!(wt.is_dir());
        let garden: Garden = serde_json::from_value(serde_json::json!({
            "id": "g", "name": "g", "root": root, "layout": "flat", "protocol": "ssh", "created_at": 0,
            "repos": [{ "name": "repo", "path": repo, "status": "local" }],
        }))
        .unwrap();
        let session = Session::default();
        let saved = HashSet::from([wt.canonicalize().unwrap()]);
        assert!(sweep(&garden, &session, &saved).is_empty());
        assert!(wt.is_dir());
        assert!(sweep(&garden, &session, &HashSet::new()).is_empty());
        assert!(!wt.exists());
        let _ = std::fs::remove_dir_all(&root);
    }
}
