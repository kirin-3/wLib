<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import type { FieldSaveState } from "../composables/useFieldAutosave";
defineProps<{ state: FieldSaveState; error: string }>();
</script>
<template>
  <p v-if="state !== 'idle'" class="text-xs mt-1" :style="{ color: state === 'error' ? 'var(--danger-text)' : 'var(--text-muted)' }" :role="state === 'error' ? 'alert' : 'status'">
    {{ state === 'saving' ? 'Saving…' : state === 'saved' ? 'Saved' : error }}
  </p>
</template>
