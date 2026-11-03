<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { confirmQueue, resolveConfirmation } from "../utils/confirm";
import { useModalKeyboard } from "../utils/modalKeyboard";

const current = computed(() => confirmQueue[0]);
const dialog = ref<HTMLElement | null>(null);
const cancelButton = ref<HTMLButtonElement | null>(null);
const cancel = () => resolveConfirmation(false);
useModalKeyboard(dialog, () => Boolean(current.value), cancel);
watch(current, async (request) => {
  if (request) {
    await nextTick();
    cancelButton.value?.focus();
  }
});
</script>

<template>
  <Teleport to="body">
    <div v-if="current" class="fixed inset-0 z-[200] flex items-center justify-center p-4" style="background: rgba(0, 0, 0, .65)" @click.self="cancel">
      <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-message" tabindex="-1" class="w-full max-w-md rounded-xl p-5 shadow-xl" style="background: var(--bg-raised); color: var(--text-primary); border: 1px solid var(--border)">
        <h2 id="confirm-title" class="text-lg font-semibold">{{ current.title }}</h2>
        <p id="confirm-message" class="mt-3 whitespace-pre-line">{{ current.message }}</p>
        <div class="mt-5 flex justify-end gap-2">
          <button ref="cancelButton" class="ui-action-btn px-3 py-2" @click="cancel">Cancel</button>
          <button class="ui-action-btn px-3 py-2" :class="{ 'confirm-danger': current.danger }" @click="resolveConfirmation(true)">{{ current.confirmLabel }}</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-danger { background: var(--danger-bg); color: var(--danger-text); border-color: var(--danger-border); }
.confirm-danger:hover { filter: brightness(.9); }
</style>
