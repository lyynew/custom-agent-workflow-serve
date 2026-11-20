# AI 智能服务引擎 · 实现报告

| 项目   | 内容                                                               |
| ---- | ---------------------------------------------------------------- |
| 仓库   | `git@github.com:lyynew/custom-agent-workflow-serve.git`          |
| 当前提交 | `8345eeb`（feat(agent): 新增编排型参考 Agent）                            |
| 报告日期 | 2026-11-20                                                       |
| 覆盖范围 | P0 选型验证 → P5 多副本/配额/模型 → 编排型参考 Agent                             |
| 代码规模 | 应用约 4.8k 行（65 个 .py）、测试约 2.6k 行（22 个文件）、迁移 327 行                 |
| 质量状态 | `pytest` 136 passed；`ruff check` 全过；`docker compose config` 校验通过 |



---

## 1. 摘要

这是一套**独立部署、统一认证、按应用隔离**的 Agent / Workflow 运行时，不是 AI 平台。它不提供可视化编排，也不管理提示词，只做四件事：验证调用方身份、隔离各业务系统的数据、按 ID 执行预先开发好的 LangGraph 图、把过程和结果可靠地持久化并流式输出。

核心判断是：**认证决定「你是谁」，隔离决定「你能看到什么」，Registry 决定「你能调用什么」**。因此项目 70% 的代码量花在认证、隔离、状态管理这三件事上，而不是花在让示例 Agent 更"聪明"上。

| 指标           | 数量                                           |
| ------------ | -------------------------------------------- |
| 对外业务接口       | 16 个（全部在 `/api/v1`）                          |
| 已注册 Agent    | 5 个（assistant / simple / echo / llm / reverse）        |
| 已注册 Workflow | 2 个（sum / text_stats）                        |
| 业务数据表        | 5 张 + 1 张版本表（`biz` schema）                   |
| 数据库迁移        | 2 个（`0001_initial`、`0002_limits_and_leases`） |
| 自动化测试        | 136 个                                        |
| 交付物          | 镜像、Compose、健康检查、备份脚本、CLI、4 份文档               |

---

## 2. 系统架构

### 2.1 整体结构

```text
业务系统 A          业务系统 B          业务系统 C
Bearer JWT-A        Bearer JWT-B        Bearer JWT-C
X-App-Id: app_a     X-App-Id: app_b     X-App-Id: app_c
    └───────────────────┼───────────────────┘
                        │
              FastAPI（Aegra 宿主进程）
                        │
         ┌──────────────┴──────────────┐
         │                             │
   Aegra 认证中间件               /api/v1 业务路由
   （app.auth.manager:auth）      （自定义 FastAPI 路由）
   查 applications 表              配额依赖（限流）
   校验 JWT 签名/算法/声明          RuntimeAdapter
         │                             │
         └──────────────┬──────────────┘
                        │  loopback HTTP，透传调用方凭证
                 ┌──────┴──────┐
                 │             │
          Agent Registry   Workflow Registry
                 │             │
                 └──────┬──────┘
                   LangGraph 图执行
                        │
              ┌─────────┴─────────┐
        PostgreSQL              Redis（可选）
        ├─ public：Aegra 运行时表（thread / run / checkpoint）
        └─ biz：业务表（applications / conversations / …）
```

### 2.2 关键架构决策

| 决策        | 结论                                       | 依据                                                                     |
| --------- | ---------------------------------------- | ---------------------------------------------------------------------- |
| 运行底座      | **Aegra 作宿主**，外面包一层 `RuntimeAdapter`     | P0 Spike 5 项验证全过；thread/run 生命周期、SSE 重连、checkpoint 恢复复用比自研划算（ADR 0001） |
| 业务路由位置    | 挂进 Aegra 的 FastAPI app（`http.app`），不另起服务 | 一套认证实现，避免「认证两次」或「内部接口裸奔」                                               |
| 多副本       | **不做跨副本接管**，改「读时对账」                      | 接管他人 run 需用户 Token / 万能凭证 / 直读运行时表，三者都突破安全或边界（ADR 0002）                |
| 数据隔离      | 仓储层强制过滤 `(app_id, user_id)`，无裸查询入口       | 靠约定容易漏，靠类型系统与唯一入口才可靠                                                   |
| Schema 划分 | 业务 `biz`、运行时 `public`，两套 Alembic 互不干扰    | Aegra 独占 `public.alembic_version`，共用会互相覆盖                              |

### 2.3 认证链路（10 步）

```text
① 取 X-App-Id → ② 查应用配置（60s TTL 缓存）→ ③ 检查 enabled
→ ④ 取 Authorization: Bearer → ⑤ 按应用配置选算法/密钥
→ ⑥ 验签（算法钉死，拒绝 alg=none 与算法混淆）→ ⑦ 校验 exp（可配 require_exp）
→ ⑧ 校验 issuer / audience（可选）→ ⑨ 提取 user_id（可配 claim 名）并做 app_id_claim 比对
→ ⑩ 构建 identity = "app_id:user_id" → 权限检查 → 执行业务
```

`X-App-Id` 只用于选择配置，**不承担身份作用**；身份只来自验签通过的 Token。

---

## 3. 功能实现清单

### 3.1 多应用 JWT 认证

**已实现**

- 每个业务系统在 `biz.applications` 注册独立配置：算法、密钥引用、`user_id` claim 名、是否要求 `exp`、`issuer`、`audience`、可选 `app_id_claim`、时钟偏移。
- 支持 `HS256/384/512` 与 `RS256/384/512`，**算法按应用钉死**，Token 头部声明的算法不会被信任。
- 密钥只以 `env:VARIABLE` 引用存放在 `jwt_config.secret_ref`（RS 用 `public_key_ref`），数据库里没有明文；`list-apps` 不输出密钥。
- 应用配置 60s TTL 缓存（`AUTH_CACHE_TTL_SECONDS`），避免每请求打库。
- 启动时 `warmup()` 遍历全部应用校验密钥可解析，**解析失败直接启动失败**，不留到第一次请求才炸。

**拒绝矩阵（均已验证）**

| 场景                     | 结果                   |
| ---------------------- | -------------------- |
| 缺 `Authorization`      | 401                  |
| 缺 `X-App-Id`           | 401                  |
| 未知 / 已禁用应用             | 401                  |
| 过期 Token               | 401 `token_expired`  |
| 签名错误                   | 401 `invalid_token`  |
| `alg=none`             | 401                  |
| 用 A 应用 Token 调 B 应用    | 401 `app_mismatch`   |
| 缺 `user_id` claim      | 401 `invalid_claim`  |
| 资源不属于当前身份              | 404（不区分「不存在」与「不属于你」） |
| 应用无权调用该 Agent/Workflow | 403 `forbidden`      |

### 3.2 身份上下文与数据隔离

**已实现**

- `AuthContext(app_id, user_id)` 为冻结模型；Agent、Workflow、服务层只接收它，**任何业务代码都不解析 JWT**。
- `ScopedRepository` 是所有业务仓储的基类：`get / list / update / delete` 全部自动附加 `app_id`、`user_id` 条件，没有「只按 ID 查」的入口。
- 运行时侧把 identity 命名为 `app_id:user_id` 传给 Aegra，thread 的 metadata 写入 `app_id` / `user_id`，列表与按 ID 读写都受运行时身份过滤。
- 隔离粒度覆盖：会话、消息、Workflow 执行记录、LangGraph Thread、Checkpoint。

**实测**：`agriculture_app:10001` 与 `management_app:10001`（**相同 user_id**）互相看不到对方会话；跨应用按 ID 读取，经业务路由与运行时原生路由均返回 404；列表分别为 3 条 / 0 条，无交集。

### 3.3 Agent 能力

| 能力          | 状态 | 说明                                                                            |
| ----------- | -- | ----------------------------------------------------------------------------- |
| 代码注册 + 启动校验 | 完成 | `AgentRegistry` 注册 `AgentDefinition`；启动时校验 `aegra.json` 中每个 Agent 都有对应图，缺失即失败 |
| 按 ID 调用     | 完成 | `POST /api/v1/agents/{agent_id}/chat`                                         |
| 多轮对话        | 完成 | 会话 1:1 绑定运行时 thread，checkpoint 保留上下文                                          |
| 工具调用        | 完成 | 工具事件在流式响应中独立下发                                                                |
| 流式输出        | 完成 | `POST /api/v1/agents/{agent_id}/chat/stream`                                  |
| 权限控制        | 完成 | 按应用 `allowed_agents` 过滤；`["*"]` 全允许、`[]` 全禁止                                  |

**五个已注册 Agent**

| Agent             | 用途             | 实现方式                                                            |
| ----------------- | -------------- | --------------------------------------------------------------- |
| `assistant_agent` | **编排型参考实现**    | `plan → tools → plan` ReAct 循环；模型驱动工具选择；3 个真实工具；循环保护；模型不可用时规则降级 |
| `llm_agent`       | 纯模型对话          | 单节点，模型来自 `biz.model_configs`                                    |
| `simple_agent`    | **`create_agent` 参考实现** | `langchain.agents.create_agent(model, tools, system_prompt)` 一步生成，约 30 行 |
| `echo_agent`      | 引擎冒烟 / 工具链路验证  | 确定性回复 + `get_server_time` 工具                                    |
| `reverse_agent`   | 多 Agent 独立调用验证 | 确定性文本反转                                                         |

`assistant_agent` 是新增的重点，它把「一个 Agent 该有的东西」写全了：

```text
START → plan ─┬─(有 tool_calls)→ tools ─┐
              │                        │
              └─(无 tool_calls)→ END   └──→ plan     ← 循环，直到出答案或触上限
```

- **模型来自配置**：每次 `plan` 从 `biz.model_configs` 取配置；默认 `DEFAULT_MODEL_CONFIG`，单个 Agent 可用 `MODEL_CONFIG_<AGENT_ID>` 覆盖。
- **工具**：`calculator`（AST 白名单求值，禁止 `eval`，限表达式长度/指数、拦除零）、`current_time`、`text_stats`。
- **循环保护**：按 state 中 `ToolMessage` 数量计轮次，达 `AGENT_MAX_TOOL_ITERATIONS`（默认 4）强制收尾并返回明确提示，不依赖 `recursion_limit` 抛异常。
- **离线可跑**：`echo` 这类不支持 tool calling 的 provider 自动切规则路由，节点、边、工具循环完全一致，CI 无需 API Key。

### 3.4 会话与消息

**已实现**

- `conversations`：`id / app_id / user_id / agent_id / thread_id / title / created_at / updated_at / deleted_at`。
- `conversation_messages`：`id / conversation_id / app_id / user_id / role / content / status / created_at`。
- 创建会话时同步创建运行时 thread；不传 `conversation_id` 的对话会自动建会话，标题取首条消息截断。
- 消息落库时机：用户消息在运行前写入；助手消息成功写 `complete`，运行失败或客户端断流写 `partial`（含已生成的部分内容，不丢半截回复）。
- 历史查询支持 `limit` / `before` 游标分页。
- 删除为软删除（`deleted_at`），并尽力删除运行时 thread，失败只记日志。
- **Message 与 Checkpoint 分离**：消息表只服务前端展示，checkpoint 只用于恢复图状态，互不反渲染。

### 3.5 SSE 流式输出

事件契约（对外只暴露这五种，运行时内部结构不外泄）：

| 事件            | 时机       | 负载                                      |
| ------------- | -------- | --------------------------------------- |
| `meta`        | 立即       | `thread_id`、`conversation_id`、`run_id`  |
| `token`       | 每个增量     | `{"delta": "..."}`，**增量而非累计快照**         |
| `tool_call`   | 模型发起工具调用 | `call_id`、`name`、`arguments`            |
| `tool_result` | 工具返回     | `call_id`、`name`、`content`              |
| `done`        | 正常结束     | `conversation_id`、`message_id`、`run_id` |
| `error`       | 失败       | `code`、`message`；此后不发 `done`            |

关键实现：Aegra 原生的 `messages/partial` 是**累计快照**且带大量内部 metadata（`langgraph_node`、`thread_id` 等）。`RuntimeAdapter` 在中间做差量计算，业务层只收到增量文本；直接透传会把内部结构泄漏给业务方，也逼着前端自己做 diff。

### 3.6 Workflow 能力

| 能力    | 状态 | 说明                                                                             |
| ----- | -- | ------------------------------------------------------------------------------ |
| 注册与发现 | 完成 | `WorkflowRegistry` + `GET /api/v1/workflows`，返回 `input_schema`                 |
| 输入校验  | 完成 | 每个 Workflow 自带 Pydantic 模型且 `extra="forbid"`                                   |
| 同步执行  | 完成 | `mode=sync`，请求内等待，超时 30s 后取消并返回 504                                            |
| 异步执行  | 完成 | `mode=async`（默认），202 + `run_id`                                                |
| 状态查询  | 完成 | `GET /workflows/runs/{run_id}`，含 `started_at` / `finished_at` / `heartbeat_at` |
| 结果查询  | 完成 | `GET /workflows/runs/{run_id}/result`，未终态返回 409                                |
| 取消    | 完成 | `POST /workflows/runs/{run_id}/cancel`，已终态返回 409                               |
| 列表    | 完成 | `GET /workflows/runs`，按身份过滤 + 游标分页                                             |
| 超时    | 完成 | 服务端超时后取消运行时 run 并记 `timed_out`，不是放任继续跑                                         |
| 并发限制  | 完成 | 按应用 `max_concurrent_workflow_runs` + 进程级 `WORKFLOW_INSTANCE_MAX_RUNS`，超出 429   |

**状态机**

```text
pending ──→ running ──→ succeeded
   │           │    └─→ failed
   │           │    └─→ timed_out
   └───────────┴──────→ cancelled
```

所有状态写入都是**条件更新**（`WHERE status IN ('pending','running')`），因此「刚启动就取消」时，后台轮询看到 `success` 也覆盖不掉已写入的 `cancelled`——这是并发下最容易出的错，已用真实运行时验证。

**两个已注册 Workflow**

| Workflow              | 输入              | 特点                                |
| --------------------- | --------------- | --------------------------------- |
| `sum_workflow`        | `{a, b}`        | 最小示例，两数相加                         |
| `text_stats_workflow` | `{text, top_n}` | 带条件分支：长文本走 `detailed` 节点，短文本走轻量节点 |

### 3.7 模型配置

**已实现**

- `biz.model_configs`：`name / provider / model_name / base_url / api_key_encrypted / temperature / max_tokens / timeout_seconds / extra / enabled`。
- 支持 `openai`（任何 OpenAI 兼容接口，含自建网关）与 `echo`（离线确定性，供本地与测试）。
- 密钥字段只存 `env:VARIABLE` 引用，与 `secret_ref` 同一套解析规则，绝不落明文。
- `ModelResolver` 按名字解析并缓存（60s TTL），换模型/网关/温度是改配置行，不改代码。
- 配置缺失或被禁用时**直接报错**，不静默降级到别的模型。
- CLI：`seed-models` / `list-models`，`validate` 一并校验模型密钥引用。

### 3.8 限流与配额

**已实现**

- `applications` 三个字段，`NULL` = 不限制：`rate_limit_per_minute`、`daily_request_quota`、`max_concurrent_workflow_runs`。
- 固定窗口计数，每次请求一次 `INCR`；Redis 可用时多副本共享计数，不可用时降级为进程内计数（**限流是保护措施，不能因为它自己故障就拒绝全部流量**）。
- 超限 429，带 `Retry-After`、`X-RateLimit-Limit`、`X-RateLimit-Remaining`、`X-RateLimit-Reset`。
- 错误码区分 `rate_limited`（每分钟）与 `daily_quota_exceeded`（每日）。
- 配额随认证结果下发到 `User`，鉴权后直接计数，不额外查库。

### 3.9 运行时适配层

`RuntimeAdapter` 是业务与 Aegra 之间唯一的耦合点，定义 7 个能力：

```text
create_thread / delete_thread
wait_run（同步等结果）
stream_thread_run（流式，产出类型化事件）
start_run / get_run / cancel_run（异步执行与生命周期）
```

事件被归一化为 `RunStarted / TokenDelta / ToolCallEvent / ToolResultEvent / RunFinished / RunFailed`，业务层不出现任何 Aegra 类型。换运行时只需重写这一层，路由与会话逻辑不动。

另外做了一次**状态词汇归一**：运行时的 `success/error/timeout/interrupted` 映射为 `succeeded/failed/timed_out/cancelled`，业务层只看到一套词。

### 3.10 部署与运维

| 项    | 实现                                                                                              |
| ---- | ----------------------------------------------------------------------------------------------- |
| 镜像   | `python:3.13-slim` + uv 0.11.26；依赖层与代码层分离（改代码不重装依赖）；`uv sync --frozen --no-dev`；非 root 用户 `app` |
| 编排   | `docker-compose.yml`：`ai-service` + `postgres:16-alpine` + `redis:7-alpine`（可选启停）               |
| 启动顺序 | 业务迁移 → 按需 upsert 应用/模型 → 启动服务（`scripts/entrypoint.sh`，幂等）                                       |
| 健康检查 | 容器探针调 `/health`，校验 PostgreSQL、checkpointer、store                                                |
| 资源上限 | `ai-service` 2G、`postgres` 1G、`redis` 256m；Redis `maxmemory-policy noeviction`（队列键被淘汰等于丢任务）     |
| 日志   | json-file，10m × 3 轮转                                                                            |
| 停机   | `stop_grace_period: 30s`，留给进行中的 run 收尾                                                          |
| 端口   | 只绑 `127.0.0.1:2026`，`/threads`、`/runs`、`/assistants`、`/store`、`/crons` 需由反代屏蔽                   |
| 备份   | `scripts/backup-db.sh`：`pg_dump` + gzip + 完整性校验 + 保留期清理；实测可完整恢复到临时库                             |
| 数据卷  | 命名卷持久化，`down` 不丢数据（`down -v` 才删）                                                                |

### 3.11 管理 CLI

| 命令                   | 作用                                      |
| -------------------- | --------------------------------------- |
| `seed-apps --file`   | 按 JSON upsert 应用配置（幂等）                  |
| `list-apps`          | 列出应用，不输出密钥                              |
| `seed-models --file` | 按 JSON upsert 模型配置                      |
| `list-models`        | 列出模型配置，只显示密钥引用                          |
| `validate`           | 校验全部 `secret_ref`、公钥引用、模型密钥引用，任一失败退出码 1 |

---

## 4. API 契约

统一前缀 `/api/v1`，所有接口要求 `Authorization: Bearer <JWT>` + `X-App-Id: <app_id>`。


| 方法 | 路径 | 说明 | 成功 |
|---|---|---|---|
| GET | `/whoami` | 当前身份与权限 | 200 |
| GET | `/apps/me` | 当前应用配置（无密钥） | 200 |
| GET | `/agents` | 有权限的 Agent 列表 | 200 |
| POST | `/agents/{agent_id}/chat` | 非流式对话 | 200 |
| POST | `/agents/{agent_id}/chat/stream` | SSE 流式对话 | 200 |
| POST | `/conversations` | 创建会话 | 201 |
| GET | `/conversations` | 会话列表（游标分页） | 200 |
| GET | `/conversations/{id}` | 会话详情 | 200 |
| GET | `/conversations/{id}/messages` | 消息历史 | 200 |
| DELETE | `/conversations/{id}` | 软删除会话 | 204 |
| GET | `/workflows` | Workflow 列表（含输入 schema） | 200 |
| POST | `/workflows/{workflow_id}/execute` | 执行（sync / async） | 200 / 202 |
| GET | `/workflows/runs` | 执行记录列表 | 200 |
| GET | `/workflows/runs/{run_id}` | 执行状态 | 200 |
| GET | `/workflows/runs/{run_id}/result` | 执行结果 | 200 |
| POST | `/workflows/runs/{run_id}/cancel` | 取消执行 | 200 |

**错误信封**（除 401 外统一）：

```json
{"error": {"code": "agent_not_found", "message": "Agent not found", "request_id": "..."}}
```

| HTTP | code |
|---|---|
| 401 | `token_expired` / `invalid_token` / `invalid_claim` / `app_mismatch` / `missing_claim`（Aegra 原生信封，见 §8） |
| 403 | `forbidden` |
| 404 | `agent_not_found` / `workflow_not_found` / `application_not_found` / `not_found` |
| 409 | `run_not_finished` / `run_not_cancellable` / `workflow_failed` / `workflow_timeout` / `workflow_cancelled` |
| 422 | `invalid_input` |
| 429 | `rate_limited` / `daily_quota_exceeded` / `too_many_runs` |
| 500 | `persistence_error` |
| 502 | `runtime_error` |
| 504 | `workflow_timeout`（sync 模式超时） |

---

## 5. 数据模型

### 5.1 `biz` schema（业务表）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `applications` | `app_id`(PK)、`app_name`、`auth_type`、`jwt_config`(JSONB)、`allowed_agents`、`allowed_workflows`、`enabled`、`rate_limit_per_minute`、`daily_request_quota`、`max_concurrent_workflow_runs` | 应用注册与认证/权限/配额 |
| `conversations` | `id`(PK)、`app_id`、`user_id`、`agent_id`、`thread_id`、`title`、`deleted_at` | 会话，1:1 绑定运行时 thread |
| `conversation_messages` | `id`(PK)、`conversation_id`、`app_id`、`user_id`、`role`、`content`、`status` | 消息，`status ∈ {complete, partial}` |
| `workflow_runs` | `id`(PK)、`app_id`、`user_id`、`workflow_id`、`aegra_run_id`、`thread_id`、`status`、`input`、`output`、`error`、`heartbeat_at`、`started_at`、`finished_at` | 执行记录，业务 UUID 对外 |
| `model_configs` | `id`(PK)、`name`(UNIQUE)、`provider`、`model_name`、`base_url`、`api_key_encrypted`、`temperature`、`max_tokens`、`timeout_seconds`、`extra`、`enabled` | 模型配置 |
| `biz_alembic_version` | `version_num` | **业务专属**版本表 |

### 5.2 `public` schema（Aegra 运行时）

由 Aegra 自己的迁移创建，业务代码不直接读写：`thread`、`run`、`assistant`、`store`、checkpoint 相关表、`alembic_version`。

**关键约束**：Aegra 独占 `public.alembic_version`，业务迁移必须使用 `biz.biz_alembic_version`，否则两套迁移互相覆盖。这条已由测试锁定。

---

## 6. 配置项

全部通过环境变量覆盖，`.env.example` 是清单来源。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://aegra:aegra@127.0.0.1:5432/ai_service` | 数据库连接（自动归一化 `postgres://` 前缀） |
| `SELF_URL` | `http://127.0.0.1:2026` | 适配层回环调用地址 |
| `REDIS_BROKER_ENABLED` | `false` | 同时控制 Aegra 队列/SSE pubsub 与本地限流计数 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 地址 |
| `AUTH_CACHE_TTL_SECONDS` | `60` | 应用配置缓存 |
| `JWT_DEFAULT_LEEWAY_SECONDS` | `30` | 时钟偏移 |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_SECONDS` | `5` / `5` / `30` | 连接池 |
| `WORKFLOW_SYNC_TIMEOUT_SECONDS` | `30` | sync 模式上限 |
| `WORKFLOW_RUN_TIMEOUT_SECONDS` | `600` | 单次执行总超时 |
| `WORKFLOW_POLL_INTERVAL_SECONDS` | `0.3` | 轮询间隔 |
| `WORKFLOW_MAX_CONCURRENT_RUNS` | `8` | 每应用并发默认值 |
| `WORKFLOW_INSTANCE_MAX_RUNS` | `32` | 单进程硬上限 |
| `WORKFLOW_HEARTBEAT_SECONDS` | `10` | 轮询者心跳间隔（运维信号） |
| `MODEL_CONFIG_CACHE_TTL_SECONDS` | `60` | 模型配置缓存 |
| `DEFAULT_MODEL_CONFIG` | `demo` | Agent 默认引用的模型配置名 |
| `MODEL_CONFIG_<AGENT_ID>` | 无 | 单 Agent 覆盖，如 `MODEL_CONFIG_SIMPLE_AGENT=deepseek` |
| `AGENT_MAX_TOOL_ITERATIONS` | `4` | 单轮最多工具往返次数 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | — | Compose 用 |
| `HOST_PORT` | `2026` | 宿主机端口 |
| `SEED_APPLICATIONS_FILE` / `SEED_MODELS_FILE` | 示例文件 | 启动时幂等写入；生产置空 |
| `LOG_LEVEL` / `ENV_MODE` | `INFO` / `LOCAL` | 日志与环境保护 |

**密钥类变量**（只存引用，不进数据库）：`AGRI_JWT_SECRET`、`MGMT_JWT_SECRET`、`OPENAI_API_KEY`。

---

## 7. 质量与验证

### 7.1 测试分布（136 个）

| 文件 | 数量 | 覆盖内容 |
|---|---|---|
| `test_workflow_api.py` | 20 | 同步/异步、取消竞态、超时、并发上限、跨应用隔离、读时对账 |
| `test_jwt.py` | 15 | 算法、过期、签名、claim、`alg=none`、跨应用复用 |
| `test_assistant_agent.py` | 14 | 工具路由、表达式解析、循环保护、多轮、降级路径 |
| `test_chat_api.py` | 12 | 同步/流式对话、失败落库、断流保留 |
| `test_models.py` | 10 | 模型解析、provider、密钥引用、缓存 |
| `test_simple_agent.py` | 3 | `create_agent` 图可执行、echo 下不挂工具节点、按 Agent 覆盖模型 |
| `test_workflow_registry.py` | 9 | 注册、重复、缺失图、输入契约 |
| `test_auth_manager.py` | 9 | 认证桥接、身份、缓存刷新、错误码 |
| 其余 12 个文件 | 41 | 隔离、权限、迁移、Adapter、SSE 解析、API 冒烟等 |

### 7.2 真实验证记录（非单测）

| 项 | 结果 |
|---|---|
| 双应用独立密钥 | 各自 200，`identity` 正确 |
| 6 类非法凭证 | 全部 401 |
| 相同 `user_id` 跨应用隔离 | 会话/消息/执行记录互不可见，按 ID 读 404 |
| SSE 增量 | 36 个 `token` 拼接与预期文本完全一致；控制事件仅 `meta`/`done` |
| 工具流式 | `tool_call → tool_result → done`，`25*4 = 100` |
| Workflow 同步/异步 | `{"a":2,"b":3,"total":5}`；async 202 → `succeeded`，时间戳齐全 |
| 取消竞态 | 取消后 1.2s 仍为 `cancelled`，未被轮询覆盖 |
| 读时对账 | 人为把已完成 run 改回 `running` 并清空结果，一次 GET 收敛回 `succeeded` |
| 第二副本收敛 | 独立容器副本把副本 1 的停滞 run 收敛为 `succeeded` |
| 限流 | 限 2 次/分钟后 `200,200,429,429`，头正确，另一应用不受影响 |
| 备份恢复 | 6.8KB dump，恢复后 `applications=2 / conversations=1 / messages=2 / runs=1 / thread=2 / run=2` |
| 重启恢复 | 历史会话、消息、执行记录均可读 |
| 资源占用 | Aegra 进程约 169MB、PostgreSQL 约 45MB（无 Redis 场景） |

### 7.3 说明书 11 条验收标准

| # | 标准 | 结论 |
|---|---|---|
| 1 | 多业务系统使用不同 JWT 密钥接入 | 通过 |
| 2 | 签名错误/过期/格式非法拒绝 | 通过（含 `alg=none`、跨应用复用） |
| 3 | 不同应用完全无法访问对方数据 | 通过 |
| 4 | 相同 `user_id` 在不同应用仍隔离 | 通过 |
| 5 | 多个 Agent 可注册、独立调用 | 通过（4 个） |
| 6 | 多个 Workflow 可注册、执行 | 通过（2 个） |
| 7 | 多轮对话与历史查询 | 通过 |
| 8 | SSE 流式响应 | 通过 |
| 9 | PostgreSQL 持久化与重启恢复 | 通过 |
| 10 | Docker Compose 独立部署 | 通过（含备份、健康检查、重启演练） |

逐条证据见 [`docs/acceptance.md`](acceptance.md)。

---

## 8. 已知限制

这些是**有意的取舍**，不是遗漏；每条都写明触发条件与影响面。

1. **401 响应体不是统一信封。** 它由 Aegra 的认证依赖在进入业务路由前产生，格式为 `{"error":"unauthorized", ...}`；其余状态码都是本项目的 `{"error":{code,message,request_id}}`。集成方应以 HTTP 状态码为判断依据。
2. **没有 Redis 时不能多副本。** 后台轮询在进程内，`--scale` 会让其他副本看不到这些任务；同时限流退化为按副本计数，实际限额变成 `配置值 × 副本数`。
3. **无人读取时状态可能停在 `running`。** 超时判定发生在读时对账里，如果既没有轮询者也没有人读，DB 状态不会自己翻转。轮询进程正常时不会出现。
4. **后台轮询沿用调用方 Token。** `WORKFLOW_RUN_TIMEOUT_SECONDS`（默认 600s）必须明显小于 Token 有效期，否则长任务会在轮询阶段 401。
5. **断流即取消。** 运行时默认 `on_disconnect=cancel`，已生成内容以 `partial` 落库；`detach=true`（断流后继续跑）未实现。
6. **并发与限流是软限制。** 并发用「统计活跃数」实现，同时到达的请求可能一起越过检查；限流是「先 INCR 再判断」，窗口边界有少量误差。
7. **未附带反向代理配置。** 必须由 nginx 等屏蔽 `/threads`、`/runs`、`/assistants`、`/store`、`/crons`，只暴露 `/api/v1`；Compose 已把端口绑在 `127.0.0.1` 兜底。
8. **会话删除是软删 + 尽力删 thread。** 运行时删除失败只记日志，残留 thread 需用 Aegra 的 `POST /threads/prune` 清理。
9. **启动种子会覆盖手工改的配额。** 幂等 upsert 会把 `rate_limit_per_minute` 等字段重置为文件值，生产环境应置空 `SEED_*_FILE`。
10. **`interrupted` 是历史状态。** P5 之后不再写入（多副本下批量标记会杀掉别人的 run），仅存量数据可能存在。

---

## 9. 未实现与后续建议

按性价比排序：

| 优先级 | 事项 | 价值 |
|---|---|---|
| 高 | `detach=true`：断流后继续执行，完成后再取结果 | 长回答、弱网场景的必要能力 |
| 高 | 反向代理配置模板 + `/threads` 屏蔽的自动化测试 | 上线前的安全底线，目前靠文档约定 |
| 中 | 结构化审计日志（谁在何时调了哪个 Agent/Workflow、耗时、token 用量） | 多租户运营与计费基础 |
| 中 | Prometheus 指标（QPS、延迟分位、run 状态分布、限流命中） | 4C4G 小机器更需要可观测性 |
| 中 | 幂等键（`Idempotency-Key`）用于 Workflow 执行 | 客户端重试不产生重复 run |
| 低 | 按用户维度配额（当前只到应用维度） | 防止单用户打满整个应用额度 |
| 低 | Agent 级工具白名单 | 同一模型给不同租户不同工具集 |
| 低 | 会话标题由模型生成 | 目前取首条消息截断 |
| 低 | 备份自动化（定时任务 + 异地存储） | 现在只有脚本，调度靠外部 |

---

## 10. 交付记录

| 提交 | 内容 |
|---|---|
| `8568ed2` | docs: 需求梳理与架构判断 |
| `477b855` | feat(spike): 验证 Aegra 运行底座并记录 ADR |
| `85dfa0f` | feat(core): P1 基础工程、认证与数据隔离 |
| `274959b` | test(auth): 锁定 allow-list 语义 |
| `7f6eb7a` | feat(agent): P2 Agent Registry、会话与流式对话 |
| `a59526d` | feat(agent): 增加第二个示例 Agent 并验证独立调用 |
| `0b89b35` | feat(workflow): P3 Workflow Registry、同步/异步执行与运行管理 |
| `52a1509` | feat(deploy): P4 Docker Compose 部署、健康检查、备份与验收对照 |
| `4a08e96` | feat(multi-replica): Redis、读时对账、应用维度配额与模型配置接入 |
| `8345eeb` | feat(agent): 新增编排型参考 Agent（plan/tools 循环 + 循环保护） |
| `4b053b4` | feat(agent): 引入 create_agent 版 Agent、工具抽离与按 Agent 的模型路由 |

配套文档：

- [`requirements-analysis.md`](requirements-analysis.md) — 需求拆解、不变量、待决策项
- [`adr/0001-runtime-choice.md`](adr/0001-runtime-choice.md) — 运行底座选型与 Spike 结论
- [`adr/0002-multi-replica.md`](adr/0002-multi-replica.md) — 多副本与读时对账
- [`acceptance.md`](acceptance.md) — 验收对照与已知限制
- [`guide-agents-and-models.md`](guide-agents-and-models.md) — 换模型提供商、新增 Agent 的实操指南

---

## 附录 A · 目录结构

```text
app/
├── main.py                  业务 FastAPI app（Aegra 的 http.app）+ 生命周期
├── config.py                环境配置
├── cli.py                   管理 CLI
├── api/
│   ├── deps.py              认证/会话/运行时依赖
│   ├── errors.py            统一错误信封与异常处理器
│   ├── quota.py             限流依赖
│   ├── sse.py               SSE 编码
│   └── v1/                  路由：system / agents / conversations / workflows / schemas
├── auth/                    jwt / manager（Aegra 认证桥接）/ context / store / permissions / secrets
├── agents/                  Registry + 4 个实现
├── workflows/               Registry + 2 个实现
├── services/                会话编排、Workflow 执行、模型解析、限流
├── runtime/                 RuntimeAdapter 协议 + Aegra 实现 + 事件适配
├── repositories/            强制按 (app_id, user_id) 过滤的仓储
├── models/                  SQLAlchemy 模型（biz schema）
├── cache/                   Redis 客户端（自动降级）
└── database/                会话与连接池
migrations/                  Alembic（biz.biz_alembic_version）
scripts/                     entrypoint / healthcheck / backup-db
seed/                        应用与模型示例配置
tests/                       136 个测试
docs/                        需求、ADR、验收、本报告
```

## 附录 B · 常用命令

```bash
uv sync                                      # 安装依赖
uv run alembic upgrade head                  # 业务迁移
uv run python -m app.cli seed-apps --file seed/applications.example.json
uv run python -m app.cli seed-models --file seed/models.example.json
uv run python -m app.cli validate            # 校验全部密钥引用
uv run pytest                                # 全量测试
uv run ruff check app tests                  # 静态检查
uv run aegra serve --host 127.0.0.1 --port 2026
docker compose up -d                         # 完整栈
COMPOSE_PROJECT_NAME=ai-service sh scripts/backup-db.sh
```
