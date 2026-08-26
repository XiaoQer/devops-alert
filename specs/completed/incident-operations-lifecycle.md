# 事故处置闭环

## 状态

已完成并归档，2026-08-26 通过自动化与真实浏览器验收。

## 背景与目标

事故中心已经能够从真实 MySQL 读取事故、关联告警、关联解释和事实时间线，并支持单一认证主体认领事故。但事故创建以后还不能记录处置过程、推进状态、解除认领、解决、重新打开或关闭，因而无法形成完整的人工事故运营闭环。

本阶段建立从“待处置”到“已关闭”的可信人工处置流程。每次操作必须使用服务端认证主体，在单个 MySQL 事务中更新 Incident 当前状态、追加不可变业务活动、追加有界安全审计并保存幂等结果。页面以中文业务操作呈现状态、下一步、快速记录和处置时间线。

## 范围

### Incident 当前状态

在现有 `incidents` 表上增加：

- `state_changed_at`：最近一次状态变化的 UTC 时间；
- `resolved_at`：当前处于已解决或已关闭状态时的解决时间，重新打开时清空；
- `closed_at`：已关闭事故的最终关闭时间；
- 延续现有 `assignee`、`claimed_at` 和 `version`。

约束：

- `resolved_at` 仅在 `RESOLVED`、`CLOSED` 时非空；
- `closed_at` 仅在 `CLOSED` 时非空，且 `CLOSED` 必须同时存在 `resolved_at`；
- 负责人和认领时间继续保持同时为空或同时非空；
- 所有业务写操作，包括添加处置记录，均递增 Incident `version`。

迁移对历史记录执行确定性回填：`state_changed_at` 使用已有 `created_at`；历史 `RESOLVED` 的 `resolved_at` 使用 `created_at`；历史 `CLOSED` 的 `resolved_at` 和 `closed_at` 均使用 `created_at`。这些回填只用于满足当前状态约束，不生成虚构业务活动。

### 不可变业务活动

新增 `incident_activities`，每条记录只属于一个 Incident，创建后不得修改或删除。字段包括：

- 活动 ID、Incident ID、固定活动类型；
- 认证操作主体、UTC 创建时间、操作完成后的 Incident 版本；
- 可空的变化前状态、变化后状态；
- 可空的记录分类、业务说明、解决分类、采取措施和根因说明。

活动类型固定为：

- `INCIDENT_CLAIMED`、`INCIDENT_RELEASED`；
- `STATE_TRANSITIONED`；
- `NOTE_ADDED`；
- `INCIDENT_RESOLVED`、`INCIDENT_REOPENED`、`INCIDENT_CLOSED`。

记录分类固定为：当前发现、已执行操作、操作结果、后续计划、普通备注。解决分类固定为：故障恢复、误报、重复告警、无需处理、其他。

活动正文均有明确长度限制：状态说明最多 1000 字符；处置记录、解决说明、重新打开原因和关闭说明最多 2000 字符；采取措施和可选根因各最多 4000 字符。活动不保存请求头、Token、原始请求正文、监控查询、实验身份或任意扩展 JSON。

### 写操作幂等

新增 `incident_operations` 保存已完成写操作的安全重放结果：

- 作用域、`Idempotency-Key` 的 SHA-256 摘要、规范化请求指纹；
- Incident ID、固定操作类型、结果版本、活动 ID、完成时间；
- 认领等没有额外正文的操作也必须形成稳定指纹。

相同作用域、相同键和相同请求返回首次成功结果，不重复更新 Incident、不重复追加活动或审计；相同键对应不同请求返回稳定冲突。表中不保存原始幂等键或业务正文。

## 状态机

允许的普通状态转换：

- `DETECTED` → `TRIAGING`、`INVESTIGATING`；
- `TRIAGING` → `INVESTIGATING`、`MITIGATING`；
- `INVESTIGATING` → `MITIGATING`、`MONITORING_RECOVERY`；
- `MITIGATING` → `INVESTIGATING`、`MONITORING_RECOVERY`；
- `MONITORING_RECOVERY` → `INVESTIGATING`、`MITIGATING`。

解决不使用普通转换接口；任意未关闭的活动状态都可通过解决操作进入 `RESOLVED`。解决必须提供解决分类、解决说明和采取措施，根因允许为空。

`RESOLVED` 只能：

- 通过重新打开操作进入 `INVESTIGATING`，必须填写原因并清空当前 `resolved_at`；
- 通过关闭操作进入 `CLOSED`，必须填写关闭说明。

`CLOSED` 是第一版终态，禁止认领、解除认领、状态变化、添加记录、重新打开或重复关闭。历史解决、重新打开和再次解决活动全部保留。

普通状态推进、解决、重新打开和关闭不强制事故必须先被当前主体认领，避免负责人不可用时阻塞处置，但每次操作都记录真实认证主体。

## 后端接口

所有写接口只接受人工控制面认证，必须提供长度 1–256 的 `Idempotency-Key`，请求体必须提供当前 `expected_version`：

- `POST /api/v1/incidents/{id}/claim`；
- `POST /api/v1/incidents/{id}/release`；
- `POST /api/v1/incidents/{id}/transitions`；
- `POST /api/v1/incidents/{id}/notes`；
- `POST /api/v1/incidents/{id}/resolve`；
- `POST /api/v1/incidents/{id}/reopen`；
- `POST /api/v1/incidents/{id}/close`。

认领由认证主体成为负责人；解除认领仅允许当前负责人执行。不接受浏览器自报负责人或操作人。可信转派需要服务端用户目录或生产身份系统，不在本阶段实现。

统一成功响应返回：Incident ID、固定操作类型、最新状态、当前负责人、新版本、活动 ID 和操作时间。前端成功后重新读取 overview，不在浏览器内推测最终状态。

每次写入的事务顺序为：

1. 检查幂等记录并验证指纹；
2. 对 Incident 执行 `SELECT FOR UPDATE`；
3. 比较 `expected_version`；
4. 校验状态、负责人和操作内容；
5. 更新 Incident 当前状态并递增版本；
6. 追加业务活动；
7. 追加仅含固定原因码和父资源 ID 的安全审计；
8. 保存幂等结果并提交。

任一步失败均完整回滚。并发写入只有一个版本成功，其余返回版本冲突。

稳定错误包括：`incident_not_found`、`incident_version_conflict`、`invalid_incident_transition`、`incident_already_claimed`、`incident_not_claimed`、`incident_operation_conflict`、`incident_closed` 和 `persistence_unavailable`。错误响应不回显请求值、数据库异常、Token 或堆栈。

### Overview 扩展

现有 `GET /api/v1/incidents/{id}/overview` 增加：

- `state_changed_at`、`resolved_at`、`closed_at`；
- 最多 200 条按时间稳定排序的业务活动和 `activities_truncated`；
- 根据事故状态、当前负责人和认证主体计算的 `allowed_actions` 和 `allowed_transitions`；
- 根据固定状态机计算的 `primary_action`，包含主操作类型和可选目标状态，不代表 AI 建议。

业务活动与现有事故创建、告警关联事实统一转换为用户时间线，但底层模型保持独立。Overview 查询数量必须有界，不随活动数量产生 N+1 查询。

## 前端交互

事故详情页采用已确认布局：

- 标题区域展示严重度、当前状态、服务、团队和负责人；
- 标题下方展示七阶段进度条、最近更新时间、后端返回的建议下一步和主推进按钮；
- 主区域左侧默认显示处置时间线，可切换关联告警和事故信息；
- 右侧提供快速记录，支持五种固定记录分类；
- 顶部提供添加处置记录、认领或解除认领、解决事故等当前允许操作；
- 解决弹窗要求解决分类、解决说明和采取措施，根因明确标记为选填；
- 已解决页面突出“重新打开”和“关闭事故”；已关闭页面全部操作只读；
- 状态与按钮完全依据后端 `allowed_actions`、`allowed_transitions` 和 `primary_action`，前端不得自行扩大权限。

写操作期间按钮禁用。成功后重新读取真实列表和详情；版本冲突提示“事故已被其他操作更新”，并提供刷新；数据库或 API 不可用时保留当前只读内容并明确提示本次写入未完成，不做乐观假成功。

## 非目标

- 操作员目录、转派、多人会签、RBAC、SSO 或值班表；
- SLA、通知升级、自动关闭、维护窗口或自动状态推进；
- 修改事故标题、严重度、服务或环境；
- 删除或编辑处置记录；
- AI 根因分析、自动取证、证据快照或可信报告；
- 任何故障场景、实验身份、注入动作或故障注入平台内部数据。

## 安全与恢复

- 浏览器不提供或持久化共享 Bearer Token，继续通过同源控制器或本地开发代理转换认证；
- 所有操作者来自服务端认证上下文，不能从请求正文指定；
- 业务活动面向用户，安全审计面向追溯，两者独立追加且不得相互代替；
- 所有枚举、正文、分页、活动数量和幂等键都有容量边界；
- 数据库错误不会产生部分状态、孤立活动、孤立审计或虚假成功响应；
- 迁移必须支持升级、降级和 ORM 元数据一致性检查；
- AI、数据源和关联 Runner 不可用不得阻塞人工事故处置。

## 验收条件

- 迁移可升级、降级并与 ORM 元数据一致，时间和状态检查约束由真实 MySQL 验证；
- 每条允许与禁止状态转换都有纯领域测试，`CLOSED` 终态不可修改；
- 认领、解除认领、推进、添加记录、解决、重新打开和关闭均通过真实 MySQL API 测试；
- 每种操作验证首次成功、精确重放、键冲突、版本冲突、并发收敛、非法状态和事务回滚；
- 解决必填项、可选根因、误报分类和重新打开清理当前解决时间均有测试；
- 活动时间线不可修改、有界、稳定排序，不返回任意 JSON、Secret 或实验身份；
- 非人工 Token、未认证请求和浏览器自报主体均被拒绝；
- 前端测试覆盖阶段展示、允许操作、快速记录、解决、重新打开、关闭、刷新、错误和版本冲突；
- 浏览器通过真实后端与真实 MySQL 完成一次“认领 → 调查 → 添加记录 → 缓解 → 恢复观察 → 解决 → 重新打开 → 再次解决 → 关闭”链路；
- Ruff、格式、Mypy、全部 Pytest、Vitest、Vite 构建、托管测试和禁止字段扫描通过。

## 验证证据

- 真实 MySQL 8.4 统一后端验收：Ruff、格式、Mypy、362 项 Pytest 全部通过，覆盖率 93.74%；临时测试数据库与卷已清理。
- 前端验收：32 项 Vitest、Vite 生产构建、4 项 Sites Worker 测试全部通过；构建产物禁止字段与凭据标识扫描 0 命中。
- 真实入口：使用服务目录和 CloudEvents 正式 API 创建本地事故，未直接写业务表。
- 浏览器旅程：完成认领、分诊、调查、处置记录、缓解、恢复观察、首次解决（根因留空）、重新打开、再次解决和关闭；每一步均重新读取后端事实。
- 最终数据库事实：Incident 状态 `CLOSED`、版本 11、10 条不可变活动、10 条幂等操作记录；认领 1、状态推进 4、记录 1、解决 2、重新打开 1、关闭 1 均有对应单条审计。
- 失败恢复：两个真实浏览器窗口触发旧版本 409 并刷新；后端中断时保留只读事故和待提交正文，恢复后使用原幂等键重试且只追加一条活动。
- 浏览器视觉检查：1280×720 下阶段栏、事实区和处置时间线可读，已关闭事故无写操作；控制台 error/warn 为 0。
