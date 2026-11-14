<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { onMounted, onUnmounted, ref } from "vue";
import { IconAlertTriangle, IconBrandSteam, IconCircleCheck, IconLoader2, IconPlus, IconRefresh } from "@tabler/icons-vue";
import { api, type SteamMode, type SteamStatus } from "../services/api";
import { notify, notifyError } from "../utils/toast";

const settingsRowVisible = useSettingsRowVisibility();

const status = ref<SteamStatus | null>(null);
let timer: ReturnType<typeof setInterval> | null = null;
const refresh = async () => {
  try { status.value = await api.getSteamStatus(); }
  catch (error) { console.error("Could not load Steam status", error); }
};
const setMode = async (event: Event) => {
  const result = await api.setSteamGamesMode((event.target as HTMLSelectElement).value as SteamMode);
  if (!result.success) notifyError(result.error || "Could not change Steam mode");
  await refresh();
};
const setAccount = async (event: Event) => {
  const result = await api.setSteamAccount((event.target as HTMLSelectElement).value);
  if (!result.success) notifyError(result.error || "Could not select Steam account");
  await refresh();
};
const addWlib = async () => {
  const result = await api.addWlibToSteam();
  if (!result.success) notifyError(result.error || "Could not add wLib to Steam");
  else if (result.queued) notify("Adding wLib to Steam; if Steam is running, the request will wait until it closes.", "info");
  else if (result.pending) notify("wLib will be added after Steam closes.", "info");
  else notify(result.already_exists ? "wLib is already in Steam." : "wLib was added to Steam.", "success");
  await refresh();
};
const sync = async () => {
  const result = await api.syncSteamShortcuts();
  if (!result.success) notifyError(result.error || "Could not sync Steam shortcuts");
  else notify("Steam synchronization started.", "info");
  await refresh();
};
onMounted(() => { void refresh(); timer = setInterval(refresh, 3000); });
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<template>
  <section id="steam" class="settings-card rounded-xl p-6 space-y-5" aria-labelledby="steam-heading">
    <h3 id="steam-heading" class="ui-section-heading text-lg font-semibold" style="color:var(--text-primary)">
      <IconBrandSteam class="ui-section-icon" />
      Steam
    </h3>
    <div data-settings-row="steam-static-0" data-settings-bound v-show="settingsRowVisible('steam-static-0')" v-if="!status" class="ui-card-row flex items-center gap-3 text-sm" style="color:var(--text-secondary)">
      <IconLoader2 class="w-4 h-4 animate-spin" />Checking Steam…
    </div>
    <template v-else>
      <div class="ui-card-row flex items-start gap-3" role="status">
        <span class="ui-status-badge" :class="status.found ? 'ui-status-badge--ok' : 'ui-status-badge--warn'" aria-hidden="true">
          <IconCircleCheck v-if="status.found" class="w-4 h-4" /><IconAlertTriangle v-else class="w-4 h-4" />
        </span>
        <div class="min-w-0">
          <h4 class="text-sm font-medium" style="color:var(--text-primary)">
            {{ !status.found ? (status.flatpak_steam ? "Flatpak Steam is not supported." : "Steam was not found.") : `Steam ${status.running ? "running" : "closed"}` }}
          </h4>
          <p v-if="status.found" class="text-xs mt-1" style="color:var(--text-muted)">
            {{ status.deck ? "Steam Deck" : "Desktop" }} · Gaming Mode {{ status.gaming_mode ? "active" : "inactive" }}<span v-if="status.running"> · Changes are applied after Steam closes.</span>
          </p>
        </div>
      </div>
      <template v-if="status.found">
        <div v-if="status.accounts.length" class="ui-card-row flex flex-wrap items-center justify-between gap-4">
          <div>
            <label for="steam-account" class="text-sm font-medium" style="color:var(--text-primary)">Account</label>
            <p class="text-xs mt-1" style="color:var(--text-muted)">The Steam user whose library gets the shortcuts.</p>
          </div>
          <select id="steam-account" :value="status.account_id" class="ui-card-select shrink-0" @change="setAccount">
            <option v-for="account in status.accounts" :key="account" :value="account">{{ account }}</option>
          </select>
        </div>
        <div class="ui-card-row flex flex-wrap items-center justify-between gap-4">
          <div>
            <label for="steam-mode" class="text-sm font-medium" style="color:var(--text-primary)">Games in Steam</label>
            <p class="text-xs mt-1" style="color:var(--text-muted)">Which wLib games appear as Steam shortcuts.</p>
          </div>
          <select id="steam-mode" :value="status.mode" class="ui-card-select shrink-0" :disabled="!status.account_id" @change="setMode">
            <option value="off">Off</option><option value="all-installed">All installed games</option><option value="marked">Only marked games</option>
          </select>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <button class="ui-card-btn" :disabled="!status.account_id" @click="addWlib"><IconPlus class="w-3.5 h-3.5" />Add wLib to Steam</button>
          <button class="ui-card-btn ui-card-btn--primary" :disabled="!status.account_id || status.sync.running" @click="sync">
            <IconLoader2 v-if="status.sync.running" class="w-3.5 h-3.5 animate-spin" /><IconRefresh v-else class="w-3.5 h-3.5" />{{ status.sync.running ? "Syncing…" : "Sync now" }}
          </button>
          <p v-if="!status.sync.running && status.sync.success" class="text-xs ml-1" style="color:var(--text-muted)">
            Added {{ status.sync.added || 0 }}, updated {{ status.sync.updated || 0 }}, removed {{ status.sync.removed || 0 }}<span v-if="status.sync.pending"> · {{ status.sync.pending }} waiting for Steam to close</span>
          </p>
        </div>
        <p v-if="!status.sync.running && status.sync.error" class="ui-callout ui-callout--danger" role="alert">
          <IconAlertTriangle class="w-4 h-4 shrink-0" /><span class="break-words min-w-0">{{ status.sync.error }}</span>
        </p>
      </template>
    </template>
  </section>
</template>
