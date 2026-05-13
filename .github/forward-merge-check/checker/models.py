from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


@dataclass
class CommitInfo:
    sha: str
    author: str
    subject: str


@dataclass
class MergeResult:
    source_label: str
    source_ref: str
    target: str
    status: str
    message: str
    conflicted_files: list[str] = field(default_factory=list)
    first_conflicting_commit: Optional[CommitInfo] = None
    candidate_commits: list[CommitInfo] = field(default_factory=list)


class NotificationReason(str, Enum):
    FIRST_RUN = "first_run"
    BROKEN = "broken"
    RESOLVED = "resolved"
    HEALTH_CHANGED = "health_changed"
    CHAIN_CHANGED = "chain_changed"
