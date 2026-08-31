# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## 已确认的产品与视觉偏好

- 前端技术路线使用 Vue 3，不切换为 React。
- 中文、非技术化表达优先；英文状态码、规则码、UUID 和任务租约默认隐藏。
- 事故中心必须一眼展示事故、影响、关联告警和中文关联原因。
- 正文保持 14–16px，标题克制，避免大字体、卡片套卡片和过度留白。
- 深色石墨控制台、蓝色主操作，红橙只表示风险，绿色只表示恢复。
- 当前视觉真值为用户选择的第 1 张事故中心分栏稿。
- Incident 规则使用用户选择的第 1 张分步向导稿：左侧步骤、中央表单、右侧中文摘要、底部操作；不使用模板、演示规则或原始 JSON 编辑器。
