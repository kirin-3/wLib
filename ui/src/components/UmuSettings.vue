<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { IconAlertTriangle, IconCircleCheck, IconDownload, IconLoader2, IconRefresh } from '@tabler/icons-vue';
import { api, onWebviewReady } from '../services/api';
import type { UmuStatus, UmuJobStatus } from '../services/api';
import SettingsGroup from "./settings/SettingsGroup.vue";
import SettingsHelpLink from "./settings/SettingsHelpLink.vue";
import type { UmuReadiness } from "../utils/settingsReadiness";

const props = defineProps<{
  /** true when the default runner is a Proton build (umu is then needed to launch); null while unknown. */
  required?: boolean | null;
}>();
const emit = defineEmits<{ status: [status: UmuReadiness] }>();

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
    emit('status', { installed: !!u.path, python3: u.python3 !== false, runtimeReady: !!r.ready, busy: !!(d.running || r.running) });
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
// The runtime that umu needs follows the default runner: the page calls this after the runner is saved.
defineExpose({ refresh });
// umu is optional while the default runner is Wine; warn only when Proton needs it.
const umuTone = computed(() => umu.value.path ? 'ok' : props.required ? 'warn' : '');
const umuText = computed(() => {
  if (umu.value.path) return `${umu.value.source === 'system' ? 'System' : 'wLib download'}: ${umu.value.path}${umu.value.version ? ` (${umu.value.version})` : ''}`;
  return props.required ? 'Not installed. Your Proton runner needs it.' : 'Optional · not installed. Only needed for Proton.';
});
onMounted(() => onWebviewReady(() => { void refresh(); }));
onUnmounted(() => { disposed = true; if (timer) clearTimeout(timer); });
</script>

<template>
  <SettingsGroup id="umu" title="Proton (umu)">
    <template #description>umu-launcher runs Proton outside Steam. Wine games do not need it.</template>
    <template #actions><SettingsHelpLink page="Launching-Games" anchor="proton-through-umu-launcher" /></template>

    <div data-settings-row="umu-static-0" data-settings-bound v-show="settingsRowVisible('umu-static-0')" class="grid gap-3 sm:grid-cols-2">
      <div class="flex items-start gap-3 min-w-0">
        <span class="ui-status-badge" :class="umuTone ? `ui-status-badge--${umuTone}` : ''" aria-hidden="true">
          <IconCircleCheck v-if="umu.path" class="w-4 h-4" /><IconAlertTriangle v-else-if="umuTone === 'warn'" class="w-4 h-4" /><IconDownload v-else class="w-4 h-4" />
        </span>
        <div class="min-w-0">
          <h5 class="text-sm font-medium" style="color:var(--text-primary)">umu-launcher</h5>
          <p class="text-xs mt-1 break-all" style="color:var(--text-muted)">
            {{ umuText }}
          </p>
        </div>
      </div>
      <div class="flex items-start gap-3 min-w-0" role="status">
        <span class="ui-status-badge" :class="runtime.ready ? 'ui-status-badge--ok' : ''" aria-hidden="true">
          <IconLoader2 v-if="runtime.running" class="w-4 h-4 animate-spin" />
          <IconCircleCheck v-else-if="runtime.ready" class="w-4 h-4" />
          <IconDownload v-else class="w-4 h-4" />
        </span>
        <div class="min-w-0">
          <h5 class="text-sm font-medium" style="color:var(--text-primary)">Steam Linux Runtime<span v-if="runtime.runtime" class="font-normal" style="color:var(--text-muted)"> ({{ runtime.runtime }})</span></h5>
          <p class="text-xs mt-1" style="color:var(--text-muted)">{{ runtime.ready ? 'Ready' : 'Not prepared' }}. Valve's base system that Proton runs in, shared by all games.</p>
        </div>
      </div>
    </div>

    <p data-settings-row="umu-static-1" data-settings-bound v-show="settingsRowVisible('umu-static-1')" v-if="umu.python3 === false" class="ui-callout ui-callout--danger" role="alert">
      <IconAlertTriangle class="w-4 h-4 shrink-0" />Python 3 is required on the host.
    </p>
    <p data-settings-row="umu-static-3" data-settings-bound v-show="settingsRowVisible('umu-static-3')" class="text-xs" style="color:var(--text-muted)">Prepare downloads the Steam Linux Runtime and the Proton build you chose. Choose the runner under Wine and Proton first. Downloads keep running if you leave this page.</p>

    <div data-settings-row="umu-static-4" data-settings-bound v-show="settingsRowVisible('umu-static-4')" v-if="download.running" class="ui-card-row" role="status">
      <p class="text-sm">{{ download.stage }} — {{ bytes(download.done) }}<span v-if="download.total"> / {{ bytes(download.total) }}</span></p>
      <div class="umu-bar mt-2"><div class="umu-bar-fill" :class="{ indeterminate: percent(download) === null }" :style="percent(download) !== null ? { width: percent(download) + '%' } : undefined"></div></div>
    </div>
    <div data-settings-row="umu-static-5" data-settings-bound v-show="settingsRowVisible('umu-static-5')" v-if="runtime.running" class="ui-card-row" role="status">
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

    <div data-settings-row="umu-static-8" data-settings-bound v-show="settingsRowVisible('umu-static-8')" class="settings-row-attached flex flex-wrap gap-2">
      <button v-if="umu.source !== 'system'" class="ui-card-btn" :class="{ 'ui-card-btn--primary': required && !umu.path }" data-readiness-target :disabled="download.running || runtime.running" @click="start(() => api.downloadUmu())">
        <IconLoader2 v-if="download.running" class="w-3.5 h-3.5 animate-spin" /><IconDownload v-else class="w-3.5 h-3.5" />{{ download.running ? 'Downloading…' : 'Download umu' }}
      </button>
      <button class="ui-card-btn" :class="{ 'ui-card-btn--primary': required && !!umu.path && !runtime.ready }" :data-readiness-target="umu.path ? '' : undefined" :disabled="!umu.path || runtime.running || download.running" @click="start(() => api.prepareUmuRuntime())">{{ runtime.ready ? 'Update runtime' : 'Prepare runtime' }}</button>
      <button v-if="runtime.running" class="ui-card-btn" @click="start(() => api.cancelUmuRuntimePrepare())">Cancel</button>
      <button class="ui-card-btn" @click="refresh"><IconRefresh class="w-3.5 h-3.5" />Recheck</button>
    </div>
  </SettingsGroup>
</template>

<style scoped>
.umu-log { max-height: 12rem; overflow: auto; white-space: pre-wrap; word-break: break-word; margin-top: .25rem; padding: .5rem; border-radius: .375rem; border: 1px solid var(--border); background: var(--bg-inset); color: var(--text-secondary); }
.umu-bar { position: relative; height: .35rem; border-radius: 9999px; background: var(--bg-overlay); overflow: hidden; }
.umu-bar-fill { height: 100%; border-radius: inherit; background: var(--brand); transition: width var(--motion-base) ease; }
.umu-bar-fill.indeterminate { width: 35%; animation: umu-slide 1.2s ease-in-out infinite; }
@keyframes umu-slide { from { transform: translateX(-100%); } to { transform: translateX(290%); } }
@media (prefers-reduced-motion: reduce) { .umu-bar-fill.indeterminate { animation: none; width: 100%; opacity: .5; } }
</style>
