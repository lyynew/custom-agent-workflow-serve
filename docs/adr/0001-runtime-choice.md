# ADR 0001：运行底座选型 —— 采纳 Aegra 作宿主

- 状态：Accepted
- 日期：2026-11-20
- 决策范围：P0 Spike 的 5 个验证点
- 验证代码：`spike/aegra/`

## Context

说明书要求：多应用独立 JWT、`app_id + user_id` 完全隔离、多 Agent / 多 Workflow、SSE、执行状态持久化、4C4G 单机部署、Redis 按需引入。候选底座 Aegra 0.12.0（FastAPI + PostgreSQL，Agent Protocol 兼容）。

在写业务代码前，必须回答：Aegra 能否承载「多租户认证 + 业务 API」，以及它的 SSE、隔离、迁移是否真的可用。

## Decision

**采纳方案 A：Aegra 作宿主。**

- Aegra 提供 threads / runs / checkpoint / SSE / 运行生命周期。
- 我们的 `/api/v1` 作为自定义 FastAPI 路由挂载在同一个 app 上，共用同一套认证。
- 业务层通过 `RuntimeAdapter` 调用运行时，不直接依赖 Aegra 类型。
- **v1 单实例不启用 Redis**（`REDIS_BROKER_ENABLED=false`），符合「按需引入」。
- 暂不采用方案 B（自研运行时）与方案 C（网关 + Aegra 双服务）。

## Evidence

| # | 验证点 | 方法 | 结果 |
|---|---|---|---|
| 1 | 认证 handler 能否查库 | `@auth.authenticate` 内用 asyncpg 连接池查 `biz.applications`，按 `secret_ref` 取环境变量验签 | 通过。两个应用各自 200；过期 / 错签名 / `alg=none` / 未知应用 / 缺 `X-App-Id` / 跨应用复用均 401 |
| 2 | 自定义路由 + 共用认证 | `http.app` 挂载 `/api/v1`，`enable_custom_route_auth=true` | 通过。无 Token 401；带 Token 200；`whoami` 返回 `app_id` / `user_id` |
| 3 | SSE 透传 | 确定性流式图 + 业务路由代理 | 通过，但**必须适配**。原生事件是 `messages/partial` 累计快照并携带内部 metadata；适配后输出 36 个 `token` 增量，拼接文本与预期完全一致，控制事件仅 `meta` / `done` |
| 4 | 跨应用隔离 | 两应用、相同 `user_id=10001` | 通过。本人读 200；跨应用经 `/api/v1` 与原生 `/threads` 均 404；列表 3 条 vs 0 条，无交集 |
| 5 | 资源与迁移共存 | `docker stats` + 进程内存 + `pg_tables` | 通过。Aegra 进程 169.4MB、PostgreSQL 45.3MB、无 Redis；Aegra 表在 `public`，业务表在 `biz`；Aegra 独占 `public.alembic_version` |

额外验证：重启服务后 thread 仍为 200、checkpoint 仍有 4 条消息、列表仍为 3 条；Workflow 同步执行返回 `{"a":2,"b":3,"total":5}`；`allowed_agents=[]` 返回 403，`["*"]` 返回 200。

## Consequences

### 正向

- run 生命周期、checkpoint、SSE 重连、并发控制直接复用，省掉最容易做砸的部分。
- 单进程 + PostgreSQL 即可跑，4C4G 余量充足；Redis 只在多实例/崩溃恢复时需要。
- `identity = {app_id}:{user_id}` 与 Aegra 的 `thread.user_id` 过滤天然契合，隔离不靠业务代码自觉。
- 默认 assistant 由 `uuid5(namespace, graph_id)` 确定性生成，`agent_id == graph_id` 即可直接调用。

### 负向 / 约束

- Aegra 0.12 较新，API 可能变动，必须固定精确版本。
- 原生 SSE 事件面向 LangGraph 生态，不适合直接暴露给业务系统，必须维护事件适配层。
- 自定义路由是「客人」：核心 `/threads`、`/runs`、`/assistants` 不能被覆盖，需要反代屏蔽。
- Aegra 动态加载 `http.app` 模块；模块内使用 `from __future__ import annotations` 会导致 Pydantic 无法解析 `dict[str, Any]`（已实测 500）。
- Aegra 迁移默认占用 `public.alembic_version`，业务 Alembic 不能再用同名表。
- 无 Redis 时，进程崩溃会丢失进行中的 run（checkpoint 仍在）。

## Guardrails

1. 业务层只依赖 `RuntimeAdapter`，禁止 `import aegra_api`（认证与路由挂载除外）。
2. 固定版本：`aegra-api==0.12.0`、`aegra-cli==0.12.0`、`langgraph==1.2.23`、`langchain-core==1.8.3`。
3. 对外只暴露 `/api/v1`；反代屏蔽 `/threads`、`/runs`、`/assistants`、`/store`、`/crons`。
4. `custom_routes.py` 禁止 `from __future__ import annotations`。
5. SSE 事件 schema 由适配层定义，禁止原样透传 Aegra 事件。
6. 业务表放 `biz` schema，Alembic 使用 `version_table="biz_alembic_version"`。
7. v1 单实例、单 worker、`REDIS_BROKER_ENABLED=false`；多实例方案另立 ADR。
8. 启动时把 `workflow_runs` 中 `running/pending` 的记录标记为 `interrupted`，不假装仍在执行。

## Follow-ups（P1）

- 搭 `app/` 骨架与 `RuntimeAdapter`，业务层不出现 Aegra 类型。
- Alembic 初始化，验证与 `public.alembic_version` 共存。
- 认证模块产品化：配置缓存、热更新、密钥轮换、错误码统一。
- 并发压测：10 / 30 路流式同时进行，观察内存与连接池。
- 反代验证：确认 `/threads`、`/runs` 外部不可达，`/api/v1` SSE 不被缓冲。

## References

- Aegra: <https://github.com/aegra/aegra>，文档 <https://docs.aegra.dev>
- 认证：<https://docs.aegra.dev/guides/authentication>
- 自定义路由：<https://docs.aegra.dev/guides/custom-routes>
- 需求梳理：`docs/requirements-analysis.md`
- Spike 复现：`spike/aegra/README.md`
