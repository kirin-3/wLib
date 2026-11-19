<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { IconAlertTriangle, IconBrandWindows, IconCircleCheck, IconExternalLink, IconLoader2, IconRefresh } from '@tabler/icons-vue';
import { api, onWebviewReady } from '../services/api';
import type { LocaleEmulatorStatus } from '../services/api';
import { LOCALE_EMULATOR_RELEASES_URL, localeEmulatorStatusMessage, saveLocaleEmulatorPath } from '../utils/localeEmulator';
import { useSettingsRowVisibility } from '../utils/settingsVisibility';
import { notifyError } from '../utils/toast';

const settingsRowVisible = useSettingsRowVisibility();

const status = ref<LocaleEmulatorStatus | null>(null);
const customPath = ref('');
const busy = ref(false);
const statusText = computed(() => localeEmulatorStatusMessage(status.value));
const statusTone = computed(() => !status.value ? 'pending' : status.value.status === 'ready' ? 'ok' : 'warn');
let disposed = false;

const recheck = async () => {
  busy.value = true;
  try {
    const next = await api.recheckLocaleEmulatorStatus();
    if (!disposed) status.value = next;
  } catch (e) { notifyError(String(e)); }
  finally { busy.value = false; }
};
const savePath = async (path: string) => {
  busy.value = true;
  try {
    const result = await saveLocaleEmulatorPath(
      { saveSettings: (payload) => api.saveSettings(payload), recheck: () => api.recheckLocaleEmulatorStatus() },
      path,
    );
    if (!result.ok) { notifyError(result.error); return; }
    customPath.value = path;
    status.value = result.status;
  } catch (e) { notifyError(String(e)); }
  finally { busy.value = false; }
};
const choosePath = async () => {
  const path = await api.browseFile(customPath.value);
  if (path) await savePath(path);
};
const openPage = () => { void api.openInBrowser(LOCALE_EMULATOR_RELEASES_URL); };

onMounted(() => onWebviewReady(() => {
  void (async () => {
    try {
      const [state, settings] = await Promise.all([api.getLocaleEmulatorStatus(), api.getSettings()]);
      if (disposed) return;
      status.value = state;
      customPath.value = settings.locale_emulator_path;
    } catch (e) { notifyError(String(e)); }
  })();
}));
onUnmounted(() => { disposed = true; });
</script>

<template>
  <section id="windows-tools" class="settings-card rounded-xl p-6 space-y-5" aria-labelledby="windows-tools-heading">
    <h3 id="windows-tools-heading" class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
      <IconBrandWindows class="ui-section-icon" />
      Windows tools
    </h3>

    <p data-settings-row="windows-tools-static-0" data-settings-bound data-keywords="windows wine proton" v-show="settingsRowVisible('windows-tools-static-0')"
      class="ui-card-row text-sm" style="color: var(--text-secondary)">
      Windows games launch directly. Proton, Wine, Winetricks, Wine
      prefixes, and Linux runtime installers are not used on Windows.
    </p>

    <div data-settings-row="windows-tools-static-1" data-settings-bound data-keywords="locale emulator japanese leproc mojibake" v-show="settingsRowVisible('windows-tools-static-1')"
      class="ui-card-row space-y-3">
      <div class="flex items-center justify-between gap-4">
        <div class="flex items-start gap-3 min-w-0" role="status">
          <span class="ui-status-badge" :class="statusTone === 'ok' ? 'ui-status-badge--ok' : statusTone === 'warn' ? 'ui-status-badge--warn' : ''" aria-hidden="true">
            <IconLoader2 v-if="statusTone === 'pending'" class="w-4 h-4 animate-spin" />
            <IconCircleCheck v-else-if="statusTone === 'ok'" class="w-4 h-4" />
            <IconAlertTriangle v-else class="w-4 h-4" />
          </span>
          <div class="min-w-0">
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Locale Emulator</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Starts games that have "Run with Locale Emulator" on with a Japanese locale, so their text shows correctly.
              Some 64-bit games do not work with it; turn the option off for a game that does not start.
            </p>
            <p class="text-xs mt-1 break-all" style="color: var(--text-muted)" data-testid="locale-emulator-status">{{ statusText }}</p>
          </div>
        </div>
        <button class="ui-card-btn shrink-0" :disabled="busy" @click="recheck"><IconRefresh class="w-3.5 h-3.5" />Check again</button>
      </div>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="min-w-0">
          <h4 class="text-sm font-medium" style="color: var(--text-primary)">LEProc.exe</h4>
          <p class="text-xs mt-1 font-mono break-all" style="color: var(--text-muted)" data-testid="locale-emulator-path">{{ customPath || 'Use the detected installation' }}</p>
        </div>
        <div class="flex flex-wrap gap-2 shrink-0">
          <button class="ui-card-btn" @click="openPage"><IconExternalLink class="w-3.5 h-3.5" />Open official page</button>
          <button class="ui-card-btn" :disabled="busy" @click="choosePath">Choose LEProc.exe</button>
          <button v-if="customPath" class="ui-card-btn" :disabled="busy" @click="savePath('')">Clear</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
section { color: var(--text-primary); background: var(--bg-surface); border: 1px solid var(--border); scroll-margin-top: 1.5rem; }
</style>
