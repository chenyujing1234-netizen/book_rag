#!/usr/bin/env python3
"""把扫描版 PDF 逐页 OCR 成 txt 再入库。

背景：这些 PDF 没有文字层，docreader 只把每页转成图片，chunk 里全是
`![](resource://...)` 占位符，检索不到任何内容。这里用外部 RapidOCR 服务
把页面图片识别成文字，拼成 txt 上传到原知识库，让内容可检索。

OCR 服务是单 worker CPU 推理（实测 4.9s/张，并发无效），9132 张约 12 小时，
所以每页结果都落盘缓存，中断后重跑会跳过已完成的页。

用法：
    python3 ocr_map.py                   # 先生成任务清单
    python3 ocr_ingest.py --only 倾听    # 先跑一本验证
    python3 ocr_ingest.py                # 全量
    python3 ocr_ingest.py --no-upload    # 只 OCR 不上传
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

TASKS = "/tmp/ocr_tasks.json"
CACHE = "/home/chenyj/my_knowledge/ocr_cache"
OCR_URL = os.environ.get("OCR_URL", "http://114.55.254.123/v1/ocr")
# 这是别人提供的服务，key 不写进代码，放在 .ocr_key（已 gitignore）或环境变量里
KEY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".ocr_key")
BASE = "http://127.0.0.1:8081/api/v1"
MIN_DISK_MB = 1500          # 低于此值中止，磁盘写满比 OCR 失败严重得多
PAGE_TIMEOUT = 180
RETRIES = 3


def ocr_key():
    k = os.environ.get("OCR_API_KEY", "").strip()
    if not k and os.path.exists(KEY_FILE):
        k = open(KEY_FILE, encoding="utf-8").read().strip()
    if not k:
        sys.exit(f"缺少 OCR key：设置 OCR_API_KEY 环境变量，或写入 {KEY_FILE}")
    return k


def jwt():
    """直接从库里取一张有效的 access token，避免把账号密码写进脚本。"""
    out = subprocess.run(
        ["docker", "exec", "WeKnora-postgres", "psql", "-U", "weknora", "-d", "weknora", "-tAc",
         "SELECT token FROM auth_tokens WHERE is_revoked=false AND expires_at>now()"
         " AND token_type='access_token' ORDER BY expires_at DESC LIMIT 1"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if not out:
        sys.exit("库里没有有效的 access_token，请先在界面登录一次")
    return out


OCR_KEY = None


def ocr_page(path):
    """识别单页，返回文本。服务偶发 5xx，重试几次。"""
    with open(path, "rb") as f:
        payload = json.dumps({"image_base64": base64.b64encode(f.read()).decode()}).encode()
    last = None
    for attempt in range(RETRIES):
        req = urllib.request.Request(OCR_URL, data=payload, method="POST")
        req.add_header("X-API-Key", OCR_KEY)
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=PAGE_TIMEOUT) as r:
                d = json.loads(r.read())
            if d.get("success"):
                return d.get("text") or ""
            last = d.get("detail") or "success=false"
        except Exception as e:                      # 网络抖动、超时、5xx
            last = repr(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"OCR 失败（{RETRIES} 次）：{last}")


def clean_text(pages):
    """拼接页面文本。去掉纯页码行，避免污染分块。"""
    out = []
    for txt in pages:
        lines = []
        for ln in txt.split("\n"):
            s = ln.strip()
            if not s:
                continue
            if re.fullmatch(r"[\-—·\s]*\d{1,4}[\-—·\s]*", s):   # 孤立页码
                continue
            lines.append(s)
        if lines:
            out.append("\n".join(lines))
    return "\n\n".join(out)


def upload(kb_id, filename, content, token):
    """multipart 上传，字段名必须是 file。"""
    boundary = "----ocrboundary7f3a2b"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n",
        content.encode("utf-8"),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(f"{BASE}/knowledge-bases/{kb_id}/knowledge/file",
                                 data=body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"上传 HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
    if not d.get("success"):
        raise RuntimeError(f"上传失败: {str(d)[:300]}")
    return (d.get("data") or {}).get("id", "?")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="只处理标题含该关键词的书")
    ap.add_argument("--limit", type=int, help="最多处理几本")
    ap.add_argument("--no-upload", action="store_true", help="只 OCR，不上传")
    args = ap.parse_args()

    tasks = json.load(open(TASKS, encoding="utf-8"))
    if args.only:
        tasks = [t for t in tasks if args.only in t["title"]]
    tasks = [t for t in tasks if t["pages"]]
    if args.limit:
        tasks = tasks[: args.limit]
    if not tasks:
        sys.exit("没有匹配的任务")

    os.makedirs(CACHE, exist_ok=True)
    global OCR_KEY
    OCR_KEY = ocr_key()
    token = jwt()
    done_log = os.path.join(CACHE, "_uploaded.json")
    uploaded = json.load(open(done_log)) if os.path.exists(done_log) else {}

    total_pages = sum(len(t["pages"]) for t in tasks)
    print(f"待处理 {len(tasks)} 本，共 {total_pages} 页，预计 {total_pages * 4.9 / 3600:.1f} 小时")
    print(f"缓存目录 {CACHE}，中断后重跑会跳过已完成页\n", flush=True)

    t0 = time.time()
    ocr_count = 0        # 本次真正调用 OCR 的页数，只用它测速
    skipped_pages = 0    # 跳过或命中缓存的页数，不参与测速
    for n, t in enumerate(tasks, 1):
        name = t["title"]
        if t["id"] in uploaded:
            print(f"[{n}/{len(tasks)}] 已入库，跳过：{name}", flush=True)
            skipped_pages += len(t["pages"])
            continue

        cache_file = os.path.join(CACHE, t["id"] + ".jsonl")
        cached = {}
        if os.path.exists(cache_file):
            for line in open(cache_file, encoding="utf-8"):
                try:
                    d = json.loads(line)
                    cached[d["i"]] = d["t"]
                except Exception:
                    pass
        print(f"[{n}/{len(tasks)}] {name}", flush=True)
        print(f"    {len(t['pages'])} 页，已缓存 {len(cached)} 页", flush=True)

        fh = open(cache_file, "a", encoding="utf-8")
        consecutive_fail = 0
        for i, page in enumerate(t["pages"]):
            if i in cached:
                skipped_pages += 1
                continue
            free_mb = shutil.disk_usage("/").free // 1048576
            if free_mb < MIN_DISK_MB:
                fh.close()
                sys.exit(f"磁盘仅剩 {free_mb}MB，中止")
            try:
                txt = ocr_page(page)
                consecutive_fail = 0
            except Exception as e:
                # 不缓存失败页，否则会被永久记成空白；下次重跑会自动重试
                consecutive_fail += 1
                print(f"    第 {i + 1} 页失败（连续 {consecutive_fail}）：{e}", flush=True)
                if consecutive_fail >= 3:
                    # 连着失败通常是 OCR 服务挂了或网络断了，硬等比刷失败日志有意义
                    print("    连续失败 3 页，判定服务异常，等待 5 分钟", flush=True)
                    time.sleep(300)
                    consecutive_fail = 0
                continue
            cached[i] = txt
            fh.write(json.dumps({"i": i, "t": txt}, ensure_ascii=False) + "\n")
            fh.flush()
            ocr_count += 1
            if (i + 1) % 25 == 0:
                el = time.time() - t0
                speed = ocr_count / el if el else 0
                remain = total_pages - skipped_pages - ocr_count
                left = remain / speed / 3600 if speed else 0
                print(f"    {i + 1}/{len(t['pages'])} 页，{speed * 60:.1f} 页/分，"
                      f"全部剩余约 {left:.1f} 小时", flush=True)
        fh.close()

        gaps = [i for i in range(len(t["pages"])) if i not in cached]
        if gaps:
            print(f"    还有 {len(gaps)} 页未成功识别，本书暂不上传，重跑会自动补齐", flush=True)
            continue

        content = clean_text([cached.get(i, "") for i in range(len(t["pages"]))])
        chars = len(content)
        txt_name = re.sub(r"\.pdf$", "", name, flags=re.I) + ".txt"
        out_path = os.path.join(CACHE, txt_name)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"    OCR 完成：{chars:,} 字符 -> {txt_name}", flush=True)

        if chars < 1000:
            print("    识别出的文字过少，跳过上传（请人工检查）", flush=True)
            continue
        if args.no_upload:
            continue
        try:
            kid = upload(t["kb_id"], txt_name, content, token)
            uploaded[t["id"]] = {"title": name, "txt": txt_name, "chars": chars, "new_id": kid}
            json.dump(uploaded, open(done_log, "w"), ensure_ascii=False, indent=1)
            print(f"    已上传到「{t['kb']}」，新文档 {kid}", flush=True)
        except Exception as e:
            print(f"    上传失败：{e}", flush=True)

    print(f"\n完成 {len(uploaded)} 本，总耗时 {(time.time() - t0) / 3600:.2f} 小时")


if __name__ == "__main__":
    main()
