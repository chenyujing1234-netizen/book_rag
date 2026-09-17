#!/usr/bin/env python3
"""清理已无引用的页面图片。

删除 WeKnora 文档并不会回收它导出的页面图片，扫描件 OCR 入库后
原 PDF 被删，就会留下大量孤立 jpg（这次是 8448 张、2.1GB）。

安全策略：先算出仍被活跃 chunk 引用的 handle 白名单，待删列表与白名单
取交集必须为空，否则直接中止。磁盘文件删完再删数据库记录。

用法：
    python3 clean_orphan_images.py            # 预演
    python3 clean_orphan_images.py --apply     # 执行
"""
import argparse
import csv
import io
import os
import subprocess
import sys

VOL = "/var/lib/docker/volumes/my_knowledge_data-files/_data"

LIVE = r"""
COPY (
  SELECT DISTINCT m[1]
  FROM chunks c
  CROSS JOIN LATERAL regexp_matches(c.content, 'resource://([A-Za-z0-9_-]+)', 'g') AS m
  JOIN knowledges k ON k.id = c.knowledge_id
  WHERE c.deleted_at IS NULL AND k.deleted_at IS NULL
) TO STDOUT WITH (FORMAT csv)
"""

IMAGES = """
COPY (
  SELECT handle, physical_path, size FROM resources WHERE mime_type LIKE 'image%%'
) TO STDOUT WITH (FORMAT csv)
"""


def psql(sql, tuples=False):
    cmd = ["docker", "exec", "WeKnora-postgres", "psql", "-U", "weknora", "-d", "weknora"]
    cmd += ["-tAc" if tuples else "-c", sql]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    live = {r[0] for r in csv.reader(io.StringIO(psql(LIVE))) if r}
    images = [(r[0], r[1], int(r[2] or 0))
              for r in csv.reader(io.StringIO(psql(IMAGES))) if len(r) == 3]
    orphans = [(h, p, s) for h, p, s in images if h not in live]

    # 安全闸门：待删集合与白名单绝不能有交集
    overlap = {h for h, _, _ in orphans} & live
    if overlap:
        sys.exit(f"中止：{len(overlap)} 个待删 handle 仍被引用")

    total = sum(s for _, _, s in orphans)
    print(f"图片 {len(images)} 张，其中仍被引用 {len(live & {h for h, _, _ in images})} 张")
    print(f"待清理 {len(orphans)} 张，约 {total / 1048576:.0f} MB")

    if not args.apply:
        print("\n这是预演。确认后加 --apply 执行。")
        return

    removed = freed = 0
    missing = 0
    for h, p, s in orphans:
        rel = p.rsplit("local://", 1)[-1].lstrip("/")
        full = os.path.join(VOL, rel)
        if os.path.exists(full):
            try:
                os.remove(full)
                removed += 1
                freed += s
            except OSError as e:
                print(f"  删除失败 {full}: {e}")
        else:
            missing += 1
    print(f"磁盘：删除 {removed} 个文件，释放 {freed / 1048576:.0f} MB"
          f"（{missing} 个文件本就不存在）")

    # 数据库记录分批删，避免单条语句参数过多
    handles = [h for h, _, _ in orphans]
    deleted = 0
    for i in range(0, len(handles), 500):
        batch = "','".join(handles[i:i + 500])
        out = psql(f"DELETE FROM resources WHERE handle IN ('{batch}')", tuples=True)
        deleted += len(handles[i:i + 500])
    print(f"数据库：清理 {deleted} 条 resources 记录")


if __name__ == "__main__":
    main()
