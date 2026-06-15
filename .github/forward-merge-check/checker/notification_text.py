import os
from typing import Optional

from .models import NotificationReason


def github_repository_label() -> str:
    return (
        os.environ.get("FORWARD_MERGE_TARGET_REPOSITORY")
        or os.environ.get("GITHUB_REPOSITORY")
        or "unknown repository"
    )


def repository_url(repository: str) -> str:
    server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    return f"{server_url}/{repository}"


def github_repository_url() -> Optional[str]:
    repository = (
        os.environ.get("FORWARD_MERGE_TARGET_REPOSITORY")
        or os.environ.get("GITHUB_REPOSITORY")
    )
    if not repository:
        return None

    return repository_url(repository)


def github_workflow_repository_url() -> Optional[str]:
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        return None

    return repository_url(repository)


def github_action_run_url() -> Optional[str]:
    repository_url = github_workflow_repository_url()
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not repository_url or not run_id:
        return None

    return f"{repository_url}/actions/runs/{run_id}"


def github_commit_url(sha: str) -> Optional[str]:
    repository_url = github_repository_url()
    if not repository_url:
        return None

    return f"{repository_url}/commit/{sha}"


def format_link(url: Optional[str], label: str, style: str) -> str:
    if not url:
        return label
    if style == "zulip":
        return f"[{label}]({url})"
    return f"<{url}|{label}>"


def humanize_notification_reasons(reasons: list[NotificationReason]) -> str:
    labels = {
        NotificationReason.FIRST_RUN: "first observed broken state",
        NotificationReason.BROKEN: "chain became blocked",
        NotificationReason.RESOLVED: "chain recovered",
        NotificationReason.HEALTH_CHANGED: "blocked result changed",
        NotificationReason.CHAIN_CHANGED: "configured chain changed",
    }
    return ", ".join(labels.get(reason, reason.value) for reason in reasons)


def result_status_label(status: str) -> str:
    labels = {
        "merge_ok": "merged cleanly",
        "nothing_to_merge": "already contained",
        "conflict": "conflict",
        "skipped": "skipped",
    }
    return labels.get(status, status)


def result_status_icon(status: str) -> str:
    if status == "conflict":
        return "❌"
    if status in {"merge_ok", "nothing_to_merge"}:
        return "✅"
    return "⚪"


def format_notification(state: dict, reasons: list[NotificationReason], style: str = "slack") -> str:
    status_text = "✅ healthy"
    if state["status"] == "broken":
        status_text = "🚨 blocked"

    lines = [
        "*Forward Merge Checker*",
        "",
        f"- *Status:* {status_text}",
        f"- *Repository:* `{github_repository_label()}`",
    ]

    if reasons:
        lines.append(f"- *Reason:* {humanize_notification_reasons(reasons)}")

    indexed_results = list(enumerate(state["results"], start=1))
    blocked_results = [
        (index, result) for index, result in indexed_results if result["status"] == "conflict"
    ]
    if blocked_results:
        blocked_edges = ", ".join(
            f"{index}. `{result['source_label']}` -> `{result['target']}`"
            for index, result in blocked_results
        )
        lines.append(f"- *Blocked edges ({len(blocked_results)}):* {blocked_edges}")

    lines.append(f"- *Chain:* {' -> '.join(f'`{branch}`' for branch in state['branches'])}")

    action_run_url = github_action_run_url()
    if action_run_url:
        lines.append(f"- *GitHub Actions run:* {format_link(action_run_url, 'open run', style)}")

    if indexed_results:
        lines.append("")
        lines.append("*Checked edges:*")
        for index, result in indexed_results:
            lines.append(
                f"{index}. {result_status_icon(result['status'])} "
                f"`{result['source_label']}` -> `{result['target']}`: "
                f"{result_status_label(result['status'])}"
            )

    if blocked_results:
        lines.append("")
        lines.append("*Conflict details:*")

    for index, blocked in blocked_results:
        lines.append("")
        lines.append(f"{index}. *Edge:* `{blocked['source_label']}` -> `{blocked['target']}`")
        commit = blocked.get("first_conflicting_commit")
        if commit:
            short_sha = commit["sha"][:12]
            commit_label = format_link(github_commit_url(commit["sha"]), short_sha, style)
            lines.append(
                "*First likely source-side commit:* "
                f"{commit_label} - {commit['subject']}"
            )
            lines.append(f"*Author:* {commit['author']}")
        else:
            lines.append("*First likely source-side commit:* not identified")

        if blocked["conflicted_files"]:
            lines.append(f"*Conflicted files ({len(blocked['conflicted_files'])}):*")
            lines.append("```")
            lines.extend(f"- {path}" for path in blocked["conflicted_files"])
            lines.append("```")

    return "\n".join(lines)
