# WeKnora MCP 对接说明（桌面助手接入）

结论：**WeKnora 官方提供 MCP server，支持 28 个工具，可以接入任何遵循 MCP 标准的桌面助手。**

本文所有内容均在本机实例（`124.222.77.32`）上实测验证：MCP 协议握手、工具清单、文件导入、检索、路径白名单拦截、stdio 与 HTTP 两种传输。

---

## 一、先搞清楚一个前提

MCP server **不在官方发布的 Docker 镜像里**。官方 Docker Hub 只有 4 个镜像（`weknora-app`、`weknora-docreader`、`weknora-ui`、`weknora-sandbox`），编排文件里的 `mcp` 服务只有 `build:` 段没有 `image:`，必须本地构建。

好在官方同时发布了 PyPI 包，这是最省事的途径：

```
包名: tencent-weknora-mcp
版本: 1.1.1（实测）
要求: Python >= 3.10
依赖: mcp>=2, requests, starlette, uvicorn
```

它本质上是对 WeKnora REST API 的一层封装，通过 `WEKNORA_BASE_URL` + `WEKNORA_API_KEY` 调用后端。**所以它跑在哪台机器都可以**，这决定了下面两种方案的选择。

---

## 二、两种接入方案，优先选方案 A

| | 方案 A：装在你的电脑上 | 方案 B：装在服务器上 |
| --- | --- | --- |
| 传输方式 | stdio（助手直接拉起进程） | Streamable HTTP 或 SSE |
| 能否导入本机文件 | **能** | 不能，只能读服务器上的文件 |
| 助手兼容性 | 最好，几乎所有 MCP 客户端都支持 stdio | 取决于助手是否支持远程 MCP |
| 需要开放端口 | 不需要 | 需要（8082 等），且必须设鉴权 token |
| 服务器资源占用 | 无 | 约 100MB 内存 |

**关键差异在文件导入。** `create_knowledge_from_file` 工具的描述原文是 "Create knowledge from a local file on the **server filesystem**" —— 这里的 "server" 指**运行 MCP server 的那台机器**。所以：

- 方案 A：MCP server 在你电脑上跑，`file_path` 就是你电脑上的路径，助手可以直接把你本地的文档导进知识库。这正是桌面助手场景要的。
- 方案 B：MCP server 在服务器上跑，`file_path` 只能是服务器上的路径，你电脑上的文件它读不到。

本机内存只剩约 1GB（3.6GB 已用 2.3GB），方案 A 还能顺带省下这份开销。**除非你的助手不支持 stdio，否则选方案 A。**

---

## 三、方案 A：装在你的电脑上（推荐）

### 3.1 准备 API Key

在 WeKnora 界面「设置 → 集成 / API」里创建，或参考 `API对接说明.md` 第 2.1 节用 API 创建。注意返回体里要取的是 **`token`** 字段（`sk-` 开头），不是 `api_key` 字段。

### 3.2 安装 uv

`uvx` 能免安装直接运行 PyPI 包，是最干净的方式：

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

> 国内网络从 PyPI 拉包可能很慢（实测服务器上 `uvx` 首次运行 5 分钟未完成）。建议配置镜像源，在下面的 `env` 里加一条：
> `"UV_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple"`
>
> 或者改用 pip 预装：`pip install -i https://pypi.tuna.tsinghua.edu.cn/simple tencent-weknora-mcp`，然后把 `command` 换成 `weknora-mcp-server`（脚本会装进 Python 的 Scripts/bin 目录）。

### 3.3 助手侧配置

绝大多数桌面助手（Claude Desktop、Cursor、KiloCode、以及其他遵循 MCP 标准的客户端）都用同一套 `mcpServers` JSON 结构。找到你助手的 MCP 配置文件或配置界面，填入：

```json
{
  "mcpServers": {
    "weknora": {
      "command": "uvx",
      "args": ["--from", "tencent-weknora-mcp", "weknora-mcp-server"],
      "env": {
        "WEKNORA_BASE_URL": "http://124.222.77.32:8081/api/v1",
        "WEKNORA_API_KEY": "sk-替换成你的key",
        "MCP_TRANSPORT": "stdio",
        "MCP_ALLOWED_UPLOAD_DIRS": "D:\\我的文档\\知识库",
        "UV_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple"
      }
    }
  }
}
```

三个必须注意的点：

1. **`WEKNORA_BASE_URL` 结尾必须是 `/api/v1`**，漏了会所有工具都失败。
2. **`MCP_ALLOWED_UPLOAD_DIRS` 不填就无法导入文件**。这是文件路径白名单，只有该目录（及子目录）下的文件允许上传。实测白名单外的路径（如 `/etc/hostname`）会被拒绝并返回 `Error executing tool create_knowledge_from_file`。多个目录用逗号分隔。
3. 腾讯云安全组需放通 **8081**（HTTP 访问 WeKnora 前端/API）。

如果你的助手不是用 JSON 配置而是图形界面，对应填：命令 `uvx`，参数 `--from tencent-weknora-mcp weknora-mcp-server`，然后逐条添加上面的环境变量。

### 3.4 验证连接成功

配好后重启助手，MCP 工具列表里应该出现 28 个 `weknora` 工具。可以让助手执行「列出所有知识库」，正常会返回你的「和合本修订版」。

> **探活请用只读调用。** 别靠「上传一篇文档看能不能成功」来做健康检查——这是共享书库，
> 探活文档会留在正式书库里，混进书目和检索结果。2026-09-18 就在「和合本修订版」里
> 清掉了一篇客户端留下的 `_health_*.txt`。要探活就调 `list_knowledge_bases`，
> 它足以验证网络、API Key 和服务状态；如果确实要测上传链路，传完请自己调
> `delete_knowledge` 删掉。

MCP server 的诊断信息全部输出到 stderr（stdout 是 JSON-RPC 通道），启动正常时 stderr 会有：

```
=== WeKnora MCP Server 环境检查 ===
Base URL: http://124.222.77.32:8081/api/v1
API Key: 已设置
正在启动 WeKnora MCP Server (transport=stdio)...
```

---

## 四、方案 B：装在服务器上（远程接入）

适用于助手不支持 stdio、或你希望多台电脑共用一个 MCP 端点的情况。

### 4.1 在服务器上启动

服务器已有 `uv`（`/root/.local/bin/uv`）。用国内源装好后以 HTTP 传输启动：

```bash
# 安装
uv venv /opt/weknora-mcp-venv --python 3.10
uv pip install --python /opt/weknora-mcp-venv/bin/python \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple tencent-weknora-mcp

# 生成一个强随机鉴权 token 并记下来
openssl rand -hex 32

# 启动（建议用 systemd 托管，这里先手动验证）
WEKNORA_BASE_URL=http://127.0.0.1:8081/api/v1 \
WEKNORA_API_KEY=sk-你的key \
MCP_TRANSPORT=http \
MCP_HOST=0.0.0.0 \
MCP_PORT=8082 \
MCP_SERVER_AUTH_TOKEN=上一步生成的token \
MCP_ALLOWED_UPLOAD_DIRS=/data/weknora-upload \
/opt/weknora-mcp-venv/bin/weknora-mcp-server
```

启动成功的日志：

```
INFO:weknora_mcp_server:MCP endpoint:  http://0.0.0.0:8082/mcp
INFO:     Uvicorn running on http://0.0.0.0:8082
```

> **`MCP_SERVER_AUTH_TOKEN` 是 http/sse 传输的必填项**，不设置会直接拒绝启动并报错：
> `MCP_SERVER_AUTH_TOKEN is required for http transport.`
> 这是合理的设计 —— MCP 端点等于你知识库的完整操作权限，裸暴露在公网上等于把知识库交出去。

### 4.2 助手侧配置

端点是 `http://124.222.77.32:8082/mcp`，鉴权头用 `Authorization: Bearer <MCP_SERVER_AUTH_TOKEN>`（也接受 `X-MCP-Auth-Token`）。实测不带 token 访问返回 401，带上则正常握手。

```json
{
  "mcpServers": {
    "weknora": {
      "url": "http://124.222.77.32:8082/mcp",
      "headers": {
        "Authorization": "Bearer 你的MCP_SERVER_AUTH_TOKEN"
      }
    }
  }
}
```

不同助手对远程 MCP 的配置字段名可能不同（有的用 `url`，有的用 `type: "http"` 或 `transport`），需要参照你助手自己的文档。如果它只支持 SSE，把服务端的 `MCP_TRANSPORT` 改成 `sse` 即可。

记得放通安全组的 8082 端口。生产环境建议在前面套一层 HTTPS 反代，否则 token 是明文传输的。

---

## 五、可用工具清单（28 个，实测）

**知识库管理**：`list_knowledge_bases`、`get_knowledge_base`、`create_knowledge_base`、`delete_knowledge_base`

**文档导入与管理**：`create_knowledge_from_file`、`create_knowledge_from_url`、`list_knowledge`、`get_knowledge`、`delete_knowledge`、`list_chunks`、`delete_chunk`

**检索与问答**：`hybrid_search`、`chat`、`agent_chat`

**会话管理**：`create_session`、`get_session`、`list_sessions`、`delete_session`

**Wiki**：`wiki_search`、`wiki_read_page`、`wiki_index_view`

**模型与智能体**：`list_models`、`get_model`、`create_model`、`list_agents`、`get_agent`

**租户**：`list_tenants`、`create_tenant`

> **`create_knowledge_base` 现在可以用了**，但这是靠服务端补的兜底。这个工具不接受 embedding 模型参数，建出来的库 `embedding_model_id` 为空，导进去的文档会全部挂死在 `processing`。本实例在 2026-09-18 装了数据库触发器 `trg_kb_fill_model_defaults`，建库时自动补上 embedding、summary 模型和分块配置（见 `sql/kb_defaults_trigger.sql`）。**如果你连的是自己部署的 WeKnora 且没装这个触发器，仍然只能在网页界面建库**，详见第七节。

### 几个常用工具的实测入参

```
create_knowledge_from_file
  必填: kb_id, file_path        可选: enable_multimodel
  注意: file_path 必须在 MCP_ALLOWED_UPLOAD_DIRS 白名单内
        目标知识库必须已配置 embedding 模型，否则必然失败（见第七节）

hybrid_search
  必填: kb_id, query           可选: vector_threshold, keyword_threshold, match_count
  便利之处: kb_id 可以直接写知识库名称（如「和合本修订版」），会自动解析成 UUID

chat
  必填: session_id, query      可选: knowledge_base_ids, web_search_enabled
  注意: 工具描述明确要求提供 knowledge_base_ids，否则检索不会执行
```

> **MCP 工具的参数名和 REST API 不一致**，别混用：检索在 MCP 里是 `query` + `match_count`，在 REST 里是 `query_text` + `top_k`。

### PyPI 版本比源码少 3 个工具

GitHub main 分支源码有 31 个 `@mcp.tool()`，PyPI 1.1.1 实际暴露 28 个。缺的是 `create_knowledge_from_text`、`update_knowledge_from_text`、`list_shared_knowledge_bases`。

如果你需要「直接把一段文本存成知识」而不是走文件，得改用源码方式运行：

```bash
git clone --depth 1 https://github.com/Tencent/WeKnora.git
# 配置里 command 改为 uv，args 改为:
#   ["--directory", "/path/WeKnora/mcp-server", "run", "run_server.py"]
```

---

## 六、环境变量完整清单

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WEKNORA_BASE_URL` | `http://localhost:8080/api/v1` | **必须带 `/api/v1` 后缀** |
| `WEKNORA_API_KEY` | 空 | WeKnora 的 API Key（`sk-` 开头的 `token`） |
| `MCP_TRANSPORT` | `stdio` | `stdio` / `sse` / `http` |
| `MCP_HOST` | `127.0.0.1` | 仅 sse/http 生效 |
| `MCP_PORT` | `8000` | 仅 sse/http 生效 |
| `MCP_SERVER_AUTH_TOKEN` | 空 | **sse/http 必填**，否则拒绝启动 |
| `MCP_ALLOWED_UPLOAD_DIRS` | 空 | 文件上传白名单，**留空则无法导入文件** |
| `WEKNORA_CHAT_TIMEOUT` | `300` | 问答读超时（秒） |
| `WEKNORA_VERIFY_SSL` | `true` | 自签证书时可设 `false` |

---

## 七、几个必须知道的坑

### 用 MCP 建的知识库导不进文档（最严重的一个）

`create_knowledge_base` 只接受名称和描述，**建出来的知识库 `embedding_model_id` 是空字符串**。往这种库里导文件，docreader 能解析出文本，但向量化时报 `model ID cannot be empty`，任务挂死在 `processing`，约 2 小时后被 housekeeping 改成 `failed`，错误信息写的是 `task stuck in processing > 2h10m0s, recovered by housekeeping`——完全看不出真实原因。

表现就是**知识库建好了，但里面一篇文档都没有**。2026-09-15 通过脚本建的 81 个库全部中招，153 篇文档无一成功。2026-09-17 又复发一次：客户端用 MCP 建了「中文公版书」，传进去的《论语》《孙子兵法》双双挂在 `processing`，从客户端看就是「服务端卡住了」——其实服务端负载只有 0.13，根本没在干活。

这个字段事后无法通过 API 修改（`UpdateKnowledgeBase` 的请求体里没有它），只能改数据库再用 `batch-reparse` 重跑。

**本实例已装触发器兜底**：`sql/kb_defaults_trigger.sql` 在 `knowledge_bases` 上加了 BEFORE INSERT 触发器，建库时若 `embedding_model_id` 为空就自动从 `models` 表取 active 的 Embedding 模型填上，`chunk_size` 为 0 时也一并补成默认分块配置。字段非空时不介入，界面建库不受影响。装了之后 MCP 建库已实测正常：建库、上传、25 秒内 `completed`。

自己部署的实例如果没装这个触发器，仍要遵守：

- **建库一律在网页界面做**，界面会强制要求选 embedding 模型；
- 让助手导文档前，先让它 `get_knowledge_base` 确认目标库的 `embedding_model_id` 不为空；
- 导完别只看「提交成功」，要 `get_knowledge` 看到 `completed` 才算数。

排查细节和修复步骤见 `API对接说明.md` 3.1 与 3.4 节。

### 扫描版 PDF 会「假成功」

扫描件没有文字层，docreader 不做 OCR，只把每页转成图片，分块内容全是 `![xxx_page_1.jpg](resource://...)` 这样的占位符。`parse_status` 显示 `completed`，但检索不到任何内容。实测 38 篇扫描版 PDF 平均只提取到 1014 个字符，而同批 txt 平均 90294 字符。

让助手导 PDF 后，可以要求它调 `list_chunks` 抽查内容，看是不是只有图片占位符。详见 `API对接说明.md` 第五节。

### 文件编码

通过 MCP 导入的文件同样走 docreader 解析，**GBK/GB2312 编码的 txt 会变成乱码，而且 `parse_status` 仍然显示 `completed` 不报错**。导入前务必转成 UTF-8，方法见 `API对接说明.md` 第四节。

### 导入是异步的

`create_knowledge_from_file` 立刻返回，`parse_status` 是 `pending`。要让助手确认导入成功，得再调 `get_knowledge` 查状态，等到 `completed`。直接问「导入好了吗」，助手看到 `pending` 可能会误报成功。

### 删除也是异步的，且有个危险陷阱

`delete_knowledge` 返回的是后台任务 ID。**删除任务跑完之前不要导入同名文件** —— 实测这样做会导致后台任务把新导入的同名文档一起删掉，并使 BM25 索引残留失效指针，之后所有检索报 500。修复方法见 `API对接说明.md` 附录。

让助手做「替换文档」这类操作时尤其要留意，建议直接用不同文件名。

### 并发

这台服务器 4 核 3.6GB 内存，docreader 解析 worker 已下调为 2。如果让助手批量导入几十个文件，请要求它串行处理，否则容易撞上内存峰值。

---

## 八、如果连不上，按这个顺序排查

1. **助手里根本没出现 weknora 工具** —— MCP server 进程没起来。看助手的 MCP 日志（stderr），常见原因是 `uvx` 下载超时（换国内源）或 Python 版本低于 3.10。
2. **工具出现但调用全失败** —— 检查 `WEKNORA_BASE_URL` 是否漏了 `/api/v1`，以及域名能否解析、443 是否放通。可以先在你电脑上直接验证连通性：
   ```bash
   curl -H "X-API-Key: sk-你的key" http://124.222.77.32:8081/api/v1/knowledge-bases
   ```
   返回知识库 JSON 说明网络和 Key 都没问题，那就是 MCP 配置的问题。
3. **只有文件导入失败** —— `MCP_ALLOWED_UPLOAD_DIRS` 没设，或目标文件不在白名单目录下。
4. **远程方案返回 401** —— `Authorization: Bearer` 后面的 token 要和服务端 `MCP_SERVER_AUTH_TOKEN` 完全一致。
5. **检索返回空但文档是 completed** —— 可能是 BM25 索引问题，见 `API对接说明.md` 附录。
