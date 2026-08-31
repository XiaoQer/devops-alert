# Incident 规则创建向导设计验收

- source visual truth path: `/Users/shaoqian.li/.codex/generated_images/01a017a7-cb3c-70d2-a5a1-5b3c1030ff47/exec-00dd3b7d-a33e-407e-a5b4-18a2accbb880.png`
- implementation screenshot path: `/Users/shaoqian.li/Documents/incident-intelligence/frontend/incident-rule-step3-final.png`
- combined comparison path: `/Users/shaoqian.li/Documents/incident-intelligence/frontend/incident-rule-design-comparison.png`
- viewport: 1536 × 1024 CSS px
- source pixels: 1487 × 1058；implementation pixels: 1536 × 1024；均按单倍截图比较，未用密度差异判断样式问题
- state: 深色桌面端、四步向导第 3 步、5 分钟窗口、服务聚合、两个 AND 条件、草稿已保存

## Findings

最终对比中没有仍需处理的 P0、P1 或 P2 问题。

- 字体与层级：沿用项目现有系统字体；标题、说明、字段和辅助文字层级与参考图一致，未出现截断或异常换行。
- 间距与布局：保留左侧平台导航、步骤导航、主配置区和右侧中文说明的三栏结构；底部动作区固定且不遮挡内容。
- 颜色与视觉变量：使用现有深色背景、蓝色主操作、低对比边框与状态色，语义和参考图一致。
- 图像与图标：页面没有业务图片；图标均来自项目既有 Phosphor 图标库，没有内联 SVG、占位图或字符图标。
- 文案与内容：条件、窗口、聚合边界和试运行边界均以中文表达；`Alertname` 保留为领域字段名。

可接受差异：参考图顶部含时间和用户入口，当前项目壳层没有这两项；参考图把窗口做成预设下拉框，当前规格允许 1–60 分钟精确输入；参考图展示比较运算符，当前后端条件类型已经固定包含 `不少于/至少为` 语义，因此不再增加无效控件。这些差异不影响任务完成，也不会造成规则含义不明确。

## Comparison history

1. 首次浏览器走查发现 P1：新增条件只能按固定顺序出现，无法直接选择条件类型。已把每一项改为结构化类型选择器，并禁用其他行已经使用的类型；随后补充自动化测试并在真实页面选择“最高告警级别至少为”通过。
2. 同一轮走查发现草稿保存失败：Vue 代理对象无法被原生结构复制。已改用安全 JSON 数据复制，草稿保存、历史试运行和后端中文摘要均在浏览器中通过。
3. 修复后重新按相同桌面视口捕获全屏和“条件与摘要”聚焦区域；没有新的 P0/P1/P2 发现。

## Primary interactions tested

- 从真实空列表进入创建向导。
- 完成名称、用途、环境、服务和聚合方式。
- 新增两个条件、切换条件类型并验证重复类型不可选。
- 保存真实草稿并读取后端生成的中文说明。
- 进入第 4 步，对最近 6 小时真实 Alert 执行只读试运行。
- 验证没有成功试运行时禁止发布，成功且未截断后才允许发布。
- 浏览器控制台无 error 或 warning。

## Follow-up polish

- P3：后续若统一升级平台壳层，可再补充顶部用户入口；它不属于本次 Incident 规则能力范围。

final result: passed
