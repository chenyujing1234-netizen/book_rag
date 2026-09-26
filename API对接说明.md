# WeKnora API 对接说明

本文档中所有端点、参数和返回字段均在本机部署实例（`124.222.77.32`）上实测验证。

---

## 一、服务地址

| 入口 | 地址 | 说明 |
| --- | --- | --- |
| 前端 + API（推荐） | `http://124.222.77.32:8081` | WeKnora 前端容器对外映射，浏览器与 API 同一入口 |
| 后端直连 | `http://124.222.77.32:8083` | 绕过前端 nginx 直达 Go 服务，两者 API 完全一致 |

推荐走 **http://124.222.77.32:8081**。前端镜像内 nginx 已对 SSE 做过调优（`proxy_buffering off`、读超时 3600s）；直连 8083 则少一层代理，适合服务器本机批量导入。

**前置条件：腾讯云安全组需放通 TCP 8081**（以及本机脚本用的 8083 若需外网直连）。域名 `www.aiwang.cloud` 当前未绑定；恢复域名时可参考 `nginx/aiwang-weknora.conf`。

---

## 二、认证

有两种方式，程序对接**务必用 API Key**（不过期、可限定权限范围）。

### 2.1 API Key（推荐）

请求头固定为 `X-API-Key`：

```
X-API-Key: sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> **注意**：API Key 只认 `X-API-Key` 头。用 `Authorization: Bearer <api_key>` 会返回 401 —— 那个头只用于下面的 JWT。

#### 创建 API Key

界面上在「设置 → 集成 / API」里创建即可。也可以用 API 创建，需要先用账号密码登录拿 JWT：

```bash
# 1) 登录取 JWT（同时拿到 tenant_id，本实例是 10000）
TOKEN=$(curl -s -X POST http://124.222.77.32:8081/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"你的邮箱","password":"你的密码"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

# 2) 创建全权限 Key
curl -s -X POST http://124.222.77.32:8081/api/v1/tenants/10000/api-keys \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"my-client","full_access":true,"knowledge_base_ids":[],"capabilities":[]}'
```

返回体里有两个容易混淆的字段，**认证要用的是 `token`**：

```json
{
  "data": {
    "id": 1,
    "name": "my-client",
    "api_key": "enc:v1:xxxxxxxx...",    // 加密存储形式，不能用于认证
    "token": "sk-xxxxxxxxxxxxxxxx",     // ← 这个才是 API Key，仅创建时返回一次
    "full_access": true
  },
  "success": true
}
```

`token` 不会再次返回，创建后立刻保存。丢了只能删掉重建。

若要做权限收窄，把 `full_access` 设为 `false`，并在 `capabilities` 里填 `retrieve`、`chat` 等能力；`knowledge_base_ids` 可限定该 Key 只能访问指定知识库（空数组表示不限制）。

#### 查询与删除

```bash
GET    /api/v1/tenants/{tenant_id}/api-keys      # 列出（不返回 token 明文）
DELETE /api/v1/tenants/{tenant_id}/api-keys/{id}
```

### 2.2 JWT（仅适合交互式场景）

`POST /api/v1/auth/login` 返回的 `token` 用 `Authorization: Bearer <token>` 携带。会过期，需要用 `refresh_token` 续期，不适合无人值守的脚本。

另外，API Key 无权访问租户管理类端点（如 `GET /api/v1/tenants/{id}` 返回 403），这类操作必须用 JWT。

---

## 三、核心端点

以下所有请求都需要带认证头。`{kb_id}` 为知识库 ID，`{kid}` 为文档 ID。

### 3.1 知识库

```bash
GET    /api/v1/knowledge-bases            # 列出所有知识库
POST   /api/v1/knowledge-bases            # 创建，body 见下方（必须带模型 ID）
GET    /api/v1/knowledge-bases/{kb_id}    # 详情
DELETE /api/v1/knowledge-bases/{kb_id}    # 删除（同步返回）
```

> **建库时必须显式传 `embedding_model_id`，这是本项目踩过的最大的坑。**
>
> 只传 `{"name":"...","description":"..."}` 也能建成功，但建出来的知识库 `embedding_model_id` 是空字符串、`chunking_config` 是 `{"chunk_size":0,"chunk_overlap":0,"separators":null}`。往这种库里导入的文档，docreader 能解析出文本，但走到向量化时 `GetEmbeddingModel` 报 `model ID cannot be empty`，任务挂死在 `processing`，约 2 小时后被 housekeeping 批量改成 `failed`，错误信息是 `task stuck in processing > 2h10m0s, recovered by housekeeping` —— 这条信息只是善后描述，看不出真正原因，真正原因要在 `docker logs WeKnora-app` 里找 `model.go:489[GetEmbeddingModel]`。
>
> 2026-09-15 通过脚本建的 81 个知识库全部中招，153 篇文档无一成功。界面上表现为知识库存在但里面是空的。
>
> **更麻烦的是这个字段事后改不了**：`PUT /api/v1/knowledge-bases/{kb_id}` 的请求体只接受 `name`、`description`、`config` 三个字段，而 `config`（`KnowledgeBaseConfig`）里根本没有 embedding 模型字段，调用会返回 200 但字段不变。只能直接 UPDATE 数据库（库里没有向量时是安全的），然后用 `POST /api/v1/knowledge/batch-reparse` 重新解析。
>
> 正确的建库请求体：
>
> ```json
> {
>   "name": "知识库名",
>   "description": "说明",
>   "embedding_model_id": "5f5d17d7-5ecb-4adf-9e22-3b2db4e5c404",
>   "summary_model_id": "71ad8542-c6c4-41a3-8c0f-2efc5d826334",
>   "chunking_config": {
>     "strategy": "auto", "chunk_size": 2048, "chunk_overlap": 80,
>     "separators": ["\n\n", "\n", "。", "！", "？", ";", "；"],
>     "child_chunk_size": 384, "parent_chunk_size": 4096
>   }
> }
> ```
>
> 模型 ID 从 `GET /api/v1/models` 取（见 3.8）。省事且不会出错的做法是**在网页界面建库**——界面会强制要求选 embedding 模型——接口只用来传文档。
>
> **本实例已装触发器兜底。** 这个坑在 2026-09-17 又复发了一次（客户端用 MCP 建库，传进去的两本书挂在 `processing`，看起来像服务端卡死，实际负载只有 0.13）。所以加了 `sql/kb_defaults_trigger.sql`：`knowledge_bases` 上的 BEFORE INSERT 触发器，`embedding_model_id` 为空时自动从 `models` 表取 active 的 Embedding 模型填上，`chunk_size` 为 0 时补成上面那套默认分块配置。字段已有值时不介入，所以界面建库和显式传参都不受影响。
>
> 安装：`docker exec -i WeKnora-postgres psql -U weknora -d weknora < sql/kb_defaults_trigger.sql`
>
> 自查当前有没有中招的库：
>
> ```sql
> SELECT name FROM knowledge_bases
> WHERE deleted_at IS NULL AND coalesce(embedding_model_id,'') = '';
> ```

列表返回的每个知识库含 `id`、`name`、`chunk_count`、`chunking_config`（分块策略）、`capabilities`（启用了向量/关键词/图谱等哪些检索能力）。

当前实例的知识库：`e8ec2c8d-5a8f-4999-a4f2-ff42d6bf54ce`（和合本修订版）。

### 3.2 导入文档

**上传本地文件**（multipart，字段名必须是 `file`）：

```bash
curl -X POST http://124.222.77.32:8081/api/v1/knowledge-bases/{kb_id}/knowledge/file \
  -H "X-API-Key: $KEY" \
  -F "file=@/path/to/doc.pdf;type=application/pdf"
```

**按 URL 抓取**：

```bash
curl -X POST http://124.222.77.32:8081/api/v1/knowledge-bases/{kb_id}/knowledge/url \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/article"}'
```

两者都立刻返回文档记录，`parse_status` 为 `pending` —— **解析是异步的**，必须轮询状态（见 3.3）。单文件上限 50MB（由 `MAX_FILE_SIZE_MB` 控制）。

### 3.3 查询文档与解析状态

```bash
GET /api/v1/knowledge-bases/{kb_id}/knowledge?page=1&page_size=20   # 列表，返回 total/page
GET /api/v1/knowledge/{kid}                                          # 单个文档详情
GET /api/v1/chunks/{kid}?page=1&page_size=20                         # 查看解析出的分块
```

`parse_status` 的取值流转：`pending` → `processing` → `completed`，失败则为 `failed` 且 `error_message` 有内容。只有 `completed` 且 `enable_status` 为 `enabled` 的文档才会参与检索。

一个 176KB 的中文 txt 大约 60 秒内完成解析和向量化，PDF 走 OCR 会明显更久。

### 3.4 重新解析已有文档

```bash
POST /api/v1/knowledge/batch-reparse
Content-Type: application/json

{"kb_id": "...", "ids": ["文档ID", ...]}
```

返回 `{"data":{"reparse_count":1,"task_id":"..."}}`。**源文件一直保存在 `data-files` 卷里，所以修复配置类问题不需要重新上传**，这是 `failed` 文档的首选补救手段。`process_config` 可选，不传就用知识库当前配置。

同样是异步，提交后轮询 `parse_status` 到 `completed` 或 `failed`。实测 35KB 的 txt 约 20-30 秒，47MB 的扫描版 PDF 约 20 秒。

批量重跑务必串行、一篇一篇等完成。这台机器 3.6GB 内存、docreader worker 为 2，并发提交几十篇会撞上内存峰值；大 PDF 还会大量写盘（38 篇扫描件产生 9398 张图片、约 2.5GB），跑之前先确认 `df -h /` 有足够余量。可直接用 `reparse_failed.sh`（带内存和磁盘保护）。

### 3.5 删除文档 —— 注意异步陷阱

```bash
DELETE /api/v1/knowledge/{kid}
```

返回的是 `{"data":{"task_id":"..."},"message":"Delete task submitted"}` —— **删除是提交后台任务，不是立即完成**。

> **重要**：删除任务完成前，**不要上传同名（同 `title`）的文件**。实测中这样做会导致后台任务把新上传的同名文档一起清掉，并且 `embeddings` 表被清空而 BM25 索引残留失效指针，之后所有检索都会返回 500：
> `ERROR: assertion failed: item_pointer_is_valid(ctid) (SQLSTATE XX000)`
>
> 正确做法是删除后轮询 `GET /api/v1/knowledge-bases/{kb_id}/knowledge`，确认目标文档已从列表消失（通常几十秒），再上传新文件。要替换同名文档，更稳妥的方式是直接用不同文件名。
>
> 如果已经踩到这个坑，修复方式见文末附录。

### 3.6 检索

```bash
POST /api/v1/knowledge-bases/{kb_id}/hybrid-search
Content-Type: application/json

{"query_text": "挪亚方舟洪水", "top_k": 3}
```

参数名是 **`query_text`**（写成 `query` 会返回 `query_text is required`）。返回向量检索与 BM25 关键词检索经 RRF 融合后的结果，每条含 `content`、`score`、`knowledge_id`、`chunk_id`。

`score` 是 RRF 融合分数，量级很小（0.01 上下属于正常高分），不要按余弦相似度的尺度去理解。实测 `top_k` 对返回条数的约束较松，会返回全部融合命中，建议自行截断。

### 3.7 问答（RAG 对话）

分两步：先建会话，再在会话里提问。

```bash
# 1) 创建会话
POST /api/v1/sessions
{"knowledge_base_id": "{kb_id}", "title": "会话标题"}
# → 返回 {"data":{"id":"<session_id>", ...}}

# 2) 提问（SSE 流式响应）
POST /api/v1/knowledge-chat/{session_id}
Content-Type: application/json
Accept: text/event-stream

{"query": "挪亚在洪水中带了几个儿子进方舟？"}
```

响应是 SSE 流，每个事件形如：

```
event:message
data:{"response_type":"answer","content":"亚在洪","done":false,"data":{...}}
```

`response_type` 的几种值：`agent_query`（会话建立，携带 message_id）、`answer`（答案增量，拼接 `content` 即为完整回答）、以及引用来源等。`done` 为 `true` 时该类型的流结束。

其他会话操作：

```bash
GET    /api/v1/sessions              # 会话列表
DELETE /api/v1/sessions/{session_id} # 删除会话（同步返回）
```

### 3.8 模型配置

```bash
GET /api/v1/models
```

返回已配置的模型。当前实例是阿里百炼：`qwen-plus`（类型 `KnowledgeQA`）和 `text-embedding-v4`（类型 `Embedding`），`source` 均为 `remote`。

---

## 四、文件编码：必须是 UTF-8

**这是最容易踩的坑。** 解析器按 UTF-8 读取文本，GBK/GB2312 编码的 txt 会被解析成乱码（汉字的高位字节被丢弃，只剩数字和标点残留），而且 `parse_status` 仍然是 `completed`，不会报错，很容易被忽略。

导入前先检查，不是 UTF-8 的先转码：

```bash
# 检查：显示 ISO-8859 text 或 Non-ISO extended-ASCII 就不是 UTF-8
file "01 创世记.txt"

# 批量转码：自检不是合法 UTF-8 的才转，已是 UTF-8 的跳过，不会转坏
for f in *.txt; do
  if ! iconv -f UTF-8 -t UTF-8 "$f" >/dev/null 2>&1; then
    iconv -f GB18030 -t UTF-8 "$f" > "utf8_$f" && echo "已转换: $f"
  fi
done
```

用 GB18030 而不是 GBK 作为源编码，因为它是 GBK 的超集，能覆盖更多生僻字。转码后文件会变大约 50%（同一汉字 GBK 占 2 字节，UTF-8 占 3 字节），这是正常的。

Python 侧可以用 `chardet` 自动判定，见下面的完整示例。

---

## 五、扫描版 PDF：状态是 completed，内容却是空的

和 GBK 乱码是同一类陷阱——**不报错，但导进去的东西没有检索价值**。

扫描件 PDF 没有文字层，docreader 不做 OCR，它会把每一页转成 jpg 存进 `data-files` 卷，分块内容就只是一串图片占位符：

```
![[性格改变孩子一生].肖悦.扫描版_page_1.jpg](resource://MRswwJRxdVkEJHqQz0JZ3A)
![[性格改变孩子一生].肖悦.扫描版_page_2.jpg](resource://03zM-UtTISMBCMwJM-L7qQ)
```

2026-09-16 实测的 38 篇扫描版 PDF：`parse_status` 全是 `completed`，但剔除图片占位符后平均只剩 1014 个字符（相当于半页文字），而同批 txt 文档平均是 90294 字符。附带产生 9398 张页面图片，占掉约 2.5GB 磁盘。

导入后用这条 SQL 自查，`文字` 一列明显偏小（一本书低于 5000 字符）的就是没提上来：

```sql
SELECT k.title, k.file_type, round(k.file_size/1048576.0,1) AS mb,
       sum(length(regexp_replace(c.content, '!\[.*?\]\(resource://[^)]*\)', '', 'g'))) AS 文字,
       sum((length(c.content) - length(replace(c.content,'resource://','')))/11) AS 图片
FROM knowledges k JOIN chunks c ON c.knowledge_id = k.id AND c.deleted_at IS NULL
WHERE k.deleted_at IS NULL
GROUP BY k.id, k.title, k.file_type, k.file_size
ORDER BY 文字;
```

注意正则要用 `!\[.*?\]\(resource://[^)]*\)`，不能用 `!\[[^]]*\]`——书名里常带方括号（如 `[六A的力量]`），后者匹配不到。

要让扫描件可用只有两条路：本地先 OCR 成 txt 再上传（推荐，可控且不占服务器资源），或者给 WeKnora 配一个视觉模型让它识图（每页一次调用，费用和耗时都要先估算）。当前实例只配了 `qwen-plus` 和 `text-embedding-v4`，没有视觉模型。

---

## 六、完整示例（Python）

批量导入一个目录下的文档，自动处理编码并等待解析完成。

```python
#!/usr/bin/env python3
"""WeKnora 批量导入脚本。依赖: pip install requests chardet"""
import time
from pathlib import Path

import chardet
import requests

BASE = "http://124.222.77.32:8081"
API_KEY = "sk-替换成你的key"
KB_ID = "e8ec2c8d-5a8f-4999-a4f2-ff42d6bf54ce"

session = requests.Session()
session.headers["X-API-Key"] = API_KEY


def ensure_utf8(path: Path) -> tuple[bytes, str]:
    """返回 (UTF-8 字节流, 说明)。非 UTF-8 的文本自动转码，二进制原样返回。"""
    raw = path.read_bytes()
    if path.suffix.lower() not in {".txt", ".md", ".csv"}:
        return raw, "二进制/结构化文件，原样上传"
    try:
        raw.decode("utf-8")
        return raw, "已是 UTF-8"
    except UnicodeDecodeError:
        enc = chardet.detect(raw[:50000])["encoding"] or "gb18030"
        # GBK 系列统一按 GB18030 解（超集，覆盖生僻字）
        if enc.lower() in {"gb2312", "gbk", "gb18030"}:
            enc = "gb18030"
        return raw.decode(enc).encode("utf-8"), f"由 {enc} 转为 UTF-8"


def upload(path: Path) -> str:
    data, note = ensure_utf8(path)
    print(f"[上传] {path.name} ({note})")
    resp = session.post(
        f"{BASE}/api/v1/knowledge-bases/{KB_ID}/knowledge/file",
        files={"file": (path.name, data, "text/plain")},
        timeout=300,
    )
    resp.raise_for_status()
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"上传失败: {body}")
    return body["data"]["id"]


def wait_parsed(kid: str, timeout: int = 900) -> str:
    """轮询到解析结束。解析是异步的，上传返回不代表可检索。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = session.get(f"{BASE}/api/v1/knowledge/{kid}", timeout=30).json()["data"]
        status = d["parse_status"]
        if status == "completed":
            return status
        if status == "failed":
            raise RuntimeError(f"解析失败: {d.get('error_message')}")
        time.sleep(5)
    raise TimeoutError(f"解析超时: {kid}")


def search(query: str, top_k: int = 3) -> list[dict]:
    resp = session.post(
        f"{BASE}/api/v1/knowledge-bases/{KB_ID}/hybrid-search",
        json={"query_text": query, "top_k": top_k},   # 注意字段名是 query_text
        timeout=120,
    )
    return resp.json().get("data") or []


def ask(question: str) -> str:
    """建会话并流式提问，拼接出完整回答。"""
    sid = session.post(
        f"{BASE}/api/v1/sessions",
        json={"knowledge_base_id": KB_ID, "title": question[:20]},
        timeout=30,
    ).json()["data"]["id"]

    answer = []
    with session.post(
        f"{BASE}/api/v1/knowledge-chat/{sid}",
        json={"query": question},
        headers={"Accept": "text/event-stream"},
        stream=True,
        timeout=300,
    ) as r:
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            import json
            evt = json.loads(line[5:])
            if evt.get("response_type") == "answer":
                answer.append(evt.get("content", ""))
    return "".join(answer)


if __name__ == "__main__":
    for f in sorted(Path("./docs").glob("*.txt")):
        kid = upload(f)
        wait_parsed(kid)          # 串行等待，避免并发压垮 4 核机器
        print(f"  完成: {kid}")

    for hit in search("挪亚方舟洪水"):
        print(f"[{hit['score']:.4f}] {hit['content'][:80]}")

    print(ask("挪亚在洪水中带了几个儿子进方舟，他们叫什么名字？"))
```

**并发建议**：这台服务器是 4 核 3.6GB 内存，docreader 的解析 worker 已下调为 2。批量导入请**串行**，或最多 2 个并发，否则容易触发内存峰值。大批量导入建议放在低峰期。

---

## 七、错误码

响应统一为 `{"success": bool, "data": ..., "error": {"code": int, "message": str}}`。

| HTTP | code | 含义与处理 |
| --- | --- | --- |
| 401 | — | 未认证。检查是否用了 `X-API-Key`（API Key 不能用 `Authorization: Bearer`） |
| 403 | — | 权限不足。API Key 访问租户管理端点会这样，改用 JWT |
| 400 | 1000 | 请求参数错误，如检索漏了 `query_text` |
| 400 | 1010 | 参数校验失败，如创建 API Key 漏了 `capabilities` |
| 404 | — | 端点或资源不存在，检查路径拼写 |
| 500 | 1007 | 服务端/数据库错误。若 message 含 `item_pointer_is_valid(ctid)`，是 BM25 索引不一致，见附录 |

---

## 附录：修复 BM25 索引不一致

症状：所有检索返回 500，`message` 为 `ERROR: assertion failed: item_pointer_is_valid(ctid) (SQLSTATE XX000)`；`app` 日志里有 `[Postgres] Keywords retrieval failed`。

成因：删除任务清空了 `embeddings` 表，但 `embeddings_search_idx`（bm25 索引）残留指向已删除行的指针。

在服务器上执行（`/home/chenyj/my_knowledge` 目录下）：

```bash
DBU=$(grep '^DB_USER=' .env | cut -d= -f2)
DBN=$(grep '^DB_NAME=' .env | cut -d= -f2)

# 1) 清理软删除残留
docker exec WeKnora-postgres psql -U "$DBU" -d "$DBN" -c \
  "DELETE FROM chunks WHERE deleted_at IS NOT NULL; DELETE FROM knowledges WHERE deleted_at IS NOT NULL;"

# 2) VACUUM 与重建索引（必须分两条执行，VACUUM 不能在事务块内运行）
docker exec WeKnora-postgres psql -U "$DBU" -d "$DBN" -c "VACUUM ANALYZE embeddings;"
docker exec WeKnora-postgres psql -U "$DBU" -d "$DBN" -c "REINDEX INDEX public.embeddings_search_idx;"
```

修复后检索会恢复，但**被误删的文档需要重新上传**。
