<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { IconCloud, IconRefresh } from "@tabler/icons-vue";
import { api, type LibrarySyncChoice, type LibrarySyncStatus } from "../services/api";
import { useModalKeyboard } from "../utils/modalKeyboard";

const status = ref<LibrarySyncStatus>({ state: "off" });
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const location = ref<"" | "folder" | "dropbox" | "gdrive">("");
const dialog = ref<"choice" | "unlock" | "encrypt" | null>(null);
const dialogElement = ref<HTMLElement | null>(null);
const passphrase = ref("");
const confirmation = ref("");
const dialogError = ref("");
let timer: ReturnType<typeof setInterval> | undefined;
let requestVersion = 0;
let previousState = "";
let pollRunning = false;
// Only status-read failures are cleared by a later successful read; action errors stay.
let errorFromStatus = false;
let browsing = false;
const disabled = computed(() => loading.value || busy.value || status.value.state === "syncing");
const connected = computed(() => Boolean(status.value.location));
const stateLabel = computed(() => ({ off: "Sync is off", idle: "Ready to sync", syncing: "Syncing your library...", needs_choice: "Choose which library to use", needs_passphrase: "Passphrase required", error: "Sync needs attention" })[status.value.state || "off"]);
const lastSynced = computed(() => status.value.last_synced_at ? new Date(status.value.last_synced_at).toLocaleString() : "Not synced yet");

function closeDialog() {
  dialog.value = null;
  passphrase.value = confirmation.value = dialogError.value = "";
}
useModalKeyboard(dialogElement, () => dialog.value !== null, closeDialog);

function acceptStatus(result: LibrarySyncStatus, forceLocation = false) {
  if (result.state) {
    if (forceLocation || loading.value || result.location !== status.value.location) location.value = result.location || "";
    status.value = result;
    if (result.state !== previousState) {
      if (result.state === "needs_choice") dialog.value = "choice";
      if (result.state === "needs_passphrase") dialog.value = "unlock";
      previousState = result.state;
    }
  }
}

async function refreshStatus() {
  if (pollRunning || busy.value) return;
  pollRunning = true;
  const version = requestVersion;
  try {
    const result = await api.getSyncStatus();
    if (version === requestVersion) {
      acceptStatus(result);
      if (result.success === false) {
        error.value = result.error || "Could not read sync status.";
        errorFromStatus = true;
      } else if (errorFromStatus) {
        error.value = "";
        errorFromStatus = false;
      }
    }
  } catch {
    error.value = "Could not read sync status. Try again.";
    errorFromStatus = true;
  } finally {
    loading.value = false;
    pollRunning = false;
  }
}

async function act(action: () => Promise<LibrarySyncStatus>, closeOnSuccess = false) {
  if (busy.value) return;
  busy.value = true;
  requestVersion++;
  error.value = dialogError.value = "";
  errorFromStatus = false;
  try {
    const result = await action();
    if (result.success === false) {
      if (dialog.value) dialogError.value = result.error || "Sync failed. Try again.";
      else error.value = result.error || "Sync failed. Try again.";
      acceptStatus(result, true);
    } else {
      if (closeOnSuccess) closeDialog();
      acceptStatus(result, true);
    }
  } catch {
    if (dialog.value) dialogError.value = "Sync failed. Try again.";
    else error.value = "Sync failed. Try again.";
  } finally {
    busy.value = false;
    await refreshStatus();
  }
}

async function changeLocation() {
  if (!location.value) await act(() => api.disconnectSync());
}
async function browseFolder() {
  if (browsing) return;
  browsing = true;
  let folder: string | null = null;
  try {
    folder = await api.browseDirectory(status.value.folder_path || "");
  } catch {
    error.value = "Could not open the folder picker.";
  } finally {
    browsing = false;
  }
  if (folder) await act(() => api.setSyncFolder(folder));
}
function choose(choice: LibrarySyncChoice) { return act(() => api.resolveSyncChoice(choice), true); }
function toggleEncryption(event: Event) {
  (event.target as HTMLInputElement).checked = Boolean(status.value.encrypted);
  if (status.value.encrypted) void act(() => api.setSyncPassphrase(null));
  else { closeDialog(); dialog.value = "encrypt"; }
}
async function submitPassphrase() {
  if (!passphrase.value) { dialogError.value = "Enter a passphrase."; return; }
  if (dialog.value === "encrypt" && passphrase.value !== confirmation.value) { dialogError.value = "The passphrases do not match."; return; }
  const value = passphrase.value;
  await act(() => dialog.value === "unlock" ? api.unlockSync(value) : api.setSyncPassphrase(value), true);
}
async function openPrivacy() {
  try {
    const result = await api.openInBrowser("https://github.com/kirin-3/wLib/blob/master/PRIVACY.md");
    if (result.success === false) error.value = result.error || "Could not open the privacy policy.";
  } catch { error.value = "Could not open the privacy policy."; }
}
onMounted(async () => { await refreshStatus(); timer = setInterval(() => void refreshStatus(), 1500); });
onBeforeUnmount(() => { if (timer) clearInterval(timer); requestVersion++; });
</script>

<template>
  <section class="sync-panel" aria-labelledby="sync-heading" :aria-busy="loading || status.state === 'syncing'">
    <div class="sync-card">
      <h3 id="sync-heading" class="ui-section-heading text-lg font-semibold"><IconCloud class="ui-section-icon" />Library sync</h3>
      <p class="sync-description">Keep one library across your machines in a folder, your Dropbox app folder, or Google Drive.</p>
      <p v-if="status.mock" class="sync-description">Browser preview with mock data. No files are synced.</p>
      <p class="sync-description sync-note">Sync shares game metadata, ratings, notes, playtime, and general settings. Executable paths and launch options stay on this machine. Use one machine at a time; the newer library replaces the older one, including deletions.</p>
      <div class="sync-field">
        <label for="sync-location">Sync location</label>
        <div class="sync-controls">
          <select id="sync-location" v-model="location" class="sync-input" :disabled="disabled" @change="changeLocation">
            <option value="">None</option><option value="folder">Folder</option><option value="dropbox">Dropbox</option><option value="gdrive">Google Drive</option>
          </select>
          <button v-if="location === 'folder'" type="button" :class="connected ? 'sync-btn' : 'sync-primary'" :disabled="disabled" @click="browseFolder">Choose folder</button>
          <button v-if="location === 'dropbox' && status.location !== 'dropbox'" type="button" :class="connected ? 'sync-btn' : 'sync-primary'" :disabled="disabled || !status.dropbox_available" @click="act(() => api.connectDropbox())">Connect Dropbox</button>
          <button v-if="location === 'gdrive' && status.location !== 'gdrive'" type="button" :class="connected ? 'sync-btn' : 'sync-primary'" :disabled="disabled || !status.gdrive_available" @click="act(() => api.connectGdrive())">Connect Google Drive</button>
          <button v-if="connected" type="button" class="sync-btn" :disabled="disabled" @click="act(() => api.disconnectSync())">Disconnect</button>
        </div>
      </div>
      <p v-if="location === 'folder'" class="sync-description">Choose a folder kept in sync by your preferred tool. wLib writes wlib-library.json there.</p>
      <p v-if="location === 'dropbox' && !status.dropbox_available" class="sync-description">{{ status.dropbox_unavailable_reason }}</p>
      <p v-if="location === 'gdrive' && !status.gdrive_available" class="sync-description">{{ status.gdrive_unavailable_reason }}</p>
      <p v-if="status.connecting" role="status">Finish {{ location === 'gdrive' ? 'Google' : 'Dropbox' }} sign-in in your browser.</p>
      <p v-if="status.folder_path" class="sync-path">{{ status.folder_path }}</p>
      <p v-if="status.account" class="sync-description">Connected account: {{ status.account }}</p>
    </div>
    <div class="sync-card">
      <h3 id="sync-status-heading" class="ui-section-heading text-lg font-semibold"><IconRefresh class="ui-section-icon" />Status</h3>
      <div class="sync-status" role="status" aria-live="polite">
        <strong>{{ loading ? "Loading sync status..." : stateLabel }}</strong>
        <dl v-if="connected"><div><dt>Last synced</dt><dd>{{ lastSynced }}</dd></div><div><dt>Last device</dt><dd>{{ status.last_device || "Not synced yet" }}</dd></div></dl>
      </div>
      <p v-if="error || (status.last_error && status.state !== 'syncing')" class="sync-error" role="alert">{{ error || status.last_error }}</p>
      <div class="sync-controls">
        <button v-if="connected" type="button" :class="status.state === 'needs_choice' || status.state === 'needs_passphrase' ? 'sync-btn' : 'sync-primary'" :disabled="disabled" @click="act(() => api.syncNow())">{{ status.state === 'syncing' ? 'Syncing...' : 'Sync now' }}</button>
        <button v-if="status.state === 'needs_choice'" type="button" class="sync-primary" :disabled="disabled" @click="dialog = 'choice'">Choose library</button>
        <button v-if="status.state === 'needs_passphrase'" type="button" class="sync-primary" :disabled="disabled" @click="dialog = 'unlock'">Enter passphrase</button>
      </div>
      <label v-if="connected" class="sync-encryption"><span>Encrypt the sync file with a passphrase</span><input type="checkbox" :checked="status.encrypted" :disabled="disabled || !status.last_synced_at || status.state === 'needs_choice' || status.state === 'needs_passphrase'" @change="toggleEncryption" /></label>
    </div>
    <footer class="sync-footer"><button type="button" class="sync-link" @click="openPrivacy">Privacy policy</button></footer>
    <Teleport to="body">
      <div v-if="dialog" class="sync-overlay" @click.self="closeDialog">
        <section ref="dialogElement" class="sync-dialog" role="dialog" aria-modal="true" aria-labelledby="sync-dialog-title" tabindex="-1">
          <template v-if="dialog === 'choice'">
            <h3 id="sync-dialog-title">Choose which library to use</h3>
            <p>Both this machine and the sync file have games. No library has been changed yet.</p>
            <button type="button" :disabled="busy" @click="choose('use-remote')"><strong>Use sync file</strong><span>Replace this library, including deletions. Keep local launch options for matching games.</span></button>
            <button type="button" :disabled="busy" @click="choose('use-local')"><strong>Use this machine</strong><span>Replace the sync file with this machine's library.</span></button>
            <button type="button" :disabled="busy" @click="choose('merge-once')"><strong>Merge once</strong><span>Combine both libraries, using sync file values for matches, then upload. Delete nothing.</span></button>
          </template>
          <form v-else @submit.prevent="submitPassphrase">
            <h3 id="sync-dialog-title">{{ dialog === 'unlock' ? 'Unlock the sync file' : 'Encrypt the sync file' }}</h3>
            <p v-if="dialog === 'unlock'">Enter the passphrase used by the sync file. Your derived key will stay on this machine for automatic sync.</p>
            <p v-else>A forgotten passphrase cannot be recovered. Keep it somewhere safe; without it, the file cannot be read.</p>
            <label for="sync-passphrase">Passphrase</label><input id="sync-passphrase" v-model="passphrase" type="password" :autocomplete="dialog === 'unlock' ? 'current-password' : 'new-password'" :disabled="busy" />
            <template v-if="dialog === 'encrypt'"><label for="sync-confirmation">Confirm passphrase</label><input id="sync-confirmation" v-model="confirmation" type="password" autocomplete="new-password" :disabled="busy" /></template>
            <button type="submit" class="sync-primary" :disabled="busy">{{ busy ? 'Working...' : dialog === 'unlock' ? 'Unlock and sync' : 'Enable encryption' }}</button>
          </form>
          <p v-if="dialogError" class="sync-error" role="alert">{{ dialogError }}</p>
          <button type="button" :disabled="busy" @click="closeDialog">Cancel</button>
        </section>
      </div>
    </Teleport>
  </section>
</template>

<style scoped>
.sync-panel { display: grid; gap: 1.5rem; margin-bottom: 1.5rem; color: var(--text-primary); overflow-wrap: anywhere; }
.sync-card { display: grid; gap: .75rem; padding: 1.5rem; border: 1px solid var(--border); border-radius: .75rem; background: var(--bg-surface); }
.sync-card h3, .sync-card p { margin: 0; }
.sync-description, dt { color: var(--text-secondary); font-size: .875rem; }
.sync-note { font-size: .8125rem; }
.sync-field { display: grid; gap: .5rem; }
.sync-field label { color: var(--text-secondary); font-weight: 500; }
.sync-controls { display: flex; flex-wrap: wrap; gap: .75rem; align-items: center; }
.sync-controls:empty { display: none; }
label { font-size: .875rem; }
button, select, input[type="password"] { background: var(--bg-raised); color: var(--text-primary); border: 1px solid var(--border); border-radius: .5rem; min-height: 44px; padding: .625rem .875rem; font-size: .875rem; max-width: 100%; min-width: 0; overflow-wrap: anywhere; }
.sync-input, input[type="password"] { padding: .625rem 1rem; transition: border-color .15s ease, box-shadow .15s ease, background-color .15s ease, color .15s ease; }
.sync-input:focus, input[type="password"]:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-glow); }
button:disabled, select:disabled { opacity: .55; cursor: not-allowed; }
button:focus-visible, .sync-dialog:focus-visible { outline: 2px solid var(--brand); outline-offset: 3px; }
.sync-btn { background: var(--bg-overlay); border-color: var(--border-hover); font-weight: 600; }
.sync-btn:hover:not(:disabled) { background: var(--border-hover); }
.sync-primary { background: var(--brand); color: var(--on-brand); border-color: var(--brand); box-shadow: var(--shadow-brand); font-weight: 600; }
.sync-primary:hover:not(:disabled) { filter: brightness(1.08); }
.sync-link { text-decoration: underline; border-color: transparent; background: transparent; padding-inline: 0; }
.sync-footer { padding-inline: 1.5rem; }
.sync-path, dd { overflow-wrap: anywhere; }
dl { display: flex; flex-wrap: wrap; gap: .5rem 2rem; margin: .5rem 0 0; }
dl > div { min-width: 0; max-width: 100%; }
dd { margin: .25rem 0 0; font-size: .875rem; }
.sync-error { color: var(--danger-text); font-size: .875rem; }
.sync-encryption { display: flex; align-items: center; justify-content: space-between; gap: 1rem; min-height: 44px; padding: .75rem 1rem; border: 1px solid var(--border); border-radius: .5rem; background: var(--bg-raised); }
.sync-overlay { position: fixed; inset: 0; background: var(--overlay-scrim); z-index: 100; display: grid; place-items: center; padding: 1rem; }
.sync-dialog { width: min(100%, 32rem); max-height: calc(100dvh - 2rem); overflow-y: auto; background: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border); border-radius: .75rem; padding: 1.5rem; box-shadow: var(--shadow-modal); }
.sync-dialog h3 { font-size: 1.125rem; font-weight: 700; margin: 0 0 .5rem; }
.sync-dialog p { margin: .5rem 0 1rem; }
.sync-dialog button { display: block; width: 100%; text-align: left; margin-top: .75rem; }
.sync-dialog button span { display: block; color: var(--text-secondary); font-size: .8125rem; margin-top: .25rem; }
.sync-dialog form { display: grid; gap: .75rem; }
@media (max-width: 480px) { .sync-card { padding: 1rem; } .sync-footer { padding-inline: 1rem; } .sync-controls select { width: 100%; } .sync-dialog { padding: 1rem; } }
</style>
