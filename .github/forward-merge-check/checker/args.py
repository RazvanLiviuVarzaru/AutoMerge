import argparse
import os
from pathlib import Path

from .config import DEFAULT_CONFIG_FILE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether changes can be forward-merged through a branch chain."
    )
    parser.add_argument("--mode", choices=["pr", "chain-health"])
    parser.add_argument("--base-branch")
    parser.add_argument("--pr-ref", help="PR head ref. Required for --mode pr.")
    parser.add_argument(
        "--config-file",
        type=Path,
        default=DEFAULT_CONFIG_FILE,
        help="Forward-merge chain YAML config.",
    )
    parser.add_argument(
        "--branch-file",
        type=Path,
        default=None,
        help="Legacy ordered branch-chain file, one branch per line.",
    )
    parser.add_argument(
        "--branches",
        nargs="+",
        help="Ordered branch chain. Overrides --branch-file and FORWARD_MERGE_BRANCHES.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path.cwd(),
        help="Repository path. Defaults to the current directory.",
    )
    parser.add_argument(
        "--keep-worktrees",
        action="store_true",
        help="Keep temporary worktree root for debugging.",
    )
    parser.add_argument(
        "--state-input",
        default="",
        help="Previous state JSON.",
    )
    parser.add_argument("--state-input-file", type=Path, help="Read previous state JSON from this file.")
    parser.add_argument("--state-output", type=Path, help="Write current state JSON here.")
    parser.add_argument("--notification-output", type=Path, help="Write notification decision JSON here.")
    parser.add_argument("--github-output", type=Path, default=Path(os.environ["GITHUB_OUTPUT"]) if os.environ.get("GITHUB_OUTPUT") else None)
    parser.add_argument("--print-branches", action="store_true", help="Print configured branches and exit.")
    parser.add_argument("--print-base-branch", action="store_true", help="Print configured base branch and exit.")
    return parser.parse_args()
