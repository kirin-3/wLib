<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue';
import { IconAlertTriangle, IconDownload, IconCircleCheck, IconExternalLink, IconLoader2, IconRefresh } from '@tabler/icons-vue';
import { api, onWebviewReady } from '../services/api';
import type { LocaleEmulatorStatus } from '../services/api';
import { LOCALE_EMULATOR_RELEASES_URL, localeEmulatorStatusMessage, saveLocaleEmulatorPath } from '../utils/localeEmulator';
import { useSettingsRowVisibility } from '../utils/settingsVisibility';
import { notifyError } from '../utils/toast';
import SettingsGroup from './settings/SettingsGroup.vue';
import SettingsHelpLink from './settings/SettingsHelpLink.vue';

const settingsRowVisible = useSettingsRowVisibility();

const status = ref<LocaleEmulatorStatus | null>(null);
const customPath = ref('');
const busy = ref(false);
const statusText = computed(() => localeEmulatorStatusMessage(status.value));
// Optional: only games with the Japanese locale option need it, so a missing install is neutral.
const statusTone = computed(() => !status.value ? 'pending' : status.value.status === 'ready' ? 'ok' : status.value.status === 'unusable' ? 'warn' : 'optional');
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
  <SettingsGroup id="windows-tools" title="Japanese locale">
    <template #actions><SettingsHelpLink page="Launching-Games" anchor="windows" /></template>
    <div data-settings-row="windows-tools-static-1" data-settings-bound data-keywords="locale emulator japanese leproc mojibake" v-show="settingsRowVisible('windows-tools-static-1')"
      class="ui-card-row space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-4">
        <div class="flex items-start gap-3 min-w-0 grow basis-56" role="status">
          <span class="ui-status-badge" :class="statusTone === 'ok' ? 'ui-status-badge--ok' : statusTone === 'warn' ? 'ui-status-badge--warn' : ''" aria-hidden="true">
            <IconLoader2 v-if="statusTone === 'pending'" class="w-4 h-4 animate-spin" />
            <IconCircleCheck v-else-if="statusTone === 'ok'" class="w-4 h-4" />
            <IconAlertTriangle v-else-if="statusTone === 'warn'" class="w-4 h-4" />
            <IconDownload v-else class="w-4 h-4" />
          </span>
          <div class="min-w-0">
            <h5 class="text-sm font-medium" style="color: var(--text-primary)">Locale Emulator</h5>
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
          <h5 class="text-sm font-medium" style="color: var(--text-primary)">LEProc.exe</h5>
          <p class="text-xs mt-1 font-mono break-all" style="color: var(--text-muted)" data-testid="locale-emulator-path">{{ customPath || 'Use the detected installation' }}</p>
        </div>
        <div class="flex flex-wrap gap-2 shrink-0">
          <button class="ui-card-btn" @click="openPage"><IconExternalLink class="w-3.5 h-3.5" />Open official page</button>
          <button class="ui-card-btn" :disabled="busy" @click="choosePath">Choose LEProc.exe</button>
          <button v-if="customPath" class="ui-card-btn" :disabled="busy" @click="savePath('')">Clear</button>
        </div>
      </div>
    </div>
  </SettingsGroup>
</template>

