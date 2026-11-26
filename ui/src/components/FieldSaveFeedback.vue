<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- "Saving…", "Saved" or the save error of one field. SettingRow uses the same
     look in its status slot; use this one for a field inside a larger row. -->
<script setup lang="ts">
import { IconAlertCircle, IconCheck } from "@tabler/icons-vue";
import type { FieldSaveState } from "../composables/useFieldAutosave";
import { errorReason } from "../utils/toast";
defineProps<{ state: FieldSaveState; error: string }>();
</script>
<template>
  <p v-if="state === 'error'" class="save-status-error" role="alert">
    <IconAlertCircle class="w-3.5 h-3.5 shrink-0 mt-px" aria-hidden="true" />
    <span>Not saved. {{ errorReason(error) }}</span>
  </p>
  <span v-else class="save-status" :class="{ 'save-status--saved': state === 'saved' }" role="status">
    <span v-if="state === 'saving'">Saving…</span>
    <span v-else-if="state === 'saved'" class="inline-flex items-center gap-1"><IconCheck class="w-3.5 h-3.5" aria-hidden="true" />Saved</span>
  </span>
</template>
