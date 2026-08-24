# Incident Intelligence

面向生产环境的多源告警归集、事故关联、自动取证和 AI 辅助分析平台。

本项目从零开始建设，与故障注入平台物理隔离。它不包含故障场景、注入器、Chaos 权限、实验恢复或评测标准答案。非生产故障实验只能通过真实监控和版本化公开契约验证本平台，不得把实验身份或答案写入诊断链路。

项目当前只有经过确认的产品和架构规格，已经确定 Python/FastAPI 后端与 Vue 3 前端技术基线，尚未实现业务代码。

## 项目事实

- 产品定义：`docs/product.md`
- 架构边界：`docs/architecture.md`
- 当前状态：`docs/current-state.md`
- 活跃规格：`specs/active/multi-source-incident-center.md`
- 完整设计：`docs/superpowers/specs/2026-08-24-multi-source-incident-center-design.md`
