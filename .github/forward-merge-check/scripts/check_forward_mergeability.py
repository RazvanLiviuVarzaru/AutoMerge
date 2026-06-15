#!/usr/bin/env python3

import shutil
import sys
import tempfile
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[1]
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from checker.args import parse_args
from checker.config import load_base_branch, load_branches
from checker.git_ops import ensure_branch_refs
from checker.modes import run_chain_health_mode, run_pr_mode
from checker.output import print_summary, write_outputs
from checker.state import (
    current_state,
    load_previous_state,
    load_previous_state_file,
    notification_reasons,
)


def main() -> int:
    args = parse_args()
    scratch_root = Path(tempfile.mkdtemp(prefix="forward-mergeability-"))

    try:
        repo = args.repo.resolve()
        branches = load_branches(args)
        base_branch = load_base_branch(args, branches)

        if args.print_branches:
            print("\n".join(branches))
            return 0

        if args.print_base_branch:
            print(base_branch)
            return 0

        if not args.mode:
            raise ValueError("--mode is required unless using --print-branches or --print-base-branch.")

        print("Configured branch chain:")
        for branch in branches:
            print(f"  - {branch}")

        print()
        print(f"Mode: {args.mode}")
        print(f"Base branch: {base_branch}")

        ensure_branch_refs(repo, branches)

        if args.mode == "pr":
            if not args.pr_ref:
                raise ValueError("--pr-ref is required in PR mode.")
            results = run_pr_mode(
                repo,
                branches,
                base_branch,
                args.pr_ref,
                scratch_root,
            )
        else:
            results = run_chain_health_mode(repo, branches, base_branch, scratch_root)

        print_summary(results)

        if args.mode == "chain-health":
            state = current_state(repo, branches, base_branch, results)
            previous_state = load_previous_state_file(args.state_input_file)
            if previous_state is None:
                previous_state = load_previous_state(args.state_input)
            reasons = notification_reasons(previous_state, state)
            write_outputs(args, state, reasons)

            if reasons:
                print()
                print("Notification decision: send")
                print("Reasons: " + ", ".join(reason.value for reason in reasons))
            else:
                print()
                print("Notification decision: suppress; no tracked state change.")

        return 1 if any(result.status == "conflict" for result in results) else 0

    except Exception as exc:
        print()
        print("ERROR")
        print("-" * 72)
        print(str(exc))
        return 2

    finally:
        if args.keep_worktrees:
            print()
            print(f"Temporary worktree root kept at: {scratch_root}")
        else:
            shutil.rmtree(scratch_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
