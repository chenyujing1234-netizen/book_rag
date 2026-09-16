#!/usr/bin/env python3
"""把当前工程推送到 GitHub。

这台服务器 github.com:443 不可达（TLS 连接被中断），普通 git push 用不了，
但 api.github.com 是通的。所以这里绕开 git 传输层，直接用 Git Data API
构造 blob/tree/commit 再更新分支。

用法：
    export GITHUB_TOKEN=ghp_xxxx
    python3 push_to_github.py "提交说明"

推送的文件范围就是 git 跟踪的文件，所以 .gitignore 照常生效
（.env、backup/、*.log、书籍原文件都不会上传）。
"""
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = "chenyujing1234-netizen/book_rag"
BRANCH = "main"
API = f"https://api.github.com/repos/{REPO}"
REPO_DIR = os.path.dirname(os.path.abspath(__file__))

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
if not TOKEN:
    sys.exit("请先设置 GITHUB_TOKEN 环境变量")


def call(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {method} {url}\n{e.read().decode(errors='replace')}")


def git(*args):
    return subprocess.run(["git", "-C", REPO_DIR, *args], capture_output=True, check=True).stdout


message = sys.argv[1] if len(sys.argv) > 1 else None
if message:
    git("add", "-A")
    if git("diff", "--cached", "--name-only").strip():
        subprocess.run(["git", "-C", REPO_DIR, "commit", "-q", "-m", message], check=True)
        print(f"已本地提交：{message}")
    else:
        print("没有改动需要提交")

# -z 拿原始字节，中文文件名才不会被 git 转义
paths = [p.decode("utf-8") for p in git("ls-files", "-z").split(b"\0") if p]
print(f"上传 {len(paths)} 个文件…")

tree = []
for p in paths:
    full = os.path.join(REPO_DIR, p)
    with open(full, "rb") as f:
        content = f.read()
    blob = call("POST", f"{API}/git/blobs",
                {"content": base64.b64encode(content).decode(), "encoding": "base64"})
    tree.append({"path": p,
                 "mode": "100755" if os.access(full, os.X_OK) else "100644",
                 "type": "blob", "sha": blob["sha"]})

tree_obj = call("POST", f"{API}/git/trees", {"tree": tree})

# 接上远端当前 commit 作为父，保留历史
try:
    parent = call("GET", f"{API}/git/refs/heads/{BRANCH}")["object"]["sha"]
    parents = [parent]
except SystemExit:
    parents = []

msg = git("log", "-1", "--pretty=%B").decode("utf-8").strip()
commit = call("POST", f"{API}/git/commits",
              {"message": msg, "tree": tree_obj["sha"], "parents": parents})
call("PATCH", f"{API}/git/refs/heads/{BRANCH}", {"sha": commit["sha"], "force": True})

print(f"已推送 commit {commit['sha'][:8]} → https://github.com/{REPO}")
