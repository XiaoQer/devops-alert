# Incident Intelligence

面向生产环境的多源告警归集、事故关联、自动取证和 AI 辅助分析平台。

本项目从零开始建设，与故障注入平台物理隔离。它不包含故障场景、注入器、Chaos 权限、实验恢复或评测标准答案。非生产故障实验只能通过真实监控和版本化公开契约验证本平台，不得把实验身份或答案写入诊断链路。

当前平台提供健康检查、原子且幂等的人工事故报告、Alertmanager Webhook、CloudEvents 1.0 两种模式接入、版本化服务目录、持久关联任务、规则优先的可解释事故关联，以及连接真实 MySQL 的事故中心列表、详情和认领闭环。自动取证、DiagnosisRun 自动创建、AI 分析和完整事故状态流转仍未实现。

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
- `POST/GET/PATCH /api/v1/catalog/services`：维护版本化服务目录；
- `POST/GET/PATCH /api/v1/catalog/dependencies`：维护同环境一跳依赖；
- `GET /api/v1/alerts/{id}/correlation`：读取关联任务、事故摘要和中文决策；
- `GET /api/v1/correlation/jobs`：分页读取关联任务；
- `POST /api/v1/correlation/jobs/{id}/retry`：人工重试失败任务；
- `GET /api/v1/incidents`：分页、筛选和搜索事故中心队列；
- `GET /api/v1/incidents/{id}/overview`：读取关联告警、关联解释和事实时间线；
- `POST /api/v1/incidents/{id}/claim`：以当前认证主体认领事故；
- `GET /api/v1/signals/{id}`、`/alerts/{id}`、`/incidents/{id}`、`/diagnosis-runs/{id}`：独立读取四类资源。

除存活检查外，业务接口使用 `Authorization: Bearer <Token>`。人工报告与资源读取使用 `II_API_TOKEN`，Alertmanager 使用 `II_ALERTMANAGER_TOKEN`，CloudEvents 使用 `II_CLOUDEVENTS_TOKEN`，三套 Token 不能交叉使用。人工报告和 CloudEvents 请求体最多 64 KiB，Alertmanager 最多 256 KiB 且单批最多 100 条；人工报告还必须提供长度为 1–256 的 `Idempotency-Key`。

CloudEvents 结构化模式最小调用示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/intake/cloudevents \
  -H 'Authorization: Bearer <CloudEvents Token>' \
  -H 'Content-Type: application/cloudevents+json' \
  --data-binary '{"specversion":"1.0","id":"<来源事件 ID>","source":"https://monitor.example.com/source","type":"com.incidentintelligence.alert.v1","subject":"payment-api","time":"2026-08-25T03:31:00Z","datacontenttype":"application/json","data":{"alert_key":"payment-error-rate","title":"支付接口错误率升高","summary":"错误率超过阈值","severity":"high","service":"payment-api","environment":"production","status":"firing","started_at":"2026-08-25T03:30:00Z","labels":{"region":"cn-east-1"}}}'
```

发送生产 high/critical 告警前，应先登记对应服务。以下示例不会写入 Secret，也不需要任何故障实验身份：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/catalog/services \
  -H 'Authorization: Bearer <人工 API Token>' \
  -H 'Content-Type: application/json' \
  --data-binary '{"service":"payment-api","environment":"production","owner_team":"payments"}'

curl http://127.0.0.1:8000/api/v1/alerts/<Alert ID>/correlation \
  -H 'Authorization: Bearer <人工 API Token>'
```

首版关联门槛固定为 ACTIVE、critical/high、production 且服务目录项启用。同服务 15 分钟内只有一个活动事故时自动关联；多个候选或一跳同症状候选会创建独立事故并保留中文解释，不会冒险合并。Alert 恢复只记录 `RECORDED_RESOLUTION`，不会自动关闭 Incident。

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
