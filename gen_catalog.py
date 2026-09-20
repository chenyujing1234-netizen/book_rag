#!/usr/bin/env python3
"""从 WeKnora 数据库生成书目，并同步写进 README。

    python3 gen_catalog.py

会更新两处：
- 知识库目录.md —— 运维台账，含分块数、字符数、扫描件标记
- README.md 里 <!-- CATALOG:START --> 到 <!-- CATALOG:END --> 之间的段落
  只放书名和简介，方便 GitHub / 搜索引擎按书名命中这个仓库

简介取自每篇文档的 AI 摘要，失败时回退为首段摘录。
扫描件（提取不到文字的 PDF）会单独标注，不混在正文里。
"""
import csv
import io
import re
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

OUT = "知识库目录.md"
README = "README.md"
SUMMARY_LEN = 150         # 简介截断长度
SCAN_THRESHOLD = 5000     # 实际文字少于此值视为扫描件/无效
CATALOG_START = "<!-- CATALOG:START -->"
CATALOG_END = "<!-- CATALOG:END -->"

# 文件名尾巴，展示时去掉，搜索命中靠书名本身
FILE_TAIL = re.compile(
    r"(?:\.扫描版|\.清晰文字版|\.彩色图文版|\.文字版)?"
    r"\.(?:txt|pdf|md|epub|doc|docx)$",
    re.I,
)
NUM_TAIL = re.compile(r"_\d+$")          # 三国演义_64
BOOKS_PREFIX = re.compile(r"^books_")
BRACKET_TITLE = re.compile(r"^\[(.+?)\](?:\.(.+))?$")

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

# qwen-plus 的内容审核会拒掉这两篇（data_inspection_failed），重试多少次都一样，
# 所以简介只能人工写在这里。键是文档标题。
MANUAL_SUMMARY = {
    "books_王怡牧师文集：背负十架——中国家庭教会史.txt":
        "王怡牧师 2018 年在秋雨圣约教会成人主日学的讲课录音整理，梳理 1807 至 2018 年"
        "新教入华与中国家庭教会的历史。全十章依次讲述新教入华两世纪、中华归主五十年、"
        "护教士与叛教者、基要派的大复兴、福音进城，直至改革宗在中国与家庭教会的传统、承继与未来。",
    "books_王怡牧师文集：论政教关系.txt":
        "王怡牧师文集「基督是主」之政教关系卷，收录《我的声明：信仰上的抗命》"
        "《2018 年宗教战争沉思录》《我们对家庭教会立场的重申（九十五条）》"
        "《宪政主义与基督教世界观》等文章，分政教关系、牧会与思考、读经灵修三部分，"
        "阐述其从改革宗神学出发对政教关系的理解。",
}


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


def display_title(title):
    """把入库文件名收成能被搜索的书名。"""
    t = FILE_TAIL.sub("", title.strip())
    t = BOOKS_PREFIX.sub("", t)
    t = NUM_TAIL.sub("", t)
    m = BRACKET_TITLE.match(t)
    if m:
        t = m.group(1)
        author = (m.group(2) or "").split(".")[0]
        if author and author not in ("扫描版", "清晰文字版", "彩色图文版", "文字版"):
            t = f"{t}（{author}）"
    return t.strip(" .-_") or title


def md_cell(text):
    return (text or "").replace("|", "／").replace("\n", " ").strip()


def render_readme_catalog(by_kb, multi, single, total_docs, ai_count):
    """给 README 用的精简书目：书名 + 简介，不要分块数这类运维字段。"""
    L = []
    w = L.append
    w(f"当前共 **{len(by_kb)} 个知识库、{total_docs} 篇文档**，"
      "涵盖中文公版古籍（正史、诸子、古典小说、医书、蒙学）、"
      "圣经和合本修订版与研经注释、家庭教育、王怡文集等。")
    w("")
    w("下面是每一本书的书名和简介。GitHub 和搜索引擎都能按书名命中；"
      "完整分块数、字符数和扫描件标记见 [知识库目录.md](知识库目录.md)，"
      "每批书从哪来见 [入库台账.md](入库台账.md)。")
    w("")
    w(f"简介由 WeKnora 用 qwen-plus 生成（{ai_count}/{total_docs} 篇），其余为正文摘录。")
    w("")

    for kb, docs in sorted(multi.items(), key=lambda x: -len(x[1])):
        w(f"### {kb}（{len(docs)} 篇）")
        w("")
        w("| 书名 | 简介 |")
        w("|---|---|")
        for d in docs:
            name = display_title(d["title"])
            if d["scan"]:
                name = f"⚠ {name}"
                intro = "扫描件，没有文字层，检索不到正文"
            else:
                intro = md_cell(d["summary"])
            w(f"| {md_cell(name)} | {intro} |")
        w("")

    if single:
        w(f"### 单文档知识库（{len(single)} 个）")
        w("")
        w("| 书名 | 简介 |")
        w("|---|---|")
        for kb in sorted(single):
            d = single[kb]
            name = display_title(d["title"]) if d["title"] else kb
            # 单文档库的库名往往就是书名，哪个更像书名用哪个
            if len(kb) > len(name) and not kb.startswith("_"):
                name = kb
            if d["scan"]:
                name = f"⚠ {name}"
                intro = "扫描件，没有文字层，检索不到正文"
            else:
                intro = md_cell(d["summary"])
            w(f"| {md_cell(name)} | {intro} |")
        w("")

    return "\n".join(L)


def write_readme_catalog(section):
    """把生成的书目嵌进 README 的标记区间，没有标记就报错，避免误改全文。"""
    path = Path(README)
    text = path.read_text(encoding="utf-8")
    if CATALOG_START not in text or CATALOG_END not in text:
        raise SystemExit(f"{README} 缺少 {CATALOG_START} / {CATALOG_END} 标记，拒绝改写")
    before, rest = text.split(CATALOG_START, 1)
    _, after = rest.split(CATALOG_END, 1)
    path.write_text(
        before + CATALOG_START + "\n\n" + section.rstrip() + "\n\n" + CATALOG_END + after,
        encoding="utf-8",
    )


def main():
    rows = fetch()
    by_kb = defaultdict(list)
    ai_count = 0
    for kb, title, ftype, fsize, chunks, textlen, first, desc, sumstat in rows:
        # AI 摘要质量远好于首段提取，优先用；失败或过短才回退
        if title in MANUAL_SUMMARY:
            summary = MANUAL_SUMMARY[title]
        elif sumstat == "completed" and len(desc) >= 80:
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
    write_readme_catalog(render_readme_catalog(by_kb, multi, single, total_docs, ai_count))
    print(f"已生成 {OUT} 并更新 {README}：{len(by_kb)} 个知识库，{total_docs} 篇文档，其中扫描件 {len(scans)} 篇")


if __name__ == "__main__":
    main()
