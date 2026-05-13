import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .models import CommitInfo


def run(
    args: list[str],
    cwd: Optional[Path] = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )

    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(args)}\n"
            f"exit code: {proc.returncode}\n"
            f"stdout:\n{proc.stdout or ''}\n"
            f"stderr:\n{proc.stderr or ''}"
        )

    return proc


def git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *args], cwd=repo, check=check)


def remote_ref(branch: str) -> str:
    return f"refs/remotes/origin/{branch}"


def local_ref(branch: str) -> str:
    return f"refs/heads/{branch}"


def ensure_ref(repo: Path, ref: str) -> None:
    git(repo, ["rev-parse", "--verify", "--quiet", ref])


def has_ref(repo: Path, ref: str) -> bool:
    proc = git(repo, ["rev-parse", "--verify", "--quiet", ref], check=False)
    return proc.returncode == 0


def branch_ref(repo: Path, branch: str) -> Optional[str]:
    if has_ref(repo, remote_ref(branch)):
        return remote_ref(branch)
    if has_ref(repo, local_ref(branch)):
        return local_ref(branch)
    return None


def require_branch_ref(repo: Path, branch: str) -> str:
    ref = branch_ref(repo, branch)
    if ref is None:
        raise ValueError(format_missing_branch_refs_error(repo, [branch], [branch]))
    return ref


def missing_branch_refs(repo: Path, branches: list[str]) -> list[str]:
    return [branch for branch in branches if branch_ref(repo, branch) is None]


def format_missing_branch_refs_error(repo: Path, branches: list[str], missing: list[str]) -> str:
    configured = "\n".join(f"  - {branch}" for branch in branches)
    fetch_lines = " \\\n  ".join(
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}" for branch in missing
    )

    return (
        "The target repository is missing configured branch refs.\n\n"
        f"Target repository: {repo}\n\n"
        "Configured branch chain:\n"
        f"{configured}\n\n"
        "Missing branch refs:\n"
        + "\n".join(
            f"  - {remote_ref(branch)} or {local_ref(branch)}" for branch in missing
        )
        + "\n\n"
        "Fetch the missing remote-tracking refs into the target repository:\n\n"
        f"git -C {repo} fetch origin \\\n"
        f"  {fetch_lines}\n\n"
        "For local-only testing, local branches with the configured names also work.\n\n"
        "If one of these branches no longer exists, update the configured chain "
        "instead of fetching it."
    )


def ensure_branch_refs(repo: Path, branches: list[str]) -> None:
    missing = missing_branch_refs(repo, branches)
    if missing:
        raise ValueError(format_missing_branch_refs_error(repo, branches, missing))


def is_ancestor(repo: Path, maybe_ancestor: str, descendant: str) -> bool:
    proc = git(
        repo,
        ["merge-base", "--is-ancestor", maybe_ancestor, descendant],
        check=False,
    )
    return proc.returncode == 0


def get_commit_info(repo: Path, sha: str) -> CommitInfo:
    fmt = "%H%x00%an <%ae>%x00%s"
    proc = git(repo, ["show", "-s", f"--format={fmt}", sha])
    commit_sha, author, subject = proc.stdout.rstrip("\n").split("\x00", 2)
    return CommitInfo(sha=commit_sha, author=author, subject=subject)


def branch_head(repo: Path, branch: str) -> str:
    return git(repo, ["rev-parse", require_branch_ref(repo, branch)]).stdout.strip()


def add_worktree(repo: Path, ref: str, scratch_root: Path) -> Path:
    worktree = Path(tempfile.mkdtemp(prefix="merge-check-", dir=scratch_root))
    shutil.rmtree(worktree)
    git(repo, ["worktree", "add", "--detach", "--quiet", str(worktree), ref])
    git(worktree, ["config", "user.name", "Forward Mergeability Checker"])
    git(worktree, ["config", "user.email", "forward-mergeability@example.invalid"])
    return worktree


def remove_worktree(repo: Path, worktree: Path) -> None:
    git(repo, ["worktree", "remove", "--force", str(worktree)], check=False)


def get_conflicted_files(worktree: Path) -> list[str]:
    proc = git(worktree, ["diff", "--name-only", "--diff-filter=U"])
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
