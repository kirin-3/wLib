<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { api, type SteamMode, type SteamStatus } from "../services/api";
import { notify, notifyError } from "../utils/toast";

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
  <section class="settings-card rounded-xl p-6 space-y-4">
    <h3 class="text-lg font-semibold" style="color:var(--text-primary)">Steam</h3>
    <p v-if="!status" class="text-sm" style="color:var(--text-secondary)">Checking Steam…</p>
    <template v-else>
      <p v-if="!status.found" class="text-sm" style="color:var(--text-secondary)">
        {{ status.flatpak_steam ? "Flatpak Steam is not supported." : "Steam was not found." }}
      </p>
      <template v-else>
        <p class="text-sm" style="color:var(--text-secondary)">
          Steam {{ status.running ? "running" : "closed" }} · {{ status.deck ? "Steam Deck" : "Desktop" }} · Gaming Mode {{ status.gaming_mode ? "active" : "inactive" }}
        </p>
        <label v-if="status.accounts.length" class="flex items-center gap-3 text-sm">Account
          <select :value="status.account_id" class="steam-select" @change="setAccount">
            <option v-for="account in status.accounts" :key="account" :value="account">{{ account }}</option>
          </select>
        </label>
        <label class="flex items-center gap-3 text-sm">Games in Steam
          <select :value="status.mode" class="steam-select" :disabled="!status.account_id" @change="setMode">
            <option value="off">Off</option><option value="all-installed">All installed games</option><option value="marked">Only marked games</option>
          </select>
        </label>
        <div class="flex flex-wrap gap-2">
          <button class="steam-button" :disabled="!status.account_id" @click="addWlib">Add wLib to Steam</button>
          <button class="steam-button" :disabled="!status.account_id || status.sync.running" @click="sync">Sync now</button>
        </div>
        <p v-if="status.sync.running" class="text-sm">Syncing…</p>
        <p v-else-if="status.sync.error" class="text-sm" style="color:var(--text-secondary)">{{ status.sync.error }}</p>
        <p v-else-if="status.sync.success" class="text-sm" style="color:var(--text-secondary)">
          Added {{ status.sync.added || 0 }}, updated {{ status.sync.updated || 0 }}, removed {{ status.sync.removed || 0 }}<span v-if="status.sync.pending"> · {{ status.sync.pending }} waiting for Steam to close</span>
        </p>
        <p v-if="status.running" class="text-xs" style="color:var(--text-secondary)">Changes are applied after Steam closes.</p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.steam-button, .steam-select { padding: .4rem .7rem; border-radius: .5rem; border: 1px solid var(--border); background: var(--bg-raised); color: var(--text-primary); font-size: .8rem; }
.steam-button { cursor: pointer; }
.steam-button:hover { border-color: var(--brand); }
.steam-button:disabled, .steam-select:disabled { opacity: .5; cursor: default; }
</style>
