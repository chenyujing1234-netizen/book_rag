#!/usr/bin/env python3
"""删除同一知识库里标题重复的文档，每本只留一份。

客户端上传失败后重试会留下重复：2026-09-18 那批就因为百炼欠费导致 28 篇
向量化失败，客户端重传了一遍，failed 的那份后来又被补跑成功，于是同一本书
在库里有两三份，检索时同一段原文会重复命中。

保留规则：正文字符数最多的一份（内容最全），并列时保留最早上传的那份——
重传偶尔会截断，字符数是比文件大小更可靠的判据。

    python3 dedup_docs.py           # 只列出计划，不动数据
    python3 dedup_docs.py --apply   # 执行删除
"""
import csv
import io
import subprocess
import sys
import urllib.error
import urllib.request
from collections import defaultdict

BASE = "http://127.0.0.1:8081/api/v1"

QUERY = r"""
COPY (
  SELECT k.id, kb.name, k.title, k.file_size, k.created_at,
         coalesce((SELECT sum(length(c.content)) FROM chunks c
                   WHERE c.knowledge_id = k.id AND c.deleted_at IS NULL), 0) AS textlen
  FROM knowledges k
  JOIN knowledge_bases kb ON kb.id = k.knowledge_base_id
  WHERE k.deleted_at IS NULL AND kb.deleted_at IS NULL
  ORDER BY kb.name, k.title, k.created_at
) TO STDOUT WITH CSV
"""


def psql(sql, csv_mode=True):
    return subprocess.run(
        ["docker", "exec", "WeKnora-postgres", "psql", "-U", "weknora", "-d", "weknora",
         "-c" if csv_mode else "-tAc", sql],
        capture_output=True, text=True, check=True,
    ).stdout


def jwt():
    out = psql("SELECT token FROM auth_tokens WHERE is_revoked=false AND expires_at>now()"
               " AND token_type='access_token' ORDER BY expires_at DESC LIMIT 1", csv_mode=False).strip()
    if not out:
        sys.exit("库里没有有效的 access_token，请先在界面登录一次")
    return out


def main():
    apply = "--apply" in sys.argv
    rows = list(csv.reader(io.StringIO(psql(QUERY))))

    groups = defaultdict(list)
    for kid, kb, title, size, created, textlen in rows:
        groups[(kb, title)].append({
            "id": kid, "kb": kb, "title": title,
            "size": int(size or 0), "created": created, "textlen": int(textlen or 0),
        })

    plan = []
    for (kb, title), docs in sorted(groups.items()):
        if len(docs) < 2:
            continue
        docs.sort(key=lambda d: (-d["textlen"], d["created"]))
        plan.append((kb, title, docs[0], docs[1:]))

    if not plan:
        print("没有重复文档")
        return

    total_del = sum(len(d) for _, _, _, d in plan)
    print(f"{len(plan)} 本书有重复，共 {sum(len(d) for _,_,_,d in plan) + len(plan)} 份，将删除 {total_del} 份\n")
    for kb, title, keep, drop in plan:
        print(f"[{kb}] {title}")
        print(f"  留 {keep['textlen']:>9,} 字符  {keep['created'][:16]}  {keep['id'][:8]}")
        for d in drop:
            mark = "" if d["textlen"] == keep["textlen"] else f"  (少 {keep['textlen']-d['textlen']:,} 字符)"
            print(f"  删 {d['textlen']:>9,} 字符  {d['created'][:16]}  {d['id'][:8]}{mark}")

    if not apply:
        print(f"\n以上为计划。加 --apply 执行。")
        return

    token = jwt()
    ok = fail = 0
    for kb, title, keep, drop in plan:
        for d in drop:
            req = urllib.request.Request(f"{BASE}/knowledge/{d['id']}", method="DELETE")
            req.add_header("Authorization", f"Bearer {token}")
            try:
                urllib.request.urlopen(req, timeout=180).read()
                ok += 1
            except urllib.error.HTTPError as e:
                print(f"  失败 {title}: HTTP {e.code}")
                fail += 1
    print(f"\n已删除 {ok} 份" + (f"，失败 {fail} 份" if fail else ""))


if __name__ == "__main__":
    main()
