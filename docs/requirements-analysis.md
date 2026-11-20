# AI 智能服务引擎 · 需求梳理与架构判断

- 文档状态：已确认，进入 P0 Spike（2026-11-20）
- 依据：《AI 智能服务引擎开发说明书》
- 仓库：`custom-agent-workflow-serve`
- 更新：2026-11-20

## 0. 一句话理解

这不是一个「AI 平台」，而是一个**多租户 Agent / Workflow 运行时**：业务系统用各自签发的 JWT 调用统一 API，引擎只认 `(app_id, user_id)`，负责执行 LangGraph 图、持久化会话与执行状态，并以 SSE 流式返回。

可视化编排、Agent 编辑器、模型市场、计费与完整后台都不在范围内。

## 1. 范围边界

### 做

- 多应用注册 + 每应用独立 JWT 校验
- Agent Registry / Workflow Registry，按 ID 调用
- 会话、消息、执行记录持久化
- SSE 流式输出
- Workflow 同步 / 异步执行与状态查询
- Docker Compose 单机部署、备份、重启恢复

### 不做

- 可视化 Agent 编辑器 / Workflow 编排器
- 完整管理后台（v1 用 CLI / seed 初始化）
- 服务端部署大模型
- 微服务拆分、K8s、Kafka 等中间件
- 计费、配额、组织/团队成员体系
- HITL、定时任务、Webhook（说明书未要求）

### 需要明确的隐含项

1. 是否需要「应用管理 API」？建议 v1 只做 CLI / seed，少一个后台就少一类越权面。
2. 是否需要 SSE 断线续传？运行时可能支持，但业务上是否作为验收项？
3. 会话删除后，消息是级联删还是保留审计？

## 2. 十条不变量

违反任意一条，后期都要返工。

| # | 不变量 | 违反后果 |
|---|---|---|
| I1 | 认证只发生在 API 边界，业务层只接收 `AuthContext` | JWT 解析散落各处，无法审计 |
| I2 | 身份 = `(app_id, user_id)`，缺一不可 | 跨应用数据泄漏 |
| I3 | 资源 ID 不是授权凭证；越权一律 404 | 可通过 ID 枚举探测他人数据 |
| I4 | 每个应用独立密钥、算法、claims 校验 | 一个应用被攻破波及全部 |
| I5 | 必须验签，禁止只 decode；不记录完整 Token | 可伪造任意身份 |
| I6 | 密钥只以 `secret_ref` 引用，不入库明文、不出接口 | 密钥泄漏 |
| I7 | Message 与 Checkpoint 分开持久化 | UI 历史与图状态互相污染 |
| I8 | Agent / Workflow 只能经 Registry 调用 | 任意代码执行、路径穿越 |
| I9 | 依赖固定版本，迁移可重复执行 | 无法重复部署 |
| I10 | 先认证与隔离，后业务功能 | 事后补隔离，成本极高 |

## 3. 需求拆解

### 3.1 多应用 JWT 认证（核心模块）

**输入**：`Authorization: Bearer <JWT>` + `X-App-Id: <app_id>`

**校验顺序（顺序本身即安全设计）**：

1. `X-App-Id` 存在 → 查应用配置（进程内缓存 + TTL / 热更新）；不存在 401，`enabled=false` 403
2. `Authorization` 为 Bearer → 否则 401
3. 解析 JWT header，`alg` 必须等于该应用配置的算法；拒绝 `none` 与算法混淆
4. 用该应用的 secret / public key 验签（HS256 用 secret，RS256 用公钥）
5. 校验 `exp`（`require_exp=true` 时缺失即拒绝）、`nbf`、`iat`，允许 30s 时钟偏移
6. 配置了 `issuer` / `audience` 则校验
7. 取 `user_id_claim`（默认 `user_id`），必须为非空字符串
8. 配置了 `app_id_claim` 时，其值必须等于 `X-App-Id`（防止 Token 跨应用复用）
9. 构造 `AuthContext(app_id, user_id, "jwt")`
10. 权限校验：目标 agent / workflow 是否在 `allowed_*` 内

**配置模型**（存 `applications` 表，密钥不入库）：

```json
{
  "app_id": "agriculture_app",
  "app_name": "农业业务系统",
  "auth_type": "jwt",
  "jwt_config": {
    "algorithm": "HS256",
    "secret_ref": "env:AGRICULTURE_JWT_SECRET",
    "user_id_claim": "user_id",
    "require_exp": true,
    "issuer": null,
    "audience": null,
    "leeway_seconds": 30
  },
  "allowed_agents": ["*"],
  "allowed_workflows": ["report_daily"],
  "enabled": true
}
```

**必须定义清楚的细节**：

- `secret_ref` 规则：`env:NAME` 从环境变量读；启动时校验全部引用可解析，缺失即启动失败（fail fast）。
- 密钥轮换：v1 建议只支持「改引用 + 重启生效」，不做双密钥并存过渡。
- 缓存：应用配置读多写少，进程内缓存 60s；提供内部 reload 或重启生效。
- 错误码：401 = 缺 Token / 签名错 / 过期 / claim 缺失；403 = 应用禁用 / 无权限；404 = 资源不存在或不属于当前身份。
- 日志：记录 `app_id`、`user_id`、`request_id`、结果；**绝不记录 Token 与 secret**。
- 头名大小写：实现时统一小写处理，避免 `Authorization` / `authorization` 差异。

### 3.2 数据隔离

隔离键：`(app_id, user_id)`。相同 `user_id` 在不同 app 下是两个用户。

| 数据 | 隔离字段 | 访问方式 |
|---|---|---|
| conversations | app_id, user_id | 仅通过带双条件的 Repository |
| conversation_messages | app_id, user_id（冗余） | 先校验会话归属，再双条件查询 |
| workflow_runs | app_id, user_id | 双条件 |
| LangGraph thread | metadata: app_id, user_id | 创建时注入，授权 handler 过滤 |
| checkpoint | 随 thread | 由运行时按 thread 归属控制 |

实现约束：

- Repository 基类强制接收 `AuthContext`，禁止裸 `session.get(Model, id)`。
- 所有 by-id 查询：`WHERE id=:id AND app_id=:app_id AND user_id=:user_id`；无结果 → 404，不区分「不存在」与「无权」。
- 运行时侧：`identity = f"{app_id}:{user_id}"`，同时把 `app_id` / `user_id` 写入 thread metadata，授权 handler 统一 filter，形成双保险。
- 禁止仅凭 `conversation_id` 查消息；必须先校验会话归属。

### 3.3 Agent 管理

- 代码内定义 Agent，启动时注册；Registry 校验 ID 唯一、Graph 可编译。
- 统一契约：`agent_id`、`name`、`description`、`graph`、`input_schema`、`default_model_config`、`tools`。
- 调用链：`/api/v1/agents/{id}/chat` → 权限校验 → 找到/创建 conversation → 确保 thread → run → 持久化消息。
- 多轮：`conversation_id` 绑定唯一 `thread_id`；checkpoint 负责上下文恢复，消息表负责 UI 历史。
- 工具调用：LangGraph 原生 tool 节点；SSE 暴露工具事件（名称 / 状态 / 结果摘要）。
- 流式：token 级增量；结束发 `done`；异常发 `error` 并保留已生成内容。

### 3.4 Workflow 管理

- 代码内定义 LangGraph 图 + Pydantic 输入模型，启动时注册。
- 调用：`POST /api/v1/workflows/{id}/execute`，请求体 `{ "input": {...}, "mode": "async|sync" }`。
- 输入校验：Pydantic 失败 → 422，返回字段级错误。
- 同步：最多等待 `WORKFLOW_SYNC_TIMEOUT`（默认 30s）；完成返回结果，超时转 202 + `run_id`。
- 异步：创建后立即 202 + `run_id`，客户端轮询状态。
- 状态机：`pending → running → succeeded | failed | cancelled | timeout`。
- 结果：持久化 `output` / `error`（code、message，不含堆栈与内部路径）。
- 幂等：`execute` 支持可选 `Idempotency-Key`，同 app 同键返回同一 run。
- 取消：建议补 `POST /workflows/runs/{run_id}/cancel`（说明书未列但实际需要）。

### 3.5 会话与消息

- Conversation 1:1 Thread；创建会话时同时创建运行时 thread，失败需补偿或回滚。
- 消息写入策略：
  - 请求进入：先写 user message（预生成 `message_id`）
  - 流式结束：写 assistant message（完整内容 + `run_id`）
  - 异常 / 中断：写 assistant message，标记 `status=partial` 并附错误
  - **不用 checkpoint 反渲染 UI 历史**，checkpoint 只用于恢复图状态
- 标题：未传则由首条用户消息截断生成（建议 50 字符）。
- 删除：软删 `conversations.deleted_at`，异步删除运行时 thread；消息保留或级联需明确。
- 分页：`limit`（默认 20，最大 100）+ `before` 游标（`created_at`, `id`）。

### 3.6 模型管理

- `model_configs`：provider、base_url、model_name、api_key（加密）、temperature、max_tokens、timeout、extra。
- 加密：Fernet，主密钥来自环境变量；接口只返回掩码（`sk-...abcd`）。
- 绑定：Agent / Workflow 引用 `model_config_id` 或使用默认；不把模型硬编码进 Agent。
- 调用：外部 API；统一超时、有限重试（仅幂等错误）、错误归类（鉴权 / 限流 / 超时 / 上游 5xx）。

### 3.7 API 约定

统一前缀 `/api/v1`；所有接口要求 `Authorization` + `X-App-Id`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/agents` | 仅返回 `allowed_agents` 内的 Agent |
| POST | `/agents/{agent_id}/chat` | 非流式；body: `conversation_id?`, `message` |
| POST | `/agents/{agent_id}/chat/stream` | SSE |
| POST | `/conversations` | 创建会话（`agent_id`, `title?`） |
| GET | `/conversations` | 列表（`agent_id?`, `cursor`） |
| GET | `/conversations/{id}` | 详情 |
| GET | `/conversations/{id}/messages` | 历史消息 |
| DELETE | `/conversations/{id}` | 软删 |
| GET | `/workflows` | 仅返回 `allowed_workflows` 内的 Workflow |
| POST | `/workflows/{workflow_id}/execute` | 同步 / 异步 |
| GET | `/workflows/runs/{run_id}` | 状态 |
| GET | `/workflows/runs/{run_id}/result` | 结果 |

统一错误体：

```json
{"error": {"code": "invalid_token", "message": "Token expired", "request_id": "..."}}
```

约定：时间统一 UTC ISO 8601；分页统一 cursor；响应带 `X-Request-Id`；不返回堆栈、SQL、文件路径；OpenAPI 自动生成，SSE 事件格式单独文档化。

### 3.8 数据库

| 表 | 关键字段 | 约束 / 索引 |
|---|---|---|
| applications | app_id UK, app_name, auth_type, jwt_config JSONB, allowed_agents TEXT[], allowed_workflows TEXT[], enabled | `app_id` unique |
| conversations | id, app_id, user_id, agent_id, thread_id UK, title, deleted_at | `(app_id,user_id,updated_at desc)`；`thread_id` unique |
| conversation_messages | id, conversation_id, app_id, user_id, role, content, status, run_id | `(conversation_id, created_at)` |
| workflow_runs | id, app_id, user_id, workflow_id, aegra_run_id, thread_id, status, input, output, error, started_at, finished_at | `(app_id,user_id,created_at desc)`；`(status)` |
| model_configs | id, name UK, provider, base_url, model_name, api_key_encrypted, temperature, max_tokens, timeout | `name` unique |
| Checkpoint 表 | 由运行时管理 | 独立 schema |

建议：业务表放 `biz` schema，运行时表留在 `public`（Aegra 管理）；两套迁移使用不同的版本表。

### 3.9 部署

- Compose：`ai-service` + `postgres:16`；v1 单实例不启用 Redis（多实例 / 崩溃恢复再引入 `redis:7`）
- 卷：`pgdata`（必需）、`redisdata`（可选）
- 健康检查：`/health`、`pg_isready`、`redis-cli ping`
- 启动顺序：PG healthy（Redis 若启用）→ 迁移 → 服务
- 资源：ai-service `mem_limit 2g` + 单 worker；PG `shared_buffers=512MB`、`max_connections=100`；Redis（若启用）`maxmemory 128mb` + `noeviction`（队列不能被淘汰）
- SSE：反代关闭缓冲（`proxy_buffering off`），读超时 ≥ 300s
- 备份：`pg_dump -Fc` 每日，保留 7 份；恢复步骤写入 README 并演练
- 重启恢复：会话 / 消息 / checkpoint 可读；进行中的 run 标记 `failed` 或 `interrupted`，不假装仍在运行
- 日志：结构化 JSON、脱敏、带 `request_id`

## 4. Aegra 适配评估

### 4.1 它是什么

Aegra 是自托管的 LangGraph Platform（LangSmith Deployments）替代品：FastAPI + PostgreSQL（可选 Redis），Agent Protocol 兼容，可直接运行现有 LangGraph 图，提供 threads / runs / assistants、SSE、checkpoint、断线重连、HITL、cron、webhook、多实例 worker。Apache 2.0。

### 4.2 能力对照

| 需求 | 是否覆盖 | 说明 |
|---|---|---|
| 每应用独立 JWT | 需自研 | 自定义 `@auth.authenticate(headers)` 可按 `X-App-Id` 查配置、用不同密钥验签；不是开箱的多租户配置 |
| app + user 隔离 | 基本覆盖，需加固 | `identity` 设为 `app_id:user_id`；thread metadata + `@auth.on.*` filter 双保险 |
| Agent / Workflow 调用 | 覆盖 | assistants + `runs/wait` / `runs/stream` |
| SSE 流式 | 覆盖 | 含事件重放与重连，比自研稳 |
| Checkpoint 持久化 | 覆盖 | Postgres checkpointer |
| 运行状态管理 | 覆盖 | runs 生命周期 |
| 业务 API（会话 / 消息 / 执行记录） | 不覆盖 | 需自定义路由 + 自建表 |
| 轻量单机 | 覆盖 | 单实例默认不启用 Redis；仅多实例 / 崩溃恢复需要 |

### 4.3 三个可选方案

| 方案 | 形态 | 优点 | 代价 |
|---|---|---|---|
| A. Aegra 作宿主 | 单进程：Aegra + `/api/v1` 自定义路由 | SSE / checkpoint / run 生命周期 / 重连现成，自研运行时最少 | 绑定 Agent Protocol 形态；默认不强制 Redis（多实例才需要）；业务路由需经 SDK / loopback 调 runtime；项目较新 |
| B. 自建运行时 | FastAPI + LangGraph + `AsyncPostgresSaver` | 依赖最少（无 Redis）、API 形状完全自控、4C4G 最省 | 需自研 run 状态、SSE、恢复；无 HITL / cron（本项目不需要） |
| C. 网关 + Aegra 双服务 | Gateway 管认证业务，Aegra 只做 runtime | 职责清晰，可独立扩缩容 | 多一跳、双写、内网信任链、4C4G 压力最大 |

### 4.4 结论（2026-11-20）

Spike 5 项全部通过，**采纳方案 A：Aegra 作宿主**；C 不采用。完整记录见 `docs/adr/0001-runtime-choice.md`，验证代码见 `spike/aegra/`。

关键实测结果：

- 认证 handler 可在请求内查 PostgreSQL（asyncpg 连接池）；两应用独立密钥生效，过期 / 错签名 / `alg=none` / 未知应用 / 跨应用复用均 401。
- `/api/v1` 自定义路由与 Agent Protocol 共用同一认证；无 Token 401。
- 原生 SSE 是 `messages/partial` 累计快照并携带内部 metadata，**不能直接对外**，必须适配为增量事件。
- 两个应用、相同 `user_id=10001`：跨应用读写均 404，列表 3 条 vs 0 条，无交集。
- Aegra 进程 169MB + PostgreSQL 45MB，单实例无需 Redis；Aegra 独占 `public.alembic_version`，业务侧需用独立版本表。
- 重启后 thread、checkpoint、列表均恢复；Workflow 同步执行返回正确结果。

## 5. 推荐落地形态（方案 A）

```text
业务系统 A/B/C
   │  Authorization: Bearer <各应用 JWT>
   │  X-App-Id: <app_id>
   ▼
┌──────────────────────────────────────────────┐
│ ai-service（单容器）                           │
│                                              │
│  /api/v1/*  自定义 FastAPI 路由                │
│      ├─ auth handler（按 app 选密钥验签）       │
│      │      └─ AuthContext(app_id, user_id)   │
│      ├─ 权限校验 allowed_agents / workflows    │
│      ├─ 业务服务（会话 / 消息 / 执行记录）        │
│      └─ Agent / Workflow Registry             │
│             │                                │
│             ▼  RuntimeAdapter                │
│      Aegra Agent Protocol（loopback）          │
│             ▼                                │
│      LangGraph 图 + Checkpointer              │
└──────────────┬───────────────────────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
  PostgreSQL        Redis（可选）
  biz + public      v1 不启用；多实例再引入
```

关键点：

- 对外只暴露 `/api/v1`；运行时原生 `/threads`、`/runs`、`/assistants` 由反代屏蔽，避免绕过业务校验。
- 内部调用透传原始 `Authorization` + `X-App-Id`，认证只有一套逻辑，不做「内部免认证」后门。
- `RuntimeAdapter` 定义 `create_thread / run_stream / run_wait / get_run / cancel_run / delete_thread`，业务层不出现运行时类型。
- `agent_id` / `workflow_id` 与运行时 graph_id 一一对应，避免额外映射表。

## 6. 分阶段与验收映射

| 阶段 | 交付物 | 覆盖验收 |
|---|---|---|
| P0 Spike | Aegra 五项验证 + ADR 决策记录 | 为 9 / 10 铺路 |
| P1 基础与认证 | 工程骨架、配置、Alembic、applications seed / CLI、认证模块、AuthContext、隔离 Repository、认证与隔离测试 | 1, 2, 3, 4 |
| P2 Agent 与会话 | Registry、示例 Agent（含工具）、会话 / 消息、同步对话、SSE、测试 | 5, 7, 8 |
| P3 Workflow | Registry、输入校验、同步 / 异步、状态 / 结果、错误与超时、测试 | 6 |
| P4 交付 | Compose、健康检查、备份、重启恢复、文档、全量验收 | 9, 10 |

每阶段门槛：测试通过 + 验收项可演示，才进入下一阶段。

### 验收标准 → 测试方式

| # | 验收 | 测试 |
|---|---|---|
| 1 | 多应用不同密钥 | app A / B 各自签发 Token，均 200 |
| 2 | 错误 / 过期 / 非法 JWT 拒绝 | 错签名、过期、`alg=none`、缺 `exp` → 401 |
| 3 | 跨应用不可访问 | A 建会话，B 同 `user_id` 读 / 删 → 404 |
| 4 | 同 `user_id` 跨应用隔离 | 两 app 同 `user_id`，列表互不可见 |
| 5 | 多 Agent 注册调用 | 两个 Agent 各自调用成功；不存在的 ID → 404 |
| 6 | 多 Workflow 注册执行 | 同步 / 异步各一，状态与结果正确 |
| 7 | 多轮与历史 | 三轮对话，上下文生效，消息顺序正确 |
| 8 | SSE | 事件顺序 `meta → token* → done`；异常有 `error`；中断可取消 |
| 9 | 持久化与重启恢复 | 重启后会话 / 消息 / checkpoint 完整；进行中 run 标记失败 |
| 10 | Compose 独立部署 | 干净机器 `docker compose up -d` 后全流程通过 |

## 7. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Aegra 较新、API 变动 | 升级破坏 | 固定精确版本；`RuntimeAdapter` 隔离；升级跑冒烟测试 |
| Redis 被误当必选 | 增加运维面 | 单实例默认关闭；仅多实例引入并写入部署文档 |
| 4C4G 内存不足 | OOM / 重启 | 单 worker；PG / Redis（若启用）限额；压测并发；预留升配 |
| 消息与 checkpoint 双源不一致 | 历史错乱 | 明确写入时机；失败补偿；UI 只读消息表 |
| SSE 被反代缓冲 | 流式失效 | `X-Accel-Buffering: no` + 关闭 buffering + 调整超时 |
| 密钥泄漏 | 全量身份伪造 | 仅环境变量；日志脱敏；最小权限；轮换流程 |
| 绕过 `/api/v1` 直连原生路由 | 越权 | 反代屏蔽 `/threads`、`/runs`、`/assistants`；identity 命名空间兜底 |
| 长任务拖垮实例 | 资源耗尽 | 单次超时、最大执行时长、并发上限、取消接口 |
| 连接池耗尽 | 请求排队 | 池大小与 PG `max_connections` 匹配 |

## 8. 待确认决策

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| D1 | 运行底座选 A / B / C | Aegra 宿主 / 自建 / 双服务 | **已定**：Spike 通过，采纳 A（Aegra 作宿主） |
| D2 | `allowed_*` 空数组语义 | 全允许 / 全禁止 | **已定**：`["*"]` 全允许，`[]` 全禁止 |
| D3 | Workflow 默认模式 | 同步 / 异步 | **已定**：默认异步；`mode=sync` 超时 30s |
| D4 | JWT 算法范围 | 仅 HS256 / HS256 + RS256 | **已定**：HS256 + RS256 |
| D5 | 断流是否取消 run | 取消 / 继续 | 默认取消省成本；`detach=true` 可继续 |
| D6 | 会话删除 | 软删 / 硬删 | 软删；清理策略后置 |
| D7 | 应用 / 模型配置入口 | 仅 CLI / seed / 管理 API | v1 仅 CLI / seed，不加后台 |
| D8 | 是否做 `Idempotency-Key` | 做 / 不做 | 做，`execute` 接口成本低收益高 |
| D9 | 是否需要 HITL / 定时任务 | 需要 / 不需要 | 说明书未要求，v1 不做 |
| D10 | 多实例扩展 | 单实例 / 预留 | v1 单实例，设计不堵死 |

## 9. 已确认的默认值（2026-11-20，D5–D10 按此执行）

- 身份键 `app_id:user_id`；越权统一 404。
- `allowed_agents` / `allowed_workflows`：`["*"]` 表示全部，`[]` 表示禁止。
- JWT 支持 HS256 + RS256；`require_exp=true`；30s 时钟偏移。
- Workflow 默认异步；同步超时 30s。
- SSE 断流取消 run；消息完成时落库，异常落 `partial`。
- 应用与模型配置先用 CLI / seed，不做管理后台。
- 业务表 `biz` schema，运行时表在 `public`（Aegra 管理）。
- 单实例 Docker Compose；v1 默认不启用 Redis。
