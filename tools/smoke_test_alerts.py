"""LIVE-23 operator smoke test for the ntfy alert channel.

Validates, against a real network and a real ntfy topic, that:
  (a) POST headers/body shape is correct for each severity (info/warning/critical)
  (b) the async sender survives bursts and inter-send gaps in a long-lived process
  (c) flush() drains the queue before the interpreter exits (the heartbeat script relies on this)
  (d) within-window dedup drops repeat events of the same key

Usage:
    python tools/smoke_test_alerts.py                       # reads ALERTS_NTFY_TOPIC_URL from cred.yml
    python tools/smoke_test_alerts.py --topic-url URL        # explicit override
    python tools/smoke_test_alerts.py --dry-run              # skip the HTTP; just exercise the code paths

After running, check your ntfy app - you should receive exactly 10 notifications
(3 severity, 5 burst, 1 flush, 1 dedup) and NOT an 11th (the dedup duplicate).

Exit code 0 on all-green; 1 if a validation gate failed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from trading_system.ops.alerts import Alert, NtfyAlertChannel


def _load_topic_from_cred() -> Optional[str]:
    path = os.path.join(_REPO_ROOT, "cred.yml")
    if not os.path.exists(path):
        return None
    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        url = data.get("ALERTS_NTFY_TOPIC_URL")
        return str(url).strip() if url else None
    except Exception as e:
        print(f"  ! could not read cred.yml: {e}", file=sys.stderr)
        return None


def _expect(label: str, actual, expected) -> bool:
    ok = actual == expected
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}: got={actual!r} expected={expected!r}")
    return ok


def run_smoke_test(topic_url: str) -> int:
    print(f"LIVE-23 smoke test - topic: {topic_url}\n")
    ch = NtfyAlertChannel(topic_url)
    all_green = True

    # ── (a) POST headers/body shape across all three severities ───────
    print("(a) severity headers - check your phone for 3 notifications with distinct priority/tags")
    all_green &= _expect("info send",     ch.send(Alert("smoke_a_info",     "info",     "RT smoke (info)",     "Hello from smoke test - info severity")), True)
    all_green &= _expect("warning send",  ch.send(Alert("smoke_a_warn",     "warning",  "RT smoke (warning)",  "Hello from smoke test - warning severity")), True)
    all_green &= _expect("critical send", ch.send(Alert("smoke_a_critical", "critical", "RT smoke (critical)", "Hello from smoke test - critical severity")), True)
    time.sleep(2.0)

    # ── (b) Async sender survives bursts + gaps in a long-lived process ─
    print("\n(b) burst-and-gap - 5 rapid sends + 3s sleep + confirm worker still alive")
    for i in range(5):
        all_green &= _expect(f"burst {i}", ch.send(Alert(f"smoke_b_{i}", "info", f"burst {i+1}/5", f"burst message {i}")), True)
    time.sleep(3.0)
    all_green &= _expect("worker thread alive after gap", ch._worker.is_alive(), True)

    # ── (c) flush() drains within-timeout - heartbeat script relies on this ─
    print("\n(c) flush() drain - enqueue 1 alert, immediately flush, assert drained")
    all_green &= _expect("flush-target send", ch.send(Alert("smoke_c_flush", "info", "flush test", "should drain cleanly")), True)
    drained = ch.flush(timeout=5.0)
    all_green &= _expect("flush returned True within 5s", drained, True)
    all_green &= _expect("queue empty after flush", ch._q.unfinished_tasks, 0)

    # ── (d) dedup drops the second same-event send within window ──────
    print("\n(d) dedup - same event twice within 60s; second should drop silently")
    first = ch.send(Alert("smoke_d_dedup", "info", "dedup 1st - should arrive", "first send"))
    second = ch.send(Alert("smoke_d_dedup", "info", "dedup 2nd - should NOT arrive", "second send (dedup)"))
    all_green &= _expect("first dedup-event accepted", first, True)
    all_green &= _expect("second dedup-event dropped", second, False)
    ch.flush(timeout=3.0)

    print("\n──────────────────────────────────────────────────────────")
    if all_green:
        print("OK - all validations passed in-process.")
        print("Now confirm on your phone: expected EXACTLY 10 notifications")
        print("  (3 severity + 5 burst + 1 flush + 1 dedup, NO dedup duplicate).")
        return 0
    print("FAIL - at least one validation gate returned the wrong result.")
    print("Check the [FAIL] lines above; dedup-window or network issues are common causes.")
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LIVE-23 ntfy smoke test")
    parser.add_argument(
        "--topic-url", default=None,
        help="ntfy topic URL (e.g. https://ntfy.sh/your-topic). "
             "Defaults to ALERTS_NTFY_TOPIC_URL from cred.yml.",
    )
    args = parser.parse_args(argv)

    url = args.topic_url or _load_topic_from_cred()
    if not url:
        print("ERROR: no topic URL - pass --topic-url or set ALERTS_NTFY_TOPIC_URL in cred.yml", file=sys.stderr)
        return 2

    return run_smoke_test(url)


if __name__ == "__main__":
    sys.exit(main())
