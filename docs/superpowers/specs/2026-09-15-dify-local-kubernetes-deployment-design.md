# 本地 Kubernetes Dify 部署与 Incident 平台联调设计

## 目标

在当前 Docker Desktop 单节点 Kubernetes 集群中部署一个独立、可持久化且可回收的 Dify Community Edition 实例，并让 Incident 平台能够以受控方式调用其中一个 Workflow。

## 范围与非目标

范围：Dify 基础组件、独立命名空间和 PVC、`dify.localhost` Ingress、平台到 Dify 的固定 Workflow 配置、Dify 到平台只读工具接口的网络连通性，以及一次人工发起的诊断沙箱验证。

非目标：生产高可用、外部对象存储、知识库/RAG、自动修复、Dify 直接访问监控/数据库/飞书/Kubernetes，或把任何 Dify/模型 Secret 提交到仓库。

## 已确认环境

- Kubernetes context：`docker-desktop`，单节点；
- ingress-nginx 已运行，`localhost` 为其 LoadBalancer 地址；
- 默认 StorageClass 为 `hostpath`，只提供 RWO；
- 当前集群无 ResourceQuota；
- 现有业务命名空间不得修改；
- Dify 通过社区 Helm Chart `BorisPolonsky/dify-helm` 部署。Dify 官方仓库把 Kubernetes 方案列为社区 Helm/YAML 部署，不是官方一键 Chart。

## 架构与数据流

```text
浏览器 → http://dify.localhost → ingress-nginx → Dify proxy → Dify Web/API
                                              ├→ PostgreSQL / Redis / Weaviate
                                              ├→ Worker / Sandbox / Plugin Daemon
                                              └→ 仅允许的 HTTP 工具请求
                                                        ↓
                                  http://host.docker.internal:8000
                                                        ↓
                         Incident 平台的冻结快照/证据详情接口

Incident 平台 Worker → http://dify.localhost/v1/workflows/run → 固定 Dify Workflow
```

平台调用使用 Dify 应用 API Key（绑定单一 Workflow），只传入 `diagnosis_run_id`、五分钟短期能力令牌与 `diagnosis-report.v1`。Dify 获取事实时携带短期能力令牌，只能读取该运行被冻结的快照和证据。平台重新校验输出后才发布报告。

## 部署决策

1. Helm release 名为 `incident-dify`，命名空间为 `dify-system`。
2. 固定 Chart `0.39.0-rc1` / Dify `1.17.0`，不能使用浮动 `latest` 标签。
3. 创建单独 Kubernetes Secret 保存 Dify 内部签名密钥；不把其值写入 values 文件、Git、日志或 API。
4. 启用 Chart 内 PostgreSQL、Redis、Weaviate 和持久卷。因 `hostpath` 不支持 RWX，所有 Chart 的共享持久卷改为 `ReadWriteOnce`，并维持各工作负载单副本、同一节点调度。
5. 通过 Ingress 绑定 `dify.localhost`，不改写现有 Ingress。
6. Dify 首次管理员初始化、模型供应商 API Key 和 Workflow 应用 API Key 通过 Dify 控制台录入；这些敏感信息不写入平台仓库。
7. 平台的 Dify 基地址设置为 `http://dify.localhost`。若本机后端无法解析该域名，改为 ingress 的本地地址，但不改变平台的固定 Dify API 路径。

## 失败与回退

- Helm 渲染和 `--dry-run` 必须先通过；
- 任意 Dify Pod 未就绪时，不设置平台 `II_DIFY_ENABLED=true`；
- 失败时仅卸载 `incident-dify` release，保留命名空间和 PVC 以便取证，除非用户明确要求删除数据；
- 平台的本地演示开关与真实 Dify 开关互斥，真实 Dify 配置不完整时诊断任务安全入队，不会调用外部服务。

## 验收

- `dify-system` 内所有已启用工作负载 Ready，PVC 全部 Bound；
- `http://dify.localhost/install` 可完成 Dify 初始化；
- Dify API 可经 Ingress 返回健康响应，且 Platform `/health` 显示 Dify 配置完整但不泄露 Secret；
- 平台人工创建一次 DiagnosisRun 后，Dify 收到固定输入；输出无效时进入人工复核、有效且引用正确时发布报告；
- 不修改其它命名空间和既有 Ingress；Dify 不能读取任何平台长期凭据、数据库、监控或 Kubernetes API。
