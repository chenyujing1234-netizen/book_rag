#!/usr/bin/env python3
"""评估 OCR 结果质量，挑出不可用的书。

OCR 模型是简体横排训练的（ch_PP-OCRv4），碰到繁体、竖排、老版本印刷会大幅退化：
错字连片、行序错乱。这类文本入库只会污染检索，必须先筛出来。

三个判据：
  common  正文里属于高频汉字的比例。正常中文散文在 0.55 以上，
          识别混乱时高频字被认成生僻字，比例明显下降。
  trad    繁体专用字比例。超过 1% 基本就是繁体书，简体模型识别不可靠。
  short   长度不足 4 的行占比。竖排被按横排切分时会碎成大量短行。
"""
import json
import os
import re
import sys

CACHE = "/home/chenyj/my_knowledge/ocr_cache"

# 现代汉语高频字（约 300 个），覆盖正常中文文本一半以上的字符
COMMON = set(
    "的一是不了在人有我他这个们中来上大为和国地到以说时要就出会可也你对生能而子那"
    "得于着下自之年过发后作里用道行所然家种事成方多经么去法学如都同现当没动面起看"
    "定天分还进好小部其些主样理心她本前开但因只从想实日军者意无力它与长把机十民第"
    "公此已工使情明性知全三又关点正业外将两高间由问很最重并物手应战向头文体政美相"
    "见被利什二等产或新己制身果加西斯月话合回特代内信表化老给世位次度门任常先海通"
    "教儿原东声提立及比员解水名真论处走义各入几口认条平系气题活尔更别打女变四神总"
    "何电数安少报才结反受目太量再感建务做接必场件计管期市直德资命山金指克许统区保"
    "至队形社便空决治展马科司五基眼书非则听白却界达光放强即像难且权思王象完设式色"
    "路记南品住告类求据程北边死张该交规万取拉格望觉术领共确传师观清今切院让识候带"
    "导争运笑飞风步改收根干造言联持组每济车亲极林服快办议往元英士证近失转夫令准布"
    "始怎呢存未远叫台单影具罗字爱击流备兵连调深商算质团集百需价花党华城石级整苦房"
)

# 繁体专用字（不与简体重合的常用繁体形）
TRAD = set(
    "們這時後來國學會個說爲無產發點開關麼樣實還應當經過縣談讓認識體題幾對從業內"
    "書處長萬與東馬車間問門聞問陽陰爲兒寫寶實實對師專屬歲歷區醫華聲學覺變讀"
    "訴語說話請誰謝謹講議論證評語調誠誤認識護變讓議顯願類願飛養龍鳥鳳鴨雞魚"
    "數樹權機橋樂藥華萬葉蘭莊處虛號蟲術衛裝襪見觀規視親覺覽計訊記訓討訪設"
)

CJK = re.compile(r"[\u4e00-\u9fff]")


def score(text):
    body = CJK.findall(text)
    n = len(body)
    if n < 500:
        return None
    common = sum(1 for c in body if c in COMMON) / n
    trad = sum(1 for c in body if c in TRAD) / n
    lines = [l for l in text.split("\n") if l.strip()]
    short = sum(1 for l in lines if len(l.strip()) < 4) / max(len(lines), 1)
    return {"chars": n, "common": common, "trad": trad, "short": short}


def verdict(s):
    """common 低、trad 高、short 高 任一超标就判定不可用。"""
    if s["trad"] > 0.010:
        return "差", "繁体书，简体模型识别不可靠"
    if s["common"] < 0.50:
        return "差", "高频字占比过低，识别混乱"
    if s["short"] > 0.35:
        return "差", "短行过多，疑似竖排被错误切分"
    if s["common"] < 0.55 or s["short"] > 0.25:
        return "中", "有明显噪声，但主体可读"
    return "好", ""


def main():
    up_path = os.path.join(CACHE, "_uploaded.json")
    uploaded = json.load(open(up_path, encoding="utf-8")) if os.path.exists(up_path) else {}
    by_txt = {v.get("txt"): (kid, v) for kid, v in uploaded.items() if v.get("txt")}

    rows = []
    for f in sorted(os.listdir(CACHE)):
        if not f.endswith(".txt"):
            continue
        s = score(open(os.path.join(CACHE, f), encoding="utf-8", errors="replace").read())
        if not s:
            continue
        v, why = verdict(s)
        kid, meta = by_txt.get(f, (None, {}))
        rows.append((v, why, f, s, kid, meta.get("new_id")))

    order = {"差": 0, "中": 1, "好": 2}
    rows.sort(key=lambda r: (order[r[0]], r[3]["common"]))

    print(f"{'评价':<4} {'字符':>8} {'高频':>6} {'繁体':>6} {'短行':>6}  书名 / 问题")
    for v, why, f, s, kid, nid in rows:
        print(f"{v:<4} {s['chars']:>8,} {s['common']:>6.2f} {s['trad']:>6.3f} "
              f"{s['short']:>6.2f}  {f[:52]}")
        if why:
            print(f"{'':>34}  └ {why}")

    bad = [r for r in rows if r[0] == "差"]
    mid = [r for r in rows if r[0] == "中"]
    print(f"\n共 {len(rows)} 本：好 {len(rows) - len(bad) - len(mid)}，中 {len(mid)}，差 {len(bad)}")
    if bad:
        json.dump([{"txt": r[2], "kid": r[4], "new_id": r[5], "why": r[1]} for r in bad],
                  open("/tmp/ocr_bad.json", "w"), ensure_ascii=False, indent=1)
        print("不可用清单已写入 /tmp/ocr_bad.json")


if __name__ == "__main__":
    main()
