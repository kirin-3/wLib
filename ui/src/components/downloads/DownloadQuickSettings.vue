<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- The four queue settings in a dialog, also in Settings > Library & Downloads. A change saves at once. -->
<script setup lang="ts">
import {ref, watch} from 'vue';
import {IconX} from '@tabler/icons-vue';
import {useModalKeyboard} from '../../utils/modalKeyboard';
import {api, type DownloadQueueSettings, type SettingsPayload} from '../../services/api';
import {MAX_ACTIVE_DOWNLOADS, clampLimitKib} from '../../utils/downloadQueue';
import {notify} from '../../utils/toast';

const props = defineProps<{ modelValue: boolean; settings: DownloadQueueSettings }>();
const emit = defineEmits<{ saved: []; 'update:modelValue': [value: boolean] }>();

const dialog = ref<HTMLElement | null>(null);
const close = () => emit('update:modelValue', false);
useModalKeyboard(dialog, () => props.modelValue, close);

const limit = ref(props.settings.download_limit_kib);
const slots = ref(props.settings.download_max_active);
const whilePlaying = ref(props.settings.download_pause_while_playing);
const onBattery = ref(props.settings.download_pause_on_battery);
let saving = false;

// Follow the backend unless a save is in flight.
watch(() => props.settings, (value) => {
  if (saving) return;
  limit.value = value.download_limit_kib;
  slots.value = value.download_max_active;
  whilePlaying.value = value.download_pause_while_playing;
  onBattery.value = value.download_pause_on_battery;
}, {deep: true});

async function save(payload: SettingsPayload) {
  saving = true;
  try {
    const result = await api.saveSettings(payload);
    if (!result.success) notify(result.error || 'Could not save the download settings.', 'error');
  } catch (error) {
    notify(error instanceof Error ? error.message : 'Could not save the download settings.', 'error');
  } finally {
    saving = false;
    emit('saved');
  }
}

const saveLimit = () => { limit.value = clampLimitKib(limit.value); void save({download_limit_kib: limit.value}); };
const saveSlots = () => { void save({download_max_active: slots.value}); };
const saveWhilePlaying = () => { void save({download_pause_while_playing: whilePlaying.value}); };
const saveOnBattery = () => { void save({download_pause_on_battery: onBattery.value}); };
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="fixed inset-0 z-[150] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
      <section ref="dialog" role="dialog" aria-modal="true" aria-labelledby="download-settings-title" tabindex="-1" class="quick w-full max-w-lg rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none" data-testid="download-quick-settings">
        <div class="px-6 pt-5 pb-5 overflow-y-auto min-h-0">
          <div class="flex items-start justify-between gap-3">
            <h3 id="download-settings-title" class="text-lg font-bold" style="color: var(--text-primary)">Download settings</h3>
            <button type="button" class="dialog-close ui-icon-btn p-1.5 rounded-lg" aria-label="Close" title="Close" @click="close">
              <IconX class="w-5 h-5" aria-hidden="true" />
            </button>
          </div>
          <div class="grid gap-3 mt-4 sm:grid-cols-2">
            <label class="field">
              <span>Speed limit (KiB/s, 0 for no limit)</span>
              <input v-model.number="limit" type="number" min="0" step="64" class="input" data-testid="quick-limit" @change="saveLimit" />
            </label>
            <label class="field">
              <span>Downloads at the same time</span>
              <select v-model.number="slots" class="input" data-testid="quick-slots" @change="saveSlots">
                <option v-for="count in MAX_ACTIVE_DOWNLOADS" :key="count" :value="count">{{ count }}</option>
              </select>
            </label>
            <label class="check">
              <input v-model="whilePlaying" type="checkbox" @change="saveWhilePlaying" />
              <span>Pause downloads while a game runs</span>
            </label>
            <label class="check">
              <input v-model="onBattery" type="checkbox" @change="saveOnBattery" />
              <span>Pause downloads on battery power</span>
            </label>
          </div>
          <p class="text-xs mt-3" style="color: var(--text-secondary)">The limit is for file hosts and torrents together. A paused download that cannot continue keeps running.</p>
        </div>
        <div class="px-6 py-3 text-sm" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
          <RouterLink to="/settings#library-downloads" class="settings-link" @click="close">Open Settings › Library &amp; Downloads</RouterLink>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.quick { background: var(--bg-surface); border: 1px solid var(--border); box-shadow: var(--shadow-modal); }
.dialog-close { color: var(--text-secondary); }
.dialog-close:focus-visible, .settings-link:focus-visible, .check input:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.settings-link { color: var(--brand); text-decoration: underline; text-underline-offset: 2px; border-radius: 0.25rem; }
.field { display: grid; gap: 0.3rem; font-size: 0.8rem; color: var(--text-secondary); }
.check { display: flex; align-items: center; gap: 0.5rem; font-size: 0.85rem; color: var(--text-primary); }
.input {
  padding: 0.4rem 0.6rem;
  border-radius: 0.5rem;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  color: var(--text-primary);
  font-size: 0.875rem;
}
.input:focus-visible { outline: 2px solid var(--brand); outline-offset: 1px; }
</style>
