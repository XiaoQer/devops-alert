# 多源信号接入阶段验收记录

## 验收范围

本记录只验收以下已实现能力：

- Alertmanager Webhook v4 与 CloudEvents 1.0 结构化、Binary 两种输入；
- 人工报告、Alertmanager、CloudEvents 三套独立认证；
- 统一 SignalCommand、不可变 SignalEvent 和 Alert 当前状态投影；
- 来源事件幂等、内容冲突、并发收敛、乱序保护和批次事务；
- firing、更新、resolved、重开、迟到和孤立恢复的固定结果；
- 有界审计、禁止实验身份、来源 URI 脱敏和安全错误；
- MySQL 8.4 单一持久化基线和 `0001_mysql_initial` 迁移。

本记录不验收服务目录、事故候选、事故关联、Incident 自动创建、DiagnosisRun 自动创建、自动取证、Worker、AI、事故运营、前端或生产部署制品。

## 自动化验证

在本机 MySQL 8.4 的隔离随机测试数据库中执行仓库统一脚本：

```bash
II_TEST_DATABASE_URL='<仅当前终端可见的测试引导地址>' ./scripts/verify-backend.sh
```

结果：

- Ruff 检查通过；
- 65 个文件格式检查通过；
- 41 个源文件与迁移文件的 Mypy 严格检查通过；
- Alembic 升级、降级、ORM 元数据和 MySQL 类型验证通过；
- 206 项测试通过，失败 0 项；
- 覆盖率 96.82%，高于 90% 门槛。

## 真实 HTTP 冒烟

在精确命名的隔离临时 MySQL 数据库上迁移 `0001_mysql_initial`，并在 `127.0.0.1:18080` 临时启动 Uvicorn。CloudEvents 验收结果：

1. 结构化模式首次 firing 返回 202 和 `opened`；
2. Binary 模式使用同一 alert_key、不同事件 ID 更新，返回 202 和 `updated`；
3. Binary resolved 返回 202 和 `resolved`；
4. 完全重复 resolved 返回 200 和 `replayed`；
5. 四次响应始终指向同一 Alert；
6. 数据库最终包含 1 条 RESOLVED Alert 和 3 条 SignalEvent；
7. Incident 与 DiagnosisRun 数量均为 0；
8. SignalEvent facts 与审计 details 中的来源 URI、查询和未审核内容扫描结果均为 0。

Alertmanager 使用另一精确临时数据库完成真实 HTTP 验收：

1. firing 首次创建返回 202 和 `opened`；
2. 完全重复 firing 返回 200 和 `replayed`；
3. 同一 fingerprint 内容更新返回 202 和 `updated`；
4. resolved 返回 202 和 `resolved`；
5. 恢复后的迟到 firing 返回 202 和 `stale`，Alert 保持 RESOLVED；
6. 五次响应始终指向同一 Alert；
7. 数据库最终包含 1 条 RESOLVED Alert 和 4 条 SignalEvent，Incident 与 DiagnosisRun 均为 0；
8. facts 与审计中的 Alertmanager URI、生成器 URL、查询和未审核内容扫描结果均为 0。

两次冒烟结束后临时 Uvicorn 均已停止，两个精确临时数据库均已删除。本记录未保存 Token、数据库密码、完整请求体、原始来源 URI 或服务日志，也未向真实外部 Alertmanager 实例下发配置。

## 安全边界扫描

- 仓库未发现本次冒烟 Token 或带明文密码的数据库地址；
- 实验身份字段只存在于拒绝实现、边界测试和说明文档；
- 接受模型、领域对象、数据库列、成功响应和审计详情中不存在故障场景、实验 ID、注入动作或标准答案；
- 外部来源 URI、生成器 URL、查询参数和原始负载不进入持久化结果；
- 认证、容量、校验、内容冲突和数据库错误均返回固定安全契约。

## 已知缺口

- 外部事件当前只形成 SignalEvent 和 Alert，不会自动创建 Incident；
- 尚无服务目录、维护窗口、候选过滤和可解释关联；
- 尚无自动取证、证据快照、Worker 或 AI 分析；
- 尚无事故认领、状态流转、恢复验证、关闭和复盘接口；
- 尚无前端、生产镜像、Kubernetes Chart、限流器和生产级身份系统；
- 尚未完成与真实外部 Alertmanager 或 CloudEvents 生产发送方的联调。
