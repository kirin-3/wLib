<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useTemplateRef } from "vue";
import { IconX } from "@tabler/icons-vue";
import { useModalKeyboard } from "../utils/modalKeyboard";

const isOpen = defineModel<boolean>({ required: true });
defineProps<{
  frequency: string;
  notificationsAvailable: boolean;
  desktopNotifications: boolean;
  notificationsSaving: boolean;
  notificationsLoaded: boolean;
  notificationsError: string;
}>();
const emit = defineEmits<{
  (event: "set-frequency", value: string): void;
  (event: "set-notifications", value: Event): void;
  (event: "retry-notifications"): void;
}>();

const frequencyOptions = [
  { key: "weekly", label: "Weekly" },
  { key: "monthly", label: "Monthly" },
  { key: "off", label: "Off" },
];

const dialog = useTemplateRef<HTMLElement>("dialog");
const close = () => { isOpen.value = false; };
useModalKeyboard(dialog, () => isOpen.value, close);
</script>

<template>
  <Teleport to="body">
    <div v-if="isOpen" class="fixed inset-0 z-[150] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
      <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="update-settings-title" tabindex="-1" class="settings-dialog w-full max-w-md rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none">
        <div class="px-6 pt-5 pb-5 overflow-y-auto min-h-0 space-y-5">
          <div class="flex items-start justify-between gap-3">
            <div>
              <h3 id="update-settings-title" class="text-lg font-bold" style="color: var(--text-primary)">Update settings</h3>
              <p class="mt-0.5 text-xs" style="color: var(--text-muted)">Changes save automatically.</p>
            </div>
            <button type="button" class="dialog-close ui-icon-btn p-1.5 rounded-lg" aria-label="Close" title="Close" @click="close">
              <IconX class="w-5 h-5" aria-hidden="true" />
            </button>
          </div>

          <div role="group" aria-labelledby="update-frequency-label">
            <p id="update-frequency-label" class="dialog-label">Automatic checks</p>
            <div class="inline-flex items-center rounded-lg overflow-hidden" style="border: 1px solid var(--border)">
              <button
                v-for="opt in frequencyOptions"
                :key="opt.key"
                type="button"
                :aria-pressed="frequency === opt.key"
                class="freq-btn px-3 py-1.5 text-xs font-medium transition-all"
                :class="{ 'freq-btn--active': frequency === opt.key }"
                @click="emit('set-frequency', opt.key)"
              >
                {{ opt.label }}
              </button>
            </div>
          </div>

          <div v-if="notificationsAvailable" class="text-sm" style="color: var(--text-primary)">
            <label class="flex items-start gap-3 cursor-pointer">
              <input type="checkbox" class="mt-1 shrink-0" :checked="desktopNotifications"
                :disabled="notificationsSaving || !notificationsLoaded"
                @change="emit('set-notifications', $event)" />
              <span>
                Desktop notifications
                <span v-if="notificationsLoaded && !notificationsError" class="block mt-0.5 text-xs" style="color: var(--text-secondary)">
                  Notify after automatic checks, or manual checks that finish while you are away from wLib.
                </span>
              </span>
            </label>
            <p v-if="notificationsError" role="alert" class="pl-7">
              {{ notificationsError }}
              <button type="button" class="underline min-h-11" @click="emit('retry-notifications')">Retry</button>
            </p>
            <p v-else-if="!notificationsLoaded" role="status" class="pl-7 text-xs" style="color: var(--text-muted)">Loading notification setting...</p>
          </div>
        </div>
        <div class="px-6 py-3 flex items-center" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
          <button type="button" class="dialog-done ml-auto px-5 py-2 rounded-lg text-sm font-semibold" @click="close">Done</button>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.settings-dialog { background: var(--bg-surface); border: 1px solid var(--border); box-shadow: var(--shadow-modal); }
.dialog-label { display: block; margin-bottom: 0.375rem; font-size: 0.875rem; font-weight: 500; color: var(--text-secondary); }
.dialog-done { color: var(--text-primary); background: var(--bg-raised); border: 1px solid var(--border); }
.dialog-done:hover { background: var(--bg-overlay); border-color: var(--border-hover); }
.dialog-close { color: var(--text-secondary); }
.freq-btn { background: var(--bg-surface); color: var(--text-muted); }
.freq-btn--active { background: var(--bg-overlay); color: var(--text-primary); }
.freq-btn:focus-visible, .dialog-close:focus-visible, .dialog-done:focus-visible, input[type="checkbox"]:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
input[type="checkbox"] { accent-color: var(--brand); }
</style>
