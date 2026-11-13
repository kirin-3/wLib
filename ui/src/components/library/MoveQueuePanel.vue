<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed } from "vue";
import { IconFolderShare } from "@tabler/icons-vue";
import type { MoveStatus } from "../../services/api";
import { moveProgressText } from "../../utils/gameFiles";

const props = defineProps<{ status: MoveStatus | null }>();
const emit = defineEmits<{ cancel: [all: boolean] }>();

const current = computed(() => props.status?.current ?? null);
const percent = computed(() => {
  const item = current.value;
  return item && item.bytes_total > 0 ? Math.min(100, Math.floor((item.bytes_done / item.bytes_total) * 100)) : 0;
});
const progressText = computed(() => moveProgressText(props.status));
const waitingCount = computed(() => props.status?.waiting.length ?? 0);
</script>

<template>
  <div v-if="status?.running" class="move-panel flex flex-wrap items-center gap-3 p-3 rounded-xl" role="region" aria-label="Game moves" aria-live="polite">
    <IconFolderShare class="ui-action-icon shrink-0" style="color: var(--brand)" aria-hidden="true" />
    <div class="min-w-0 flex-1">
      <div class="flex flex-wrap items-baseline gap-x-2 text-sm">
        <strong style="color: var(--text-primary)">Moving {{ status.position || 1 }} of {{ status.total || 1 }}</strong>
        <span v-if="current" class="truncate" style="color: var(--text-secondary)">{{ current.title }}</span>
      </div>
      <div class="move-track mt-1.5" role="progressbar" :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100" :aria-label="current ? `Moving ${current.title}` : 'Moving games'">
        <div class="move-fill" :style="{ width: `${percent}%` }"></div>
      </div>
      <div class="mt-1 text-xs" style="color: var(--text-muted)">
        {{ progressText }}<span v-if="waitingCount"> · {{ waitingCount }} waiting</span>
      </div>
    </div>
    <button class="ui-action-btn move-btn px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface" :disabled="!current" @click="emit('cancel', false)">Cancel</button>
    <button v-if="waitingCount" class="ui-action-btn move-btn move-danger px-3 py-1.5 rounded-lg text-sm font-medium ui-hover-surface" @click="emit('cancel', true)">Cancel all</button>
  </div>
</template>

<style scoped>
.move-panel { background: var(--bg-surface); border: 1px solid var(--border-hover); box-shadow: var(--shadow-card); }
.move-track { height: 0.375rem; border-radius: 9999px; background: var(--bg-inset); overflow: hidden; }
.move-fill { height: 100%; background: var(--brand); transition: width 0.4s ease; }
.move-btn { color: var(--text-primary); border: 1px solid var(--border); }
.move-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.move-danger { color: var(--danger-text); border-color: var(--danger-border); }
.move-btn:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
</style>
