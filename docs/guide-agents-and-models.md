# 怎么接模型、怎么加 Agent

这份文档只回答两个问题：**换模型提供商要改哪里**、**加一个 Agent 要改哪里**。顺带把「为什么代码里没有 `create_agent`」讲清楚。

---

## 0. 先解三个疑惑

### 疑惑一：这不是 LangChain 吗，怎么没有 `create_agent`？

因为 `create_agent` 在 **`langchain`** 这个包里，而项目之前只装了 `langchain-core`（模型、消息、工具的抽象层）和 `langgraph`（图运行时）。`langchain-openai` 只依赖 `langchain-core`，不会把 `langchain` 带进来。

```python
# 之前：ModuleNotFoundError: No module named 'langchain'
from langchain.agents import create_agent
```

现在已安装 `langchain==1.5.2`，并新增了 `simple_agent`——它就是 `create_agent` 写的：

```python
# app/agents/implementations/simple_agent.py（核心 8 行）
async def build():
    resolved = await model_resolver.resolve_full(get_settings().model_config_for("simple_agent"))
    return create_agent(
        model=resolved.model,
        tools=TOOLS,
        system_prompt=SYSTEM_PROMPT,
        name="simple_agent",
    )

graph = build
```

### 疑惑二：那 `assistant_agent` 里那一堆 `StateGraph`、`add_node` 是什么？

是**同一个东西的手写版**。`create_agent` 内部也是拼一张 LangGraph 图（`model → tools → model`），只是它把节点和边写好了。手写版多出来的是：规则降级、循环保护、自定义路由。

### 疑惑三：Aegra 又是什么？它和 LangGraph 什么关系？

Aegra 是「把 LangGraph 图跑成 HTTP 服务」的那一层（自托管版的 LangGraph Platform）。它负责 thread、run、checkpoint、SSE、断线重连；它**不关心你的图是 `create_agent` 生成的还是手写的**，只要拿到一个可编译的图就行。

| 层 | 项目里的位置 | 负责什么 |
|---|---|---|
| LangChain Core | `langchain_core.messages` / `langchain_core.tools` | 消息、模型、工具的标准接口 |
| LangChain | `langchain.agents.create_agent` → `simple_agent.py` | 预置 Agent 工厂：模型 + 工具 + 提示词 → 图 |
| LangGraph | `StateGraph` → `assistant_agent.py` | 自己画节点和边，控制流程 |
| Aegra | `aegra serve` + `aegra.json` | 把图跑成服务：thread / run / checkpoint / SSE |
| 本项目 | `app/` | 认证、隔离、会话、Workflow、配额、错误信封 |

一句话：**LangChain 提供零件，LangGraph 提供装配方式，Aegra 提供运行时，本项目提供多租户的外壳。**

---

## 1. 两条路线怎么选

| | `create_agent`（`simple_agent`） | 手写 `StateGraph`（`assistant_agent`） |
|---|---|---|
| 代码量 | 约 30 行 | 约 250 行 |
| 工具循环 | 自动 | 自己加 `tools` 节点和条件边 |
| 自定义状态字段 | 需要 middleware | 直接改 state |
| 循环上限 | 由框架处理 | 自己写 guard（`AGENT_MAX_TOOL_ITERATIONS`） |
| 不支持 tool calling 的模型 | 只能传 `tools=None` | 可降级为规则路由，工具仍可用 |
| 适用 | 大多数业务 Agent | 流程特殊、需要强控制 |

**建议**：先照 `simple_agent.py` 抄一份，跑通再说。真的需要特殊流程再换手写。

---

## 2. 怎么改模型提供商

### 2.1 关键认知：模型不是代码，是数据库里的一行

`biz.model_configs` 表：

| 字段 | 含义 |
|---|---|
| `name` | 配置名，Agent 按这个名字引用（唯一） |
| `provider` | 只有两种：`openai`（任何 OpenAI 兼容接口）、`echo`（离线假模型） |
| `base_url` | 接口地址，留空用官方地址 |
| `model_name` | 模型名，如 `gpt-4o-mini`、`deepseek-chat` |
| `api_key_encrypted` | **只存引用**：`env:DEEPSEEK_API_KEY`，不存明文 |
| `temperature` / `max_tokens` / `timeout_seconds` | 调用参数 |
| `enabled` | 关闭后引用它的 Agent 会直接报错 |

所以「换提供商」= 改这一行（或加一行），**不是改 Python 代码**。DeepSeek、通义、Kimi、vLLM、Ollama、one-api 这类都算 `openai` 兼容，只改 `base_url` + `model_name` 就行。

### 2.2 三步换掉现有配置

> **DeepSeek 已经内置**：`seed/models.example.json` 里已有一条 `deepseek` 配置（`https://api.deepseek.com/v1` + `deepseek-chat` + `env:DEEPSEEK_API_KEY`），你只需要在 `.env` 填好 `DEEPSEEK_API_KEY`，执行 `seed-models` 即可。下面以 DeepSeek 为例说明这类改造的完整形态。

**第一步**：改 `seed/models.example.json`（或你自己维护的 json）：

```json
[
  {
    "name": "demo",
    "provider": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "model_name": "deepseek-chat",
    "api_key_encrypted": "env:DEEPSEEK_API_KEY",
    "temperature": 0.3,
    "max_tokens": 1024,
    "timeout_seconds": 60,
    "extra": {},
    "enabled": true
  }
]
```

**第二步**：把密钥放进环境变量（`.env` 或容器环境）：

```bash
DEEPSEEK_API_KEY=sk-xxxxxxxx
```

**第三步**：写入数据库并校验：

```bash
uv run python -m app.cli seed-models --file seed/models.example.json
uv run python -m app.cli validate        # 会检查密钥引用能不能解析
uv run python -m app.cli list-models     # 只显示 env:XXX，不显示密钥本身
```

**第四步**：重启服务（`create_agent` 版在启动时编译图，必须重启；调用时解析的 Agent 最多 60 秒后生效）。

```bash
uv run aegra serve --host 127.0.0.1 --port 2026
```

### 2.3 加一条新配置，不影响旧的

`name` 唯一，多建一条即可：

```bash
# 追加到 json 后重新 seed，或在数据库里直接插一行再 seed
uv run python -m app.cli seed-models --file seed/models.example.json
```

然后让默认指向它：

```bash
# .env
DEFAULT_MODEL_CONFIG=deepseek
```

### 2.4 只给某一个 Agent 换模型

不用改代码，用约定式环境变量 `MODEL_CONFIG_<AGENT_ID>`：

```bash
# simple_agent 用 deepseek，其他 Agent 仍用默认的 demo
MODEL_CONFIG_SIMPLE_AGENT=deepseek
```

代码里读的就是这一行：

```python
# app/config.py
def model_config_for(self, agent_id: str) -> str:
    override = os.environ.get(f"MODEL_CONFIG_{agent_id.upper()}")
    return override or self.default_model_config
```

### 2.5 要接一个非 OpenAI 协议的 SDK（如 Anthropic 官方 SDK）

这时才需要动代码，位置在 `app/services/models.py` 的 `_build`，加一个分支：

```python
SUPPORTED_PROVIDERS = ("openai", "echo", "anthropic")

def _build(self, row: ModelConfig) -> BaseChatModel:
    if row.provider == "echo":
        ...
    if row.provider == "openai":
        return self._build_openai(row)
    if row.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=row.model_name,
            anthropic_api_key=resolve_secret(row.api_key_encrypted),
            temperature=row.temperature,
            timeout=row.timeout_seconds,
        )
    ...
```

然后 `uv add langchain-anthropic`，并把这行写进 `pyproject.toml` 的固定版本列表。

### 2.6 验证换了没有

```bash
TOKEN=$(uv run python -c "import os,time,jwt;from app.config import get_settings;get_settings();print(jwt.encode({'user_id':'10001','app_id':'agriculture_app','exp':int(time.time())+3600}, os.environ['AGRI_JWT_SECRET'], algorithm='HS256'))")

curl -s -X POST http://127.0.0.1:2026/api/v1/agents/simple_agent/chat \
  -H "Authorization: Bearer $TOKEN" -H "X-App-Id: agriculture_app" \
  -H "Content-Type: application/json" \
  -d '{"message":"你好"}'
```

返回的 `assistant_message.content` 就是新模型的回答。

### 2.7 四个常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `Model configuration 'xxx' is not registered` | `name` 没 seed 进去，或写错 | `list-models` 核对 |
| `has an unresolvable key reference` | `env:XXX` 里的变量不存在 | 检查 `.env` 是否被容器读到 |
| 401 / 404 来自上游 | `base_url` 少了 `/v1`，或该网关路径不同 | 用 curl 直连网关确认 |
| 工具永远不调用 | 该模型/网关不支持 tool calling | 换支持 function calling 的模型，或用 `assistant_agent` 的规则降级 |

---

## 3. 怎么加一个 Agent

一个 Agent 在项目里由**三处**构成，缺一处就起不来：

| 位置 | 作用 |
|---|---|
| `app/agents/implementations/xxx.py` | 图的实现，必须导出名为 `graph` 的对象或工厂 |
| `aegra.json` 的 `graphs` | 告诉 Aegra 去哪加载这个图 |
| `app/agents/__init__.py` | 注册 `AgentDefinition`，决定列表展示与调用权限 |

### 3.1 路线 A：`create_agent`（推荐先走这条）

**第 1 步**：新建 `app/agents/implementations/weather_agent.py`：

```python
from typing import Any

from langchain.agents import create_agent
from langchain_core.tools import tool

from app.agents.tools import calculator          # 复用已有工具，也可以自己写
from app.config import get_settings
from app.services.models import model_resolver

SYSTEM_PROMPT = "你是天气助手，回答简洁。"


@tool
def get_weather(city: str) -> str:
    """查询城市天气（示例：真实实现请调用气象 API）。"""
    return f"{city}：晴，24℃"


async def build() -> Any:
    resolved = await model_resolver.resolve_full(get_settings().model_config_for("weather_agent"))
    return create_agent(
        model=resolved.model,
        tools=[get_weather, calculator],
        system_prompt=SYSTEM_PROMPT,
        name="weather_agent",
    )


graph = build      # 关键：Aegra 会调用这个零参数工厂来拿到编译好的图
```

**第 2 步**：在 `aegra.json` 的 `graphs` 里加一项：

```json
"weather_agent": {
  "path": "./app/agents/implementations/weather_agent.py:graph",
  "description": "Weather demo built with create_agent"
}
```

**第 3 步**：在 `app/agents/__init__.py` 注册：

```python
agent_registry.register(
    AgentDefinition(
        agent_id="weather_agent",
        name="Weather Agent",
        description="查询城市天气。",
        tags=("demo", "tool"),
    )
)
```

**第 4 步**：重启并验证：

```bash
uv run aegra serve --host 127.0.0.1 --port 2026
curl -s http://127.0.0.1:2026/api/v1/agents -H "Authorization: Bearer $TOKEN" -H "X-App-Id: agriculture_app"
```

列表里出现 `weather_agent` 就成功了。调用方式与其他 Agent 完全一样：

```bash
curl -N -X POST http://127.0.0.1:2026/api/v1/agents/weather_agent/chat/stream ...
```

### 3.2 路线 B：手写 `StateGraph`

把 `assistant_agent.py` 复制一份改名，改三处：`SYSTEM_PROMPT`、`TOOLS`、`compile(name=...)`。骨架如下：

```python
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

builder = StateGraph(MessagesState)
builder.add_node("plan", plan_node)              # 调模型，决定是否调工具
builder.add_node("tools", ToolNode(TOOLS))       # 执行工具
builder.add_edge(START, "plan")
builder.add_conditional_edges("plan", route, {"tools": "tools", END: END})
builder.add_edge("tools", "plan")                # 回到 plan，形成循环
graph = builder.compile(name="my_agent")
```

其余注册步骤与路线 A 完全一致。

### 3.3 缺了会报什么错

| 缺哪一步 | 现象 |
|---|---|
| 没在 `aegra.json` 加 | 启动失败：`Agents registered without a graph in aegra.json: weather_agent` |
| 没在 registry 注册 | 图能跑，但 `/api/v1/agents` 列表里没有，也调不到（`404 agent_not_found`） |
| 文件里没有导出 `graph` | 启动失败：`Graph export not found: graph` |
| 应用没权限 | `403 forbidden`，需要把 agent_id 加进 `allowed_agents` |

### 3.4 关于「记忆」和「会话」

**不要自己存历史**。Aegra 用 `thread_id` 把消息存进 checkpoint，你只要在图上声明 `MessagesState`（或 `AgentState`），多轮上下文自动带上。业务侧的 `conversations` / `conversation_messages` 只负责给前端展示。

### 3.5 关于工具

- 工具就是普通函数加 `@tool` 装饰器（`langchain_core.tools`），第一个字符串是给模型看的说明。
- 工具里**不要**做鉴权：鉴权在进入路由前已经完成，工具拿不到也不需要 `app_id` / `user_id`。如果工具需要按租户取数，通过 state 或闭包传入。
- 单个工具抛异常不会打断整轮（框架会转成一条错误结果给模型），但会浪费一次模型调用，能提前校验就提前校验。
- **优先用官方集成，别自己包 HTTP**：需要联网搜索就用 `langchain-tavily` 的 `TavilySearch`，需要数据库就用 `langchain-community` 里对应的工具。参数校验、超时、空结果提示、异常兜底都已经写好，你只需覆盖名字、描述和密钥来源——参考 `app/agents/web_search.py`（约 50 行，其中一半是注释）。
- **工具里可以再调一次模型**：当一步需要稳定的结构化产出（例如「症状 → 病害方向 → 农药关键词」），把它做成独立工具比塞进主提示词更好——格式可控、失败可降级、还能单独指定更便宜的模型（`MODEL_CONFIG_PESTICIDE_RECOMMENDATION`）。参考 `app/agents/pesticide.py` 的 `PesticideRecommendation`：输入作物/生长期/症状，返回可检索的关键词，用量与安全间隔期一律不答，交给登记信息核实。

### 3.6 硬约束用中间件，不要拿提示词当开关

「每次最多搜 3 次」「最多循环 5 轮」这类要求，写进提示词只能算建议，模型该超还是超。`create_agent` 的中间件是这类约束的落点，`bingfeng_agent.py` 就是完整例子：

```python
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware, dynamic_prompt

@dynamic_prompt
def prompt_with_today(request) -> str:          # 每次调模型时重新计算
    return f"{SYSTEM_PROMPT}\n\n今天是 {_today()}。"

agent = create_agent(
    model=model,
    tools=[search_tool],
    system_prompt=SYSTEM_PROMPT,
    middleware=[
        prompt_with_today,
        # 每次 run 只放行 3 次 web_search；第 4 次起用一条
        # "已达上限"的 ToolMessage 回给模型，让它收口作答
        ToolCallLimitMiddleware(tool_name="web_search", run_limit=3, exit_behavior="continue"),
    ],
    name="bingfeng_agent",
)
```

两个细节值得记：

- `run_limit` 是**每次调用**计数（`thread_limit` 才是整条会话累计），所以刷新预算不需要清任何状态。
- 模型一次并行发 4 个搜索时，中间件按数量裁到 3 个，超出的调用不会真的打到 API。

### 3.7 图片输入怎么走

农户拍张病叶照片是最自然的入口，所以 `/chat` 和 `/chat/stream` 都吃 `images`：

```json
{"message": "这叶子怎么了", "images": [{"url": "https://example.com/leaf.jpg"}]}
```

链路是这样的，中间没有任何自定义消息格式：

```text
ChatRequest.images ──归一──> [{"type":"image_url","image_url":{"url": ...}}]
                        │
                        ├─ url 原样透传（模型服务自己取图）
                        └─ base64 解码 → 按魔数核对格式 → 重新编码成 data: URL
                        ↓
   input={"messages":[{"role":"user","content":[text块, image块...]}]}
                        ↓
   Aegra → LangGraph convert_to_messages → HumanMessage(content=[...])
                        ↓
   ChatOpenAI 请求体里的 content 数组（OpenAI 兼容，data URL 也认）
```

几个取值上的判断：

| 事情 | 做法 | 原因 |
|---|---|---|
| 校验时机 | 进运行时之前，schema 层做 | 400/422 比跑到一半失败便宜，也不浪费配额 |
| 格式判定 | 看文件头魔数，不信任 `mime_type` | 调用方经常把 png 声明成 jpeg |
| 体积上限 | 单张 5MB、每条最多 4 张 | base64 膨胀 1/3，再多就是在给模型塞垃圾 |
| 是否入库 | 不入库，只留「（附图 N 张）」 | 几 MB 的 base64 进 PG 会把消息表和备份一起拖垮 |
| 模型要求 | 换成视觉模型，按 Agent 覆盖 | 纯文本模型收到 image 块会直接报错 |

工具不需要改：图片在 `HumanMessage` 里，`web_search` 和 `PesticideRecommendation` 照常拿文字参数干活。反过来，提示词里"不要对图片做联网识图"这类约束仍然有效，因为工具只看得到文字。

---

## 4. FAQ

**Q：Agent 为什么不是 class？**
因为框架要的是「一张可执行的图」，不是一个对象。业务身份、权限、会话这些状态都在图外面（`AuthContext` + checkpoint），图本身保持纯函数式，才好复用和测试。

**Q：为什么模型配置放数据库，不放 `.env`？**
因为要支持多租户/多 Agent 使用不同模型，并且要能不重启就换。密钥仍放环境变量，库里只存 `env:` 引用。

**Q：改了 `model_configs` 为什么没立刻生效？**
两处缓存：`MODEL_CONFIG_CACHE_TTL_SECONDS`（默认 60 秒，调用时解析的 Agent）；`create_agent` 版在启动时编译图，必须重启。

**Q：`simple_agent` 为什么用 `echo` 时没有工具？**
`echo` 是离线假模型，不支持 `bind_tools`，传工具会在构建时直接报错，所以按 provider 跳过（见 `simple_agent.py` 的 `TOOL_CALLING_PROVIDERS`）。

**Q：能不能一个 Agent 同时暴露多个接口/多种输出？**
目前一个 Agent 一个图。需要多形态就注册多个 agent_id，内部复用同一批工具与提示词。

**Q：我改了图，需要重新跑迁移吗？**
不需要。迁移只跟数据表结构有关；改图只重启服务。

---

## 5. 五分钟自检清单

```bash
# 1. 依赖在
uv pip list | grep -E "langchain|langgraph"

# 2. 模型配置在
uv run python -m app.cli list-models

# 3. 密钥能解析
uv run python -m app.cli validate

# 4. 图的注册没漏
uv run python -c "from app.agents import agent_registry; print([a.agent_id for a in agent_registry.list()])"

# 5. 图能编译
uv run python -c "from app.agents.implementations.simple_agent import build; print('ok')"

# 6. 服务能起、接口能通
uv run pytest -q
```
