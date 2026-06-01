#!/usr/bin/env python3
"""BMO worker —— 跑在 bmo 上，輪詢後端佇列，用 Claude Code headless 執行任務。

每個新任務會開一個獨立 git 分支（bmo/job-<id>-<時間>）再執行；review 後續任務
（帶 branch）則沿用同一分支繼續修改。跑完把變更提交在分支、切回原分支，分支保留
供 review，並把 branch / diff 回報後端。

設定（環境變數）：
  BMO_API_BASE      後端網址，預設 https://lxc.tail92862c.ts.net
  BMO_WORKER_TOKEN  與後端相同的 worker token
  BMO_WORKSPACE     Claude 執行的工作目錄（git repo），預設 ~/project-manager
  BMO_POLL_SECONDS  輪詢間隔秒數，預設 10
  BMO_TIMEOUT       單一任務逾時秒數，預設 600
  BMO_CLAUDE_BIN    claude 執行檔路徑，預設用 PATH 中的 claude
  BMO_CLAUDE_ARGS   附加在 claude 後的旗標（空白分隔）

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

API_BASE = os.getenv("BMO_API_BASE", "https://lxc.tail92862c.ts.net").rstrip("/")
TOKEN = os.getenv("BMO_WORKER_TOKEN", "")
WORKSPACE = os.path.expanduser(os.getenv("BMO_WORKSPACE", "~/project-manager"))
POLL = int(os.getenv("BMO_POLL_SECONDS", "10"))
TIMEOUT = int(os.getenv("BMO_TIMEOUT", "600"))
CLAUDE_BIN = os.getenv("BMO_CLAUDE_BIN", "claude")
CLAUDE_ARGS = shlex.split(os.getenv("BMO_CLAUDE_ARGS", ""))

HEADERS = {"X-Worker-Token": TOKEN} if TOKEN else {}


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def git(*args) -> str:
    p = subprocess.run(["git", "-C", WORKSPACE, *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip() or p.stdout.strip()}")
    return p.stdout.strip()


def is_git_repo() -> bool:
    return os.path.isdir(os.path.join(WORKSPACE, ".git"))


def run_claude(prompt: str) -> tuple[str | None, str | None]:
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


def run_job_on_branch(prompt: str, job_id: int, existing_branch: str | None):
    """回傳 (result, error, branch, diff)。

    existing_branch 有值＝review 後續任務，沿用同分支；否則開新分支。
    """
    if not is_git_repo():
        result, error = run_claude(prompt)
        return result, error, None, None

    try:
        orig = git("rev-parse", "--abbrev-ref", "HEAD")
    except Exception as e:
        result, error = run_claude(prompt)
        return result, error, None, f"取得分支失敗：{e}"

    # 新任務開分支；後續任務沿用既有分支
    if existing_branch:
        branch = existing_branch
        try:
            git("checkout", branch)
        except Exception as e:
            return None, f"切到既有分支 {branch} 失敗：{e}", branch, None
        base = orig  # diff 相對於主線
    else:
        branch = f"bmo/job-{job_id}-{int(time.time())}"
        try:
            git("checkout", "-b", branch)
        except Exception as e:
            return None, f"建立分支失敗：{e}", None, None
        base = orig

    result, error = run_claude(prompt)

    diff = None
    info = ""
    try:
        changed = git("status", "--porcelain")
        if changed.strip():
            git("add", "-A")
            git("commit", "-m", f"BMO job #{job_id}", "-m", prompt[:200])
        # 不論這輪有無新變更，回報分支相對主線的完整 diff
        diff = git("diff", f"{base}...{branch}")
        if changed.strip():
            stat = git("diff", "--stat", f"{base}...{branch}")
            info = f"\n\n🌿 變更已提交於分支 `{branch}`（已切回 {orig} 待 review）\n{stat}"
        else:
            info = f"\n\n🌿 這一輪未產生新的檔案變更（分支 `{branch}` 維持原樣）"
        git("checkout", orig)
    except Exception as e:
        try:
            git("checkout", orig)
        except Exception:
            pass
        info = f"\n\n⚠️ 分支處理出錯：{e}"

    if error:
        return None, (error + info), branch, diff
    return (result or "") + info, None, branch, diff


def deploy_branch(branch: str):
    """把 branch 合併進 main、push，並 ssh 到 LXC 部署。回傳 (result, error, branch, None)。"""
    if not is_git_repo():
        return None, "workspace 不是 git repo", branch, None
    try:
        git("checkout", "main")
        git("fetch", "origin")
        try:
            git("merge", "--ff-only", "origin/main")  # 先跟上遠端
        except Exception:
            pass
    except Exception as e:
        return None, f"切到 main 失敗：{e}", branch, None
    # 合併分支
    try:
        git("merge", "--no-ff", "--no-edit", branch)
    except Exception as e:
        try:
            git("merge", "--abort")
        except Exception:
            pass
        return None, f"合併失敗（可能有衝突，已 abort）：{e}", branch, None
    # push
    p = subprocess.run(["git", "-C", WORKSPACE, "push", "origin", "main"], capture_output=True, text=True)
    if p.returncode != 0:
        return None, f"push 失敗：{(p.stderr or p.stdout)[:200]}", branch, None
    # 部署到 LXC
    deploy_cmd = ("cd ~/project-manager && git pull --ff-only && "
                  "venv/bin/alembic upgrade head && systemctl restart project-manager && "
                  "sleep 2 && systemctl is-active project-manager")
    try:
        p = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "lxc", deploy_cmd],
                           capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:
        return None, "LXC 部署逾時", branch, None
    if p.returncode != 0:
        return None, f"LXC 部署失敗：{(p.stderr or p.stdout)[:300]}", branch, None
    # 清掉本機已合併分支
    try:
        git("branch", "-d", branch)
    except Exception:
        pass
    return f"🚀 已合併 `{branch}` 進 main 並部署上線\n{p.stdout.strip()[-200:]}", None, branch, None


def process(job):
    jid = job["id"]
    r = requests.post(f"{API_BASE}/api/bmo/jobs/{jid}/claim", headers=HEADERS, timeout=15)
    if r.status_code != 200:
        log(f"claim #{jid} 失敗：{r.status_code} {r.text[:120]}")
        return
    log(f"▶ 執行 #{jid}（{job.get('kind','task')}）：{job['prompt'][:50]!r}")
    if job.get("kind") == "deploy":
        result, error, branch, diff = deploy_branch(job.get("branch"))
    else:
        result, error, branch, diff = run_job_on_branch(job["prompt"], jid, job.get("branch"))
    requests.post(f"{API_BASE}/api/bmo/jobs/{jid}/complete", headers=HEADERS,
                  json={"result": result, "error": error, "branch": branch, "diff": diff}, timeout=30)
    log(f"✓ 完成 #{jid}" + (f"（錯誤：{error[:60]}）" if error else f"（分支 {branch}）"))


def main():
    if not TOKEN:
        log("⚠️ 未設定 BMO_WORKER_TOKEN，僅適合本機測試")
    log(f"BMO worker 啟動：API={API_BASE} workspace={WORKSPACE} poll={POLL}s")
    while True:
        try:
            r = requests.get(f"{API_BASE}/api/bmo/jobs/queued", headers=HEADERS, timeout=15)
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
