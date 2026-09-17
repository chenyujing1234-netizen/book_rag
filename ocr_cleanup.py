#!/usr/bin/env python3
"""OCR 入库完成后，删除已被 txt 版取代的原始扫描 PDF。

删除是不可逆的，所以每一本都要先过校验：替代文档必须真实存在、
解析完成、且正文字符数达标。任何一条不满足就跳过，不删。

OCR 失败的书（_uploaded.json 里标了 ocr_failed）一律保留 PDF——
那是它内容的唯一载体。

用法：
    python3 ocr_cleanup.py            # 只检查并列出计划，不动数据
    python3 ocr_cleanup.py --apply    # 真正删除
"""
import argparse
import csv
import io
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

CACHE = "/home/chenyj/my_knowledge/ocr_cache"
BASE = "http://127.0.0.1:8081/api/v1"
MIN_CHARS = 20000     # 替代文档正文低于此值就不敢删原件

# 正文字符数达标、但内容主体不是文字的书。OCR 文本替代不了原件，PDF 必须留。
KEEP_PDF = {
    "让孩子越玩越聪明": "图形智力题集，199 页平均每页仅 97 字，题目离开图形无意义",
}


def psql(sql):
    return subprocess.run(
        ["docker", "exec", "WeKnora-postgres", "psql", "-U", "weknora", "-d", "weknora", "-c", sql],
        capture_output=True, text=True, check=True,
    ).stdout


def jwt():
    out = subprocess.run(
        ["docker", "exec", "WeKnora-postgres", "psql", "-U", "weknora", "-d", "weknora", "-tAc",
         "SELECT token FROM auth_tokens WHERE is_revoked=false AND expires_at>now()"
         " AND token_type='access_token' ORDER BY expires_at DESC LIMIT 1"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not out:
        sys.exit("库里没有有效的 access_token，请先在界面登录一次")
    return out


def load_docs():
    """取所有文档的状态与正文长度，按 id 索引。"""
    q = r"""
COPY (
  SELECT k.id, kb.name, k.title, k.parse_status, k.file_type,
         coalesce((SELECT sum(length(regexp_replace(c.content,'!\[.*?\]\(resource://[^)]*\)','','g')))
                   FROM chunks c WHERE c.knowledge_id=k.id AND c.deleted_at IS NULL), 0)
  FROM knowledges k JOIN knowledge_bases kb ON kb.id=k.knowledge_base_id
  WHERE k.deleted_at IS NULL AND kb.deleted_at IS NULL
) TO STDOUT WITH (FORMAT csv)
"""
    docs = {}
    for r in csv.reader(io.StringIO(psql(q))):
        if len(r) == 6:
            docs[r[0]] = {"kb": r[1], "title": r[2], "status": r[3],
                          "ftype": r[4], "textlen": int(r[5] or 0)}
    return docs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行删除")
    args = ap.parse_args()

    uploaded = json.load(open(os.path.join(CACHE, "_uploaded.json"), encoding="utf-8"))
    docs = load_docs()

    plan, keep = [], []
    for pdf_id, meta in uploaded.items():
        title = meta.get("title", "?")
        if meta.get("ocr_failed"):
            keep.append((title, "OCR 失败，PDF 是内容唯一载体"))
            continue
        if pdf_id not in docs:
            keep.append((title, "PDF 文档已不在库中"))
            continue
        hit = next((why for kw, why in KEEP_PDF.items() if kw in title), None)
        if hit:
            keep.append((title, hit))
            continue

        if meta.get("skipped"):
            # 这类书原本就有完整 txt 版，找同名替代文档
            stem = os.path.splitext(title)[0]
            alt = [d for i, d in docs.items()
                   if i != pdf_id and os.path.splitext(d["title"])[0] == stem
                   and d["ftype"] != "pdf"]
            if not alt or alt[0]["textlen"] < MIN_CHARS:
                keep.append((title, "找不到达标的替代文档"))
                continue
            plan.append((pdf_id, title, alt[0]["title"], alt[0]["textlen"]))
            continue

        nid = meta.get("new_id")
        alt = docs.get(nid)
        if not alt:
            keep.append((title, "OCR 版不在库中"))
            continue
        if alt["status"] != "completed":
            keep.append((title, f"OCR 版状态为 {alt['status']}"))
            continue
        if alt["textlen"] < MIN_CHARS:
            keep.append((title, f"OCR 版正文仅 {alt['textlen']} 字符"))
            continue
        plan.append((pdf_id, title, alt["title"], alt["textlen"]))

    print(f"计划删除 {len(plan)} 个原始 PDF，保留 {len(keep)} 个\n")
    for _, title, altname, n in plan:
        print(f"  删 {title[:48]}")
        print(f"     ← 已由 {altname[:48]}（{n:,} 字符）取代")
    if keep:
        print("\n保留：")
        for title, why in keep:
            print(f"  {title[:48]} —— {why}")

    if not args.apply:
        print("\n这是预演。确认无误后加 --apply 执行。")
        return

    token = jwt()
    ok = 0
    for pdf_id, title, _, _ in plan:
        req = urllib.request.Request(f"{BASE}/knowledge/{pdf_id}", method="DELETE")
        req.add_header("Authorization", f"Bearer {token}")
        try:
            urllib.request.urlopen(req, timeout=120).read()
            ok += 1
            print(f"  已删 {title[:50]}")
        except urllib.error.HTTPError as e:
            print(f"  失败 {title[:40]}: HTTP {e.code}")
    print(f"\n完成，删除 {ok}/{len(plan)} 个 PDF")


if __name__ == "__main__":
    main()
