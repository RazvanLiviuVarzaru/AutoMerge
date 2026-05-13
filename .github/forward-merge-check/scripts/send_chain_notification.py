#!/usr/bin/env python3

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[1]
if str(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOL_ROOT))

from checker.config import load_config


def post_json(url: str, payload: dict) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status >= 300:
            raise RuntimeError(f"HTTP {response.status} from {url}")


def post_zulip_webhook(url: str, text: str) -> None:
    post_json(url, {"text": text})


def post_slack_webhook(url: str, text: str) -> None:
    post_json(url, {"text": text})


def main() -> int:
    parser = argparse.ArgumentParser(description="Send forward-merge chain notifications.")
    parser.add_argument("--notification", type=Path, required=True)
    parser.add_argument(
        "--config-file",
        type=Path,
        default=Path(".github/forward-merge-check/repositories/mariadb-server.yml"),
    )
    args = parser.parse_args()

    payload = json.loads(args.notification.read_text(encoding="utf-8"))
    config = load_config(args.config_file)
    notifications = config.get("notifications") or {}
    slack_cfg = notifications.get("slack") if isinstance(notifications, dict) else {}
    zulip_cfg = notifications.get("zulip") if isinstance(notifications, dict) else {}
    slack_enabled = not isinstance(slack_cfg, dict) or slack_cfg.get("enabled", True)
    zulip_enabled = not isinstance(zulip_cfg, dict) or zulip_cfg.get("enabled", True)

    if not payload.get("notify"):
        print("Notification suppressed by state comparison.")
        return 0

    slack_text = payload.get("slack_text") or payload.get("text") or "Forward merge chain changed."
    zulip_text = payload.get("zulip_text") or payload.get("text") or "Forward merge chain changed."
    sent = False

    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL") if slack_enabled else None
    zulip_webhook = os.environ.get("ZULIP_WEBHOOK_URL") if zulip_enabled else None

    try:
        if slack_webhook:
            post_slack_webhook(slack_webhook, slack_text)
            print("Sent Slack notification.")
            sent = True

        if zulip_webhook:
            post_zulip_webhook(zulip_webhook, zulip_text)
            print("Sent Zulip notification.")
            sent = True

    except (urllib.error.URLError, RuntimeError) as exc:
        print(f"Notification delivery failed: {exc}", file=sys.stderr)
        return 1

    if not sent:
        print("No notification webhook secrets configured.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
