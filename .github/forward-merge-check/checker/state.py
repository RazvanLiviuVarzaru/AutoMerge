import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .git_ops import branch_head
from .models import CommitInfo, ConflictCommit, MergeResult, NotificationReason


def commit_to_dict(commit: Optional[CommitInfo]) -> Optional[dict]:
    if commit is None:
        return None
    return {
        "sha": commit.sha,
        "author": commit.author,
        "subject": commit.subject,
    }


def conflict_commit_to_dict(commit: ConflictCommit) -> dict:
    return {
        "side": commit.side,
        "sha": commit.sha,
        "author": commit.author,
        "subject": commit.subject,
    }


def result_to_dict(result: MergeResult) -> dict:
    return {
        "source_label": result.source_label,
        "source_ref": result.source_ref,
        "target": result.target,
        "status": result.status,
        "message": result.message,
        "conflicted_files": result.conflicted_files,
        "first_conflicting_commit": commit_to_dict(result.first_conflicting_commit),
        "candidate_commits": [
            conflict_commit_to_dict(commit) for commit in result.candidate_commits
        ],
    }


def stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def fingerprint(value: object) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def current_state(repo: Path, branches: list[str], base_branch: str, results: list[MergeResult]) -> dict:
    branch_heads = {branch: branch_head(repo, branch) for branch in branches}
    blocked = next((result for result in results if result.status == "conflict"), None)
    status = "broken" if blocked else "healthy"

    config_payload = {
        "branches": branches,
        "base_branch": base_branch,
    }
    chain_payload = {
        **config_payload,
        "branch_heads": branch_heads,
    }
    health_payload = {
        "status": status,
        "blocked_edge": None if blocked is None else [blocked.source_label, blocked.target],
        "conflicted_files": [] if blocked is None else blocked.conflicted_files,
        "first_conflicting_commit": None
        if blocked is None or blocked.first_conflicting_commit is None
        else blocked.first_conflicting_commit.sha,
        "results": [
            {
                "target": result.target,
                "status": result.status,
                "conflicted_files": result.conflicted_files,
            }
            for result in results
        ],
    }

    return {
        "version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "base_branch": base_branch,
        "branches": branches,
        "branch_heads": branch_heads,
        "config_fingerprint": fingerprint(config_payload),
        "chain_fingerprint": fingerprint(chain_payload),
        "health_fingerprint": fingerprint(health_payload),
        "results": [result_to_dict(result) for result in results],
    }


def load_previous_state(raw_state: str | None) -> Optional[dict]:
    if not raw_state:
        return None

    try:
        parsed = json.loads(raw_state)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def load_previous_state_file(path: Optional[Path]) -> Optional[dict]:
    if path is None or not path.exists():
        return None

    return load_previous_state(path.read_text(encoding="utf-8"))


def notification_reasons(previous: Optional[dict], state: dict) -> list[NotificationReason]:
    if previous is None:
        return [NotificationReason.FIRST_RUN] if state["status"] == "broken" else []

    reasons: list[NotificationReason] = []

    if previous.get("config_fingerprint") != state.get("config_fingerprint"):
        reasons.append(NotificationReason.CHAIN_CHANGED)

    old_status = previous.get("status")
    new_status = state.get("status")

    if old_status != "broken" and new_status == "broken":
        reasons.append(NotificationReason.BROKEN)
    elif old_status == "broken" and new_status != "broken":
        reasons.append(NotificationReason.RESOLVED)
    elif old_status == "broken" and new_status == "broken":
        if previous.get("health_fingerprint") != state.get("health_fingerprint"):
            reasons.append(NotificationReason.HEALTH_CHANGED)

    if (
        previous.get("chain_fingerprint") != state.get("chain_fingerprint")
        and NotificationReason.CHAIN_CHANGED not in reasons
        and new_status == "broken"
    ):
        reasons.append(NotificationReason.CHAIN_CHANGED)

    return reasons


def compact_state(state: dict) -> dict:
    return {
        "version": state["version"],
        "checked_at": state["checked_at"],
        "status": state["status"],
        "base_branch": state["base_branch"],
        "branches": state["branches"],
        "branch_heads": state["branch_heads"],
        "config_fingerprint": state["config_fingerprint"],
        "chain_fingerprint": state["chain_fingerprint"],
        "health_fingerprint": state["health_fingerprint"],
        "results": [
            {
                "source_label": result["source_label"],
                "target": result["target"],
                "status": result["status"],
                "conflicted_files": result["conflicted_files"],
                "first_conflicting_commit": None
                if result["first_conflicting_commit"] is None
                else {
                    "sha": result["first_conflicting_commit"]["sha"],
                    "subject": result["first_conflicting_commit"]["subject"],
                },
            }
            for result in state["results"]
        ],
    }
