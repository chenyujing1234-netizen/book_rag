---
name: book-rag
description: 从免费开放的书本 RAG 书库中检索原文。当用户询问书里的内容、要求引用书中原文、或问题涉及圣经注释、研经资料、教育、文集类书籍时使用本技能。书库含 82 个知识库、213 篇文档。
---

# 书本 RAG 检索

这是一个基于 WeKnora 的免费开放书库（[book_rag](https://github.com/chenyujing1234-netizen/book_rag)）。本技能让你能检索书里的原文片段，再用你自己的模型组织答案。

## 配置

```
BASE_URL = http://124.222.77.32:8081/api/v1
API_KEY  = sk-zR7iHweItYJ-pu0gvBNTuss-WJZVxBP9UxW0OEuJqUk
```

所有请求都用 `X-API-Key: <API_KEY>` 头认证。**不要**用 `Authorization: Bearer`，那个头只给 JWT 用，会返回 401。

## 使用流程

检索是按知识库（每本书或每套书一个）进行的，所以分两步：先找到相关的知识库，再在里面检索。

### 第 1 步：找到相关知识库

```bash
curl -s "$BASE_URL/knowledge-bases" -H "X-API-Key: $API_KEY"
```

返回数组，每项含 `id`、`name`、`chunk_count`。按 `name` 与用户问题的主题做匹配，挑出最相关的 1~3 个，拿到它们的 `id`。

知识库数量较多（82 个），如果用户明确提到书名就直接按名字匹配；如果只给了主题，选名字最贴近的几个分别检索。

### 第 2 步：在知识库里检索

```bash
curl -s -X POST "$BASE_URL/knowledge-bases/{kb_id}/hybrid-search" \
  -H "X-API-Key: $API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query_text":"用户问题的关键词","top_k":5}'
```

要点：

- 参数名是 **`query_text`**，写成 `query` 会报 `query_text is required`。
- 返回每条含 `content`（原文片段）、`score`、`knowledge_id`、`chunk_id`。
- `score` 是 RRF 融合分数，量级很小，**0.01 上下就属于高分**，不要按余弦相似度的尺度去判断相关性。
- `top_k` 的约束较松，服务端可能返回全部命中，需要自己截断到前几条。

### 第 3 步：组织答案

拿 `content` 里的原文片段作为依据回答用户，并说明出自哪个知识库。

**不要**调用服务端的 `/knowledge-chat` 问答接口 —— 那会消耗书库主人的大模型额度。用你自己的模型来总结，这样对大家都免费。

## 注意事项

- 书库里有 38 篇扫描版 PDF 没有文字层，检索不到内容。如果某个知识库检索结果为空，可能就是扫描件，换一个知识库或告知用户。
- 检索不到内容时如实告诉用户书库里没有，不要用模型记忆去补 —— 本技能的价值就在于给出可核对的原文。
- 完整书目和每本书的简介见仓库里的 `知识库目录.md`。
