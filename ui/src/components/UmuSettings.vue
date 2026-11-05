<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { IconAlertTriangle, IconBox, IconCircleCheck, IconDownload, IconLoader2, IconRefresh } from '@tabler/icons-vue';
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
const percent = (job: UmuJobStatus) => job.total ? Math.min(100, Math.round((job.done || 0) / job.total * 100)) : null;
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
  <section id="umu" class="settings-card rounded-xl p-6 space-y-5" style="scroll-margin-top:1.5rem" aria-labelledby="umu-heading">
    <h3 id="umu-heading" class="ui-section-heading text-lg font-semibold" style="color:var(--text-primary)">
      <IconBox class="ui-section-icon" />
      Proton (umu)
    </h3>

    <div data-settings-row="umu-static-0" data-settings-bound v-show="settingsRowVisible('umu-static-0')" class="grid gap-3 sm:grid-cols-2">
      <div class="ui-card-row flex items-start gap-3 p-4 rounded-lg min-w-0">
        <span class="ui-status-badge" :class="umu.path ? 'ui-status-badge--ok' : 'ui-status-badge--warn'" aria-hidden="true">
          <IconCircleCheck v-if="umu.path" class="w-4 h-4" /><IconAlertTriangle v-else class="w-4 h-4" />
        </span>
        <div class="min-w-0">
          <h4 class="text-sm font-medium" style="color:var(--text-primary)">umu-launcher</h4>
          <p class="text-xs mt-1 break-all" style="color:var(--text-muted)">
            {{ umu.path ? `${umu.source === 'system' ? 'System' : 'wLib download'}: ${umu.path}${umu.version ? ` (${umu.version})` : ''}` : 'umu-launcher is not installed.' }}
          </p>
        </div>
      </div>
      <div class="ui-card-row flex items-start gap-3 p-4 rounded-lg min-w-0" role="status">
        <span class="ui-status-badge" :class="runtime.ready ? 'ui-status-badge--ok' : ''" aria-hidden="true">
          <IconLoader2 v-if="runtime.running" class="w-4 h-4 animate-spin" />
          <IconCircleCheck v-else-if="runtime.ready" class="w-4 h-4" />
          <IconDownload v-else class="w-4 h-4" />
        </span>
        <div class="min-w-0">
          <h4 class="text-sm font-medium" style="color:var(--text-primary)">{{ runtime.runtime || 'Steam Linux Runtime' }}</h4>
          <p class="text-xs mt-1" style="color:var(--text-muted)">{{ runtime.ready ? 'Ready' : 'Not prepared' }}</p>
        </div>
      </div>
    </div>

    <p data-settings-row="umu-static-1" data-settings-bound v-show="settingsRowVisible('umu-static-1')" v-if="umu.python3 === false" class="ui-callout ui-callout--danger" role="alert">
      <IconAlertTriangle class="w-4 h-4 shrink-0" />Python 3 is required on the host.
    </p>
    <p data-settings-row="umu-static-3" data-settings-bound v-show="settingsRowVisible('umu-static-3')" class="text-xs" style="color:var(--text-muted)">Prepare downloads the shared Steam Linux Runtime and any selected Proton build. Choose the runner above first.</p>

    <div data-settings-row="umu-static-4" data-settings-bound v-show="settingsRowVisible('umu-static-4')" v-if="download.running" class="ui-card-row p-4 rounded-lg" role="status">
      <p class="text-sm">{{ download.stage }} — {{ bytes(download.done) }}<span v-if="download.total"> / {{ bytes(download.total) }}</span></p>
      <div class="umu-bar mt-2"><div class="umu-bar-fill" :class="{ indeterminate: percent(download) === null }" :style="percent(download) !== null ? { width: percent(download) + '%' } : undefined"></div></div>
    </div>
    <div data-settings-row="umu-static-5" data-settings-bound v-show="settingsRowVisible('umu-static-5')" v-if="runtime.running" class="ui-card-row p-4 rounded-lg" role="status">
      <p class="text-sm">{{ runtime.stage }} — {{ bytes(runtime.done) }} downloaded or installed</p>
      <div class="umu-bar mt-2"><div class="umu-bar-fill indeterminate"></div></div>
    </div>
    <p data-settings-row="umu-static-6" data-settings-bound v-show="settingsRowVisible('umu-static-6')" v-if="error || download.error" class="ui-callout ui-callout--danger" role="alert">
      <IconAlertTriangle class="w-4 h-4 shrink-0" /><span class="break-words min-w-0">{{ error || download.error }}</span>
    </p>
    <div data-settings-row="umu-static-7" data-settings-bound v-show="settingsRowVisible('umu-static-7')" v-if="runtimeMessage" class="ui-callout ui-callout--danger" role="alert">
      <IconAlertTriangle class="w-4 h-4 shrink-0" />
      <div class="min-w-0 flex-1">
        <p>{{ runtimeMessage }}</p>
        <details class="text-xs mt-1">
          <summary class="cursor-pointer">Details</summary>
          <pre class="umu-log">{{ runtime.error }}</pre>
        </details>
      </div>
    </div>

    <div data-settings-row="umu-static-8" data-settings-bound v-show="settingsRowVisible('umu-static-8')" class="flex flex-wrap gap-2">
      <button v-if="umu.source !== 'system'" class="ui-card-btn" :disabled="download.running || runtime.running" @click="start(() => api.downloadUmu())">
        <IconLoader2 v-if="download.running" class="w-3.5 h-3.5 animate-spin" /><IconDownload v-else class="w-3.5 h-3.5" />{{ download.running ? 'Downloading…' : 'Download umu' }}
      </button>
      <button class="ui-card-btn ui-card-btn--primary" :disabled="!umu.path || runtime.running || download.running" @click="start(() => api.prepareUmuRuntime())">{{ runtime.ready ? 'Update runtime' : 'Prepare runtime' }}</button>
      <button v-if="runtime.running" class="ui-card-btn" @click="start(() => api.cancelUmuRuntimePrepare())">Cancel</button>
      <button class="ui-card-btn" @click="refresh"><IconRefresh class="w-3.5 h-3.5" />Recheck</button>
    </div>
  </section>
</template>

<style scoped>
.umu-log { max-height: 12rem; overflow: auto; white-space: pre-wrap; word-break: break-word; margin-top: .25rem; padding: .5rem; border-radius: .375rem; border: 1px solid var(--border); background: var(--bg-inset); color: var(--text-secondary); }
.umu-bar { position: relative; height: .35rem; border-radius: 9999px; background: var(--bg-overlay); overflow: hidden; }
.umu-bar-fill { height: 100%; border-radius: inherit; background: var(--brand); transition: width var(--motion-base) ease; }
.umu-bar-fill.indeterminate { width: 35%; animation: umu-slide 1.2s ease-in-out infinite; }
@keyframes umu-slide { from { transform: translateX(-100%); } to { transform: translateX(290%); } }
@media (prefers-reduced-motion: reduce) { .umu-bar-fill.indeterminate { animation: none; width: 100%; opacity: .5; } }
</style>
