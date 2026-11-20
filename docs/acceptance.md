# 验收对照

对照《AI 智能服务引擎开发说明书》第十一节的 10 条验收标准，逐条给出结论、自动化证据和实际运行证据。

复现方式：

```bash
uv run pytest                 # 全部自动化测试
uv run ruff check app tests   # 静态检查
docker compose up -d --build  # 部署验证（见文末「部署验证记录」）
```

## 总览

| # | 验收项 | 结论 | 主要证据 |
|---|---|---|---|
| 1 | 多业务系统用不同 JWT 密钥接入 | 通过 | `test_auth_manager.py` + 容器内双应用实测 |
| 2 | 签名错误 / 过期 / 格式非法一律拒绝 | 通过 | `test_jwt.py`（15 例）+ 坏 Token 实测 401 |
| 3 | 应用之间无法访问对方会话与消息 | 通过 | `test_isolation.py` + 跨应用 404 实测 |
| 4 | 相同 `user_id` 在不同应用仍隔离 | 通过 | `test_isolation.py` + P0 原生接口验证 |
| 5 | 多 Agent 注册并独立调用 | 通过 | `test_agent_registry.py` + 双 Agent 实测 |
| 6 | 多 Workflow 注册并执行 | 通过 | `test_workflow_registry.py` + 双 Workflow 实测 |
| 7 | 多轮对话与历史记录 | 通过 | `test_chat_api.py` + 重启后读取历史 |
| 8 | SSE 流式响应 | 通过 | `test_sse_adapter.py` + 增量 token 实测 |
| 9 | PostgreSQL 持久化与重启恢复 | 通过 | `test_migrations.py` + 容器重启演练 |
| 10 | Docker Compose 独立部署 | 通过 | `docker-compose.yml` + healthy 双容器 + 冒烟 |

## 逐条说明

### 1. 多个业务系统可以使用不同 JWT 密钥接入

每个应用在 `biz.applications` 里有独立的 `algorithm`、`secret_ref`、`user_id_claim`、`require_exp` 等配置；`X-App-Id` 选择配置，签名用该应用自己的密钥验证。

- `tests/test_auth_manager.py::test_two_apps_get_namespaced_identities` — 两个应用各自验证，得到 `agriculture_app:10001` 与 `management_app:10001`
- `tests/test_api.py::test_whoami_returns_namespaced_identity` — HTTP 层身份上下文
- 实测：容器内用两套密钥各签一个 Token，`/api/v1/whoami` 均 200 且 `identity` 正确

### 2. JWT 签名错误、过期或格式不合法时拒绝请求

- `tests/test_jwt.py` — 过期、`leeway` 容忍、错签名、`alg=none`、缺 `exp`（`require_exp` 时拒绝）、缺 user claim、`app_id_claim` 不匹配、RS256 与算法钉死，共 15 例
- `tests/test_auth_manager.py` — 未知应用、停用应用、缺 `X-App-Id`、缺 Bearer
- 实测：`Authorization: Bearer nope` → `401`

### 3. 不同应用之间完全无法访问对方的会话和消息

- `tests/test_isolation.py` — 会话列表、按 ID 读取、消息列表全部按 `(app_id, user_id)` 过滤
- `tests/test_conversations_api.py::test_same_user_id_in_another_app_cannot_read_conversation`
- `tests/test_chat_api.py::test_cannot_chat_into_another_apps_conversation`
- 实测：跨应用 `GET /api/v1/conversations/{id}` → `404`（不区分「不存在」与「不属于你」）

### 4. 相同 `user_id` 在不同应用中仍然相互隔离

- `tests/test_isolation.py::test_same_user_id_in_two_apps_is_isolated`
- `docs/adr/0001-runtime-choice.md` 记录 P0 阶段对运行时原生接口的验证：两个应用都用 `user_id=10001`，跨应用读 `/threads/{id}` 与 `/api/v1/threads/{id}` 均 404，列表无交集

### 5. 多个 Agent 可以注册和独立调用

- `tests/test_agent_registry.py`（5 例）— 注册、重复 ID、未注册、`aegra.json` 一致性
- `tests/test_agents_api.py`（3 例）— `allowed_agents` 过滤
- `tests/test_chat_api.py::test_each_agent_is_called_independently`
- 实测：`echo_agent` → `Echo: hello`，`reverse_agent` → `dcba`

### 6. 多个 Workflow 可以注册和执行

- `tests/test_workflow_registry.py`（9 例）— 注册表、输入契约、未知 Workflow、JSON Schema 导出
- `tests/test_workflow_graphs.py`（3 例）— 加法图、文本统计图的两个分支
- `tests/test_workflow_api.py`（17 例）— 同步 / 异步、状态流转、结果、取消、超时、并发上限、隔离、启动对账
- 实测：`sum_workflow` → `{"a":2,"b":3,"total":5}`；`text_stats_workflow` 长文本走 `detailed` 分支

### 7. 支持多轮对话和历史记录查询

- `tests/test_chat_api.py::test_multi_turn_reuses_conversation_and_thread` — 复用同一 `conversation_id` 与 `thread_id`，checkpoint 累积上下文
- `tests/test_conversations_api.py::test_create_list_get_delete_conversation`
- 实测：容器重启后仍能读到 `[('user', ...), ('assistant', 'Echo: ...')]`

### 8. 支持 SSE 流式响应

- `tests/test_sse_adapter.py`（3 例）— 累计快照 → 增量 `token`，跨事件去重
- `tests/test_chat_api.py` — `meta → token → done`、工具事件、运行失败落 `partial`、客户端断流落 `partial`
- 实测：36 个 `token` 拼接后与预期文本完全一致（P0 记录）

### 9. 支持 PostgreSQL 持久化和服务重启恢复

- `tests/test_migrations.py` — 业务表在 `biz` schema，使用 `biz.biz_alembic_version`，未污染 `public.alembic_version`
- `tests/test_workflow_api.py::test_startup_reconciliation_interrupts_orphan_runs` — 启动把 `pending`/`running` 标为 `interrupted`
- 实测（容器）：埋一条 `running` 的 run → `docker compose restart ai-service` → 该 run 变为 `interrupted` 且 `finished_at` 有值，日志 `Marked 1 in-flight workflow run(s) as interrupted`；会话与消息仍可读

### 10. 可以通过 Docker Compose 独立部署

- `Dockerfile` — 分层构建，依赖层用 `uv sync --frozen`，非 root 运行
- `docker-compose.yml` — `ai-service` + `postgres:16`，健康检查、内存上限、日志轮转、命名卷
- `scripts/entrypoint.sh` — 先跑业务迁移，再按需 upsert 应用，最后启动服务
- 实测见下表

## 部署验证记录

在 Windows + Docker Desktop 上实际执行（项目名 `p4check`，验证后已 `down -v`）：

| 步骤 | 命令 | 结果 |
|---|---|---|
| 构建 | `docker compose build` | 镜像构建成功 |
| 启动 | `docker compose up -d` | postgres 先 healthy，再启动 ai-service |
| 启动顺序 | `docker compose logs ai-service` | `biz` 迁移 → 种子 2 个应用 → Aegra `public` 迁移 → 服务监听 |
| 健康检查 | `docker compose ps` | 两个容器均 `(healthy)`，探针命中 `/health` |
| 冒烟 | `/whoami`、`/agents`、`/workflows`、同步对话、同步 Workflow | 全部 200，结果正确 |
| 隔离 | 跨应用读会话 | `404` |
| 备份 | `scripts/backup-db.sh` | 生成 6.8KB dump，含 `CREATE SCHEMA biz`，19 张表 |
| 恢复 | 恢复到临时库 `restore_check` | `applications=2`、`conversations=1`、`messages=2`、`workflow_runs=1`、`thread=2`、`run=2` |
| 重启恢复 | `docker compose restart ai-service` | 恢复 healthy；孤儿 run 变 `interrupted`；历史数据仍可读 |

## 已知限制

这些是**有意的取舍**，不是缺陷遗漏；每条都写明了触发条件和影响面。

1. **没有 Redis，不能多副本。** 后台工作流轮询在进程内，进程崩溃会丢失进行中的 run（checkpoint 不受影响，重启时统一标 `interrupted`）。因此 `docker-compose.yml` 固定单副本，不要 `--scale`。
2. **后台轮询沿用调用方 Token。** `WORKFLOW_RUN_TIMEOUT_SECONDS`（默认 600s）必须明显小于 Token 有效期，否则长任务会在轮询阶段 401。
3. **客户端断流会取消运行。** 运行时默认 `on_disconnect=cancel`，已生成内容以 `status="partial"` 落库；`detach=true`（断流后继续跑）未实现。
4. **401 的响应体是 Aegra 的 Agent Protocol 信封**（`{"error":"unauthorized","message":...,"details":null}`），其余错误（403/404/409/422/429/502/504）用本项目的统一信封 `{"error":{"code","message","request_id"}}`。401 由 Aegra 的认证依赖在进入业务路由前产生，无法用异常处理器改写。集成方应以 HTTP 状态码为判断依据。
5. **并发上限是软限制。** `WORKFLOW_MAX_CONCURRENT_RUNS` 通过统计活跃 run 数实现，同时到达的请求可能同时越过检查。
6. **没有附带反向代理配置。** 部署时必须由 nginx 等屏蔽 `/threads`、`/runs`、`/assistants`、`/store`、`/crons`，只暴露 `/api/v1`；compose 已把端口绑定在 `127.0.0.1` 作为兜底。
7. **会话删除只对业务侧软删并尽力删除运行时 thread**，失败只记日志；残留 thread 由 Aegra 的 `POST /threads/prune` 清理。
