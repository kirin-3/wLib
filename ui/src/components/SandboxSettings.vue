<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { confirmAction } from "../utils/confirm";
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { IconAlertTriangle, IconCircleCheck, IconCopy, IconExternalLink, IconLoader2, IconRefresh, IconShieldLock } from '@tabler/icons-vue';
import { api, onWebviewReady } from '../services/api';
import type { SandboxStatus, SandboxInstallInfo } from '../services/api';
import { sandboxStatusMessage } from '../utils/sandbox';
import { copyText } from '../utils/copyText';
import { notify, notifyError } from '../utils/toast';

const settingsRowVisible = useSettingsRowVisibility();

const status = ref<SandboxStatus | null>(null);
const info = ref<SandboxInstallInfo | null>(null);
const platform = ref('unsupported');
const defaultEnabled = ref(false);
const customPath = ref('');
const busy = ref(false);
const installing = ref(false);
const error = ref('');
const statusText = computed(() => sandboxStatusMessage(status.value));
const statusTone = computed(() => !status.value ? 'pending' : status.value.status === 'ready' ? 'ok' : 'warn');
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
const recheck = async () => { status.value = await api.recheckSandboxStatus(); };
const poll = async () => {
  if (disposed) return;
  try {
    const progress = await api.getSandboxInstallStatus();
    if (disposed) return;
    installing.value = progress.running;
    error.value = progress.error;
    if (progress.running) timer = setTimeout(() => { void poll(); }, 1000);
    else await recheck();
  } catch (e) { error.value = String(e); installing.value = false; }
};
const install = async () => {
  if (!info.value) return;
  installing.value = true;
  try {
    const result = await api.installSandboxTool(info.value.tool);
    if (!result.success) throw new Error(result.error || 'Could not install sandbox tool.');
    await poll();
  } catch (e) { installing.value = false; error.value = String(e); }
};
const saveDefault = async (event: Event) => {
  const enabled = (event.target as HTMLInputElement).checked;
  busy.value = true;
  try {
    const result = await api.saveSettings({sandbox_new_games: enabled});
    if (!result.success) throw new Error(result.error);
    defaultEnabled.value = enabled;
  } catch (e) { (event.target as HTMLInputElement).checked = defaultEnabled.value; notifyError(String(e)); }
  finally { busy.value = false; }
};
const setAll = async (enabled: boolean) => {
  if (!(await confirmAction({ title: `${enabled ? 'Enable' : 'Disable'} sandboxing`, message: `${enabled ? 'Enable' : 'Disable'} sandboxing for every library game? Running games keep their current mode until their next launch.`, confirmLabel: enabled ? 'Enable' : 'Disable' }))) return;
  busy.value = true;
  try {
    const result = await api.setSandboxForAllGames(enabled);
    if (!result.success) throw new Error(result.error);
    notify(`Sandbox preference changed for ${result.changed ?? 0} games.`, 'success');
  } catch (e) { notifyError(String(e)); }
  finally { busy.value = false; }
};
const savePath = async (path: string) => {
  const result = await api.saveSettings({sandboxie_custom_path: path});
  if (!result.success) { notifyError(result.error || 'Could not save Sandboxie path.'); return; }
  customPath.value = path;
  await recheck();
};
const choosePath = async () => {
  const path = await api.browseFile(customPath.value);
  if (path) await savePath(path);
};
const copyCommand = async () => {
  if (info.value && await copyText(info.value.command)) notify('Install command copied.', 'success');
  else notifyError('Could not copy the command.');
};
onMounted(() => onWebviewReady(() => {
  void (async () => {
    try {
      const [state, installInfo, settings, capabilities] = await Promise.all([api.getSandboxStatus(),api.getSandboxInstallInfo(),api.getSettings(),api.getPlatformCapabilities()]);
      if (disposed) return;
      status.value = state; info.value = installInfo; platform.value = capabilities.platform;
      defaultEnabled.value = settings.sandbox_new_games; customPath.value = settings.sandboxie_custom_path;
      await poll();
    } catch (e) { error.value = String(e); }
  })();
}));
onUnmounted(() => { disposed = true; if (timer) clearTimeout(timer); });
</script>

<template>
  <section id="sandbox" class="settings-card rounded-xl p-6 space-y-5" aria-labelledby="sandbox-heading">
    <h3 id="sandbox-heading" class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
      <IconShieldLock class="ui-section-icon" />
      Sandbox
    </h3>

    <div data-settings-row="sandbox-static-0" data-settings-bound v-show="settingsRowVisible('sandbox-static-0')" class="ui-card-row flex items-center justify-between gap-4 p-4 rounded-lg">
      <div class="flex items-start gap-3 min-w-0" role="status">
        <span class="ui-status-badge" :class="statusTone === 'ok' ? 'ui-status-badge--ok' : statusTone === 'warn' ? 'ui-status-badge--warn' : ''" aria-hidden="true">
          <IconLoader2 v-if="statusTone === 'pending'" class="w-4 h-4 animate-spin" />
          <IconCircleCheck v-else-if="statusTone === 'ok'" class="w-4 h-4" />
          <IconAlertTriangle v-else class="w-4 h-4" />
        </span>
        <div class="min-w-0">
          <h4 class="text-sm font-medium" style="color: var(--text-primary)">Sandbox tool</h4>
          <p class="text-xs mt-1 break-words" style="color: var(--text-muted)">{{ statusText }}</p>
        </div>
      </div>
      <button class="ui-card-btn shrink-0" :disabled="installing" @click="recheck().catch(e => notifyError(String(e)))"><IconRefresh class="w-3.5 h-3.5" />Recheck</button>
    </div>

    <p data-settings-row="sandbox-static-1" data-settings-bound v-show="settingsRowVisible('sandbox-static-1')" class="text-xs" style="color: var(--text-muted)">Sandboxing gives each game separate profile storage. Saves inside the game folder still write to that folder.</p>
    <p data-settings-row="sandbox-static-2" data-settings-bound v-show="settingsRowVisible('sandbox-static-2')" v-if="platform === 'linux'" class="text-xs !mt-2" style="color: var(--text-muted)">Sandboxed Proton games make a temporary runtime copy for each launch, using extra disk space and startup time. Cheat Engine auto-injection is unavailable for these games.</p>

    <div data-settings-row="sandbox-static-3" data-settings-bound v-show="settingsRowVisible('sandbox-static-3')" v-if="status?.status === 'missing' && info" class="ui-card-row p-4 rounded-lg space-y-3">
      <h4 class="text-sm font-medium" style="color: var(--text-primary)">Install the sandbox tool</h4>
      <div v-if="info.command" class="command flex items-center gap-2 rounded-lg pl-3 pr-1 py-1">
        <code class="text-xs font-mono break-all flex-1 min-w-0">{{ info.command }}</code>
        <button class="icon-btn" title="Copy command" aria-label="Copy install command" @click="copyCommand"><IconCopy class="w-4 h-4" /></button>
      </div>
      <p v-else-if="platform === 'linux'" class="text-xs" style="color: var(--text-muted)">Install bubblewrap with your system package manager, then choose Recheck.</p>
      <div v-if="info.can_install || info.download_url" class="flex flex-wrap gap-2">
        <button v-if="info.can_install" class="ui-card-btn ui-card-btn--primary" :disabled="installing" @click="install">
          <IconLoader2 v-if="installing" class="w-3.5 h-3.5 animate-spin" />{{ installing ? 'Installing…' : 'Install' }}
        </button>
        <button v-if="info.download_url" class="ui-card-btn" @click="api.openInBrowser(info.download_url)"><IconExternalLink class="w-3.5 h-3.5" />Official download page</button>
      </div>
    </div>

    <p data-settings-row="sandbox-static-4" data-settings-bound v-show="settingsRowVisible('sandbox-static-4')" v-if="installing" class="ui-callout" role="status">
      <IconLoader2 class="w-4 h-4 shrink-0 animate-spin" style="color: var(--brand)" />
      Installing sandbox tool. Complete the system authentication prompt if one appears.
    </p>
    <p data-settings-row="sandbox-static-5" data-settings-bound v-show="settingsRowVisible('sandbox-static-5')" v-if="error" class="ui-callout ui-callout--danger" role="alert">
      <IconAlertTriangle class="w-4 h-4 shrink-0" />
      <span class="break-words min-w-0">{{ error }}</span>
    </p>

    <div data-settings-row="sandbox-static-6" data-settings-bound v-show="settingsRowVisible('sandbox-static-6')" v-if="platform === 'windows'" class="ui-card-row flex flex-wrap items-center justify-between gap-4 p-4 rounded-lg">
      <div class="min-w-0">
        <h4 class="text-sm font-medium" style="color: var(--text-primary)">Custom Sandboxie Start.exe</h4>
        <p class="text-xs mt-1 font-mono break-all" style="color: var(--text-muted)">{{ customPath || 'Use the detected installation' }}</p>
      </div>
      <div class="flex gap-2 shrink-0">
        <button class="ui-card-btn" @click="choosePath">Choose Start.exe</button>
        <button v-if="customPath" class="ui-card-btn" @click="savePath('')">Clear</button>
      </div>
    </div>

    <div data-settings-row="sandbox-static-7" data-settings-bound v-show="settingsRowVisible('sandbox-static-7')" class="ui-card-row flex items-center justify-between gap-4 p-4 rounded-lg">
      <div>
        <h4 class="text-sm font-medium" style="color: var(--text-primary)">Sandbox new games</h4>
        <p class="text-xs mt-1" style="color: var(--text-muted)">Games you add from now on start with sandboxing turned on.</p>
      </div>
      <label class="relative inline-flex items-center cursor-pointer shrink-0">
        <input type="checkbox" role="switch" class="sr-only peer" aria-label="Sandbox new games" :checked="defaultEnabled" :disabled="busy" @change="saveDefault" />
        <div class="ui-toggle"></div>
      </label>
    </div>

    <div data-settings-row="sandbox-static-8" data-settings-bound v-show="settingsRowVisible('sandbox-static-8')" class="ui-card-row flex flex-wrap items-center justify-between gap-4 p-4 rounded-lg">
      <div>
        <h4 class="text-sm font-medium" style="color: var(--text-primary)">Existing games</h4>
        <p class="text-xs mt-1" style="color: var(--text-muted)">Change sandboxing for every game in your library at once.</p>
      </div>
      <div class="flex flex-wrap gap-2 shrink-0">
        <button class="ui-card-btn" :disabled="busy" @click="setAll(true)">Enable for all games</button>
        <button class="ui-card-btn" :disabled="busy" @click="setAll(false)">Disable for all games</button>
      </div>
    </div>
  </section>
</template>

<style scoped>
section { color: var(--text-primary); background: var(--bg-surface); border: 1px solid var(--border); scroll-margin-top: 1.5rem; }
.command { background: var(--bg-inset); border: 1px solid var(--border); color: var(--text-primary); }
.icon-btn { display: inline-flex; padding: 0.4rem; border-radius: 0.375rem; color: var(--text-muted); }
.icon-btn:hover { color: var(--text-primary); background: var(--bg-overlay); }
</style>
