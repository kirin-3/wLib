<script setup lang="ts">
// SPDX-License-Identifier: GPL-3.0-or-later
// Placeholder for a game without a cover: up to two initials on a gradient from the title's tag swatch to the
// surface color. The parent sets the size and corner radius. Hidden from assistive technology, because the
// title is already named next to it.
import { computed } from "vue";
import { IconDeviceGamepad2 } from "@tabler/icons-vue";
import { titleInitials, titleSwatch } from "../utils/coverPlaceholder";

const props = defineProps<{ title: string | null | undefined }>();

const initials = computed(() => titleInitials(props.title));
const swatch = computed(() => titleSwatch(props.title));
</script>

<template>
  <div class="cover-placeholder" :data-tag-color="swatch" aria-hidden="true">
    <span v-if="initials" class="cover-placeholder-initials">{{ initials }}</span>
    <IconDeviceGamepad2 v-else class="cover-placeholder-icon" />
  </div>
</template>

<style scoped>
/* --tag-text passes 4.5:1 against --tag-bg and against --bg-surface in both themes, so it also passes against
   every mix of the two along the gradient. */
.cover-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  container-type: size;
  background: linear-gradient(
    135deg,
    var(--tag-bg),
    color-mix(in srgb, var(--tag-bg) 35%, var(--bg-surface))
  );
  color: var(--tag-text);
}

.cover-placeholder-initials {
  font-size: clamp(0.75rem, 22cqmin, 3rem);
  font-weight: 700;
  letter-spacing: 0.04em;
  line-height: 1;
}

.cover-placeholder-icon {
  width: clamp(1rem, 30cqmin, 3.5rem);
  height: clamp(1rem, 30cqmin, 3.5rem);
}
</style>
