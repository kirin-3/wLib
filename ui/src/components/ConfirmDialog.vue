<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { IconAlertTriangle, IconHelpCircle } from "@tabler/icons-vue";
import { confirmQueue, resolveConfirmation } from "../utils/confirm";
import { useModalKeyboard } from "../utils/modalKeyboard";

const current = computed(() => confirmQueue[0]);
const dialog = ref<HTMLElement | null>(null);
const cancelButton = ref<HTMLButtonElement | null>(null);
const checked = ref(false);
const cancel = () => resolveConfirmation(false, checked.value);
useModalKeyboard(dialog, () => Boolean(current.value), cancel);
watch(current, async (request) => {
  checked.value = false;
  if (request) {
    await nextTick();
    cancelButton.value?.focus();
  }
});
</script>

<template>
  <Teleport to="body">
    <div v-if="current" class="fixed inset-0 z-[200] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="cancel"></div>
      <section
        ref="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-message"
        tabindex="-1"
        class="confirm-content w-full max-w-md rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none"
      >
        <div class="px-6 pt-6 pb-5 flex gap-4 overflow-y-auto min-h-0">
          <span class="confirm-icon" :class="{ danger: current.danger }" aria-hidden="true">
            <IconAlertTriangle v-if="current.danger" class="w-5 h-5" />
            <IconHelpCircle v-else class="w-5 h-5" />
          </span>
          <div class="min-w-0 flex-1 pt-1.5">
            <h3 id="confirm-title" class="text-lg font-bold" style="color: var(--text-primary)">{{ current.title }}</h3>
            <p id="confirm-message" class="mt-2 text-sm whitespace-pre-line break-words" style="color: var(--text-secondary)">{{ current.message }}</p>
            <label v-if="current.checkboxLabel" class="mt-4 flex items-center gap-2 text-sm" style="color: var(--text-primary)">
              <input v-model="checked" type="checkbox" />
              {{ current.checkboxLabel }}
            </label>
          </div>
        </div>
        <div class="px-6 py-4 flex justify-end gap-3" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
          <button ref="cancelButton" class="confirm-cancel px-5 py-2.5 rounded-lg text-sm font-medium" @click="cancel">{{ current.cancelLabel || "Cancel" }}</button>
          <button
            class="px-6 py-2.5 rounded-lg text-sm font-bold"
            :class="current.danger ? 'confirm-danger' : 'confirm-primary'"
            @click="resolveConfirmation(true, checked)"
          >
            {{ current.confirmLabel }}
          </button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.confirm-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
}
.confirm-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.5rem;
  height: 2.5rem;
  flex-shrink: 0;
  border-radius: 9999px;
  background: var(--brand-glow);
  color: var(--brand);
}
.confirm-icon.danger {
  background: var(--danger-bg);
  color: var(--danger-text);
  border: 1px solid var(--danger-border);
}
.confirm-cancel { color: var(--text-secondary); }
.confirm-cancel:hover { color: var(--text-primary); background: var(--bg-overlay); }
.confirm-primary {
  background: var(--brand);
  color: var(--on-brand);
  box-shadow: var(--shadow-brand);
}
.confirm-primary:hover { filter: brightness(1.08); }
.confirm-danger {
  background: #dc2626;
  color: #fff;
  box-shadow: 0 4px 14px rgba(220, 38, 38, 0.3);
}
.confirm-danger:hover { background: #b91c1c; }
.confirm-cancel:focus-visible,
.confirm-primary:focus-visible,
.confirm-danger:focus-visible { outline: none; box-shadow: 0 0 0 3px var(--brand-glow); }
</style>
