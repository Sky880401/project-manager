#!/usr/bin/env python3
"""解析 Claude Code transcript，統計用量並回報到 LXC API。
用法：定期執行（cron）或手動跑。
"""
import json
import os
import glob
import httpx
from datetime import datetime, timezone, timedelta

API_URL = os.getenv("PM_API_URL", "https://lxc.tail92862c.ts.net")
PROJECTS_DIR = os.path.expanduser("~/.claude/projects")

now = datetime.now(timezone.utc)
window_start = now - timedelta(hours=5)
today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

w = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "messages": 0}
t = {"input": 0, "output": 0, "messages": 0}


def parse_ts(d):
    ts = d.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


for path in glob.glob(os.path.join(PROJECTS_DIR, "**", "*.jsonl"), recursive=True):
    try:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                msg = d.get("message")
                if not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not usage:
                    continue
                ts = parse_ts(d)
                if not ts:
                    continue

                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                cr = usage.get("cache_read_input_tokens", 0)
                cw = usage.get("cache_creation_input_tokens", 0)

                if ts >= window_start:
                    w["input"] += inp
                    w["output"] += out
                    w["cache_read"] += cr
                    w["cache_write"] += cw
                    w["messages"] += 1
                if ts >= today_start:
                    t["input"] += inp
                    t["output"] += out
                    t["messages"] += 1
    except Exception:
        continue

payload = {
    "window_5h_input": w["input"],
    "window_5h_output": w["output"],
    "window_5h_cache_read": w["cache_read"],
    "window_5h_cache_write": w["cache_write"],
    "window_5h_messages": w["messages"],
    "today_input": t["input"],
    "today_output": t["output"],
    "today_messages": t["messages"],
}

try:
    r = httpx.post(f"{API_URL}/api/claude/code-usage", json=payload, timeout=5)
    print(f"Reported: {r.status_code}")
    print(json.dumps(payload, indent=2))
except Exception as e:
    print(f"Failed to report: {e}")
