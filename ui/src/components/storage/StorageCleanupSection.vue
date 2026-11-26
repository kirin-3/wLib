<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed } from "vue";
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconCircleCheck,
  IconEye,
  IconLoader2,
  IconShieldCheck,
  IconSparkles,
} from "@tabler/icons-vue";
import type {
  StorageCategoryReport,
  StorageCleanupAction,
  StorageCleanupTier,
} from "../../services/api";
import {
  cleanupActionBlockedReason,
  cleanupActionScopeNote,
  formatStorageBytes,
  groupCleanupActionsByTier,
  type StorageCleanupBusy,
} from "../../utils/storage";

const props = defineProps<{
  actions: StorageCleanupAction[];
  busyAction: StorageCleanupBusy;
  /** What the busy cleanup does now, such as "Checking..." or "Cleaning...". */
  busyLabel: string;
  /** Report categories, to say how much of a category an action frees. */
  categories: StorageCategoryReport[];
  resultMessage: string;
  errorMessage: string;
}>();

const emit = defineEmits<{
  (event: "clean", action: StorageCleanupAction): void;
}>();

const TIER_ICONS: Record<StorageCleanupTier, typeof IconShieldCheck> = {
  safe: IconShieldCheck,
  review: IconEye,
  risky: IconAlertTriangle,
};

const groups = computed(() => groupCleanupActionsByTier(props.actions));

// The warning names a risk in what the action would delete. With nothing to
// delete there is no risk to name, and the row says "Nothing to free".
function rowWarning(action: StorageCleanupAction): string {
  return action.bytes > 0 || action.count > 0 ? action.warning : "";
}

// A Clean button that cannot run keeps its place in the tab order
// (aria-disabled), so focus does not fall to the page while a cleanup runs
// or after the confirmation closes.
function onClean(action: StorageCleanupAction) {
  if (cleanupActionBlockedReason(action, props.busyAction)) return;
  emit("clean", action);
}
</script>

<template>
  <section
    id="storage-cleanup"
    class="storage-card rounded-xl"
    aria-labelledby="storage-cleanup-heading"
  >
    <div class="mb-4">
      <h3 id="storage-cleanup-heading" class="ui-section-heading text-lg font-semibold">
        <IconSparkles class="ui-section-icon" />
        <span>Free up space</span>
      </h3>
      <p class="text-xs mt-1" style="color: var(--text-muted)">
        Each action checks its files again and asks you before it deletes anything.
      </p>
    </div>

    <div
      v-if="resultMessage"
      role="status"
      class="cleanup-banner cleanup-banner--ok rounded-lg px-4 py-3 text-sm mb-4 break-words flex items-start gap-2"
    >
      <IconCircleCheck class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
      <span>{{ resultMessage }}</span>
    </div>
    <div
      v-if="errorMessage"
      role="alert"
      class="cleanup-banner cleanup-banner--error rounded-lg px-4 py-3 text-sm mb-4 break-words flex items-start gap-2"
    >
      <IconAlertCircle class="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
      <span>{{ errorMessage }}</span>
    </div>

    <div class="space-y-6">
      <section
        v-for="group in groups"
        :key="group.tier"
        :aria-labelledby="`storage-cleanup-tier-${group.tier}`"
        :data-cleanup-tier="group.tier"
      >
        <div class="mb-3">
          <h4
            :id="`storage-cleanup-tier-${group.tier}`"
            class="flex flex-wrap items-center gap-2 text-sm font-semibold"
            style="color: var(--text-primary)"
          >
            <span class="tier-chip" :class="`tier-chip--${group.tier}`">
              <component :is="TIER_ICONS[group.tier]" class="w-3.5 h-3.5" aria-hidden="true" />
              <span>{{ group.label }}</span>
            </span>
            <span class="tabular-nums whitespace-nowrap" style="color: var(--text-secondary)">
              {{ formatStorageBytes(group.bytes) }}
            </span>
          </h4>
          <p class="text-xs mt-1.5" style="color: var(--text-muted)">{{ group.description }}</p>
        </div>

        <ul class="space-y-2.5" :aria-label="`${group.label} cleanup actions`">
          <li
            v-for="action in group.actions"
            :key="action.id"
            class="action-row rounded-xl flex flex-wrap items-center justify-between gap-3"
            :data-cleanup-action="action.id"
          >
            <div class="action-row-text min-w-0">
              <div class="flex flex-wrap items-center gap-2">
                <span class="font-semibold text-sm" style="color: var(--text-primary)">
                  {{ action.label }}
                </span>
                <span
                  v-if="action.bytes > 0 || action.count > 0"
                  class="action-size text-xs font-semibold tabular-nums"
                >
                  {{ formatStorageBytes(action.bytes) }}
                </span>
                <span v-else class="text-xs" style="color: var(--text-muted)">
                  Nothing to free
                </span>
              </div>
              <p
                v-if="rowWarning(action)"
                class="cleanup-warning text-xs mt-1.5 flex items-start gap-1.5"
                :id="`storage-cleanup-warning-${action.id}`"
                data-cleanup-warning
              >
                <IconAlertTriangle class="w-3.5 h-3.5 shrink-0 mt-px" aria-hidden="true" />
                <span>{{ rowWarning(action) }}</span>
              </p>
              <p v-if="action.blocked_reason" class="action-note text-xs mt-1">
                {{ action.blocked_reason }}
              </p>
              <p v-else class="action-note text-xs mt-1">
                <template v-if="cleanupActionScopeNote(action, categories)">
                  {{ cleanupActionScopeNote(action, categories) }}
                </template>
                {{ action.what_stays }}
              </p>
            </div>

            <button
              type="button"
              class="cleanup-btn px-4 py-2 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 shrink-0"
              :aria-disabled="Boolean(cleanupActionBlockedReason(action, busyAction)) || undefined"
              :aria-busy="busyAction === action.id || undefined"
              :aria-label="`Clean ${action.label}`"
              :aria-describedby="[
                rowWarning(action) ? `storage-cleanup-warning-${action.id}` : '',
                cleanupActionBlockedReason(action, busyAction) ? `storage-cleanup-reason-${action.id}` : '',
              ].filter(Boolean).join(' ') || undefined"
              :title="cleanupActionBlockedReason(action, busyAction) || undefined"
              :data-cleanup-button="action.id"
              @click="onClean(action)"
            >
              <IconLoader2 v-if="busyAction === action.id" class="w-3.5 h-3.5 animate-spin" />
              <span>{{ busyAction === action.id ? busyLabel : "Clean..." }}</span>
            </button>
            <span
              v-if="cleanupActionBlockedReason(action, busyAction)"
              :id="`storage-cleanup-reason-${action.id}`"
              class="sr-only"
            >{{ cleanupActionBlockedReason(action, busyAction) }}</span>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>

<style scoped>
.storage-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  scroll-margin-top: 1rem;
  padding: 1.5rem;
}
.action-row {
  background: var(--bg-inset);
  border: 1px solid var(--border);
  padding: 1rem;
}
/* The text keeps a readable width; in a narrow row the Clean button wraps under it. */
.action-row-text {
  flex: 1 1 16rem;
}
.cleanup-btn {
  min-height: 2rem;
}
/* Phone-width room (container set by StorageView): tighter card and rows. */
@container storage-page (max-width: 30rem) {
  .storage-card {
    padding: 1rem;
  }
  .action-row {
    padding: 0.75rem;
  }
}
.tier-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  padding: 0.125rem 0.5rem;
  border-radius: 9999px;
  border: 1px solid var(--border);
  font-size: 0.75rem;
  line-height: 1.25rem;
}
.tier-chip--safe {
  background: var(--success-bg);
  border-color: var(--success-border);
  color: var(--success-text);
}
.tier-chip--review {
  background: var(--warning-bg);
  border-color: var(--warning-border);
  color: var(--warning-text);
}
.tier-chip--risky {
  background: var(--danger-bg);
  border-color: var(--danger-border);
  color: var(--danger-text);
}
.cleanup-banner--ok {
  background: var(--success-bg);
  color: var(--success-text);
  border: 1px solid var(--success-border);
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
.cleanup-btn:hover:not([aria-disabled="true"]) {
  background: var(--bg-overlay);
}
/* Blocked: the app's disabled-button look (.ui-card-btn in style.css), flat with a dashed edge. */
.cleanup-btn[aria-disabled="true"] {
  background: var(--bg-raised);
  color: var(--text-muted);
  border-color: transparent;
  outline: 1px dashed var(--border-hover);
  outline-offset: -1px;
  cursor: not-allowed;
}
/* The running action keeps its normal look so its "Checking..." or "Cleaning..." reads clearly. */
.cleanup-btn[aria-busy="true"] {
  background: var(--bg-surface);
  color: var(--text-primary);
  border-color: var(--border);
  outline: none;
  cursor: progress;
}
/* What the action keeps: a sentence people read before they clean, so it gets
   the secondary text colour (muted text is only 4.56:1 on the light inset row). */
.action-note {
  color: var(--text-secondary);
}
.cleanup-warning {
  color: var(--warning-text);
}
/* The accent as text: 4.5:1 or more on the inset row for every accent preset (--brand alone is 3.9:1 in light). */
.action-size {
  color: var(--brand-text);
}
.cleanup-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--brand-glow);
}
</style>
