# Aegra Spike（P0 验证）

验证 Aegra 0.12.0 能否作为本项目运行底座。**这是验证代码，不是生产代码**；结论见 `../../docs/adr/0001-runtime-choice.md`。

## 验证结果（2026-11-20）

| 验证点 | 结果 |
|---|---|
| 认证 handler 查 PostgreSQL，按 app 选密钥 | 通过 |
| `/api/v1` 自定义路由与 Agent Protocol 共用认证 | 通过 |
| SSE 经事件适配输出增量 token | 通过（36 个 delta，文本一致） |
| 两应用相同 `user_id` 完全隔离 | 通过（跨应用 404，列表 3 vs 0） |
| 内存与迁移共存 | 通过（Aegra 169MB + PG 45MB；`public.alembic_version` 与 `biz` 分离） |
| 重启恢复 | 通过（thread 200，checkpoint 4 条消息） |
| Workflow 同步执行 | 通过（`{"a":2,"b":3,"total":5}`） |

## 复现

```bash
cd spike/aegra

# 1. 依赖
uv venv --python 3.13
uv pip install aegra-cli pyjwt colorama

# 2. PostgreSQL（仅用于 Spike）
docker compose -f docker-compose.pg.yml up -d --wait

# 3. 业务表与两个应用配置
DATABASE_URL="postgresql://aegra:aegra@127.0.0.1:5432/aegra" \
  .venv/Scripts/python.exe scripts/seed_db.py

# 4. 启动 Aegra（会先跑它自己的迁移）
.venv/Scripts/aegra.exe serve --host 127.0.0.1 --port 2026

# 5. 生成测试 Token
.venv/Scripts/python.exe scripts/make_tokens.py > tokens.json
```

Linux / macOS 将 `.venv/Scripts/python.exe` 换成 `.venv/bin/python`，`.venv/Scripts/aegra.exe` 换成 `.venv/bin/aegra`。

## 手工验证

```bash
AGRI=$(python -c "import json;print(json.load(open('tokens.json'))['agri'])")
MGMT=$(python -c "import json;print(json.load(open('tokens.json'))['mgmt'])")

# 身份上下文
curl -s -H "Authorization: Bearer $AGRI" -H "X-App-Id: agriculture_app" \
  http://127.0.0.1:2026/api/v1/whoami

# SSE（应看到 meta -> token* -> done）
curl -N -X POST http://127.0.0.1:2026/api/v1/agents/echo_agent/chat/stream \
  -H "Authorization: Bearer $AGRI" -H "X-App-Id: agriculture_app" \
  -H "Content-Type: application/json" -d '{"message":"你好"}'

# 跨应用隔离（同一 user_id，另一个应用应 404）
curl -i -H "Authorization: Bearer $MGMT" -H "X-App-Id: management_app" \
  http://127.0.0.1:2026/api/v1/threads/<thread_id>

# Workflow
curl -s -X POST http://127.0.0.1:2026/api/v1/workflows/sum_workflow/execute \
  -H "Authorization: Bearer $AGRI" -H "X-App-Id: agriculture_app" \
  -H "Content-Type: application/json" -d '{"input":{"a":2,"b":3}}'
```

## 实测坑位

1. **Windows 依赖**：structlog 的彩色输出需要 `colorama`，否则 `aegra serve` 启动即报 `SystemError`。
2. **不要写 `from __future__ import annotations`**：Aegra 动态加载 `custom_routes.py`，Pydantic 无法在该合成模块里解析 `dict[str, Any]`，请求会 500。
3. **原生 SSE 不是增量**：`messages/partial` 携带累计内容与大量内部 metadata，必须适配后才能对外。
4. **`/threads/search` 返回裸数组**，不是 `{threads, total}`。
5. **Aegra 独占 `public.alembic_version`**：业务 Alembic 必须使用 `biz_alembic_version` 或独立 schema，否则版本表互相覆盖。
6. **单实例默认无 Redis**：`REDIS_BROKER_ENABLED=false` 时用进程内任务与内存 broker；进程崩溃会丢失进行中的 run，checkpoint 不受影响。
7. **默认 assistant 是 `uuid5(NS, graph_id)`**，所以 `agent_id == graph_id` 可直接调用，无需额外映射表。
