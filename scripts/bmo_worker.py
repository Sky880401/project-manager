#!/usr/bin/env python3
"""BMO worker —— 跑在 bmo 上，輪詢後端佇列，用 Claude Code headless 執行任務。

每個任務會先開一個獨立 git 分支（bmo/job-<id>-<時間>）再執行，跑完把變更
提交在該分支、再切回原分支，分支保留供你 review（git diff / 合併）。

設定（環境變數）：
  HERMES_API_BASE      後端網址，預設 https://lxc.tail92862c.ts.net
  HERMES_WORKER_TOKEN  與後端相同的 worker token
  HERMES_WORKSPACE     Claude 執行的工作目錄（git repo），預設 ~/project-manager
  HERMES_POLL_SECONDS  輪詢間隔秒數，預設 10
  HERMES_TIMEOUT       單一任務逾時秒數，預設 600
  HERMES_CLAUDE_BIN    claude 執行檔路徑，預設用 PATH 中的 claude
  HERMES_CLAUDE_ARGS   附加在 claude 後的旗標（空白分隔）

執行：
  python3 scripts/bmo_worker.py
"""
import os
import sys
import time
import shlex
import subprocess
from pathlib import Path

import requests

API_BASE = os.getenv("HERMES_API_BASE", "https://lxc.tail92862c.ts.net").rstrip("/")
TOKEN = os.getenv("HERMES_WORKER_TOKEN", "")
WORKSPACE = os.path.expanduser(os.getenv("HERMES_WORKSPACE", "~/project-manager"))
POLL = int(os.getenv("HERMES_POLL_SECONDS", "10"))
TIMEOUT = int(os.getenv("HERMES_TIMEOUT", "600"))
CLAUDE_BIN = os.getenv("HERMES_CLAUDE_BIN", "claude")
CLAUDE_ARGS = shlex.split(os.getenv("HERMES_CLAUDE_ARGS", ""))

HEADERS = {"X-Worker-Token": TOKEN} if TOKEN else {}


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def git(*args) -> str:
    """在 workspace 跑 git，回傳 stdout（失敗丟例外）。"""
    p = subprocess.run(["git", "-C", WORKSPACE, *args],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def is_git_repo() -> bool:
    return os.path.isdir(os.path.join(WORKSPACE, ".git"))


def run_claude(prompt: str) -> tuple[str | None, str | None]:
    """回傳 (result, error)。"""
    Path(WORKSPACE).mkdir(parents=True, exist_ok=True)
    cmd = [CLAUDE_BIN, *CLAUDE_ARGS, "-p", prompt]
    try:
        proc = subprocess.run(cmd, cwd=WORKSPACE, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"逾時（>{TIMEOUT}s）"
    except FileNotFoundError:
        return None, f"找不到 claude 執行檔：{CLAUDE_BIN}"
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return None, f"claude exit {proc.returncode}\n{err or out}"
    return (out or "(無輸出)"), None


def run_job_on_branch(prompt: str, job_id: int) -> tuple[str | None, str | None]:
    """先開獨立分支再執行；跑完提交變更、切回原分支，分支保留待 review。"""
    if not is_git_repo():
        return run_claude(prompt)

    branch = f"bmo/job-{job_id}-{int(time.time())}"
    try:
        orig = git("rev-parse", "--abbrev-ref", "HEAD")
    except Exception as e:
        log(f"取得目前分支失敗，改用無分支模式：{e}")
        return run_claude(prompt)

    try:
        git("checkout", "-b", branch)
    except Exception as e:
        return None, f"建立分支失敗：{e}"

    result, error = run_claude(prompt)

    info = ""
    try:
        changed = git("status", "--porcelain")
        if changed.strip():
            git("add", "-A")
            git("commit", "-m", f"BMO job #{job_id}", "-m", prompt[:200])
            stat = git("diff", "--stat", f"{orig}..{branch}")
            info = f"\n\n🌿 變更已提交於分支 `{branch}`（已切回 {orig} 待 review）\n{stat}"
            git("checkout", orig)
        else:
            git("checkout", orig)
            git("branch", "-D", branch)
            info = "\n\n🌿 任務未產生檔案變更（未保留分支）"
    except Exception as e:
        try:
            git("checkout", orig)
        except Exception:
            pass
        info = f"\n\n⚠️ 分支處理出錯：{e}"

    if error:
        return None, (error + info)
    return (result or "") + info, None


def process(job):
    jid = job["id"]
    r = requests.post(f"{API_BASE}/api/hermes/jobs/{jid}/claim", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        log(f"claim #{jid} 失敗：{r.status_code} {r.text[:120]}")
        return
    log(f"▶ 執行 #{jid}：{job['prompt'][:60]!r}")
    result, error = run_job_on_branch(job["prompt"], jid)
    requests.post(f"{API_BASE}/api/hermes/jobs/{jid}/complete", headers=HEADERS,
                  json={"result": result, "error": error}, timeout=30)
    log(f"✓ 完成 #{jid}" + (f"（錯誤：{error[:60]}）" if error else ""))


def main():
    if not TOKEN:
        log("⚠️ 未設定 HERMES_WORKER_TOKEN，僅適合本機測試")
    log(f"BMO worker 啟動：API={API_BASE} workspace={WORKSPACE} poll={POLL}s")
    while True:
        try:
            r = requests.get(f"{API_BASE}/api/hermes/jobs/queued", headers=HEADERS, timeout=15)
            if r.status_code == 200:
                for job in r.json():
                    process(job)
            else:
                log(f"輪詢失敗：{r.status_code} {r.text[:120]}")
        except Exception as e:
            log(f"輪詢例外：{e}")
        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("worker 結束")
        sys.exit(0)
