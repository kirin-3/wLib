<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { IconBox } from '@tabler/icons-vue';
import { api, onWebviewReady } from '../services/api';
import type { UmuStatus, UmuJobStatus } from '../services/api';

const settingsRowVisible = useSettingsRowVisibility();

const umu = ref<UmuStatus>({});
const download = ref<UmuJobStatus>({});
const runtime = ref<UmuJobStatus>({});
const error = ref('');
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
const refresh = async () => {
  if (timer) clearTimeout(timer);
  timer = undefined;
  try {
    const [u, d, r] = await Promise.all([api.getUmuStatus(), api.getUmuDownloadStatus(), api.getUmuRuntimeStatus()]);
    if (disposed) return;
    umu.value = u; download.value = d; runtime.value = r;
    if (d.running || r.running) timer = setTimeout(() => { void refresh(); }, 1000);
  } catch (e) { error.value = String(e); }
};
const start = async (action: () => Promise<{ success?: boolean; error?: string }>) => {
  error.value = '';
  try {
    const result = await action();
    if (!result.success) throw new Error(result.error || 'The operation could not start.');
    await refresh();
  } catch (e) { error.value = String(e); }
};
const bytes = (size: number | undefined) => `${((size || 0) / 1048576).toFixed(1)} MiB`;
const prepareMessages: Record<string, string> = {
  disk: 'Runtime preparation failed: not enough disk space.',
  network: 'Runtime preparation failed: no network connection. Connect to the internet and try again.',
  python: 'Runtime preparation failed: Python 3 is missing on the host.',
  other: 'Runtime preparation failed.',
};
// The runtime job reports umu's raw output tail; keep it behind Details.
const runtimeMessage = computed(() => {
  if (!runtime.value.error) return '';
  return prepareMessages[runtime.value.error_class || ''] || prepareMessages.other;
});
onMounted(() => onWebviewReady(() => { void refresh(); }));
onUnmounted(() => { disposed = true; if (timer) clearTimeout(timer); });
</script>

<template>
  <section id="umu" class="settings-card rounded-xl p-6 space-y-4" style="scroll-margin-top:1.5rem" aria-labelledby="umu-heading">
    <h3 id="umu-heading" class="ui-section-heading text-lg font-semibold" style="color:var(--text-primary)">
      <IconBox class="ui-section-icon" />
      Proton (umu)
    </h3>
    <p data-settings-row="umu-static-0" data-settings-bound v-show="settingsRowVisible('umu-static-0')" class="text-sm" style="color:var(--text-secondary)">
      {{ umu.path ? `${umu.source === 'system' ? 'System' : 'wLib download'}: ${umu.path}${umu.version ? ` (${umu.version})` : ''}` : 'umu-launcher is not installed.' }}
    </p>
    <p data-settings-row="umu-static-1" data-settings-bound v-show="settingsRowVisible('umu-static-1')" v-if="umu.python3 === false" class="text-sm" role="alert">Python 3 is required on the host.</p>
    <p data-settings-row="umu-static-2" data-settings-bound v-show="settingsRowVisible('umu-static-2')" class="text-sm" role="status">{{ runtime.runtime || 'Steam Linux Runtime' }}: {{ runtime.ready ? 'Ready' : 'Not prepared' }}</p>
    <p data-settings-row="umu-static-3" data-settings-bound v-show="settingsRowVisible('umu-static-3')" class="text-xs" style="color:var(--text-secondary)">Prepare downloads the shared Steam Linux Runtime and any selected Proton build. Choose the runner above first.</p>
    <p data-settings-row="umu-static-4" data-settings-bound v-show="settingsRowVisible('umu-static-4')" v-if="download.running" class="text-sm" role="status">{{ download.stage }} — {{ bytes(download.done) }}<span v-if="download.total"> / {{ bytes(download.total) }}</span></p>
    <p data-settings-row="umu-static-5" data-settings-bound v-show="settingsRowVisible('umu-static-5')" v-if="runtime.running" class="text-sm" role="status">{{ runtime.stage }} — {{ bytes(runtime.done) }} downloaded or installed</p>
    <p data-settings-row="umu-static-6" data-settings-bound v-show="settingsRowVisible('umu-static-6')" v-if="error || download.error" class="text-sm" role="alert" style="color:var(--danger, #dc2626)">{{ error || download.error }}</p>
    <div data-settings-row="umu-static-7" data-settings-bound v-show="settingsRowVisible('umu-static-7')" v-if="runtimeMessage" role="alert">
      <p class="text-sm" style="color:var(--danger, #dc2626)">{{ runtimeMessage }}</p>
      <details class="text-xs mt-1" style="color:var(--text-secondary)">
        <summary class="cursor-pointer">Details</summary>
        <pre class="umu-log">{{ runtime.error }}</pre>
      </details>
    </div>
    <div data-settings-row="umu-static-8" data-settings-bound v-show="settingsRowVisible('umu-static-8')" class="flex flex-wrap gap-2">
      <button v-if="umu.source !== 'system'" class="umu-button" :disabled="download.running || runtime.running" @click="start(() => api.downloadUmu())">{{ download.running ? 'Downloading…' : 'Download umu' }}</button>
      <button class="umu-button" :disabled="!umu.path || runtime.running || download.running" @click="start(() => api.prepareUmuRuntime())">{{ runtime.ready ? 'Update runtime' : 'Prepare runtime' }}</button>
      <button v-if="runtime.running" class="umu-button" @click="start(() => api.cancelUmuRuntimePrepare())">Cancel</button>
      <button class="umu-button" @click="refresh">Recheck</button>
    </div>
  </section>
</template>

<style scoped>
.umu-button { padding: .4rem .7rem; border-radius: .5rem; border: 1px solid var(--border); background: var(--bg-raised); color: var(--text-primary); font-size: .8rem; cursor: pointer; }
.umu-button:hover { border-color: var(--brand); }
.umu-button:disabled { opacity: .5; cursor: default; }
.umu-log { max-height: 12rem; overflow: auto; white-space: pre-wrap; word-break: break-word; margin-top: .25rem; padding: .5rem; border-radius: .375rem; border: 1px solid var(--border); background: var(--bg-raised); color: var(--text-secondary); }
</style>
