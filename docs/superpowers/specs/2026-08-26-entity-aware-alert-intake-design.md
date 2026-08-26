# 宽容的 Alertmanager 告警接入设计

## 设计结论

平台采用“协议严格、业务宽容”的接入策略。Alertmanager v4 的结构、状态、时间、批次和安全边界继续校验；`service` 等业务标签全部视为可选。字段缺失不会触发外部补全流程，也不会访问 Kubernetes。

## 接入边界

必须满足：请求认证、JSON 可解析、Webhook 版本为 4、至少一条告警、状态合法、时间可解析、单批不超过 100 条、请求体不超过 256 KiB。告警必须具有 Alertmanager 提供的稳定 fingerprint；触发和恢复使用它维护同一 Alert 投影。

允许缺少：service、environment、severity、summary、description、symptom 以及全部资源标签。未知顶层和单条扩展字段使用 `extra=ignore`，但禁止身份扫描在忽略前覆盖完整已解析输入。

## 标准化规则

- 标题：`annotations.summary` → `labels.alertname` → `未命名告警`；
- 摘要：`annotations.description` → 标题；
- 服务：显式 `labels.service` 或合并后的 `commonLabels.service`，否则 null；
- 环境：显式 environment，否则 unknown；
- 严重度：现有别名映射，否则 medium，并记录 `severity_defaulted`；
- 标签合并：先 commonLabels，后单条 labels，单条优先；
- 事实：只保留既有白名单和有界值；未知扩展不保存。

## 实体摘要

实体摘要只描述告警已经提供的事实，不承担 service 解析：

1. service → SERVICE；
2. workload → WORKLOAD；
3. namespace + pod → POD；
4. cluster + node 或 node → NODE；
5. namespace + job 或 job → JOB；
6. instance → INSTANCE；
7. cluster → CLUSTER；
8. 其他 → UNKNOWN。

实体键使用版本化 SHA-256 计算，显示名使用真实有界值。Pod 名称不会被截取或正则推导成服务。

## 持久化与归组

SignalEvent、Alert、AlertGroup 的 service 改为可空，并增加实体类型、键和显示名。旧数据以真实 service 回填 SERVICE 实体。AlertGroup 的相似性键改为 entity_key + environment + symptom + 时间窗口，因此无服务 Pod 或 Node 告警仍能完成风暴收敛。

Incident 暂时保持 service 必填。服务为空的告警组保存 `SKIPPED_SERVICE_MISSING` 决策，不创建 Incident；服务存在时维持现有服务目录和事故关联规则。

## API 与前端

告警和告警组响应把 service 声明为可空，增加主要实体类型和显示名，不返回内部实体摘要算法。前端服务为空时显示“服务未提供”；有实体时显示如 `Pod · devops-platform/demo-0`，无实体时显示“对象未提供”。不出现“正在解析”“解析失败”或 Kubernetes 相关文案。

## 测试

先写失败测试覆盖无 service、混合批次、公共标签合并、未知扩展字段、UNKNOWN 实体、数据库空服务、实体归组、事故跳过和前端中文展示；再运行现有完整回归，确保协议安全、幂等、恢复和有服务告警行为不变。
