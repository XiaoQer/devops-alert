# 宽容的 Alertmanager 告警接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 在不访问 Kubernetes、不解析 service 的前提下，可靠接收缺少业务标签的 Alertmanager 告警。

**架构：** Alertmanager 适配器只严格约束协议、安全和容量；业务字段使用可空值或明确默认值。真实标签生成实体摘要用于展示与归组，服务为空的告警组安全跳过服务型事故关联。

**技术栈：** Python 3.13、FastAPI、Pydantic、SQLAlchemy、Alembic、MySQL 8.4、Vue 3、Vitest、Pytest。

**规格：** `specs/active/entity-aware-alert-intake.md`。

## 全局约束

- 直接在 main 开发，不使用分支、worktree 或子 Agent；
- 每项行为先观察失败测试；
- 不增加 Kubernetes SDK、凭据、RBAC、Worker 或异步 service 解析；
- 不猜测 service，不保存未知字段和原始 Webhook；
- CloudEvents、人工报告和 Incident 的 service 契约保持不变。

### 任务 1：建立可空服务和真实实体领域契约

- [x] 新增 `domain/entities.py` 和实体优先级测试；
- [x] SignalCommand、SignalEvent、Alert 支持可空 service 和实体字段；
- [ ] 删除服务解析状态、来源、可信度和原因码，仅保留实体类型、键和显示名；
- [ ] 运行领域测试、Ruff、格式和 Mypy；
- [ ] 提交修订。

### 任务 2：实现业务字段宽容的 Alertmanager 适配器

- [x] 编写无 service 和 commonLabels 合并失败测试；
- [ ] 允许 service 为空，缺少标题内容时安全回退；
- [ ] 将未知顶层和单条字段改为忽略，同时保留完整禁止身份扫描；
- [ ] 增加 API 混合批次测试并验证 202、原子写入和未知字段不持久化；
- [ ] 运行适配器和 HTTP 回归并提交。

### 任务 3：迁移 MySQL 并持久化可空服务与实体

- [ ] 先写 0007 升级、回填、空服务和保护性降级测试；
- [ ] 修改 ORM、仓储和接入服务映射；
- [ ] 验证重放、并发收敛和批次回滚；
- [ ] 运行迁移与持久化回归并提交。

### 任务 4：按实体归组并安全跳过事故关联

- [ ] 先写相同实体合并、不同实体隔离和无服务跳过测试；
- [ ] AlertGroup 以实体键作为相似性主键，service 可空；
- [ ] 保存 `SKIPPED_SERVICE_MISSING` 决策，不创建虚假 Incident；
- [ ] 运行归组、关联和 100 条风暴测试并提交。

### 任务 5：更新告警 API 和前端展示

- [ ] 先写 API service=null 与实体字段测试；
- [ ] 先写前端“服务未提供”“对象未提供”测试；
- [ ] 实现列表、详情、告警组和筛选兼容；
- [ ] 运行前后端聚焦测试并提交。

### 任务 6：统一验收和文档收口

- [ ] 运行后端全部测试、覆盖率、Ruff、格式、Mypy 和迁移往返；
- [ ] 运行前端 Vitest、构建和 Sites 测试；
- [ ] 用真实 KubePodNotReady 标签构造 Webhook，验证接收但不访问集群；
- [ ] 更新 current-state、architecture 和验证记录；
- [ ] 将规格移入 completed 并提交。
