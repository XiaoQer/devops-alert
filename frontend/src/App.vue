<script setup>
import { computed, ref } from "vue";
import { PhBell, PhGear, PhListChecks, PhShieldCheck, PhSiren } from "@phosphor-icons/vue";

import AlertCenter from "./components/AlertCenter.vue";
import AlertSourceManager from "./components/AlertSourceManager.vue";
import IncidentRuleCenter from "./components/IncidentRuleCenter.vue";
import IncidentCenter from "./components/IncidentCenter.vue";

const activePage = ref("alerts");
const pages = [
  { id: "alerts", label: "告警中心", icon: PhBell },
  { id: "incidents", label: "Incident 中心", icon: PhSiren },
  { id: "incident-rules", label: "Incident 规则", icon: PhListChecks },
  { id: "alert-sources", label: "接入源管理", icon: PhGear },
];
const title = computed(() => pages.find((page) => page.id === activePage.value)?.label);
</script>

<template>
  <div class="app-shell minimal-shell">
    <aside class="sidebar" aria-label="主导航">
      <div class="brand"><PhShieldCheck :size="23" weight="fill" /><span>告警中心</span></div>
      <nav class="nav-list">
        <button v-for="page in pages" :key="page.id" type="button" :data-testid="`nav-${page.id}`" :aria-current="activePage === page.id ? 'page' : undefined" :class="['nav-item', { active: activePage === page.id }]" @click="activePage = page.id">
          <component :is="page.icon" :size="21" /><span>{{ page.label }}</span>
        </button>
      </nav>
    </aside>
    <main class="main-surface">
      <header class="topbar minimal-topbar"><div class="title-group"><h1>{{ title }}</h1></div></header>
      <AlertCenter v-if="activePage === 'alerts'" />
      <IncidentCenter v-else-if="activePage === 'incidents'" />
      <IncidentRuleCenter v-else-if="activePage === 'incident-rules'" />
      <AlertSourceManager v-else />
    </main>
  </div>
</template>
