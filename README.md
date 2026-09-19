# book_rag — 免费开放的书本 RAG 服务

一个基于 [WeKnora](https://github.com/Tencent/WeKnora) 搭建的开源免费书本 RAG。

我把电子书做成 RAG 之后免费提供给大家用。你可以通过 **MCP** 或 **Skill** 的方式把它接入自己的个人助手，让助手获得书本内容的支持 —— 提问时它能引用书里的原文，而不是靠模型记忆去编。

目前已接入的个人助手包括 **workbuddy**、**hermes-agent**、**open-claw** 等，任何遵循 MCP 标准的客户端（Claude Desktop、Cursor 等）也都能直接用。

**平常大家使用时完全免费，全部的 RAG 都是免费开放使用的。** 整理这个仓库就是为了给大家提供方便。

---

## 书库里现在有什么

当前共 **82 个知识库、343 篇文档**，涵盖中文公版古籍、圣经注释与研经资料、教育、文集等。

完整书目和每本书的简介见 **[知识库目录.md](知识库目录.md)** —— 每篇都有 AI 生成的内容简介、分块数和字符数，可以先在那里确认有没有你要的书。每批书的来源和入库时出过什么问题，记在 [入库台账.md](入库台账.md)。

> 2026-09-18 新增「中文公版书」132 本，含《史记》《汉书》《后汉书》《三国志》等正史，
> 《红楼梦》《三国演义》《西游记》《聊斋志异》等小说，以及诸子、医书、蒙学共 113 MB。

> 原先有 38 篇扫描版 PDF 没有文字层、检索不到内容，现已逐页 OCR（9,184 页）重新入库，
> 新增 494 万字符可检索正文。剩 3 篇因繁体竖排识别成乱码或内容主体是图形而保留原样，
> 在目录里用 ⚠ 标出。处理过程与质量评估见 [扫描版PDF清单.md](扫描版PDF清单.md)。

---

## 接入方式一：MCP

MCP 适合桌面助手，接入后助手会多出一批工具（检索、列知识库、导入文件等 28 个）。

### 1. API Key

直接用下面这个公开 Key，不用申请：

```
sk-zR7iHweItYJ-pu0gvBNTuss-WJZVxBP9UxW0OEuJqUk
```

它是只读的，只能列知识库和检索，不能新增、修改、删除任何内容，请放心使用。

### 2. 安装 uv

`uvx` 可以免安装直接运行 PyPI 包，最省事：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 3. 在助手里配置

绝大多数助手都用同一套 `mcpServers` 结构，找到你助手的 MCP 配置文件填入：

```json
{
  "mcpServers": {
    "book_rag": {
      "command": "uvx",
      "args": ["--from", "tencent-weknora-mcp", "weknora-mcp-server"],
      "env": {
        "WEKNORA_BASE_URL": "http://124.222.77.32:8081/api/v1",
        "WEKNORA_API_KEY": "sk-zR7iHweItYJ-pu0gvBNTuss-WJZVxBP9UxW0OEuJqUk",
        "MCP_TRANSPORT": "stdio",
        "UV_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple"
      }
    }
  }
}
```

国内从 PyPI 拉包较慢，上面的 `UV_INDEX_URL` 已配好清华镜像。

重启助手，问一句「书库里关于挪亚方舟怎么说」试试。更多细节、两种传输方式的取舍、以及踩过的坑都写在 **[MCP对接说明.md](MCP对接说明.md)**。

---

## 接入方式二：Skill

如果你的助手支持 Agent Skill（`SKILL.md` 形式），这种方式更轻 —— 不用装任何东西，直接调 HTTP 接口。

把 [`skills/book-rag/SKILL.md`](skills/book-rag/SKILL.md) 复制到你助手的 skills 目录即可：

```bash
# 以 Cursor / Claude 为例
cp -r skills/book-rag ~/.cursor/skills/
```

Key 已经写在 `SKILL.md` 里了，复制过去就能直接用。

Skill 的工作方式是：助手先列出知识库找到相关的那本书，再对它做混合检索拿回原文片段，最后**用助手自己的模型**来组织答案。好处是不消耗服务端的大模型额度，所以对大家都免费。

---

## 直接调接口

不想用助手、想自己写程序的话，检索一条命令就能试：

```bash
curl -X POST "http://124.222.77.32:8081/api/v1/knowledge-bases/{kb_id}/hybrid-search" \
  -H "X-API-Key: sk-zR7iHweItYJ-pu0gvBNTuss-WJZVxBP9UxW0OEuJqUk" \
  -H 'Content-Type: application/json' \
  -d '{"query_text":"挪亚方舟洪水","top_k":3}'
```

注意参数名是 `query_text` 而不是 `query`。完整的接口文档、认证方式、错误码见 **[API对接说明.md](API对接说明.md)**。

---

## 欢迎提供书本资源

**如果大家有书本资源，也可以提供给我**，我来做成 RAG 加进书库，大家都能用。

可以直接发给我，也可以在这个仓库提 Issue 告诉我书名。文字版的 PDF、EPUB、TXT 都可以；扫描版的 PDF 因为没有文字层，暂时做不出可检索的内容。

---

## 联系我

有任何问题、或者要提供书本资源，欢迎加我微信联系：

<img src="wechat-qrcode.jpg" width="260" alt="微信二维码">

---

## 自己部署（可选）

想搭一套自己的也完全可以，本仓库带了可用的编排文件：

```bash
git clone https://github.com/chenyujing1234-netizen/book_rag.git
cd book_rag
cp .env.example .env    # 填入你自己的数据库密码和大模型 API Key
docker compose up -d
```

建库时**务必**带上 `embedding_model_id`、`summary_model_id` 和 `chunking_config` 三个参数，否则文档会全部解析失败 —— 这是最容易踩的坑，原因和修复办法写在 [API对接说明.md](API对接说明.md) 第 3.1 节。

### 仓库文件说明

| 文件 | 说明 |
|---|---|
| [知识库目录.md](知识库目录.md) | 全部书目及简介，由 `gen_catalog.py` 生成 |
| [扫描版PDF清单.md](扫描版PDF清单.md) | 提取不到文字的扫描件清单 |
| [API对接说明.md](API对接说明.md) | REST 接口文档，含认证、检索、问答、避坑指南 |
| [MCP对接说明.md](MCP对接说明.md) | MCP 接入详解，两种方案对比与实测结论 |
| `skills/book-rag/SKILL.md` | 可直接复制使用的 Skill 定义 |
| `gen_catalog.py` | 从数据库重新生成书目 |
| `reparse_failed.sh` | 批量重新解析失败文档，带内存和磁盘保护 |
| `docker-compose.yml` / `config/` / `nginx/` | 部署编排与配置 |

---

## 致谢与许可

书本 RAG 能力来自腾讯开源的 [WeKnora](https://github.com/Tencent/WeKnora)。

本仓库中的**脚本与文档**按 MIT 许可开放。书库中的书籍版权归原作者与出版方所有，本服务仅供个人学习研究使用，不提供原书文件下载。
