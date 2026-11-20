<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// One three-state filter pill (neutral -> include -> exclude) for the library
// Tags, Engines and Sources sections. Other listeners and attributes, such as
// the tag pill's contextmenu and Shift+F10 handlers, fall through to the button.
import { IconBan, IconCheck } from "@tabler/icons-vue";
import type { FilterValueState } from "../utils/libraryGames";

const props = defineProps<{
  label: string;
  state: FilterValueState;
  count?: number | undefined;
  color?: string | undefined;
}>();

const emit = defineEmits<{ activate: [] }>();
</script>

<template>
  <button
    type="button"
    class="filter-pill relative px-2.5 py-1 rounded-full text-[0.6875rem] font-medium"
    :class="`filter-pill--${props.state}`"
    :data-tag-color="props.color || undefined"
    :data-filter-state="props.state"
    :aria-pressed="props.state !== 'neutral'"
    @click="emit('activate')"
  >
    <IconCheck
      v-if="props.state === 'include' && props.color"
      class="inline w-3 h-3 mr-1 align-[-0.125em]"
      aria-hidden="true"
    />
    <IconBan
      v-if="props.state === 'exclude'"
      class="inline w-3 h-3 mr-1 align-[-0.125em]"
      aria-hidden="true"
    />
    <span class="filter-pill-label">{{ props.label }}</span>
    <span v-if="props.count !== undefined" class="ml-1 opacity-70">{{ props.count }}</span>
    <!-- The pill is relative so this absolute span stays inside the filter
         pane's scroll area; otherwise it can stretch <main> past the window. -->
    <span v-if="props.state !== 'neutral'" class="sr-only">
      {{ props.state === "include" ? ", included" : ", excluded" }}
    </span>
  </button>
</template>

<style scoped>
.filter-pill {
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.filter-pill:not([data-tag-color]).filter-pill--neutral {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}
.filter-pill:not([data-tag-color]).filter-pill--include {
  background: var(--brand-glow);
  border: 1px solid var(--brand-deep);
  color: var(--brand);
}
.filter-pill:not([data-tag-color]).filter-pill--exclude {
  background: var(--danger-bg);
  border: 1px solid var(--danger-border);
  color: var(--danger-text);
}
.filter-pill--exclude .filter-pill-label {
  text-decoration: line-through;
  text-decoration-thickness: 1.5px;
}
</style>
