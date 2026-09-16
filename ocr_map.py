#!/usr/bin/env python3
"""建立「扫描件 → 有序页面图片」映射，供 OCR 入库使用。

扫描件的每个 chunk 内容形如 ![xxx_page_N.jpg](resource://<handle>)，
handle 对应 resources.handle，再由 physical_path 定位磁盘文件。
按 chunk_index 顺序取 handle，页序天然正确。
"""
import json
import os
import re
import subprocess

VOL = "/var/lib/docker/volumes/my_knowledge_data-files/_data"
OUT = "/tmp/ocr_tasks.json"
CACHE_DIR = "/home/chenyj/my_knowledge/ocr_cache"

# textlen 剔除图片占位符后仍低于此值 = 没有文字层
SCAN_THRESHOLD = 5000

QUERY = r"""
COPY (
  WITH stat AS (
    SELECT knowledge_id,
           sum(length(regexp_replace(content, '!\[.*?\]\(resource://[^)]*\)', '', 'g'))) AS textlen
    FROM chunks WHERE deleted_at IS NULL GROUP BY knowledge_id
  )
  SELECT k.id, kb.name, k.title, k.knowledge_base_id, c.chunk_index, c.content
  FROM knowledges k
  JOIN knowledge_bases kb ON kb.id = k.knowledge_base_id
  JOIN stat s ON s.knowledge_id = k.id
  JOIN chunks c ON c.knowledge_id = k.id AND c.deleted_at IS NULL
  WHERE k.deleted_at IS NULL AND kb.deleted_at IS NULL
    AND k.file_type = 'pdf' AND s.textlen < 5000
  ORDER BY kb.name, k.title, c.chunk_index
) TO STDOUT WITH (FORMAT csv)
"""

RES_QUERY = "COPY (SELECT handle, physical_path FROM resources) TO STDOUT WITH (FORMAT csv)"


def psql(sql):
    return subprocess.run(
        ["docker", "exec", "WeKnora-postgres", "psql", "-U", "weknora", "-d", "weknora", "-c", sql],
        capture_output=True, text=True, check=True,
    ).stdout


def main():
    import csv
    import io

    handle2path = {}
    for row in csv.reader(io.StringIO(psql(RES_QUERY))):
        if len(row) == 2:
            handle2path[row[0]] = row[1]
    print(f"resources 表: {len(handle2path)} 条")

    docs = {}
    for row in csv.reader(io.StringIO(psql(QUERY))):
        if len(row) != 6:
            continue
        kid, kbname, title, kbid, cidx, content = row
        d = docs.setdefault(kid, {
            "id": kid, "kb": kbname, "kb_id": kbid, "title": title, "handles": [],
        })
        # 一个 chunk 里可能有多张页面图，保持出现顺序
        d["handles"].extend(re.findall(r"resource://([A-Za-z0-9_\-]+)", content))

    tasks, missing_total = [], 0
    for d in docs.values():
        paths, missing = [], 0
        seen = set()
        for h in d["handles"]:
            if h in seen:          # 同一页可能被相邻 chunk 重复引用
                continue
            seen.add(h)
            p = handle2path.get(h)
            if not p:
                missing += 1
                continue
            # physical_path 形如 storage://<backend>/local://10000/exports/x.jpg，
            # 真实位置是卷根目录加上最后一个 local:// 之后的部分
            rel = p.rsplit("local://", 1)[-1].lstrip("/")
            full = os.path.join(VOL, rel)
            if os.path.exists(full):
                paths.append(full)
            else:
                missing += 1
        # 少数 PDF（如 FreePic2Pdf 生成的）docreader 一张页面图都没导出，
        # 只解析出元数据。这类书用 pdftoppm 本地渲染后放在 render_<id 前 8 位>/ 下。
        if not paths:
            rd = os.path.join(CACHE_DIR, "render_" + d["id"][:8])
            if os.path.isdir(rd):
                paths = [os.path.join(rd, f) for f in sorted(os.listdir(rd))
                         if f.lower().endswith((".jpg", ".jpeg", ".png"))]
                missing = 0

        missing_total += missing
        tasks.append({
            "id": d["id"], "kb": d["kb"], "kb_id": d["kb_id"], "title": d["title"],
            "pages": paths, "missing": missing,
        })

    tasks.sort(key=lambda t: len(t["pages"]))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False)

    total = sum(len(t["pages"]) for t in tasks)
    print(f"\n扫描件 {len(tasks)} 篇，可 OCR 页面 {total} 张，缺失 {missing_total} 张")
    print(f"预计耗时 {total * 4.9 / 3600:.1f} 小时（实测 4.9s/张，服务单 worker 无法并发）\n")
    print(f"{'页数':>5} {'缺':>4}  {'知识库':<12} 文件")
    for t in tasks:
        print(f"{len(t['pages']):>5} {t['missing']:>4}  {t['kb'][:12]:<12} {t['title'][:58]}")
    print(f"\n任务清单已写入 {OUT}")


if __name__ == "__main__":
    main()
