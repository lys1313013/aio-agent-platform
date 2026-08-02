# Hermes / OpenClaw web_search 与 web_fetch 机制调研

> 调研日期：2026-08-02
> 目的：为本项目引入 `web_search`（网页搜索）与 `web_fetch`（网页抓取）两个内置工具提供设计依据。

## 1. 结论摘要

- OpenClaw 与 Hermes 都把"搜索"和"抓取"拆成**两个独立工具**：`web_search` 返回结构化结果列表（标题/URL/摘要），`web_fetch`（Hermes 叫 `web_extract`）负责把单个 URL 转成干净 markdown。Agent 的典型工作流是先 search 拿 URL，再逐个 fetch 读正文。
- 两者都采用**多提供商后端 + 按 API key 自动检测**的配置方式，未配置 key 时明确报错而非静默失败。
- `web_fetch` 的核心是**本地 Readability 正文提取 + 多级回退**（OpenClaw：Readability → Firecrawl → 基础 HTML 清理），并带严格的 SSRF 防护、大小限制与结果缓存。
- Hermes 的差异化设计：**搜索与抓取可分别配置不同后端**（per-capability split）、大页面用辅助 LLM 做摘要压缩、另有一组浏览器工具处理 JS 渲染/交互页面。
- 本项目目前**没有任何网络类内置工具**，可直接借鉴 OpenClaw 的工具拆分 + 安全限制 + 缓存设计，以及 Hermes 的后端抽象与大页面摘要策略。

---

## 2. OpenClaw 的机制

### 2.1 web_search

| 项 | 说明 |
|---|---|
| 所属工具组 | `group:web`，可整组启用或单独加入允许列表 |
| 输入 | 查询字符串 |
| 输出 | 结构化结果列表：标题、URL、摘要片段 |
| 默认结果数 | 5 条，可配置至 10 条 |
| 缓存 | 结果缓存 15 分钟 |
| 无 key 行为 | 直接报配置错误，不静默失败 |

**提供商自动检测**（未显式配置时按优先级检测环境变量中的 API key）：

```
Brave → MiniMax → Gemini → Grok → Kimi → Perplexity → Firecrawl → Exa → Tavily → DuckDuckGo → Ollama → SearXNG
```

共 12 个提供商，Brave 为默认/最高优先级（`BRAVE_API_KEY`）。

### 2.2 web_fetch

**参数（3 个）：**

| 参数 | 类型 | 说明 |
|---|---|---|
| `url` | string（必填） | 仅允许 http/https |
| `extractMode` | string | `"markdown"`（默认）或 `"text"` |
| `maxChars` | number | 输出截断长度，硬上限受 `maxCharsCap` 钳制（默认 50000） |

**处理流水线（4 阶段）：**

1. **Fetch** — 普通 HTTP GET，带 Chrome 风格 User-Agent 和 `Accept-Language` 头；**阻断内网/私有主机名**（SSRF 防护），重定向时重新校验目标（最多 3 次）；**不执行 JavaScript**。
2. **Extract** — 本地运行 Mozilla **Readability** 算法提取正文。
3. **Fallback** — Readability 失败时：配置了 `FIRECRAWL_API_KEY` 则走 Firecrawl API（真实浏览器渲染 + 反爬绕过）；再不行退化为**基础 HTML 清理**（仅剥标签，噪音多）。
4. **Cache** — 结果默认缓存 15 分钟（`cacheTtlMinutes: 15`）。

**配置块（json5）：**

```json5
{
  tools: {
    web: {
      fetch: {
        enabled: true,            // 默认 true
        maxChars: 50000,          // 单次输出上限
        maxCharsCap: 50000,       // maxChars 的硬上限
        maxResponseBytes: 2000000,// 响应体上限 2MB（解析前截断）
        timeoutSeconds: 30,
        cacheTtlMinutes: 15,
        maxRedirects: 3,
        readability: true,
        userAgent: "Mozilla/5.0 ...",
      },
    },
  },
}
```

**已知局限：** 对 JS 渲染页面拿到空壳或不完整内容；对登录墙/强反爬站点失效。官方建议此类场景改用浏览器工具。

---

## 3. Hermes Agent 的机制

Hermes 把网页访问分为**三层**，抓取工具名为 `web_extract`（不是 web_fetch）：

| 层 | 工具 | 职责 |
|---|---|---|
| 搜索 | `web_search` | 查询字符串 → 排序结果（标题/URL/描述），`limit` 1–100，默认 5 |
| 抓取 | `web_extract` | URL → markdown 正文 |
| 浏览器 | `browser_navigate`、`browser_snapshot`、`browser_vision` 等 10 个工具 | 登录、表单、JS 动态渲染等交互场景 |

### 3.1 后端配置

通过 `hermes tools` CLI 或 `config.yaml` 配置，支持的后端：

| 提供商 | 环境变量 | search | extract |
|---|---|---|---|
| Firecrawl（默认） | `FIRECRAWL_API_KEY` | ✔ | ✔ |
| SearXNG（自托管） | `SEARXNG_URL` | ✔ | — |
| Brave | `BRAVE_SEARCH_API_KEY` | ✔ | — |
| DDGS（DuckDuckGo） | 无需 key | ✔ | — |
| Tavily | `TAVILY_API_KEY` | ✔ | ✔ |
| Exa | `EXA_API_KEY` | ✔ | ✔ |
| Parallel | `PARALLEL_API_KEY` | ✔ | ✔ |
| xAI (Grok) | `XAI_API_KEY` | ✔ | — |

**Per-capability split**：搜索与抓取可配不同后端，例如免费的 SearXNG 做搜索 + Firecrawl 做抓取：

```yaml
web:
  search_backend: "searxng"
  extract_backend: "firecrawl"
```

### 3.2 关键行为

- **Firecrawl 为后端时 `web_search` 直接返回页面 markdown 全文**而非摘要片段——搜索结果即内容，减少一次 fetch 调用。
- **`web_extract` 大页面 LLM 摘要**：小于 5000 字符全文返回；更大页面交给辅助 LLM 摘要（模型与超时独立可配）。这是面向上下文窗口的压缩策略。
- **无后端时退化**：extract 退化为纯 HTTP 抓取，JS 页面失败。
- **Tool Gateway**（v0.10.0+）：Nous Portal 订阅者可按工具 `use_gateway: true` 走托管网关，免配 API key，网关调用计费优先于直连 key。
- 实现位于 `tools.web_tools` Python 模块，可通过 `python -m tools.web_tools` 自测。

---

## 4. 两者对比与设计要点

| 维度 | OpenClaw | Hermes |
|---|---|---|
| 工具命名 | `web_search` / `web_fetch` | `web_search` / `web_extract` |
| 提供商选择 | 单 provider，按 key 优先级自动检测（12 家） | search/extract 分别配 backend（8 家） |
| 正文提取 | 本地 Readability → Firecrawl → 基础清理 | 后端抓取（Firecrawl 等），无后端退化纯 HTTP |
| 大页面处理 | `maxChars` 截断（默认 50000） | <5000 字符全文，更大走辅助 LLM 摘要 |
| 缓存 | search/fetch 均 15 分钟 | 未见明确缓存说明 |
| 安全 | SSRF 阻断、重定向复检、2MB 响应上限、30s 超时 | 依赖后端服务 |
| JS 页面 | 建议改用浏览器工具 | 专用浏览器工具组（10 个） |
| 失败策略 | 无 key 直接报配置错误 | 无后端时退化并可能失败 |

**可借鉴的设计要点：**

1. **搜索与抓取拆成两个工具**，schema 尽量小（search 只需 query+limit；fetch 只需 url+maxChars）。
2. **Provider 抽象层**：定义统一接口（`search(query, limit) -> list[{title, url, snippet}]`），各家 API 做适配；按 env key 自动检测 + 显式配置优先。
3. **安全是 fetch 的一等公民**：SSRF 阻断（内网 IP/私有主机名）、重定向目标复检、响应体大小上限、超时、输出字符上限——缺一不可。
4. **本地 Readability 提取为主**，付费抓取 API（Firecrawl/Jina 等）作为可选 fallback。
5. **结果缓存**（15 分钟 TTL）显著降低重复抓取成本和延迟。
6. **大页面压缩**：简单截断（OpenClaw）或 LLM 摘要（Hermes），后者更保信息但引入额外调用；本项目已有上下文压缩基础设施，截断即可起步。
7. **无 key 时明确报错**，把配置指引写进错误信息。

---

## 5. 本项目现状与引入方案

### 5.1 现状

- `backend/src/aio_agent_platform/tools/builtin.py` 注册了 24 个内置工具，**无任何网络类工具**（无 web_search / web_fetch / http_request）。
- 全仓 HTTP 客户端统一为 **httpx**（`httpx>=0.28.0`），`tools/remote/executor.py` 的远程工具执行器已有 `httpx.AsyncClient(timeout=..., max_redirects=3)` 用法可参考。
- 工具注册：`Tool` dataclass（`tools/registry.py:8`）+ `register_builtin_tools(registry)`（`builtin.py:10`）显式注册，启动时由 `interface/api.py` 调用。`Tool` 字段含 `permission_level`（read|write|dangerous）、`requires_sandbox`、`timeout`。
- 配置：`core/config.py` 用 pydantic-settings 子配置类 + `env_prefix`，聚合进 `AppSettings`（`config.py:165`）。
- 可通过 MCP 挂外部搜索服务，但无内置实现。

### 5.2 建议方案

**新增模块** `tools/web.py`（或拆 `tools/web/search.py` + `tools/web/fetch.py`），在 `builtin.py` 的 `register_builtin_tools` 中注册两个工具：

```python
WEB_SEARCH = Tool(
    name="web_search",
    description="Search the web and return ranked results (title, url, snippet).",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    },
    requires_sandbox=False,      # 直接在宿主执行，沙箱默认禁网
    permission_level="read",
    timeout=30,
)

WEB_FETCH = Tool(
    name="web_fetch",
    description="Fetch a URL and extract readable content as markdown.",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "http/https URL"},
            "max_chars": {"type": "integer", "default": 20000},
        },
        "required": ["url"],
    },
    requires_sandbox=False,
    permission_level="read",
    timeout=30,
)
```

**配置**：新增 `WebSettings`（`env_prefix="WEB_"`）挂到 `AppSettings`：

```python
class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEB_")

    search_provider: str = Field(default="auto")   # auto | brave | tavily | searxng | duckduckgo
    brave_api_key: str = Field(default="")
    tavily_api_key: str = Field(default="")
    searxng_url: str = Field(default="")
    fetch_max_chars: int = Field(default=20000)
    fetch_max_response_bytes: int = Field(default=2_000_000)
    fetch_timeout_seconds: int = Field(default=30)
    fetch_max_redirects: int = Field(default=3)
    cache_ttl_seconds: int = Field(default=900)
```

**实现要点（按优先级）：**

1. **Provider 抽象**：`SearchProvider` 协议（`async def search(query, limit)`），先实现 DuckDuckGo（免 key）+ Brave/Tavily（需 key），`auto` 模式按已配置 key 检测。
2. **SSRF 防护**：解析 URL host → DNS 解析 → 拒绝私网/环回/链路本地地址（`ipaddress` 模块判定）；每次重定向后复检。这是必须做的，否则 Agent 可被诱导访问内网服务。
3. **正文提取**：`readability-lxml`（Readability 的 Python 移植）+ `markdownify` 转 markdown；失败时退化为基础 HTML 文本剥离。Firecrawl fallback 可作为后续增强。
4. **缓存**：进程内 TTL 缓存（`cachetools.TTLCache`），key 为 query/url，先做单进程即可。
5. **输出截断**：`max_chars` 截断 + 复用 executor 的统一截断兜底。
6. **失败语义**：未配置任何 provider 时返回明确错误信息（含配置指引），不静默返回空。

**暂不引入的：** Hermes 的 LLM 摘要压缩（依赖辅助模型，后续可结合本项目上下文压缩能力再做）、浏览器工具组（重量级，需要 Playwright/无头浏览器，JS 页面场景可先靠 Firecrawl fallback 覆盖）。

---

## 6. 参考资料

- [Web fetch · OpenClaw 官方文档](https://docs.openclaw.ai/tools/web-fetch)
- [Web Search & Extract · Hermes Agent 官方文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-search)
- [OpenClaw Web Search: How to Make Your Agent Actually Read the Web · Firecrawl](https://www.firecrawl.dev/blog/openclaw-web-search)
- [Web Search in Hermes Agent: What's Built In and How to Use It · Firecrawl](https://www.firecrawl.dev/blog/hermes-web-search)
- [Hermes Agent vs. OpenClaw: Features, Scraping, and Proxy Setup Compared · Decodo](https://decodo.com/blog/hermes-agent-vs-openclaw)
- [OpenClaw vs OpenHuman vs Hermes Agent: Three Architectures of the Open-Source Agent Stack](https://menuagentic.com/blogs/openclaw-vs-openhuman-vs-hermes-agent)
- [Hermes Agent Web Search: How to Wire Tavily Into a Self-Improving Agent · Tavily](https://www.tavily.com/blog/hermes-agent-web-search-how-to-wire-tavily-into-a-self-improving-agent)
- [OpenClaw Web Tools 文档镜像](https://openclawlab.com/en/docs/tools/web/)
