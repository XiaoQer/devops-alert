# Incident Intelligence

面向生产环境的多源告警归集、事故关联、自动取证和 AI 辅助分析平台。

本项目从零开始建设，与故障注入平台物理隔离。它不包含故障场景、注入器、Chaos 权限、实验恢复或评测标准答案。非生产故障实验只能通过真实监控和版本化公开契约验证本平台，不得把实验身份或答案写入诊断链路。

后端阶段 1 已完成：当前提供健康检查、原子且幂等的人工事故报告入口，以及 SignalEvent、Alert、Incident、DiagnosisRun 四类资源的独立读取 API。Alertmanager、CloudEvents、事故关联、自动取证、Worker、AI 分析、事故运营写接口和前端仍未实现。

## 项目事实

- 产品定义：`docs/product.md`
- 架构边界：`docs/architecture.md`
- 当前状态：`docs/current-state.md`
- 活跃规格：`specs/active/multi-source-incident-center.md`
- 完整设计：`docs/superpowers/specs/2026-08-24-multi-source-incident-center-design.md`
- 阶段验收：`docs/verification/2026-08-24-backend-foundation.md`

## 本地启动

需要 Python 3.13 或 3.14、Docker 和 Docker Compose。数据库密码与 API Token 只通过当前终端或密钥管理器提供，不要写入仓库文件。

```bash
export II_POSTGRES_DB=incident_intelligence
export II_POSTGRES_USER=incident_intelligence
export II_POSTGRES_PASSWORD='<本地随机密码>'
export II_POSTGRES_PORT=55432
docker compose up -d --wait postgres

cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
export II_DATABASE_URL='postgresql+psycopg://<用户>:<密码>@127.0.0.1:55432/<数据库>'
export II_API_TOKEN='<本地随机 Token>'
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn incident_intelligence.main:create_app --factory --host 127.0.0.1 --port 8000
```

服务启动后可访问：

- `GET /health/live`：进程存活；
- `GET /health/ready`：数据库就绪；
- `POST /api/v1/manual-reports`：提交人工事故报告；
- `GET /api/v1/signals/{id}`、`/alerts/{id}`、`/incidents/{id}`、`/diagnosis-runs/{id}`：独立读取四类资源。

除存活检查外，业务接口使用 `Authorization: Bearer <Token>`。人工报告还必须提供长度为 1–256 的 `Idempotency-Key`。

## 后端验证

测试必须连接专用 PostgreSQL 测试库，不要指向生产或共享数据库：

```bash
export II_TEST_DATABASE_URL='postgresql+psycopg://<测试用户>:<密码>@127.0.0.1:55432/<测试数据库>'
scripts/verify-backend.sh
```

统一脚本会执行 Ruff、格式检查、Mypy、迁移集成测试、API 测试和覆盖率门槛。
