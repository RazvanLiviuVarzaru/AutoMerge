from pathlib import Path
from typing import Optional

from .git_ops import (
    add_worktree,
    ensure_ref,
    get_commit_info,
    get_conflicted_files,
    git,
    is_ancestor,
    remove_worktree,
    require_branch_ref,
)
from .models import CommitInfo, ConflictCommit, MergeResult


def source_commits(repo: Path, target_ref: str, source_ref: str) -> list[str]:
    proc = git(
        repo,
        [
            "rev-list",
            "--reverse",
            "--no-merges",
            f"{target_ref}..{source_ref}",
        ],
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def candidate_commits_for_active_conflict(
    worktree: Path,
    limit_per_side: int = 20,
) -> list[ConflictCommit]:
    proc = git(
        worktree,
        [
            "log",
            "--left-right",
            "--merge",
            "--format=%m%x00%H%x00%an <%ae>%x00%s",
        ],
        check=False,
    )
    if proc.returncode != 0:
        return []

    side_for_marker = {
        "<": "target",
        ">": "source",
    }
    counts = {"target": 0, "source": 0, "unknown": 0}
    seen: set[str] = set()
    candidates: list[ConflictCommit] = []

    for line in proc.stdout.splitlines():
        parts = line.split("\x00", 3)
        if len(parts) != 4:
            continue

        marker, sha, author, subject = parts
        if sha in seen:
            continue

        side = side_for_marker.get(marker, "unknown")
        if counts[side] >= limit_per_side:
            continue

        seen.add(sha)
        counts[side] += 1
        candidates.append(
            ConflictCommit(
                side=side,
                sha=sha,
                author=author,
                subject=subject,
            )
        )

    return candidates


def try_merge(
    repo: Path,
    base_ref: str,
    source_ref: str,
    scratch_root: Path,
    collect_conflict_commits: bool = False,
) -> tuple[bool, list[str], Optional[str], list[ConflictCommit]]:
    worktree = add_worktree(repo, base_ref, scratch_root)

    try:
        proc = git(
            worktree,
            [
                "merge",
                "--no-ff",
                "-m",
                f"Local test merge: {source_ref} into {base_ref}",
                source_ref,
            ],
            check=False,
        )

        if proc.returncode == 0:
            sha = git(worktree, ["rev-parse", "HEAD"]).stdout.strip()
            return True, [], sha, []

        conflicted_files = get_conflicted_files(worktree)
        candidates = (
            candidate_commits_for_active_conflict(worktree)
            if collect_conflict_commits
            else []
        )
        return False, conflicted_files, None, candidates

    finally:
        git(worktree, ["merge", "--abort"], check=False)
        remove_worktree(repo, worktree)


def find_first_conflicting_commit(
    repo: Path,
    source_ref: str,
    target: str,
    scratch_root: Path,
) -> Optional[CommitInfo]:
    """
    Approximation.

    Each source-side non-merge commit is tried independently against the target.
    Git conflicts are produced by both branches together, so this is not a
    perfect root cause. It is a practical answer to "which source-side commit
    should I inspect first?" for scheduled chain-health runs too, not only PRs.
    """
    target_ref = require_branch_ref(repo, target)

    for sha in source_commits(repo, target_ref, source_ref):
        ok, _files, _merge_sha, _candidates = try_merge(
            repo,
            target_ref,
            sha,
            scratch_root,
        )
        if not ok:
            return get_commit_info(repo, sha)

    return None


def check_merge(
    repo: Path,
    source_label: str,
    source_ref: str,
    target: str,
    scratch_root: Path,
) -> MergeResult:
    target_ref = require_branch_ref(repo, target)
    ensure_ref(repo, target_ref)
    ensure_ref(repo, source_ref)

    if is_ancestor(repo, source_ref, target_ref):
        return MergeResult(
            source_label=source_label,
            source_ref=source_ref,
            target=target,
            status="nothing_to_merge",
            message=f"{source_label} is already contained in {target}.",
        )

    ok, conflicted_files, merge_sha, candidates = try_merge(
        repo,
        target_ref,
        source_ref,
        scratch_root,
        collect_conflict_commits=True,
    )

    if ok:
        assert merge_sha is not None
        return MergeResult(
            source_label=source_label,
            source_ref=merge_sha,
            target=target,
            status="merge_ok",
            message=f"{source_label} merges cleanly into {target}.",
        )

    first_conflicting_commit = find_first_conflicting_commit(
        repo=repo,
        source_ref=source_ref,
        target=target,
        scratch_root=scratch_root,
    )
    return MergeResult(
        source_label=source_label,
        source_ref=source_ref,
        target=target,
        status="conflict",
        message=f"{source_label} cannot be cleanly merged into {target}.",
        conflicted_files=conflicted_files,
        first_conflicting_commit=first_conflicting_commit,
        candidate_commits=candidates,
    )


def branch_targets_after(branches: list[str], base_branch: str) -> list[str]:
    if base_branch not in branches:
        raise ValueError(
            f"Base branch {base_branch!r} is not in the configured branch chain: "
            + ", ".join(branches)
        )

    return branches[branches.index(base_branch) + 1 :]
