<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- The setup summary at the top of Settings > Running games (Linux): what
     Windows games need now, and how many optional tools are installed. The
     next action jumps to the group that does it. The statuses come from the
     page and its groups; nothing here calls the backend. -->
<script setup lang="ts">
import { IconAlertTriangle, IconCircleCheck, IconCircleDashed, IconLoader2 } from "@tabler/icons-vue";
import type { OptionalToolsSummary, ReadinessTarget, WindowsReadiness } from "../../utils/settingsReadiness";

defineProps<{
  windows: WindowsReadiness;
  optional: OptionalToolsSummary;
}>();
const emit = defineEmits<{ go: [target: ReadinessTarget] }>();
</script>

<template>
  <div class="readiness" data-testid="settings-readiness">
    <h4 id="settings-readiness-heading" class="readiness-title">Setup</h4>
    <div role="status" aria-labelledby="settings-readiness-heading">
    <ul class="readiness-list">
      <li class="readiness-item">
        <IconCircleCheck class="readiness-icon readiness-icon--ok" aria-hidden="true" />
        <span>Linux, Ren'Py and HTML games: nothing to set up.</span>
      </li>
      <li class="readiness-item" :data-readiness-windows="windows.tone">
        <IconLoader2 v-if="windows.tone === 'checking'" class="readiness-icon animate-spin" aria-hidden="true" />
        <IconCircleCheck v-else-if="windows.tone === 'ready'" class="readiness-icon readiness-icon--ok" aria-hidden="true" />
        <IconAlertTriangle v-else class="readiness-icon readiness-icon--warn" aria-hidden="true" />
        <span class="min-w-0">
          {{ windows.text }}
          <button v-if="windows.action" type="button" class="ui-card-btn ui-card-btn--link readiness-action" data-readiness-action
            @click="emit('go', windows.action.target)">{{ windows.action.label }}</button>
        </span>
      </li>
      <li class="readiness-item" data-readiness-optional>
        <IconLoader2 v-if="optional.tone === 'checking'" class="readiness-icon animate-spin" aria-hidden="true" />
        <IconCircleDashed v-else class="readiness-icon" aria-hidden="true" />
        <span class="min-w-0">
          {{ optional.text }}
          <span v-if="optional.missing.length" class="readiness-muted">Not installed: {{ optional.missing.join(", ") }}. Games run without them.</span>
        </span>
      </li>
    </ul>
    </div>
  </div>
</template>

<style scoped>
.readiness-title {
  font-size: 0.875rem;
  line-height: 1.25rem;
  font-weight: 500;
  color: var(--text-primary);
}
.readiness-list { margin-top: 0.5rem; display: grid; gap: 0.375rem; }
.readiness-item {
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  font-size: 0.8125rem;
  line-height: 1.25rem;
  color: var(--text-secondary);
}
.readiness-icon { width: 1rem; height: 1rem; margin-top: 0.125rem; flex-shrink: 0; color: var(--text-muted); }
.readiness-icon--ok { color: var(--success-text); }
.readiness-icon--warn { color: var(--warning-text); }
.readiness-muted { color: var(--text-muted); }
.readiness-action { margin-left: 0.375rem; vertical-align: baseline; font-size: inherit; }
</style>
