<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- One Settings row: a label, help text, the control and a save status.
     "inline" puts the control on the right of the text; "stacked" puts it under
     the text (paths, lists), with optional header actions on the right.
     The status slot keeps its width, so "Saving…" and "Saved" never move the
     control. A save error shows as a line under the control. The root is the
     row itself, so settings search can tag and hide it like any other row. -->
<script setup lang="ts">
import { computed, inject } from "vue";
import { IconAlertCircle, IconCheck } from "@tabler/icons-vue";
import type { FieldSaveState } from "../../composables/useFieldAutosave";
import { settingsGroupTitle } from "../../utils/settingsVisibility";
import { errorReason } from "../../utils/toast";

const props = withDefaults(defineProps<{
  label?: string;
  help?: string;
  /** The id of the control. The label then becomes a <label for>; without it the label is a heading. */
  controlId?: string;
  labelId?: string;
  helpId?: string;
  layout?: "inline" | "stacked";
  /** Set (also to "idle") to show the save status for this row. */
  saveState?: FieldSaveState;
  saveError?: string;
}>(), {
  label: "",
  help: "",
  layout: "inline",
  saveError: "",
});

const hasStatus = computed(() => props.saveState !== undefined);
// Section titles are h3 and group titles h4, so a row title is one level under its parent.
const headingTag = inject(settingsGroupTitle, null) ? "h5" : "h4";
</script>

<template>
  <div class="setting-row ui-card-row" :class="`setting-row--${layout}`">
    <div class="setting-row-head">
      <div class="setting-row-text">
        <label v-if="controlId" :id="labelId" :for="controlId" class="setting-row-label"><slot name="label">{{ label }}</slot></label>
        <component :is="headingTag" v-else :id="labelId" class="setting-row-label"><slot name="label">{{ label }}</slot></component>
        <p v-if="help || $slots.help" :id="helpId" class="setting-row-help"><slot name="help">{{ help }}</slot></p>
      </div>
      <div v-if="hasStatus || layout === 'inline' || $slots.actions" class="setting-row-aside">
        <span v-if="hasStatus" class="save-status save-status-slot" :class="{ 'save-status--saved': saveState === 'saved' }" role="status">
          <span v-if="saveState === 'saving'">Saving…</span>
          <span v-else-if="saveState === 'saved'" class="inline-flex items-center gap-1"><IconCheck class="w-3.5 h-3.5" aria-hidden="true" />Saved</span>
        </span>
        <div v-if="layout === 'inline'" class="setting-row-control"><slot /></div>
        <slot name="actions" />
      </div>
    </div>
    <div v-if="layout === 'stacked' && $slots.default" class="setting-row-body"><slot /></div>
    <p v-if="saveState === 'error'" class="save-status-error setting-row-error" role="alert">
      <IconAlertCircle class="w-3.5 h-3.5 shrink-0 mt-px" aria-hidden="true" />
      <span>Not saved. {{ errorReason(saveError) }}</span>
    </p>
    <div v-if="$slots.after" class="setting-row-after"><slot name="after" /></div>
  </div>
</template>

<style scoped>
.setting-row-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  column-gap: 1rem;
  row-gap: 0.75rem;
}
.setting-row--stacked .setting-row-head { align-items: flex-start; }
/* The text takes the free width; the control wraps under it only when less than 16rem is left. */
.setting-row-text { flex: 1 1 16rem; min-width: 0; }
.setting-row-label {
  display: block;
  font-size: 0.875rem;
  line-height: 1.25rem;
  font-weight: 500;
  color: var(--text-primary);
}
/* A readable measure for help text (about 80 characters at this size). */
.setting-row-help {
  max-width: 65ch;
  margin-top: 0.25rem;
  font-size: 0.75rem;
  line-height: 1rem;
  color: var(--text-muted);
}
.setting-row-aside {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 0.75rem;
  margin-left: auto;
}
.setting-row-control { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 0.5rem; }
.setting-row-body { margin-top: 0.75rem; }
.setting-row-error { margin-top: 0.5rem; }
.setting-row-after { margin-top: 0.75rem; }
</style>
