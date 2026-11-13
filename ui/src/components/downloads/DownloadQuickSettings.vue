<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<!-- The four queue settings, also in Settings > Library & Downloads. A change saves at once. -->
<script setup lang="ts">
import {ref, watch} from 'vue';
import {api, type DownloadQueueSettings, type SettingsPayload} from '../../services/api';
import {MAX_ACTIVE_DOWNLOADS, clampLimitKib} from '../../utils/downloadQueue';
import {notify} from '../../utils/toast';

const props = defineProps<{ settings: DownloadQueueSettings }>();
const emit = defineEmits<{ saved: [] }>();

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
  <details class="quick" data-testid="download-quick-settings">
    <summary class="text-sm font-medium cursor-pointer select-none" style="color: var(--text-secondary)">Download settings</summary>
    <div class="grid gap-3 mt-3 sm:grid-cols-2">
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
    <p class="text-xs mt-2" style="color: var(--text-muted)">The limit is for file hosts and torrents together. A paused download that cannot continue keeps running.</p>
  </details>
</template>

<style scoped>
.quick {
  padding: 0.75rem 1rem;
  border-radius: 0.75rem;
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
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
