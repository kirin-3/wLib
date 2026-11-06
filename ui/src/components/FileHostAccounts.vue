<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useSettingsRowVisibility } from "../utils/settingsVisibility";
import { onMounted, ref } from "vue";
import { IconCircleCheck, IconCloudDownload, IconLoader2 } from "@tabler/icons-vue";
import { api, onWebviewReady, type DownloadAccounts } from "../services/api";
import { notify, notifyError } from "../utils/toast";

const settingsRowVisible = useSettingsRowVisibility();

const accounts = ref<DownloadAccounts | null>(null);
const pixeldrainKey = ref("");
const megaEmail = ref("");
const megaPassword = ref("");
const megaCode = ref("");
const megaNeedsCode = ref(false);
const busy = ref<"" | "pixeldrain" | "mega">("");

const refresh = async () => {
  try { accounts.value = await api.getDownloadAccounts(); }
  catch (error) { console.error("Could not load file host accounts", error); }
};

const run = async (host: "pixeldrain" | "mega", action: () => Promise<void>) => {
  busy.value = host;
  try { await action(); }
  catch (error) { notifyError(error instanceof Error ? error.message : String(error)); }
  finally { busy.value = ""; await refresh(); }
};

const connectPixeldrain = () => run("pixeldrain", async () => {
  const result = await api.connectPixeldrainAccount(pixeldrainKey.value);
  if (!result.success) throw new Error(result.error || "Could not connect Pixeldrain");
  pixeldrainKey.value = "";
  notify(`Pixeldrain connected as ${result.username}.`, "success");
});
const disconnectPixeldrain = () => run("pixeldrain", async () => {
  const result = await api.disconnectPixeldrainAccount();
  if (!result.success) throw new Error(result.error || "Could not remove the API key");
});

const connectMega = () => run("mega", async () => {
  const result = await api.connectMegaAccount(megaEmail.value, megaPassword.value, megaNeedsCode.value ? megaCode.value : "");
  if (!result.success) {
    if (result.needs_code) {
      megaNeedsCode.value = true;
      notify("Enter the code from your authenticator app.", "info");
      return;
    }
    throw new Error(result.error || "Could not sign in to MEGA");
  }
  megaPassword.value = "";
  megaCode.value = "";
  megaNeedsCode.value = false;
  notify(`Signed in to MEGA as ${result.email}.`, "success");
});
const disconnectMega = () => run("mega", async () => {
  const result = await api.disconnectMegaAccount();
  if (!result.success) throw new Error(result.error || "Could not sign out of MEGA");
});

const openPage = (url: string) => { void api.openInBrowser(url); };

onMounted(() => onWebviewReady(() => { void refresh(); }));
</script>

<template>
  <section id="file-hosts" class="settings-card rounded-xl p-6 space-y-5" aria-labelledby="file-hosts-heading">
    <h3 id="file-hosts-heading" class="ui-section-heading text-lg font-semibold" style="color:var(--text-primary)">
      <IconCloudDownload class="ui-section-icon" />
      File host accounts
    </h3>
    <p data-settings-row="file-hosts-intro" data-settings-bound v-show="settingsRowVisible('file-hosts-intro')" class="text-xs" style="color:var(--text-muted)">
      In-app update downloads use these accounts, so your premium speed and transfer quota apply. They stay on this device and are never included in backups or sync.
    </p>

    <div data-settings-row="file-hosts-pixeldrain" data-keywords="pixeldrain api key premium" data-settings-bound v-show="settingsRowVisible('file-hosts-pixeldrain')" class="ui-card-row p-4 rounded-lg space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h4 class="text-sm font-medium" style="color:var(--text-primary)">Pixeldrain</h4>
          <p v-if="accounts?.pixeldrain.connected" class="text-xs mt-1 flex items-center gap-1" style="color:var(--text-muted)">
            <IconCircleCheck class="w-3.5 h-3.5" />Connected as {{ accounts.pixeldrain.username }}
          </p>
          <p v-else class="text-xs mt-1" style="color:var(--text-muted)">
            Paste an API key from
            <button type="button" class="underline" @click="openPage('https://pixeldrain.com/user/api_keys')">pixeldrain.com/user/api_keys</button>.
          </p>
        </div>
        <button v-if="accounts?.pixeldrain.connected" class="ui-card-btn" :disabled="busy === 'pixeldrain'" @click="disconnectPixeldrain">Disconnect</button>
      </div>
      <form v-if="accounts && !accounts.pixeldrain.connected" class="flex flex-wrap gap-2" @submit.prevent="connectPixeldrain">
        <input v-model="pixeldrainKey" type="password" autocomplete="off" class="host-input flex-1 min-w-[12rem] font-mono" placeholder="API key" aria-label="Pixeldrain API key" />
        <button type="submit" class="ui-card-btn ui-card-btn--primary" :disabled="busy === 'pixeldrain' || !pixeldrainKey.trim()">
          <IconLoader2 v-if="busy === 'pixeldrain'" class="w-3.5 h-3.5 animate-spin" />Connect
        </button>
      </form>
    </div>

    <div data-settings-row="file-hosts-mega" data-keywords="mega account sign in login premium pro" data-settings-bound v-show="settingsRowVisible('file-hosts-mega')" class="ui-card-row p-4 rounded-lg space-y-3">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h4 class="text-sm font-medium" style="color:var(--text-primary)">MEGA</h4>
          <p v-if="accounts?.mega.connected" class="text-xs mt-1 flex items-center gap-1" style="color:var(--text-muted)">
            <IconCircleCheck class="w-3.5 h-3.5" />Signed in as {{ accounts.mega.email }}
          </p>
          <p v-else class="text-xs mt-1" style="color:var(--text-muted)">
            wLib keeps only the session, not your password. Signing out ends the session.
          </p>
        </div>
        <button v-if="accounts?.mega.connected" class="ui-card-btn" :disabled="busy === 'mega'" @click="disconnectMega">Sign out</button>
      </div>
      <form v-if="accounts && !accounts.mega.connected" class="grid gap-2 sm:grid-cols-2" @submit.prevent="connectMega">
        <input v-model="megaEmail" type="email" autocomplete="username" class="host-input" placeholder="Email" aria-label="MEGA email" />
        <input v-model="megaPassword" type="password" autocomplete="current-password" class="host-input" placeholder="Password" aria-label="MEGA password" />
        <input v-if="megaNeedsCode" v-model="megaCode" inputmode="numeric" autocomplete="one-time-code" maxlength="6" class="host-input font-mono" placeholder="6-digit code" aria-label="MEGA two-factor code" />
        <div class="flex items-center sm:col-span-2">
          <button type="submit" class="ui-card-btn ui-card-btn--primary" :disabled="busy === 'mega' || !megaEmail.trim() || !megaPassword || (megaNeedsCode && megaCode.trim().length !== 6)">
            <IconLoader2 v-if="busy === 'mega'" class="w-3.5 h-3.5 animate-spin" />Sign in
          </button>
        </div>
      </form>
    </div>
  </section>
</template>

<style scoped>
.host-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.5rem 0.875rem;
  font-size: 0.875rem;
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.host-input::placeholder { color: var(--text-muted); }
.host-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}
</style>
