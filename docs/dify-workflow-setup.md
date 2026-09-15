# Dify Workflow 最小接入说明

本平台只把 Dify 当作一次受控诊断的执行器。Dify 不保存 Incident 事实、不连接 MySQL 或监控系统，也不持有平台的长期 API Token。

## 1. 创建 Workflow 应用

在 Dify 新建一个 Workflow 应用，并创建三个开始节点变量：

| 变量 | 类型 | 用途 |
| --- | --- | --- |
| `diagnosis_run_id` | 短文本 | 当前平台诊断运行的唯一 ID |
| `capability_token` | 短文本 | 当前运行专用、5 分钟过期的只读能力凭证 |
| `output_contract_version` | 短文本 | 当前固定为 `diagnosis-report.v1` |

为该应用生成 API Key。这个 Key 绑定该 Workflow；平台不会接受请求动态选择其它 Workflow。

## 2. 配置两个受控 HTTP 读取节点

在 Dify 中只能配置以下两个 HTTP 请求，均使用 `Authorization: Bearer {{ capability_token }}`。平台必须可被 Dify 网络访问；Dify 云端不能访问本机 `127.0.0.1`。

| 节点 | 方法和地址 | 用途 |
| --- | --- | --- |
| 诊断快照 | `GET {平台地址}/api/v1/diagnosis-runs/{{ diagnosis_run_id }}/tools/snapshot` | 获取冻结的 Incident、告警和证据引用摘要 |
| 证据详情 | `GET {平台地址}/api/v1/diagnosis-runs/{{ diagnosis_run_id }}/tools/evidence/{{ evidence_item_id }}` | 获取快照中一项证据的受控明细 |

不得在 Workflow 中添加数据库、Prometheus、Elasticsearch、SkyWalking、飞书、Kubernetes、任意 URL 请求、Shell 或写操作节点。知识检索节点当前也尚未开放。

## 3. 提示词和输出

Workflow 先读取诊断快照；只在快照存在的 `evidence_item_id` 中选择需要核对的一项，再读取其详情。模型只能基于这些结果生成下列 JSON，不能输出思维过程、命令或自动化操作：

```json
{
  "diagnosis_report": {
    "confirmed_facts": [
      {"text": "可由证据直接确认的事实", "reference_ids": ["evitem_..."]}
    ],
    "hypotheses": [],
    "references": [
      {"kind": "EVIDENCE", "target_id": "evitem_...", "content_hash": "..."}
    ],
    "unknowns": ["当前尚不能确认的内容"],
    "suggested_human_actions": ["由人工执行的下一步核对建议"]
  }
}
```

`confirmed_facts` 必须能被相应证据直接支持；不确定的判断放进 `hypotheses` 或 `unknowns`。平台会重新校验引用和事实，不合格的输出不会成为可信报告。

## 4. 启用平台配置

将以下配置仅放入后端运行环境，再重启后端：

```text
II_DIFY_ENABLED=true
II_DIFY_BASE_URL=https://你的-dify-地址
II_DIFY_API_KEY=应用生成的-api-key
II_DIFY_WORKFLOW_LABEL=incident-diagnosis.v1
II_DIAGNOSIS_CAPABILITY_SECRET=随机且足够长的签名密钥
```

不要把这些值写入前端、仓库、Dify 提示词、事故快照或截图。启动后可访问 `GET /health`：`dify.configured` 为 `true` 表示平台已具备启动真实 Worker 的本地配置，但仍需从 Incident 页面手动发起一次诊断做沙箱验证。
