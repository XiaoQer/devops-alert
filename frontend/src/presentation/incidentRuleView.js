const stateLabels = { DRAFT: "草稿", PUBLISHED: "已发布", DISABLED: "已停用" };
const stateTones = { DRAFT: "draft", PUBLISHED: "published", DISABLED: "disabled" };
const groupLabels = { SERVICE: "同一服务", ENTITY: "同一实体" };

export function toIncidentRuleListItem(rule) {
  return {
    ...rule,
    stateLabel: stateLabels[rule.state] ?? rule.state,
    stateTone: stateTones[rule.state] ?? "draft",
    groupLabel: groupLabels[rule.config?.group_by] ?? "未设置",
    updatedAt: formatDateTime(rule.updated_at),
  };
}

export function createEmptyRuleDraft() {
  return {
    name: "",
    description: "",
    config: {
      environment: "",
      alert_source_ids: [],
      services: [],
      group_by: "SERVICE",
      window_minutes: 5,
      conditions: [],
    },
  };
}

export function ruleToDraft(rule) {
  return {
    name: rule.name,
    description: rule.description,
    config: structuredClone(rule.config),
  };
}

function formatDateTime(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  }).format(new Date(value));
}
