<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// One item in a bulk-action menu. With a `reason`, the item is unavailable: it
// stays focusable (a menu keeps unavailable items reachable), says why on a
// second line, and links that line as its description. So the reason never
// depends on a hover-only title, and a screen reader reads it on focus.
import { useId } from "vue";

const props = defineProps<{ reason?: string | undefined }>();
const emit = defineEmits<{ select: [] }>();
const reasonId = useId();

function onClick(): void {
  if (!props.reason) emit("select");
}
</script>

<template>
  <button
    type="button"
    role="menuitem"
    class="bulk-item"
    :aria-disabled="props.reason ? 'true' : undefined"
    :aria-describedby="props.reason ? reasonId : undefined"
    @click="onClick"
  >
    <span class="bulk-item-label"><slot /></span>
    <!-- Hidden from the name only: aria-describedby still reads it. -->
    <span v-if="props.reason" :id="reasonId" class="bulk-item-reason" aria-hidden="true">{{ props.reason }}</span>
  </button>
</template>

<style scoped>
.bulk-item {
  flex-wrap: wrap;
  row-gap: 0.125rem;
}
.bulk-item-label {
  display: flex;
  flex: 1 1 100%;
  align-items: center;
  justify-content: inherit;
  gap: inherit;
  min-width: 0;
}
.bulk-item[aria-disabled="true"] {
  cursor: not-allowed;
}
.bulk-item[aria-disabled="true"] .bulk-item-label {
  opacity: 0.55;
}
.bulk-item-reason {
  flex: 1 1 100%;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 400;
  line-height: 1.3;
  text-align: inherit;
}
</style>
