# AI Service Engine

多应用、多用户的 Agent / Workflow 服务引擎。业务系统用各自签发的 JWT 调用统一 API，引擎只认 `(app_id, user_id)`，负责执行 LangGraph 图、持久化会话与执行状态，并以 SSE 流式返回。

运行底座采用 [Aegra](https://github.com/aegra/aegra)（自托管 LangGraph Platform 替代品），业务 API 以自定义 FastAPI 路由挂载在同一个进程内。选型依据见 [`docs/adr/0001-runtime-choice.md`](docs/adr/0001-runtime-choice.md)。

## 当前进度

| 阶段 | 状态 | 内容 |
|---|---|---|
| P0 Spike | 完成 | 认证查库、自定义路由、SSE 适配、跨应用隔离、资源与迁移验证 |
| P1 基础与认证 | 完成 | 工程骨架、`biz` schema、Alembic、JWT 校验、应用缓存、隔离仓储、`RuntimeAdapter`、39 个测试 |
| P2 Agent 与会话 | 完成 | Agent Registry、会话 / 消息 API、同步 / 流式对话、多轮上下文、工具事件 |
| P3 Workflow | 完成 | Workflow Registry、同步 / 异步执行、run 状态与结果、取消与超时、98 个测试 |
| P4 交付 | 待开始 | Docker Compose、健康检查、备份、重启恢复、全量验收 |

## 架构

```text
业务系统 A / B / C
   │  Authorization: Bearer <各自 JWT>
   │  X-App-Id: <app_id>
   ▼
Aegra 进程（FastAPI）
   ├─ /api/v1       业务路由（本仓库 app/）
   ├─ 认证 handler  按 app 选密钥验签 → AuthContext
   ├─ /threads      Agent Protocol（反代层对外屏蔽）
   └─ LangGraph     图执行 + checkpoint
        │
   PostgreSQL
   ├─ public        Aegra 自己的表与 alembic_version
   └─ biz           业务表与 biz_alembic_version
```

v1 单实例默认 **不启用 Redis**（`REDIS_BROKER_ENABLED=false`）。代价是进程崩溃会丢失进行中的 run；checkpoint 不受影响，启动时会把未完成的 `workflow_runs` 标记为 `interrupted`。

## 目录

```text
app/
├── main.py                 业务 FastAPI app（Aegra 的 http.app）
├── config.py               环境配置（含 .env 加载）
├── cli.py                  应用注册 CLI
├── api/                    路由、统一错误、依赖
├── auth/                   JWT、AuthContext、应用缓存、Aegra 认证桥接
├── agents/                 Agent Registry 与示例 Agent（echo_agent / reverse_agent）
├── workflows/              Workflow Registry 与示例 Workflow（sum / text_stats）
├── services/               会话编排、消息持久化、Workflow 执行编排
├── models/                 SQLAlchemy 模型（biz schema）
├── repositories/           强制按 (app_id, user_id) 过滤的仓储
├── database/               异步引擎与会话
└── runtime/                RuntimeAdapter（业务层不依赖 Aegra 类型）
migrations/                 Alembic（biz.biz_alembic_version）
tests/                      认证、隔离、迁移、API、Adapter 测试
spike/aegra/                P0 验证工程（不是生产代码）
```

## 快速开始

前置：Python 3.12+、[uv](https://docs.astral.sh/uv/)、PostgreSQL 16。

```bash
# 1. 依赖
uv sync

# 2. 配置：至少替换两个 JWT 密钥（建议 32 字节以上）
cp .env.example .env

# 3. 数据库（示例；已有 PostgreSQL 可跳过）
docker run -d --name ai-service-pg \
  -e POSTGRES_USER=aegra -e POSTGRES_PASSWORD=aegra -e POSTGRES_DB=ai_service \
  -p 5432:5432 -v ai_service_pgdata:/var/lib/postgresql/data postgres:16-alpine

# 4. 建业务表
uv run alembic upgrade head

# 5. 注册应用
uv run python -m app.cli seed-apps --file seed/applications.example.json
uv run python -m app.cli validate

# 6. 启动
uv run aegra serve --host 127.0.0.1 --port 2026
```

验证身份：

```bash
TOKEN=$(uv run python - <<'PY'
import os, time, jwt
from app.config import get_settings
get_settings()
print(jwt.encode(
    {"user_id": "10001", "app_id": "agriculture_app", "exp": int(time.time()) + 3600},
    os.environ["AGRI_JWT_SECRET"],
    algorithm="HS256",
))
PY
)

curl -s -H "Authorization: Bearer $TOKEN" -H "X-App-Id: agriculture_app" \
  http://127.0.0.1:2026/api/v1/whoami

curl -s -X POST http://127.0.0.1:2026/api/v1/agents/echo_agent/chat \
  -H "Authorization: Bearer $TOKEN" -H "X-App-Id: agriculture_app" \
  -H "Content-Type: application/json" -d '{"message":"hello"}'
```

## 认证契约

请求头：

```http
Authorization: Bearer <JWT>
X-App-Id: <app_id>
```

- `X-App-Id` 只用于选择应用配置，**不是身份凭证**。
- 每个应用独立配置 `algorithm`、`secret_ref`（或 `public_key_ref`）、`user_id_claim`、`require_exp`、`issuer`、`audience`。
- 密钥只以 `env:NAME` 引用，数据库不存明文。
- 身份统一为 `app_id:user_id`；相同 `user_id` 在不同应用下是两个人。
- 越权统一返回 404，不区分「不存在」与「不属于你」。
- `allowed_agents` / `allowed_workflows`：`["*"]` 全允许，`[]` 全禁止。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/agents` | 仅返回当前应用 `allowed_agents` 内的 Agent |
| POST | `/api/v1/agents/{agent_id}/chat` | 非流式对话 |
| POST | `/api/v1/agents/{agent_id}/chat/stream` | SSE 流式对话 |
| POST | `/api/v1/conversations` | 创建会话（同时创建运行时 thread） |
| GET | `/api/v1/conversations` | 会话列表（`agent_id` / `limit` / `before`） |
| GET | `/api/v1/conversations/{id}` | 会话详情 |
| GET | `/api/v1/conversations/{id}/messages` | 消息历史（`limit` / `before`） |
| DELETE | `/api/v1/conversations/{id}` | 软删除会话，并尽力删除运行时 thread |
| GET | `/api/v1/workflows` | 仅返回当前应用 `allowed_workflows` 内的 Workflow（含输入 JSON Schema） |
| POST | `/api/v1/workflows/{workflow_id}/execute` | 执行 Workflow；`mode=async` 返回 202，`mode=sync` 直接返回结果 |
| GET | `/api/v1/workflows/runs` | 执行记录列表（`workflow_id` / `status` / `limit` / `before`） |
| GET | `/api/v1/workflows/runs/{run_id}` | 执行状态 |
| GET | `/api/v1/workflows/runs/{run_id}/result` | 执行结果 |
| POST | `/api/v1/workflows/runs/{run_id}/cancel` | 取消执行（对已结束的 run 返回 409） |

对话请求体：

```json
{"message": "你好", "conversation_id": "可选；不传则自动创建会话"}
```

### SSE 事件契约

```text
event: meta         {conversation_id, thread_id, user_message_id, agent_id}
event: token        {delta}
event: tool_call    {call_id, name, arguments}
event: tool_result  {call_id, name, content}
event: error        {code, message}
event: done         {conversation_id, message_id, run_id, finish_reason}
```

`token` 是**增量**，不是累计快照。流式失败时，已生成内容会以 `status="partial"` 落库，随后发送 `error`，不发送 `done`。

## Workflow

执行请求体：

```json
{"mode": "async", "input": {"a": 2, "b": 3}}
```

- `mode` 默认 `async`：立即返回 `202` 与 `run_id`，之后轮询状态 / 结果。
- `mode=sync`：在请求内等待，默认上限 30s（`WORKFLOW_SYNC_TIMEOUT_SECONDS`）。超时会取消运行并返回 `504 workflow_timeout`，长任务请用异步模式。
- `input` 按 Workflow 自身的 Pydantic 模型校验，未知字段直接 `422 invalid_input`，不会创建 thread 或执行记录。

运行状态：`pending → running → succeeded | failed | timed_out | cancelled`。进程重启时，未完成的 `pending` / `running` 会被标记为 `interrupted`。

结果接口只对 `succeeded` 返回 200；其余终态返回 `409`，`error.code` 分别是 `workflow_failed` / `workflow_timeout` / `workflow_cancelled`，未结束则返回 `run_not_finished`。

```bash
curl -s -X POST http://127.0.0.1:2026/api/v1/workflows/sum_workflow/execute \
  -H "Authorization: Bearer $TOKEN" -H "X-App-Id: agriculture_app" \
  -H "Content-Type: application/json" \
  -d '{"mode":"sync","input":{"a":2,"b":3}}'
# {"run_id":"...","workflow_id":"sum_workflow","status":"succeeded","output":{"a":2,"b":3,"total":5}, ...}
```

两个已知边界：

1. 后台轮询沿用调用方 Token 访问运行时的 run 接口，因此 `WORKFLOW_RUN_TIMEOUT_SECONDS`（默认 600s）必须明显小于 Token 有效期。
2. 并发上限是实例级软限制（`WORKFLOW_MAX_CONCURRENT_RUNS`，默认 8），超出返回 `429 too_many_runs`；同时到达的请求可能同时越过检查。

### 新增 Workflow

1. 写一个 LangGraph 图并 `compile()`，放在 `app/workflows/implementations/`，同时导出输入用的 Pydantic 模型。
2. 在 `aegra.json` 的 `graphs` 中增加 `workflow_id → 图路径`。
3. 在 `app/workflows/__init__.py` 注册 `WorkflowDefinition`（含 `input_model`）。
4. 重启服务；启动时会校验每个注册 Workflow 都有对应的图。

### 新增 Agent

1. 写一个 LangGraph 图并 `compile()`，放在 `app/agents/implementations/`。
2. 在 `aegra.json` 的 `graphs` 中增加 `agent_id → 图路径`。
3. 在 `app/agents/__init__.py` 注册 `AgentDefinition`。
4. 重启服务；启动时会校验每个注册 Agent 都有对应的图。

## 测试

```bash
uv run pytest
```

测试会自动创建 `ai_service_test` 数据库并执行迁移，不会碰业务库。默认连接 `postgresql+asyncpg://aegra:aegra@127.0.0.1:5432/ai_service_test`，可用 `TEST_DATABASE_URL` 覆盖。

```bash
uv run ruff check app tests
```

## 设计约束

1. 业务层只依赖 `AuthContext` 与 `RuntimeAdapter`，不解析 JWT，不直接使用 Aegra 类型。
2. Repository 必须携带 `AuthContext`；没有「只按 ID 查询」的入口。
3. Message 与 Checkpoint 分开持久化：消息服务 UI，checkpoint 服务图恢复。
4. 业务表在 `biz` schema，Alembic 使用 `biz.biz_alembic_version`；Aegra 独占 `public.alembic_version`。
5. 对外只暴露 `/api/v1`；部署时由反代屏蔽 `/threads`、`/runs`、`/assistants`、`/store`、`/crons`。
6. 依赖固定版本，`uv.lock` 随仓库提交。
7. `workflow_runs` 是运行时 run 的业务视图：对外只出现业务 UUID，运行时的 `run_id` / `thread_id` 仅内部留存；状态变更用条件更新，避免后台轮询覆盖取消结果。

## 下一步

P4：Docker Compose 独立部署、健康检查、数据库备份、重启恢复演练、全量验收对照。

更完整的背景与验收映射见 [`docs/requirements-analysis.md`](docs/requirements-analysis.md)。
