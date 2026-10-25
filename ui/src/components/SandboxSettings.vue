<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';
import { IconShieldLock } from '@tabler/icons-vue';
import { api, onWebviewReady } from '../services/api';
import type { SandboxStatus, SandboxInstallInfo } from '../services/api';
import { sandboxStatusMessage } from '../utils/sandbox';
import { copyText } from '../utils/copyText';
import { notify, notifyError } from '../utils/toast';

const route = useRoute();
const status = ref<SandboxStatus | null>(null);
const info = ref<SandboxInstallInfo | null>(null);
const platform = ref('unsupported');
const defaultEnabled = ref(false);
const customPath = ref('');
const busy = ref(false);
const installing = ref(false);
const error = ref('');
const statusText = computed(() => sandboxStatusMessage(status.value));
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
  if (!confirm(`${enabled ? 'Enable' : 'Disable'} sandboxing for every library game? Running games keep their current mode until their next launch.`)) return;
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
watch(() => route.hash, async hash => {
  if (hash === '#sandbox') { await nextTick(); document.getElementById('sandbox')?.scrollIntoView({block:'start'}); }
}, {immediate:true});
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
    <div class="flex items-start justify-between gap-4">
      <p class="text-sm" role="status">{{ statusText }}</p>
      <button class="sandbox-button" :disabled="installing" @click="recheck().catch(e => notifyError(String(e)))">Recheck</button>
    </div>
    <p class="text-sm" style="color:var(--text-secondary)">Sandboxing gives each game separate profile storage. Saves inside the game folder still write to that folder.</p>
    <div v-if="status?.status === 'missing' && info" class="space-y-3">
      <div v-if="info.command" class="flex flex-wrap items-center gap-2">
        <code class="text-sm break-all">{{ info.command }}</code>
        <button class="sandbox-button" @click="copyCommand">Copy</button>
      </div>
      <p v-else-if="platform === 'linux'" class="text-sm">Install bubblewrap with your system package manager, then choose Recheck.</p>
      <div class="flex flex-wrap gap-2">
        <button v-if="info.can_install" class="sandbox-button" :disabled="installing" @click="install">{{ installing ? 'Installing…' : 'Install' }}</button>
        <button v-if="info.download_url" class="sandbox-button" @click="api.openInBrowser(info.download_url)">Official download page</button>
      </div>
    </div>
    <p v-if="installing" class="text-sm" role="status">Installing sandbox tool. Complete the system authentication prompt if one appears.</p>
    <p v-if="error" class="text-sm" role="alert" style="color:var(--danger, #dc2626)">{{ error }}</p>
    <div v-if="platform === 'windows'" class="space-y-2">
      <p class="text-sm font-medium">Custom Sandboxie Start.exe</p>
      <div class="flex flex-wrap items-center gap-2">
        <code class="text-xs break-all">{{ customPath || 'Use the detected installation' }}</code>
        <button class="sandbox-button" @click="choosePath">Choose Start.exe</button>
        <button v-if="customPath" class="sandbox-button" @click="savePath('')">Clear</button>
      </div>
    </div>
    <label class="flex items-center justify-between gap-4 text-sm">
      <span>Sandbox new games</span>
      <input type="checkbox" role="switch" class="sandbox-switch" :checked="defaultEnabled" :disabled="busy" @change="saveDefault" />
    </label>
    <div class="flex flex-wrap gap-2">
      <button class="sandbox-button" :disabled="busy" @click="setAll(true)">Enable for all games</button>
      <button class="sandbox-button" :disabled="busy" @click="setAll(false)">Disable for all games</button>
    </div>
  </section>
</template>

<style scoped>
section { color: var(--text-primary); background: var(--bg-surface); border: 1px solid var(--border); scroll-margin-top: 1.5rem; }
.sandbox-button { padding: .4rem .7rem; border-radius: .5rem; border: 1px solid var(--border); background: var(--bg-raised); color: var(--text-primary); font-size: .8rem; cursor: pointer; }
.sandbox-button:hover { border-color: var(--brand); }
.sandbox-button:disabled { opacity: .5; cursor: default; }
input { accent-color: var(--brand); }
</style>
