<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { IconCheck, IconX } from "@tabler/icons-vue";
import { api, type PathCheckKind } from "../services/api";

const props = defineProps<{ path: string; kind: PathCheckKind }>();

const status = ref<{ ok: boolean; reason: string } | null>(null);

let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let requestId = 0;

const clearTimer = () => {
  if (debounceTimer) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
};

const checkNow = async (value: string, kind: PathCheckKind) => {
  const request = ++requestId;
  try {
    const result = await api.checkPath(value, kind);
    // Ignore responses that arrived after the value changed.
    if (request !== requestId) return;
    if (result && typeof result.ok === "boolean") {
      status.value = { ok: result.ok, reason: result.reason || "" };
    } else {
      status.value = null;
    }
  } catch (error) {
    if (request !== requestId) return;
    console.warn("Path check failed", error);
    status.value = null;
  }
};

const scheduleCheck = (value: string, kind: PathCheckKind) => {
  clearTimer();
  // Empty fields show nothing: optional ones may stay empty, and required
  // ones already say so when the form is submitted.
  if (!value.trim()) {
    requestId++;
    status.value = null;
    return;
  }
  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    void checkNow(value, kind);
  }, 300);
};

watch(
  () => [props.path, props.kind] as const,
  ([value, kind]) => scheduleCheck(value, kind),
  { immediate: true },
);

onUnmounted(() => {
  clearTimer();
  requestId++;
});
</script>

<template>
  <p
    v-if="status"
    role="status"
    class="path-status text-xs mt-1 flex items-center gap-1.5"
    :style="status.ok ? 'color: var(--success-text)' : 'color: var(--danger-text)'"
  >
    <IconCheck v-if="status.ok" class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
    <IconX v-else class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
    <span>{{ status.ok ? "Found" : status.reason || "Not found" }}</span>
  </p>
</template>

<style scoped>
.path-status {
  min-height: 1rem;
}
</style>
