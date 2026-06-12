import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))

from checker import config as config_mod
from checker import git_ops, modes, output, state as state_mod
from checker.models import NotificationReason


def load_script(name: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check = types.SimpleNamespace(
    NotificationReason=NotificationReason,
    branch_ref=git_ops.branch_ref,
    compact_state=state_mod.compact_state,
    current_state=state_mod.current_state,
    ensure_branch_refs=git_ops.ensure_branch_refs,
    load_branches=config_mod.load_branches,
    load_config=config_mod.load_config,
    load_previous_state_file=state_mod.load_previous_state_file,
    notification_reasons=state_mod.notification_reasons,
    run_chain_health_mode=modes.run_chain_health_mode,
    run_pr_mode=modes.run_pr_mode,
    write_outputs=output.write_outputs,
)
send = load_script("send_chain_notification")


def quietly(func, *args, **kwargs):
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return func(*args, **kwargs)


def run(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout.strip()


def git(repo: Path, *args: str) -> str:
    return run(["git", *args], repo)


class GitRepo:
    def __init__(self, path: Path):
        self.path = path
        run(["git", "init", "--initial-branch=master", str(path)], Path.cwd())
        git(path, "config", "user.name", "Tester")
        git(path, "config", "user.email", "tester@example.invalid")

    def write(self, relative: str, content: str) -> None:
        target = self.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, message: str) -> str:
        git(self.path, "add", ".")
        git(self.path, "commit", "-m", message)
        return git(self.path, "rev-parse", "HEAD")

    def checkout(self, branch: str, start: str | None = None) -> None:
        args = ["checkout", "-B", branch]
        if start:
            args.append(start)
        git(self.path, *args)

    def expose_origin_ref(self, branch: str) -> str:
        git(self.path, "update-ref", f"refs/remotes/origin/{branch}", branch)
        return git(self.path, "rev-parse", branch)


def make_clean_chain(root: Path, expose_origin: bool = True) -> GitRepo:
    repo = GitRepo(root / "repo")
    repo.write("file1.txt", "base\n")
    repo.write("file2.txt", "base\n")
    repo.write("file3.txt", "base\n")
    repo.commit("base")

    repo.checkout("old", "master")
    repo.write("file1.txt", "old\n")
    repo.commit("old file1")

    repo.checkout("next", "master")
    repo.write("file2.txt", "next\n")
    repo.commit("next file2")

    repo.checkout("main", "master")
    repo.write("file3.txt", "main\n")
    repo.commit("main file3")

    if expose_origin:
        for branch in ["old", "next", "main"]:
            repo.expose_origin_ref(branch)

    return repo


def make_conflicting_chain(root: Path) -> tuple[GitRepo, str]:
    repo = GitRepo(root / "repo")
    repo.write("file.txt", "base\n")
    repo.commit("base")

    repo.checkout("old", "master")
    repo.write("file.txt", "old\n")
    old_sha = repo.commit("old changes line")

    repo.checkout("next", "master")
    repo.write("file.txt", "next\n")
    repo.commit("next changes line")

    repo.checkout("main", "next")

    for branch in ["old", "next", "main"]:
        repo.expose_origin_ref(branch)

    return repo, old_sha


def make_downstream_broken_chain(root: Path) -> GitRepo:
    repo = GitRepo(root / "repo")
    repo.write("old-only.txt", "base\n")
    repo.write("mid-only.txt", "base\n")
    repo.write("shared-with-later.txt", "base\n")
    repo.write("pr-only.txt", "base\n")
    repo.commit("base")

    repo.checkout("old", "master")
    repo.write("old-only.txt", "old\n")
    repo.commit("old changes")

    repo.checkout("mid", "master")
    repo.write("mid-only.txt", "mid\n")
    repo.commit("mid changes")

    repo.checkout("next", "master")
    repo.write("shared-with-later.txt", "next\n")
    repo.commit("next changes")

    repo.checkout("later", "master")
    repo.write("shared-with-later.txt", "later\n")
    repo.commit("later changes")

    repo.checkout("pr", "mid")
    repo.write("pr-only.txt", "pr\n")
    repo.commit("pr changes")

    for branch in ["old", "mid", "next", "later"]:
        repo.expose_origin_ref(branch)

    return repo


class ConfigTests(unittest.TestCase):
    def test_loads_chain_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "chain.yml"
            config.write_text(
                """
base_branch: "old"
branches:
  - "old"
  - "next"
  - "main"
notifications:
  slack:
    enabled: false
  zulip:
    enabled: true
""".lstrip(),
                encoding="utf-8",
            )

            loaded = check.load_config(config)

        self.assertEqual(loaded["base_branch"], "old")
        self.assertEqual(loaded["branches"], ["old", "next", "main"])
        self.assertEqual(loaded["notifications"]["slack"]["enabled"], False)
        self.assertEqual(loaded["notifications"]["zulip"]["enabled"], True)

    def test_rejects_duplicate_branch_list(self):
        args = argparse.Namespace(
            branches=["old", "old"],
            branch_file=None,
            config_file=ROOT / "repositories" / "mariadb-server.yml",
        )

        with self.assertRaisesRegex(ValueError, "duplicates"):
            check.load_branches(args)


class MergeCheckTests(unittest.TestCase):
    def test_missing_branch_refs_reports_fetch_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_clean_chain(Path(tmp))

            with self.assertRaisesRegex(ValueError, "refs/remotes/origin/missing"):
                check.ensure_branch_refs(repo.path, ["old", "missing"])

    def test_chain_health_uses_local_branches_without_origin_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_clean_chain(Path(tmp), expose_origin=False)
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()

            check.ensure_branch_refs(repo.path, ["old", "next", "main"])
            results = quietly(
                check.run_chain_health_mode,
                repo.path,
                ["old", "next", "main"],
                "old",
                scratch,
            )
            old_ref = check.branch_ref(repo.path, "old")

        self.assertEqual([result.status for result in results], ["merge_ok", "merge_ok"])
        self.assertEqual(old_ref, "refs/heads/old")

    def test_chain_health_clean_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_clean_chain(Path(tmp))
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()

            results = quietly(
                check.run_chain_health_mode,
                repo.path,
                ["old", "next", "main"],
                "old",
                scratch,
            )

        self.assertEqual([result.status for result in results], ["merge_ok", "merge_ok"])

    def test_chain_health_conflict_reports_likely_commit_and_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, old_sha = make_conflicting_chain(Path(tmp))
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()

            results = quietly(
                check.run_chain_health_mode,
                repo.path,
                ["old", "next", "main"],
                "old",
                scratch,
            )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].status, "conflict")
        self.assertEqual(results[0].conflicted_files, ["file.txt"])
        self.assertIsNotNone(results[0].first_conflicting_commit)
        self.assertEqual(results[0].first_conflicting_commit.sha, old_sha)
        self.assertEqual(results[1].source_label, "next")
        self.assertEqual(results[1].target, "main")
        self.assertEqual(results[1].status, "nothing_to_merge")

    def test_pr_mode_stops_before_preexisting_downstream_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_downstream_broken_chain(Path(tmp))
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()

            results = quietly(
                check.run_pr_mode,
                repo.path,
                ["old", "mid", "next", "later"],
                "mid",
                "pr",
                scratch,
            )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].target, "next")
        self.assertEqual(results[0].status, "merge_ok")

    def test_pr_mode_skips_unconfigured_base_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_clean_chain(Path(tmp))
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()

            results = quietly(check.run_pr_mode, repo.path, ["old", "next"], "other", "HEAD", scratch)

        self.assertEqual(results, [])


class StateTests(unittest.TestCase):
    def test_first_broken_run_notifies_and_duplicate_is_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _old_sha = make_conflicting_chain(Path(tmp))
            scratch = Path(tmp) / "scratch"
            scratch.mkdir()
            results = quietly(
                check.run_chain_health_mode,
                repo.path,
                ["old", "next", "main"],
                "old",
                scratch,
            )
            state = check.current_state(repo.path, ["old", "next", "main"], "old", results)

        self.assertEqual(
            check.notification_reasons(None, state),
            [check.NotificationReason.FIRST_RUN],
        )
        self.assertEqual(check.notification_reasons(check.compact_state(state), state), [])

    def test_load_previous_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text('{"status": "healthy"}', encoding="utf-8")

            state = check.load_previous_state_file(state_file)

        self.assertEqual(state, {"status": "healthy"})
        self.assertIsNone(check.load_previous_state_file(Path(tmp) / "missing.json"))

    def test_resolution_and_health_change_reasons(self):
        previous_broken = {
            "status": "broken",
            "config_fingerprint": "cfg",
            "chain_fingerprint": "chain-a",
            "health_fingerprint": "health-a",
        }
        resolved = {
            "status": "healthy",
            "config_fingerprint": "cfg",
            "chain_fingerprint": "chain-b",
            "health_fingerprint": "health-b",
        }
        changed_broken = {
            "status": "broken",
            "config_fingerprint": "cfg",
            "chain_fingerprint": "chain-b",
            "health_fingerprint": "health-b",
        }

        self.assertIn(
            check.NotificationReason.RESOLVED,
            check.notification_reasons(previous_broken, resolved),
        )
        self.assertIn(
            check.NotificationReason.HEALTH_CHANGED,
            check.notification_reasons(previous_broken, changed_broken),
        )
        self.assertIn(
            check.NotificationReason.CHAIN_CHANGED,
            check.notification_reasons(previous_broken, changed_broken),
        )

    def test_write_outputs_writes_compact_state_notification_and_github_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_output = Path(tmp) / "state.json"
            notification_output = Path(tmp) / "notification.json"
            github_output = Path(tmp) / "github-output.txt"
            args = argparse.Namespace(
                state_output=state_output,
                notification_output=notification_output,
                github_output=github_output,
            )
            state = {
                "version": 1,
                "checked_at": "2026-05-11T00:00:00+00:00",
                "status": "broken",
                "base_branch": "old",
                "branches": ["old", "next", "main"],
                "branch_heads": {"old": "a", "next": "b", "main": "c"},
                "config_fingerprint": "cfg",
                "chain_fingerprint": "chain",
                "health_fingerprint": "health",
                "results": [
                    {
                        "source_label": "old",
                        "source_ref": "refs/remotes/origin/old",
                        "target": "next",
                        "status": "conflict",
                        "message": "blocked",
                        "conflicted_files": ["file.txt"],
                        "first_conflicting_commit": {
                            "sha": "abc",
                            "author": "Tester <tester@example.invalid>",
                            "subject": "break it",
                        },
                        "candidate_commits": [],
                    },
                    {
                        "source_label": "next",
                        "source_ref": "refs/remotes/origin/next",
                        "target": "main",
                        "status": "conflict",
                        "message": "blocked again",
                        "conflicted_files": ["other.txt", "src/deep/path.cc"],
                        "first_conflicting_commit": {
                            "sha": "def456789012",
                            "author": "Second Tester <second@example.invalid>",
                            "subject": "break it again",
                        },
                        "candidate_commits": [],
                    }
                ],
            }

            with mock.patch.dict(
                os.environ,
                {
                    "GITHUB_REPOSITORY": "owner/repo",
                    "GITHUB_RUN_ID": "12345",
                    "GITHUB_SERVER_URL": "https://github.example",
                },
                clear=False,
            ):
                check.write_outputs(args, state, [check.NotificationReason.BROKEN])
            compact = json.loads(state_output.read_text(encoding="utf-8"))
            notification = json.loads(notification_output.read_text(encoding="utf-8"))
            github_lines = github_output.read_text(encoding="utf-8").splitlines()
            slack_text = notification["slack_text"]
            zulip_text = notification["zulip_text"]

        self.assertNotIn("candidate_commits", compact["results"][0])
        self.assertTrue(notification["notify"])
        self.assertEqual(notification["text"], slack_text)
        self.assertTrue(slack_text.startswith("*Forward Merge Checker*\n\n- *Status:* 🚨 blocked"))
        self.assertIn("- *Repository:* `owner/repo`", slack_text)
        self.assertIn("- *Reason:* chain became blocked", slack_text)
        self.assertIn("- *Blocked edges (2):* 1. `old` -> `next`, 2. `next` -> `main`", slack_text)
        self.assertIn("- *Chain:* `old` -> `next` -> `main`", slack_text)
        self.assertIn("- *GitHub Actions run:* <https://github.example/owner/repo/actions/runs/12345|open run>", slack_text)
        self.assertIn("- *GitHub Actions run:* [open run](https://github.example/owner/repo/actions/runs/12345)", zulip_text)
        self.assertIn("\n\n*Checked edges:*", slack_text)
        self.assertIn("1. ❌ `old` -> `next`: conflict", slack_text)
        self.assertIn("2. ❌ `next` -> `main`: conflict", slack_text)
        self.assertIn("\n\n*Conflict details:*\n\n1. *Edge:* `old` -> `next`", slack_text)
        self.assertIn("\n\n2. *Edge:* `next` -> `main`", slack_text)
        self.assertIn("<https://github.example/owner/repo/commit/abc|abc>", slack_text)
        self.assertIn("[abc](https://github.example/owner/repo/commit/abc)", zulip_text)
        self.assertIn(
            "<https://github.example/owner/repo/commit/def456789012|def456789012>",
            slack_text,
        )
        self.assertIn(
            "[def456789012](https://github.example/owner/repo/commit/def456789012)",
            zulip_text,
        )
        self.assertIn("*Conflicted files (1):*\n```\n- file.txt\n```", slack_text)
        self.assertIn(
            "*Conflicted files (2):*\n```\n- other.txt\n- src/deep/path.cc\n```",
            slack_text,
        )
        self.assertNotIn("Base branch:", slack_text)
        self.assertNotIn("Health fingerprint:", slack_text)
        self.assertNotIn("Chain fingerprint:", slack_text)
        self.assertIn("should_notify=true", github_lines)
        self.assertIn("status=broken", github_lines)

    def test_write_outputs_uses_target_repository_override_for_notification_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            notification_output = Path(tmp) / "notification.json"
            args = argparse.Namespace(
                state_output=None,
                notification_output=notification_output,
                github_output=None,
            )
            state = {
                "version": 1,
                "checked_at": "2026-05-11T00:00:00+00:00",
                "status": "broken",
                "base_branch": "old",
                "branches": ["old", "next"],
                "branch_heads": {"old": "a", "next": "b"},
                "config_fingerprint": "cfg",
                "chain_fingerprint": "chain",
                "health_fingerprint": "health",
                "results": [
                    {
                        "source_label": "old",
                        "source_ref": "refs/remotes/origin/old",
                        "target": "next",
                        "status": "conflict",
                        "message": "blocked",
                        "conflicted_files": ["file.txt"],
                        "first_conflicting_commit": {
                            "sha": "abc123",
                            "author": "Tester <tester@example.invalid>",
                            "subject": "break it",
                        },
                        "candidate_commits": [],
                    }
                ],
            }

            with mock.patch.dict(
                os.environ,
                {
                    "FORWARD_MERGE_TARGET_REPOSITORY": "target/repo",
                    "GITHUB_REPOSITORY": "owner/workflow-runner",
                    "GITHUB_RUN_ID": "12345",
                    "GITHUB_SERVER_URL": "https://github.example",
                },
                clear=False,
            ):
                check.write_outputs(args, state, [check.NotificationReason.BROKEN])
            notification = json.loads(notification_output.read_text(encoding="utf-8"))
            slack_text = notification["slack_text"]
            zulip_text = notification["zulip_text"]

        self.assertIn("- *Repository:* `target/repo`", slack_text)
        self.assertIn("<https://github.example/target/repo/commit/abc123|abc123>", slack_text)
        self.assertIn("[abc123](https://github.example/target/repo/commit/abc123)", zulip_text)
        self.assertIn(
            "- *GitHub Actions run:* <https://github.example/owner/workflow-runner/actions/runs/12345|open run>",
            slack_text,
        )


class NotificationScriptTests(unittest.TestCase):
    def test_zulip_webhook_uses_slack_compatible_text_payload(self):
        with mock.patch.object(send, "post_json") as post_json:
            send.post_zulip_webhook("https://zulip.example", "hello zulip")

        post_json.assert_called_once_with("https://zulip.example", {"text": "hello zulip"})

    def test_suppressed_notification_does_not_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            notification = Path(tmp) / "notification.json"
            notification.write_text('{"notify": false}', encoding="utf-8")
            config = Path(tmp) / "config.yml"
            config.write_text("branches:\n  - old\n  - next\n", encoding="utf-8")

            with mock.patch.object(send, "post_slack_webhook") as slack_post:
                with mock.patch.object(sys, "argv", ["send", "--notification", str(notification), "--config-file", str(config)]):
                    code = quietly(send.main)

        self.assertEqual(code, 0)
        slack_post.assert_not_called()

    def test_posts_to_enabled_webhooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            notification = Path(tmp) / "notification.json"
            notification.write_text(
                '{"notify": true, "text": "fallback", "slack_text": "hello slack", "zulip_text": "hello zulip"}',
                encoding="utf-8",
            )
            config = Path(tmp) / "config.yml"
            config.write_text(
                """
branches:
  - old
  - next
notifications:
  slack:
    enabled: true
  zulip:
    enabled: true
""".lstrip(),
                encoding="utf-8",
            )

            with mock.patch.dict(
                os.environ,
                {
                    "SLACK_WEBHOOK_URL": "https://slack.example",
                    "ZULIP_WEBHOOK_URL": "https://zulip.example",
                },
                clear=False,
            ):
                with mock.patch.object(send, "post_slack_webhook") as slack_post:
                    with mock.patch.object(send, "post_zulip_webhook") as zulip_post:
                        with mock.patch.object(sys, "argv", ["send", "--notification", str(notification), "--config-file", str(config)]):
                            code = quietly(send.main)

        self.assertEqual(code, 0)
        slack_post.assert_called_once_with("https://slack.example", "hello slack")
        zulip_post.assert_called_once_with("https://zulip.example", "hello zulip")


if __name__ == "__main__":
    unittest.main()
