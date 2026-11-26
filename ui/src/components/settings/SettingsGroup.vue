<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- A titled group of rows inside a Settings section. The id is a stable deep
     link (/settings#<id>). Settings search reads each direct child after the
     heading block as one row, adds the group title to its keywords, and hides
     the group when none of its rows match. -->
<script setup lang="ts">
import { provide, toRef } from "vue";
import { settingsGroupTitle } from "../../utils/settingsVisibility";

const props = defineProps<{
  id: string;
  title: string;
  description?: string;
}>();
provide(settingsGroupTitle, toRef(props, "title"));
</script>

<template>
  <div :id="id" class="settings-group space-y-5" role="group" :aria-labelledby="`${id}-heading`" data-settings-group>
    <div data-settings-subheading class="settings-group-head">
      <div class="min-w-0">
        <h4 :id="`${id}-heading`" class="settings-group-title">{{ title }}</h4>
        <p v-if="description || $slots.description" class="settings-group-description"><slot name="description">{{ description }}</slot></p>
      </div>
      <slot name="actions" />
    </div>
    <slot />
  </div>
</template>

<style>
/* Not scoped: the rows come from the slot and from child components. */
.settings-group-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  /* Less space under the heading than above it (the gap and the top padding around the group's line).
     Unlayered, so it replaces the space-y-5 gap of the group. */
  margin-block-end: 0.75rem;
}
.settings-group-title {
  font-size: 1rem;
  line-height: 1.5rem;
  font-weight: 600;
  color: var(--text-primary);
}
.settings-group-description {
  max-width: 65ch;
  margin-top: 0.125rem;
  font-size: 0.75rem;
  line-height: 1rem;
  color: var(--text-muted);
}
/* Each row in a group draws its own top line, like the rows of a section. A callout has its own border and a
   note continues the row above it, so neither draws one. */
.settings-group > :not([data-settings-subheading]):not(table):not(:empty):not(.ui-callout):not(.settings-row-attached) {
  border-top: 1px solid var(--border);
  padding-top: var(--row-gap, 1.25rem);
}
/* A note sits closer to the row it explains (0.75rem instead of the 1.25rem row gap; margins collapse). */
.settings-group > .settings-row-attached { margin-top: -0.5rem; }
/* The first visible row after the heading draws no line: the line above the group already separates it.
   Settings search and v-show hide rows with an inline display: none, so a hidden first row does not count. */
.settings-group.settings-group > :nth-child(1 of :not([data-settings-subheading]):not(:empty):not([style*="display: none"])) {
  border-top: 0;
  padding-top: 0;
  margin-top: 0;
}
</style>
