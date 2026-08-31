# 当前架构

## 运行组件

- 一个 Python/FastAPI 后端；
- 一个 Vue 3 前端；
- 一个 MySQL 8.4 数据库。

当前没有关联 Worker、诊断 Worker或 AI Worker。

## 数据流

```text
Alertmanager / CloudEvents
          ↓
来源认证、容量限制与安全规范化
          ↓
Alertmanager Watchdog 心跳忽略
          ↓
来源事件身份幂等检查
          ↓
同一 MySQL 事务
  ├─ 不可变 SignalEvent + 接入结果 + 回执 + 有界审计
  └─ Alert 生命周期新建或更新
          ↓
告警列表、详情和按首次接收时间统计的趋势
          ↓
用户定义 Incident 规则草稿
          ↓
有界只读历史试运行（最多扫描 10,000 条 Alert）
          ↓
发布或停用规则（当前不创建 Incident）
```

## 领域边界

### AlertSource

描述谁可以向平台发送数据，包括来源类型、可信环境、启停状态、凭据和接收统计。来源停用后可以修改环境；已有 Alert 保存的是接收时环境快照，不随来源配置变化。

### SignalEvent

描述平台首次收到的一次规范化外部告警事实。firing 与 resolved 分别保存，用于传输幂等、冲突保护、审计和回放，不直接作为用户侧告警数量。

### Alert

描述一次告警周期。周期身份为：

```text
(alert_source_id, source_alert_key, episode_started_at)
```

首期只有 `ACTIVE` 和 `RESOLVED` 两种状态。同一周期重复 firing 只更新最近时间；resolved 更新原 Alert；resolved 先到时创建不完整的已恢复 Alert，晚到 firing 只补全事实而不重新打开。

### SignalIntakeResult

保存来源事件身份、内容指纹、SignalEvent ID 和 Alert ID，用于精确重放和内容冲突保护。

### IncidentRule

描述用户定义的 Incident 识别条件，与 Incident 实例相互独立。规则状态为 `DRAFT`、`PUBLISHED` 或 `DISABLED`；发布版本不可直接修改，只能复制为新草稿。环境是强制隔离边界，来源和服务是可选过滤条件，分组只支持同一服务或同一实体，全部条件使用 AND。

### IncidentRuleDryRun

保存某一规则版本在指定历史范围上的只读评估结果，包括扫描数量、命中窗口、示例 Alert 和是否达到安全上限。只有当前版本存在成功且未截断的试运行结果时才允许发布。试运行不写入 Incident。

## 事务与并发

- SignalEvent、Alert 投影、接入结果和审计在同一事务提交或回滚；
- Alert 周期唯一约束是并发收敛的最终防线；
- 更新使用行锁和乐观版本条件；
- 精确重放返回原 SignalEvent ID 与 Alert ID，不重复更新 Alert 版本；
- 历史回填按稳定顺序和有界批次执行，可重复运行。
- 规则修改、发布、停用、复制和删除草稿使用版本号与幂等键，避免并发页面互相覆盖；
- 规则试运行最多读取 10,001 条记录以识别截断，最多返回 100 个命中和每个命中 10 个示例 Alert。

## API 边界

保留：

- `/api/v1/alert-sources`
- `/api/v1/intake/alertmanager`
- `/api/v1/intake/cloudevents`
- `/api/v1/alerts`
- `/api/v1/alerts/timeseries`
- `/api/v1/incident-rules`
- `/health/live`
- `/health/ready`

`/api/v1/alerts` 与详情按 Alert ID 读取生命周期；趋势按 `first_received_at` 统计新建 Alert。`/api/v1/incident-rules` 提供规则管理、历史试运行、发布、停用和复制；它不创建 Incident。旧告警汇总、告警组、目录、关联、事故、人工事故报告和诊断 API 不注册。

## 历史兼容

仓库保留已有 Alembic 历史迁移，避免破坏已安装数据库。旧复杂业务表不再由运行时读写；新物理表使用 `alert_lifecycles`，避免复用带旧事故外键的历史 `alerts` 表。
