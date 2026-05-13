import argparse
import os
from pathlib import Path


DEFAULT_BRANCHES = ["10.6", "10.11", "11.4", "11.8", "12.3", "main"]
DEFAULT_CONFIG_FILE = Path(".github/forward-merge-check/repositories/mariadb-server.yml")
LEGACY_BRANCH_FILE = Path(".github/forward-merge-branches.txt")


def parse_scalar(value: str) -> object:
    value = value.strip()

    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "~"}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    return value


def read_simple_yaml(path: Path) -> dict:
    """
    Parse the tiny YAML subset used by repository config files.

    Supported forms:
      key: value
      parent:
        child: value
      list:
        - value
    """
    root: dict = {}
    stack: list[tuple[int, dict | list]] = [(-1, root)]
    pending_key: tuple[int, dict, str] | None = None

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        while stack and indent <= stack[-1][0]:
            stack.pop()

        if pending_key and indent > pending_key[0]:
            parent_indent, parent, key = pending_key
            container: dict | list = [] if stripped.startswith("- ") else {}
            parent[key] = container
            stack.append((parent_indent, container))
            pending_key = None

        parent = stack[-1][1]

        if stripped.startswith("- "):
            if not isinstance(parent, list):
                raise ValueError(f"{path}:{line_number}: list item outside a list")
            parent.append(parse_scalar(stripped[2:]))
            continue

        if ":" not in stripped:
            raise ValueError(f"{path}:{line_number}: expected 'key: value'")

        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip()

        if not isinstance(parent, dict):
            raise ValueError(f"{path}:{line_number}: mapping item outside a mapping")

        if value:
            parent[key] = parse_scalar(value)
            pending_key = None
        else:
            pending_key = (indent, parent, key)

    return root


def read_branch_config(path: Path) -> list[str]:
    branches: list[str] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        branches.append(line)

    if len(branches) < 2:
        raise ValueError(f"{path} must contain at least two branches.")

    if len(set(branches)) != len(branches):
        raise ValueError(f"{path} contains duplicate branches.")

    return branches


def load_config(path: Path) -> dict:
    if path.exists():
        cfg = read_simple_yaml(path)
        branches = cfg.get("branches")
        if not isinstance(branches, list) or not all(isinstance(branch, str) for branch in branches):
            raise ValueError(f"{path} must define a 'branches' list.")

        base_branch = cfg.get("base_branch") or branches[0]
        if not isinstance(base_branch, str):
            raise ValueError(f"{path} base_branch must be a string.")

        return {
            "branches": branches,
            "base_branch": base_branch,
            "notifications": cfg.get("notifications") or {},
        }

    if LEGACY_BRANCH_FILE.exists():
        branches = read_branch_config(LEGACY_BRANCH_FILE)
        return {
            "branches": branches,
            "base_branch": branches[0],
            "notifications": {},
        }

    return {
        "branches": DEFAULT_BRANCHES,
        "base_branch": DEFAULT_BRANCHES[0],
        "notifications": {},
    }


def load_branches(args: argparse.Namespace) -> list[str]:
    if args.branches:
        branches = args.branches
    elif args.branch_file and args.branch_file.exists():
        branches = read_branch_config(args.branch_file)
    elif os.environ.get("FORWARD_MERGE_BRANCHES"):
        branches = os.environ["FORWARD_MERGE_BRANCHES"].split()
    else:
        branches = load_config(args.config_file)["branches"]

    if len(branches) < 2:
        raise ValueError("At least two branches are required.")

    if len(set(branches)) != len(branches):
        raise ValueError("Branch list contains duplicates.")

    return branches


def load_base_branch(args: argparse.Namespace, branches: list[str]) -> str:
    if args.base_branch:
        return args.base_branch

    cfg = load_config(args.config_file)
    base_branch = cfg.get("base_branch") or branches[0]
    if not isinstance(base_branch, str):
        raise ValueError("Configured base_branch must be a string.")

    return base_branch
