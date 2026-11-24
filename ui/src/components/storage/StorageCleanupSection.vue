<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { IconLoader2, IconSparkles } from "@tabler/icons-vue";
import type { StorageCleanupAction, StorageCleanupActionId } from "../../services/api";
import { formatStorageBytes, isCleanupActionAvailable } from "../../utils/storage";

defineProps<{
  actions: StorageCleanupAction[];
  busyAction: StorageCleanupActionId | "";
  resultMessage: string;
  errorMessage: string;
}>();

const emit = defineEmits<{
  (event: "clean", action: StorageCleanupAction): void;
}>();
</script>

<template>
  <section class="storage-card rounded-xl p-6" aria-labelledby="storage-cleanup-heading">
    <div class="mb-4">
      <h3 id="storage-cleanup-heading" class="ui-section-heading text-lg font-semibold">
        <IconSparkles class="ui-section-icon" />
        <span>Free up space</span>
      </h3>
      <p class="text-xs mt-1" style="color: var(--text-muted)">
        Each action checks its targets again and asks for confirmation before deleting anything.
      </p>
    </div>

    <div
      v-if="resultMessage"
      role="status"
      class="cleanup-banner cleanup-banner--ok rounded-lg px-4 py-3 text-sm mb-4"
    >
      {{ resultMessage }}
    </div>
    <div
      v-if="errorMessage"
      role="alert"
      class="cleanup-banner cleanup-banner--error rounded-lg px-4 py-3 text-sm mb-4"
    >
      {{ errorMessage }}
    </div>

    <ul class="space-y-2.5" aria-label="Cleanup actions">
      <li
        v-for="action in actions"
        :key="action.id"
        class="action-row rounded-xl p-4 flex flex-wrap items-center justify-between gap-3"
        :data-cleanup-action="action.id"
      >
        <div class="min-w-0 flex-1">
          <div class="flex flex-wrap items-center gap-2">
            <span class="font-semibold text-sm" style="color: var(--text-primary)">
              {{ action.label }}
            </span>
            <span
              v-if="action.bytes > 0 || action.count > 0"
              class="text-xs font-semibold tabular-nums"
              style="color: var(--brand)"
            >
              {{ formatStorageBytes(action.bytes) }}
            </span>
            <span v-else class="text-xs" style="color: var(--text-muted)">
              Nothing to free
            </span>
          </div>
          <p v-if="action.blocked_reason" class="text-xs mt-1" style="color: var(--text-muted)">
            {{ action.blocked_reason }}
          </p>
          <p v-else class="text-xs mt-1" style="color: var(--text-muted)">
            {{ action.what_stays }}
          </p>
        </div>

        <button
          type="button"
          class="cleanup-btn px-4 py-2 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 shrink-0"
          :disabled="!isCleanupActionAvailable(action) || Boolean(busyAction)"
          :aria-label="`Clean ${action.label}`"
          @click="emit('clean', action)"
        >
          <IconLoader2 v-if="busyAction === action.id" class="w-3.5 h-3.5 animate-spin" />
          <span>{{ busyAction === action.id ? "Working..." : "Clean..." }}</span>
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.storage-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
.action-row {
  background: var(--bg-inset);
  border: 1px solid var(--border);
}
.cleanup-banner--ok {
  background: rgba(16, 185, 129, 0.12);
  color: var(--text-primary);
  border: 1px solid rgba(16, 185, 129, 0.35);
}
.cleanup-banner--error {
  background: var(--danger-bg);
  color: var(--danger-text);
  border: 1px solid var(--danger-border);
}
.cleanup-btn {
  color: var(--text-primary);
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
.cleanup-btn:hover:not(:disabled) {
  background: var(--bg-overlay);
}
.cleanup-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.cleanup-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--brand-glow);
}
</style>
