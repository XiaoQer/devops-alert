# 服务目录与可解释事故关联验收记录

## 验收范围

本记录验收版本化服务目录、一跳依赖、持久关联任务、确定性候选规则、Incident 创建与关联、不可变中文决策、读取与人工重试 API，以及后台 Runner。自动取证、DiagnosisRun 自动创建、AI、事故运营写接口和前端不在本次范围内。

## 自动化验证

在本机 MySQL 8.4 的隔离随机测试数据库中执行：

```bash
II_TEST_DATABASE_URL='<仅当前终端可见的测试引导地址>' ./scripts/verify-backend.sh
```

结果：

- Ruff 检查通过，89 个文件格式检查通过；
- 54 个源文件与迁移文件的 Mypy 严格检查通过；
- Alembic `0001_mysql_initial ↔ 0002_service_catalog_correlation` 升级、降级、回填、约束和 ORM 一致性通过；
- 292 项测试通过，失败 0 项；
- 覆盖率 94.83%，高于 90% 门槛。

## 规则与事务矩阵

- ACTIVE、critical/high、production、目录启用四项门槛逐项验证；
- 同服务 15 分钟内零候选创建事故、唯一候选自动关联、多候选安全创建独立事故；
- 一跳依赖按双向邻接读取，同一标准症状只记录候选，不自动合并；
- 未知症状、跨环境、窗口外和终态事故不作为候选；
- 已关联活动 Alert 保持原关系，恢复 Alert 只记录恢复事实，不自动关闭事故；
- critical Alert 关联 high Incident 时只提升严重度和版本；
- 旧任务版本记录 `SUPERSEDED`；
- Incident、关系、决策和任务完成单事务提交，故障注入式写失败时业务记录零残留；
- 两个同服务任务并发处理最终收敛为一条 Incident、两条唯一关系和两条决策；
- 接入重放不追加关联任务，整个自动关联路径不创建 DiagnosisRun。

## 任务、API 与 Runner

- 关联任务与 Alert 投影变化同事务写入；
- `SKIP LOCKED` 批量领取、租约过期接管、五次上限和人工重试通过真实 MySQL 测试；
- 关联读取只返回任务状态、事故摘要、规则版本、原因码、白名单事实、有限候选和中文解释；
- API 不返回 `lease_owner`、异常正文、Alert 标题摘要、来源 URI或完整 facts；
- 三套 Token 隔离，目录与关联管理接口只接受人工 API Token；
- Runner 单任务失败使用固定 `correlation_processing_failed`，同批其他任务继续处理；
- FastAPI 生命周期启停 Runner，不遗留后台任务。

## 真实 HTTP 冒烟

在确认不存在的精确临时 MySQL 数据库中迁移到 `0002_service_catalog_correlation`，临时启动 Uvicorn 后执行：

1. 目录 API 登记 `payment-api/production`，返回 201；
2. 第一条 high CloudEvent 形成 `CREATED_NO_MATCH`；
3. 第二条同服务不同 alert_key 形成 `LINKED_EXACT_SERVICE`，Incident ID 相同；
4. 第一条 Alert resolved 后形成 `RECORDED_RESOLUTION`，Incident 保持 `DETECTED`；
5. 数据库最终为 1 个 Incident、0 个 DiagnosisRun、3 个不可变决策；
6. 决策与审计中的禁止实验身份、原始 URI 标记和 Secret 扫描为 0。

冒烟完成后 Uvicorn 已停止，临时数据库已精确删除。记录未保存 Token、数据库密码、完整请求或服务日志。

## 已知缺口

- 无自动取证和不可变证据快照；
- 无 DiagnosisRun 自动创建；
- 无 AI 分析和可信报告；
- 无人工合并拆分与完整事故运营；
- 无前端和生产部署制品。
