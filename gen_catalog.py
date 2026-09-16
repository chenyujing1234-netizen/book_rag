#!/usr/bin/env python3
"""从 WeKnora 数据库生成《知识库目录.md》。

新增或删除文档后重跑一次即可保持目录同步：

    python3 gen_catalog.py

简介取自每篇文档的首个分块，已剔除图片占位符和版权页噪声。
扫描件（提取不到文字的 PDF）会单独标注，不混在正文里。
"""
import csv
import io
import re
import subprocess
from collections import defaultdict
from datetime import datetime

OUT = "知识库目录.md"
SUMMARY_LEN = 150         # 简介截断长度
SCAN_THRESHOLD = 5000     # 实际文字少于此值视为扫描件/无效

QUERY = r"""
COPY (
  WITH firstchunk AS (
    SELECT DISTINCT ON (knowledge_id) knowledge_id, content
    FROM chunks WHERE deleted_at IS NULL ORDER BY knowledge_id, chunk_index
  ),
  stat AS (
    SELECT knowledge_id,
           count(*) AS chunks,
           sum(length(regexp_replace(content, '!\[.*?\]\(resource://[^)]*\)', '', 'g'))) AS textlen
    FROM chunks WHERE deleted_at IS NULL GROUP BY knowledge_id
  )
  SELECT kb.name, k.title, k.file_type, k.file_size,
         coalesce(s.chunks, 0), coalesce(s.textlen, 0),
         coalesce(left(regexp_replace(f.content, '!\[.*?\]\(resource://[^)]*\)', '', 'g'), 2000), ''),
         coalesce(k.description, ''), k.summary_status
  FROM knowledges k
  JOIN knowledge_bases kb ON kb.id = k.knowledge_base_id
  LEFT JOIN firstchunk f ON f.knowledge_id = k.id
  LEFT JOIN stat s ON s.knowledge_id = k.id
  WHERE k.deleted_at IS NULL AND kb.deleted_at IS NULL
  ORDER BY kb.name, k.title
) TO STDOUT WITH CSV
"""


def fetch():
    out = subprocess.run(
        ["docker", "exec", "WeKnora-postgres", "psql", "-U", "weknora", "-d", "weknora", "-c", QUERY],
        capture_output=True, text=True, check=True,
    ).stdout
    return list(csv.reader(io.StringIO(out)))


# 版权页、扉页的特征词。这些行是噪声，不是内容简介
NOISE = re.compile(
    r"(出版|發行|发行|译者|譯者|翻译|翻譯|著$|ISBN|版权|版權|Copyright|Translated by|All rights"
    r"|地址|電话|电话|邮箱|信箱|印刷|书号|書號|定价|定價|责任编辑|責任編輯|初\s*版|总代理|總代理"
    r"|www\.|http|@|第\s*[一二三四五六七八九十\d]+\s*章$|目\s*录|目\s*錄)",
    re.I,
)
CJK = re.compile(r"[\u4e00-\u9fff]")


def from_summary(desc):
    """AI 摘要的第一段就是总述，最适合当简介。"""
    # 去掉 markdown 强调符和列表符号
    t = re.sub(r"[*_#`]+", "", desc)
    paras = [re.sub(r"[ \t]+", " ", p).strip() for p in t.split("\n\n")]
    first = next((p for p in paras if len(p) >= 40), "")
    if not first:
        first = re.sub(r"\s+", " ", t).strip()
    first = first.replace("\n", " ")
    if len(first) > SUMMARY_LEN:
        cut = first[:SUMMARY_LEN]
        # 尽量断在句号处，读起来完整
        pos = max(cut.rfind("。"), cut.rfind("；"))
        first = (cut[: pos + 1] if pos > SUMMARY_LEN * 0.6 else cut.rstrip() + "…")
    return first


def clean(text):
    """从首段里挑出一句像样的简介，跳过版权页和扉页噪声。"""
    text = text.replace("\u3000", " ")
    # 先按空行分段，段内再合并成一行
    paras = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n\s*\n|\n", text)]

    def usable(p):
        if len(p) < 30:
            return False
        if NOISE.search(p):
            return False
        # 中文占比太低的多半是英文书名页或乱码残留
        return len(CJK.findall(p)) / len(p) > 0.5

    pick = next((p for p in paras if usable(p)), None)
    if pick is None:
        # 退化：合并全文取开头，至少给点信息
        pick = re.sub(r"\s+", " ", text).strip()
    pick = re.sub(r"^(简\s*介|內容簡介|内容简介|前\s*言|序\s*言)[:：\s]*", "", pick)
    pick = pick.strip(" -—·*#\\")
    if len(pick) > SUMMARY_LEN:
        pick = pick[:SUMMARY_LEN].rstrip() + "…"
    return pick or "（无法提取简介）"


def human(size):
    size = int(size)
    if size >= 1048576:
        return f"{size / 1048576:.1f}MB"
    return f"{size // 1024}KB"


def main():
    rows = fetch()
    by_kb = defaultdict(list)
    ai_count = 0
    for kb, title, ftype, fsize, chunks, textlen, first, desc, sumstat in rows:
        # AI 摘要质量远好于首段提取，优先用；失败或过短才回退
        if sumstat == "completed" and len(desc) >= 80:
            summary = from_summary(desc)
            ai_count += 1
        else:
            summary = clean(desc if len(desc) > 200 else first)
        by_kb[kb].append({
            "title": title, "type": ftype, "size": int(fsize or 0),
            "chunks": int(chunks), "textlen": int(textlen),
            "summary": summary, "ai": sumstat == "completed" and len(desc) >= 80,
            "scan": int(textlen) < SCAN_THRESHOLD and ftype == "pdf",
        })

    total_docs = sum(len(v) for v in by_kb.values())
    scans = [(kb, d) for kb, docs in by_kb.items() for d in docs if d["scan"]]
    # 文档多的知识库单独成节，单文档的合并成一张表
    multi = {k: v for k, v in by_kb.items() if len(v) > 1}
    single = {k: v[0] for k, v in by_kb.items() if len(v) == 1}

    L = []
    w = L.append
    w("# 知识库目录")
    w("")
    w(f"最后更新：{datetime.now():%Y-%m-%d %H:%M}　|　"
      f"{len(by_kb)} 个知识库，{total_docs} 篇文档")
    w("")
    w("由 `gen_catalog.py` 从数据库生成。新增或删除文档后重跑 `python3 gen_catalog.py` 即可更新，不要手工改本文件。")
    w("")
    w(f"简介由 WeKnora 用 qwen-plus 自动生成（{ai_count}/{total_docs} 篇），"
      "其余回退为文档开头摘录。完整摘要可在 WeKnora 界面上每篇文档的详情里查看。")
    w("")
    w("标记 ⚠ 的是扫描件，提取不到文字，检索不到内容。")
    w("")

    w("## 总览")
    w("")
    w("| 知识库 | 文档数 | 可检索 | 说明 |")
    w("|---|---|---|---|")
    for kb, docs in sorted(multi.items(), key=lambda x: -len(x[1])):
        ok = sum(1 for d in docs if not d["scan"])
        note = f"{len(docs) - ok} 篇扫描件无内容" if ok < len(docs) else "全部正常"
        w(f"| {kb} | {len(docs)} | {ok} | {note} |")
    single_scan = sum(1 for d in single.values() if d["scan"])
    w(f"| （{len(single)} 个单文档知识库） | {len(single)} | {len(single) - single_scan} | 见下方汇总表 |")
    w("")

    for kb, docs in sorted(multi.items(), key=lambda x: -len(x[1])):
        w(f"## {kb}（{len(docs)} 篇）")
        w("")
        for d in docs:
            flag = "⚠ " if d["scan"] else ""
            w(f"**{flag}{d['title']}**")
            w("")
            if d["scan"]:
                w(f"　　{human(d['size'])} · 扫描件，未提取到文字（仅 {d['textlen']} 字符），检索不到内容")
            else:
                w(f"　　{human(d['size'])} · {d['chunks']} 分块 · {d['textlen']:,} 字符")
                w("")
                w(f"　　{d['summary']}")
            w("")

    if single:
        w(f"## 单文档知识库（{len(single)} 个）")
        w("")
        w("每个知识库里只有一篇同名文档，是按文件批量导入时自动建的。")
        w("")
        w("| 知识库 / 文档 | 大小 | 字符 | 简介 |")
        w("|---|---|---|---|")
        for kb in sorted(single):
            d = single[kb]
            name = f"⚠ {kb}" if d["scan"] else kb
            s = "扫描件，无可检索文本" if d["scan"] else d["summary"].replace("|", "／").replace("\\", "")
            w(f"| {name} | {human(d['size'])} | {d['textlen']:,} | {s} |")
        w("")

    if scans:
        w(f"## 待处理：扫描件（{len(scans)} 篇）")
        w("")
        w("这些 PDF 没有文字层，解析时每页被转成图片，`parse_status` 显示 `completed` 但检索不到内容。")
        w("要让它们可用，需要在本地 OCR 成 txt 后重新上传。详见 `API对接说明.md` 第五节。")
        w("")
        w("| 所在知识库 | 文件 | 大小 |")
        w("|---|---|---|")
        for kb, d in sorted(scans, key=lambda x: -x[1]["size"]):
            w(f"| {kb} | {d['title']} | {human(d['size'])} |")
        w("")

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))
    print(f"已生成 {OUT}：{len(by_kb)} 个知识库，{total_docs} 篇文档，其中扫描件 {len(scans)} 篇")


if __name__ == "__main__":
    main()
