# 后端阶段 1 验收记录

## 验收范围

本记录只验收以下已实现能力：

- FastAPI 工程、存活与数据库就绪检查；
- SignalEvent、Alert、Incident、DiagnosisRun 四个独立模型和三套状态策略；
- PostgreSQL 初始迁移 `0001_initial_domain`；
- 原子且幂等的人工事故报告服务；
- 认证、有界输入和稳定错误契约的人工报告 API；
- 四类领域资源的认证独立读取 API；
- 幂等键、追加式审计和禁止实验身份边界。

本记录不验收 Alertmanager、CloudEvents、服务目录、事故关联、自动取证、Worker、AI 分析、事故运营写接口、恢复闭环、前端或生产部署制品。

## 自动化验证

在由仓库 `compose.yaml` 启动的独立 PostgreSQL 16 环境中执行：

```bash
II_TEST_DATABASE_URL='<仅当前终端可见的专用测试库地址>' scripts/verify-backend.sh
```

结果：

- Ruff 检查通过；
- 格式检查通过；
- Mypy 严格类型检查通过；
- Alembic 升级、降级和 ORM 元数据一致性检查通过；
- 91 项测试通过，失败 0 项；
- 覆盖率 95.21%，高于 90% 门槛。

## 真实 HTTP 冒烟

使用只存在于临时终端环境的随机数据库密码和 API Token，完成数据库迁移并在 `127.0.0.1:18080` 启动 API。验收结果：

1. 存活检查成功；
2. 首次提交人工报告返回 201；
3. 使用同一幂等键和相同内容重放返回 200；
4. 两次响应的 SignalEvent、Alert、Incident、DiagnosisRun ID 全部一致；
5. 四个独立读取接口均返回 200，成功 4/4；
6. 冒烟结束后 API 进程已停止。

验收记录未保存 Token、完整请求体、数据库密码、带凭据连接地址或原始服务日志。

## 安全边界扫描

仓库扫描结果：

- 常见 API Key、Bearer Token、云访问密钥和私钥形态无命中；
- 实验身份和注入相关词仅存在于拒绝实现、边界测试和说明文档；
- 接受模型、数据库列、API 响应和业务持久化路径中未发现 `scenario_id`、`scenario_version`、`experiment_id`、注入动作或标准答案字段。

## 已知缺口

- 当前唯一业务输入是人工报告，尚未接入 Alertmanager 或 CloudEvents；
- 一个报告当前确定性生成一个 Alert 和一个 Incident，尚未实现去重投影、服务目录或事故关联；
- DiagnosisRun 仅进入 `QUEUED`，尚无自动取证、证据快照、Worker 或 AI 分析；
- 尚无事故认领、状态流转、恢复验证、关闭或复盘接口；
- 尚无前端、生产镜像、Kubernetes Chart、限流器和生产级身份系统。
