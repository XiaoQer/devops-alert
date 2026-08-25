# Incident Intelligence

面向生产环境的多源告警归集、事故关联、自动取证和 AI 辅助分析平台。

本项目从零开始建设，与故障注入平台物理隔离。它不包含故障场景、注入器、Chaos 权限、实验恢复或评测标准答案。非生产故障实验只能通过真实监控和版本化公开契约验证本平台，不得把实验身份或答案写入诊断链路。

当前后端提供健康检查、原子且幂等的人工事故报告、Alertmanager Webhook、CloudEvents 1.0 两种模式接入，以及 SignalEvent、Alert、Incident、DiagnosisRun 四类资源的独立读取 API。事故关联、自动取证、Worker、AI 分析、事故运营写接口和前端仍未实现。

## 项目事实

- 产品定义：`docs/product.md`
- 架构边界：`docs/architecture.md`
- 当前状态：`docs/current-state.md`
- 活跃规格：`specs/active/multi-source-incident-center.md`
- 完整设计：`docs/superpowers/specs/2026-08-24-multi-source-incident-center-design.md`
- 阶段验收：`docs/verification/2026-08-24-backend-foundation.md`

## 本地启动

需要 Python 3.13 或 3.14 和 MySQL 8.4。数据库密码与 API Token 只通过当前终端或密钥管理器提供，不要写入仓库文件。以下示例连接本机已有 MySQL；项目 Compose 只用于自动化测试。

```bash
cd backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
export II_DATABASE_URL='mysql+pymysql://<用户>:<URL编码密码>@127.0.0.1:3307/incident_intelligence'
export II_API_TOKEN='<本地随机 Token>'
export II_ALERTMANAGER_TOKEN='<Alertmanager 专用随机 Token>'
export II_CLOUDEVENTS_TOKEN='<CloudEvents 专用随机 Token>'
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m uvicorn incident_intelligence.main:create_app --factory --host 127.0.0.1 --port 8000
```

服务启动后可访问：

- `GET /health/live`：进程存活；
- `GET /health/ready`：数据库就绪；
- `POST /api/v1/manual-reports`：提交人工事故报告；
- `POST /api/v1/intake/alertmanager`：接收 Alertmanager Webhook v4；
- `POST /api/v1/intake/cloudevents`：接收 CloudEvents 1.0 结构化或 Binary 事件；
- `GET /api/v1/signals/{id}`、`/alerts/{id}`、`/incidents/{id}`、`/diagnosis-runs/{id}`：独立读取四类资源。

除存活检查外，业务接口使用 `Authorization: Bearer <Token>`。人工报告与资源读取使用 `II_API_TOKEN`，Alertmanager 使用 `II_ALERTMANAGER_TOKEN`，CloudEvents 使用 `II_CLOUDEVENTS_TOKEN`，三套 Token 不能交叉使用。人工报告和 CloudEvents 请求体最多 64 KiB，Alertmanager 最多 256 KiB 且单批最多 100 条；人工报告还必须提供长度为 1–256 的 `Idempotency-Key`。

CloudEvents 结构化模式最小调用示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/intake/cloudevents \
  -H 'Authorization: Bearer <CloudEvents Token>' \
  -H 'Content-Type: application/cloudevents+json' \
  --data-binary '{"specversion":"1.0","id":"<来源事件 ID>","source":"https://monitor.example.com/source","type":"com.incidentintelligence.alert.v1","subject":"payment-api","time":"2026-08-25T03:31:00Z","datacontenttype":"application/json","data":{"alert_key":"payment-error-rate","title":"支付接口错误率升高","summary":"错误率超过阈值","severity":"high","service":"payment-api","environment":"production","status":"firing","started_at":"2026-08-25T03:30:00Z","labels":{"region":"cn-east-1"}}}'
```

## 后端验证

测试必须使用项目独立 MySQL 8.4 容器。测试引导地址只能指向 MySQL 自带的 `mysql` 数据库；每轮验证会自动创建并精确删除随机测试数据库：

```bash
ii_mysql_test_password=$(openssl rand -hex 24)
export II_MYSQL_TEST_ROOT_PASSWORD="$ii_mysql_test_password"
export II_MYSQL_TEST_PORT=43306
docker compose up -d --wait mysql-test
export II_TEST_DATABASE_URL="mysql+pymysql://root:${ii_mysql_test_password}@127.0.0.1:43306/mysql"
./scripts/verify-backend.sh
docker compose down -v
```

统一脚本会执行 Ruff、格式检查、Mypy、迁移集成测试、API 测试和覆盖率门槛。
