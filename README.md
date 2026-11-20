# AI Service Engine

多应用、多用户的 Agent / Workflow 服务引擎。业务系统用各自签发的 JWT 调用统一 API，引擎只认 `(app_id, user_id)`，负责执行 LangGraph 图、持久化会话与执行状态，并以 SSE 流式返回。

运行底座采用 [Aegra](https://github.com/aegra/aegra)（自托管 LangGraph Platform 替代品），业务 API 以自定义 FastAPI 路由挂载在同一个进程内。选型依据见 [`docs/adr/0001-runtime-choice.md`](docs/adr/0001-runtime-choice.md)。

## 当前进度

| 阶段 | 状态 | 内容 |
|---|---|---|
| P0 Spike | 完成 | 认证查库、自定义路由、SSE 适配、跨应用隔离、资源与迁移验证 |
| P1 基础与认证 | 完成 | 工程骨架、`biz` schema、Alembic、JWT 校验、应用缓存、隔离仓储、`RuntimeAdapter`、39 个测试 |
| P2 Agent 与会话 | 完成 | Agent Registry、会话 / 消息 API、同步 / 流式对话、多轮上下文、工具事件 |
| P3 Workflow | 完成 | Workflow Registry、同步 / 异步执行、run 状态与结果、取消与超时 |
| P4 交付 | 完成 | Docker Compose、健康检查、备份脚本、断流保留、验收对照（[docs/acceptance.md](docs/acceptance.md)） |
| P5 多副本与配额 | 完成 | Redis broker、工作流读时对账、应用维度限流 / 配额 / 并发、`model_configs` 接入调用链 |

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
   PostgreSQL          Redis（可选，多副本必开）
   ├─ public        Aegra 自己的表与 alembic_version     队列 / SSE pubsub / 限流计数
   └─ biz           业务表与 biz_alembic_version
```

单实例可不启用 Redis（`REDIS_BROKER_ENABLED=false`），此时队列、SSE 广播和限流计数都在进程内。开启后任意副本都能服务任意 thread / run；工作流 run 即使轮询者挂掉，也会在下一次读状态时对账收敛，见 [`docs/adr/0002-multi-replica.md`](docs/adr/0002-multi-replica.md)。

## 目录

```text
app/
├── main.py                 业务 FastAPI app（Aegra 的 http.app）
├── config.py               环境配置（含 .env 加载）
├── cli.py                  应用注册 CLI
├── api/                    路由、统一错误、依赖
├── auth/                   JWT、AuthContext、应用缓存、Aegra 认证桥接
├── agents/                 Agent Registry 与示例 Agent（assistant / echo / reverse / llm）
├── workflows/              Workflow Registry 与示例 Workflow（sum / text_stats）
├── services/               会话编排、消息持久化、Workflow 执行编排、模型解析、限流
├── cache/                  Redis 客户端（不可用时自动降级）
├── models/                 SQLAlchemy 模型（biz schema）
├── repositories/           强制按 (app_id, user_id) 过滤的仓储
├── database/               异步引擎与会话
└── runtime/                RuntimeAdapter（业务层不依赖 Aegra 类型）
migrations/                 Alembic（biz.biz_alembic_version）
scripts/                    容器入口、健康检查、备份
tests/                      认证、隔离、迁移、API、Adapter、Workflow 测试
Dockerfile / docker-compose.yml
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

## 部署（Docker Compose）

前置：Docker 24+ 与 Compose v2，建议 4C / 4G 起。

```bash
cp .env.example .env     # 至少替换 POSTGRES_PASSWORD、AGRI_JWT_SECRET、MGMT_JWT_SECRET
docker compose up -d --build
docker compose ps        # ai-service 变为 healthy 即可用
curl -s http://127.0.0.1:2026/health
```

首次启动顺序：Postgres healthy → `alembic upgrade head`（`biz`）→ 按需 upsert 应用与模型配置 → Aegra 迁移（`public`）→ 监听端口。两套迁移都幂等，重启安全。

资源与安全边界：

- 端口只绑定 `127.0.0.1`。`/threads`、`/runs`、`/assistants`、`/store`、`/crons` 是运行时内部接口，**必须**由反代屏蔽，只对外暴露 `/api/v1`。
- `ai-service` 限 2G、`postgres` 限 1G、`redis` 限 256m，日志 json-file 10m × 3。
- `redis` 用 `--maxmemory-policy noeviction`：队列键被淘汰等于丢任务，宁可写入失败。
- `stop_grace_period: 30s` 给关闭流程留出时间。

### 扩到多副本

默认单副本。要跑多个：

```bash
# 1. 去掉 ai-service 的 ports 映射（两个副本不能绑同一个宿主端口）
# 2. 用反代指向服务名 ai-service:2026，只转发 /api/v1
docker compose up -d --scale ai-service=2
```

Redis 已在 compose 中开启，`REDIS_BROKER_ENABLED=true`，因此任意副本都能：创建/续读任意 thread、以 SSE 跟随任意 run、共享限流计数。工作流的 run 不依赖「起它的那个副本」活着——状态与结果读接口会用调用方凭证即时对账。

多副本下有三点要知道：

1. `running` 且 `heartbeat_at` 陈旧，说明轮询者已死；下一次读状态会自动收敛（成功、失败或超时）。
2. 没人读的 run 会一直停在 `running` 直到被读或被取消——异步工作流的客户端本来就要轮询。
3. Redis 挂掉时，Aegra 的队列不可用（服务会不健康）；限流计数则自动降级为进程内计数，不会阻断业务。

备份与恢复：

```bash
./scripts/backup-db.sh                      # 写 ./backups，保留 7 天
BACKUP_DIR=/var/backups/ai RETENTION_DAYS=14 ./scripts/backup-db.sh

gunzip -c backups/ai_service-20261120-153406.sql.gz \
  | docker compose exec -T postgres psql -U aegra -d ai_service
docker compose restart ai-service
```

`pg_dump` 取 MVCC 快照，备份期间可继续服务；dump 同时包含 `biz`（业务表）与 `public`（Aegra 表）。

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
- 业务错误统一为 `{"error": {"code", "message", "request_id"}}`；唯一例外是 401，它由 Aegra 的认证依赖在进入业务路由前产生，使用 Aegra 自己的信封。集成方请以 HTTP 状态码判断。

## 配额与限流

每个应用可单独设置，`NULL` 表示不限制：

| 字段 | 含义 |
|---|---|
| `rate_limit_per_minute` | 每分钟业务请求数 |
| `daily_request_quota` | 每日业务请求数 |
| `max_concurrent_workflow_runs` | 同时进行中的工作流 run 数，留空则用 `WORKFLOW_MAX_CONCURRENT_RUNS` |

超限返回 `429`，带 `Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`；`error.code` 为 `rate_limited` 或 `daily_quota_exceeded`。计数是 Redis 固定窗口；Redis 不可用时降级为进程内计数，不会阻断业务（多副本下实际阈值放宽为 `limit × 副本数`）。

另有一道进程级硬上限 `WORKFLOW_INSTANCE_MAX_RUNS`，避免多租户叠加把机器打满。

```bash
uv run python -m app.cli seed-apps --file seed/applications.example.json   # 含配额示例
```

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

参考实现见 `app/agents/implementations/assistant_agent.py`，它把 Agent 该有的四件事都写全了：

```text
START → plan ─┬─(有 tool_calls)→ tools ─┐
              │                        │
              └─(无 tool_calls)→ END   └──→ plan   ← 循环，直到给出答案或触到上限
```

- **模型来自配置**：`plan` 节点每次调用 `biz.model_configs` 里的模型，不写死；
- **工具**：`calculator`（AST 求值，不走 `eval`）、`current_time`、`text_stats`；
- **循环保护**：`AGENT_MAX_TOOL_ITERATIONS`（默认 4）轮后强制收尾，避免模型无限调工具；
- **离线可跑**：`echo` 这类不支持 tool calling 的 provider 自动切到规则路由，节点、边、工具循环完全一致，CI 不需要 API key。

拷贝这个文件改 `SYSTEM_PROMPT` 和 `TOOLS` 就能作为新业务 Agent 的起点。

## 模型配置

Agent 不写死模型。`llm_agent` 在调用时按名字从 `biz.model_configs` 取配置并构建 LangChain 客户端，`DEFAULT_MODEL_CONFIG`（默认 `demo`）决定用哪一条。

| 字段 | 说明 |
|---|---|
| `name` | 配置名，Agent 按它引用 |
| `provider` | `openai`（任何 OpenAI 兼容接口，含自建网关）或 `echo`（离线确定性，供本地与测试） |
| `base_url` | 兼容网关地址，留空用官方地址 |
| `model_name` | 模型名 |
| `api_key_encrypted` | **只存引用**，如 `env:OPENAI_API_KEY`，绝不存明文 |
| `temperature` / `max_tokens` / `timeout_seconds` | 调用参数 |

```bash
uv run python -m app.cli seed-models --file seed/models.example.json
uv run python -m app.cli list-models     # 只输出 key 引用，不输出密钥
```

换供应商、换模型、调温度都是改配置行，不改代码；`MODEL_CONFIG_CACHE_TTL_SECONDS`（默认 60s）后生效。配置缺失或被禁用时，Agent 直接报错而不是静默降级。

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
8. 没有任何跨租户的服务级凭证。需要读他人 run 的地方（多副本接管）一律改为「用调用方自己的凭证读时对账」。
9. 限流、配额、Redis 都属于**保护性设施**，自身故障时必须降级而不是拒绝全部业务流量。

## 下一步

验收对照、部署验证记录与已知限制见 [`docs/acceptance.md`](docs/acceptance.md)，多副本决策见 [`docs/adr/0002-multi-replica.md`](docs/adr/0002-multi-replica.md)。

仍然开放的方向：

1. `detach=true`：断流后继续执行（当前断流即取消，已生成内容落 `partial`）；
2. 限流改为令牌桶，避免固定窗口在边界处的突发；
3. 模型调用的用量与成本统计（token 计数落到 `workflow_runs` / 消息表）；
4. 把 `applications` 的配额变更做成带审计的管理接口，不再只能改库。

更完整的背景与验收映射见 [`docs/requirements-analysis.md`](docs/requirements-analysis.md)。
