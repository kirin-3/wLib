<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { IconAlertTriangle, IconArrowsExchange, IconCloud, IconEye, IconEyeOff, IconHistory, IconLock, IconLockOpen, IconRefresh } from "@tabler/icons-vue";
import { api, type LibrarySyncChoice, type LibrarySyncStatus, type SyncSnapshot } from "../services/api";
import { useModalKeyboard } from "../utils/modalKeyboard";

const status = ref<LibrarySyncStatus>({ state: "off" });
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const location = ref<"" | "folder" | "dropbox" | "gdrive">("");
const dialog = ref<"choice" | "unlock" | "encrypt" | "restore" | null>(null);
const snapshots = ref<SyncSnapshot[]>([]);
const snapshotsLoaded = ref(false);
const restoreTarget = ref<SyncSnapshot | null>(null);
const restoreMessage = ref("");
const dialogElement = ref<HTMLElement | null>(null);
const passphrase = ref("");
const confirmation = ref("");
const dialogError = ref("");
const showPassphrase = ref(false);
// Live check of the two encrypt fields; the submit check stays as the last guard.
const passphraseMismatch = computed(() => dialog.value === "encrypt" && confirmation.value !== "" && passphrase.value !== confirmation.value);
const canSubmitPassphrase = computed(() => passphrase.value !== "" && (dialog.value !== "encrypt" || passphrase.value === confirmation.value));
let timer: ReturnType<typeof setInterval> | undefined;
let requestVersion = 0;
let previousState = "";
let pollRunning = false;
let disposed = false;
// Only status-read failures are cleared by a later successful read; action errors stay.
let errorFromStatus = false;
let browsing = false;
const disabled = computed(() => loading.value || busy.value || status.value.state === "syncing");
const connected = computed(() => Boolean(status.value.location));
const choiceMessage = computed(() => ({
  conflict: "This machine and the sync file both changed since the last sync.",
  removal: "The sync file would remove most of the games on this machine.",
})[status.value.choice_reason as "conflict" | "removal"] ?? "Both this machine and the sync file have games.");
const stateLabel = computed(() => ({ off: "Sync is off", idle: "Ready to sync", syncing: "Syncing your library...", needs_choice: "Choose which library to use", needs_passphrase: "Passphrase required", error: "Sync needs attention", offline: "Sync location is offline", paused: "Sync is paused" })[status.value.state || "off"]);
const paused = computed(() => Boolean(status.value.paused));
const lastSynced = computed(() => status.value.last_synced_at ? new Date(status.value.last_synced_at).toLocaleString() : "Not synced yet");

function closeDialog() {
  dialog.value = null;
  restoreTarget.value = null;
  passphrase.value = confirmation.value = dialogError.value = "";
  showPassphrase.value = false;
}
const snapshotLabel = (snapshot: SyncSnapshot) =>
  `${new Date(snapshot.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })} · ${snapshot.games} ${snapshot.games === 1 ? "game" : "games"}`;
async function loadSnapshots() {
  try {
    const result = await api.listSyncSnapshots();
    snapshots.value = result.success === false ? [] : result.snapshots ?? [];
  } catch {
    snapshots.value = [];
  } finally {
    snapshotsLoaded.value = true;
  }
}
async function openRestore() {
  closeDialog();
  restoreMessage.value = "";
  dialog.value = "restore";
  await loadSnapshots();
}
async function restoreSnapshot() {
  const target = restoreTarget.value;
  if (!target || busy.value) return;
  busy.value = true;
  dialogError.value = "";
  try {
    const result = await api.restoreSyncSnapshot(target.name);
    if (result.success === false) {
      dialogError.value = result.error || "Could not restore that library.";
      return;
    }
    closeDialog();
    restoreMessage.value = `Restored the library from ${new Date(target.created_at).toLocaleString()}. It will be uploaded on the next sync.`;
  } catch {
    dialogError.value = "Could not restore that library.";
  } finally {
    busy.value = false;
    await loadSnapshots();
    await refreshStatus();
  }
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
onMounted(async () => {
  await Promise.all([refreshStatus(), loadSnapshots()]);
  // The view can unmount while the first status read is still in flight.
  if (disposed) return;
  timer = setInterval(() => void refreshStatus(), 1500);
});
onBeforeUnmount(() => {
  disposed = true;
  if (timer) clearInterval(timer);
  requestVersion++;
});
</script>

<template>
  <section class="sync-panel" aria-labelledby="sync-heading" :aria-busy="loading || status.state === 'syncing'">
    <div class="sync-card">
      <h3 id="sync-heading" class="ui-section-heading text-lg font-semibold"><IconCloud class="ui-section-icon" />Library sync</h3>
      <p class="sync-description">Keep one library across your machines in a folder, your Dropbox app folder, or Google Drive.</p>
      <p v-if="status.mock" class="sync-description">Browser preview with mock data. No files are synced.</p>
      <p class="sync-description sync-note">Sync shares game metadata, ratings, notes, playtime, and general settings. Executable paths and launch options stay on this machine. Use one machine at a time; a newer library replaces the older one, including deletions. If both changed, you choose which to keep.</p>
      <div class="sync-field">
        <label for="sync-location">Sync location</label>
        <div class="sync-controls">
          <select id="sync-location" v-model="location" class="sync-input" :disabled="disabled" @change="changeLocation">
            <option value="">None</option><option value="folder">Folder</option><option value="dropbox">Dropbox</option><option value="gdrive">Google Drive</option>
          </select>
          <button v-if="location === 'folder'" type="button" :class="connected ? 'sync-btn' : 'sync-primary'" :disabled="disabled" @click="browseFolder">Choose folder</button>
          <button v-if="location === 'dropbox' && (status.location !== 'dropbox' || status.error_code === 'dropbox_auth_failed')" type="button" :class="connected && status.location !== 'dropbox' ? 'sync-btn' : 'sync-primary'" :disabled="disabled || !status.dropbox_available" @click="act(() => api.connectDropbox())">{{ status.location === 'dropbox' ? 'Sign in again' : 'Connect Dropbox' }}</button>
          <button v-if="location === 'gdrive' && (status.location !== 'gdrive' || status.error_code === 'gdrive_auth_failed')" type="button" :class="connected && status.location !== 'gdrive' ? 'sync-btn' : 'sync-primary'" :disabled="disabled || !status.gdrive_available" @click="act(() => api.connectGdrive())">{{ status.location === 'gdrive' ? 'Sign in again' : 'Connect Google Drive' }}</button>
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
      <p v-if="error || (status.last_error && !['syncing', 'offline', 'paused'].includes(status.state || ''))" class="sync-error" role="alert">{{ error || status.last_error }}</p>
      <p v-if="!error && status.state === 'offline'" class="sync-description">{{ status.last_error || "The sync location cannot be reached. wLib will try again." }}</p>
      <p v-if="paused" class="sync-description">Changes on this machine are not synced until you click Resume.</p>
      <div class="sync-controls">
        <button v-if="connected" type="button" :class="paused || status.state === 'needs_choice' || status.state === 'needs_passphrase' ? 'sync-btn' : 'sync-primary'" :disabled="disabled || paused" @click="act(() => api.syncNow())">{{ status.state === 'syncing' ? 'Syncing...' : 'Sync now' }}</button>
        <button v-if="connected && paused" type="button" class="sync-primary" :disabled="loading || busy" @click="act(() => api.resumeSync())">Resume</button>
        <button v-else-if="connected" type="button" class="sync-btn" :disabled="loading || busy" @click="act(() => api.pauseSync())">Pause sync</button>
        <button v-if="status.state === 'needs_choice'" type="button" class="sync-primary" :disabled="disabled" @click="dialog = 'choice'">Choose library</button>
        <button v-if="status.state === 'needs_passphrase'" type="button" class="sync-primary" :disabled="disabled" @click="dialog = 'unlock'">Enter passphrase</button>
      </div>
      <div v-if="snapshots.length" class="sync-controls">
        <button type="button" class="sync-btn" :disabled="disabled" @click="openRestore">Restore an earlier library</button>
      </div>
      <p v-if="restoreMessage" class="sync-description" role="status">{{ restoreMessage }}</p>
      <label v-if="connected" class="sync-encryption"><span>Encrypt the sync file with a passphrase</span><input type="checkbox" :checked="status.encrypted" :disabled="disabled || !status.last_synced_at || status.state === 'needs_choice' || status.state === 'needs_passphrase'" @change="toggleEncryption" /></label>
    </div>
    <footer class="sync-footer"><button type="button" class="sync-link" @click="openPrivacy">Privacy policy</button></footer>
    <Teleport to="body">
      <div v-if="dialog" class="fixed inset-0 z-[100] flex items-center justify-center p-4">
        <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="closeDialog"></div>
        <section ref="dialogElement" class="sync-dialog relative w-full max-w-lg rounded-2xl overflow-hidden flex flex-col outline-none" role="dialog" aria-modal="true" aria-labelledby="sync-dialog-title" tabindex="-1">
          <div class="sync-dialog-body">
            <div class="sync-dialog-head">
              <span class="sync-dialog-icon" aria-hidden="true">
                <IconArrowsExchange v-if="dialog === 'choice'" class="w-5 h-5" />
                <IconHistory v-else-if="dialog === 'restore'" class="w-5 h-5" />
                <IconLockOpen v-else-if="dialog === 'unlock'" class="w-5 h-5" />
                <IconLock v-else class="w-5 h-5" />
              </span>
              <div class="min-w-0">
                <template v-if="dialog === 'choice'">
                  <h3 id="sync-dialog-title">Choose which library to use</h3>
                  <p>{{ choiceMessage }} No library has been changed yet.</p>
                </template>
                <template v-else-if="dialog === 'restore' && !restoreTarget">
                  <h3 id="sync-dialog-title">Restore an earlier library</h3>
                  <p>wLib saves this machine's library before each sync changes it and keeps the last five copies.</p>
                </template>
                <template v-else-if="dialog === 'restore'">
                  <h3 id="sync-dialog-title">Restore this library?</h3>
                  <p>This machine's library goes back to {{ snapshotLabel(restoreTarget!) }}, including its executable paths and launch options, and games added since are removed. The current library is saved first, so you can undo this the same way. The restored library is uploaded on the next sync.</p>
                </template>
                <template v-else>
                  <h3 id="sync-dialog-title">{{ dialog === 'unlock' ? 'Unlock the sync file' : 'Encrypt the sync file' }}</h3>
                  <p v-if="dialog === 'unlock'">Enter the passphrase used by the sync file. Your derived key will stay on this machine for automatic sync.</p>
                  <p v-else>A forgotten passphrase cannot be recovered. Keep it somewhere safe; without it, the file cannot be read.</p>
                </template>
              </div>
            </div>
            <div v-if="dialog === 'choice'" class="sync-options">
              <button type="button" class="sync-option" :disabled="busy" @click="choose('use-remote')"><strong>Use sync file</strong><span>Replace this library, including deletions. Keep local launch options for matching games.</span><span class="sync-option-risk"><IconAlertTriangle class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />Games that are not in the sync file are removed from this machine.</span></button>
              <button type="button" class="sync-option" :disabled="busy" @click="choose('use-local')"><strong>Use this machine</strong><span>Replace the sync file with this machine's library.</span><span class="sync-option-risk"><IconAlertTriangle class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />Changes that other machines saved to the sync file are lost.</span></button>
              <button type="button" class="sync-option" :disabled="busy" @click="choose('merge-once')"><strong>Merge once</strong><span>Combine both libraries, using sync file values for matches, then upload. Delete nothing.</span></button>
            </div>
            <template v-else-if="dialog === 'restore' && !restoreTarget">
              <p v-if="snapshotsLoaded && !snapshots.length" class="sync-dialog-note">No earlier library is saved yet.</p>
              <div v-else class="sync-options">
                <button v-for="snapshot in snapshots" :key="snapshot.name" type="button" class="sync-option" :disabled="busy" @click="restoreTarget = snapshot"><strong>{{ snapshotLabel(snapshot) }}</strong></button>
              </div>
            </template>
            <form v-else-if="dialog === 'unlock' || dialog === 'encrypt'" id="sync-passphrase-form" class="sync-fields" @submit.prevent="submitPassphrase">
              <label for="sync-passphrase">Passphrase</label>
              <div class="sync-secret">
                <input id="sync-passphrase" class="sync-pass" v-model="passphrase" :type="showPassphrase ? 'text' : 'password'" :autocomplete="dialog === 'unlock' ? 'current-password' : 'new-password'" spellcheck="false" :disabled="busy" />
                <button type="button" class="sync-reveal" :aria-pressed="showPassphrase" :aria-label="showPassphrase ? 'Hide passphrase' : 'Show passphrase'" :title="showPassphrase ? 'Hide passphrase' : 'Show passphrase'" @click="showPassphrase = !showPassphrase">
                  <IconEyeOff v-if="showPassphrase" class="w-4 h-4" /><IconEye v-else class="w-4 h-4" />
                </button>
              </div>
              <template v-if="dialog === 'encrypt'">
                <label for="sync-confirmation">Confirm passphrase</label>
                <input id="sync-confirmation" class="sync-pass" v-model="confirmation" :type="showPassphrase ? 'text' : 'password'" autocomplete="new-password" spellcheck="false" :disabled="busy" :aria-invalid="passphraseMismatch" aria-describedby="sync-match" />
                <p id="sync-match" class="sync-match" :class="{ 'sync-match-bad': passphraseMismatch, 'sync-match-ok': canSubmitPassphrase && confirmation !== '' }" aria-live="polite">{{ passphraseMismatch ? 'The passphrases do not match.' : canSubmitPassphrase && confirmation !== '' ? 'The passphrases match.' : 'Type the passphrase again.' }}</p>
              </template>
            </form>
            <p v-if="dialogError" class="sync-error" role="alert">{{ dialogError }}</p>
          </div>
          <div class="sync-dialog-foot">
            <template v-if="dialog === 'restore' && restoreTarget">
              <button type="button" class="sync-cancel" :disabled="busy" @click="restoreTarget = null">Back</button>
              <button type="button" class="sync-primary ui-btn-solid" :disabled="busy" @click="restoreSnapshot">{{ busy ? 'Restoring...' : 'Restore' }}</button>
            </template>
            <template v-else>
              <button type="button" class="sync-cancel" :disabled="busy" @click="closeDialog">Cancel</button>
              <button v-if="dialog === 'unlock' || dialog === 'encrypt'" type="submit" form="sync-passphrase-form" class="sync-primary ui-btn-solid" :disabled="busy || !canSubmitPassphrase">{{ busy ? 'Working...' : dialog === 'unlock' ? 'Unlock and sync' : 'Enable encryption' }}</button>
            </template>
          </div>
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
button, select, .sync-pass { background: var(--bg-raised); color: var(--text-primary); border: 1px solid var(--border); border-radius: .5rem; min-height: 44px; padding: .625rem .875rem; font-size: .875rem; max-width: 100%; min-width: 0; overflow-wrap: anywhere; }
.sync-input, .sync-pass { padding: .625rem 1rem; transition: border-color .15s ease, box-shadow .15s ease, background-color .15s ease, color .15s ease; }
.sync-input:focus, .sync-pass:focus { outline: none; border-color: var(--brand); box-shadow: 0 0 0 3px var(--brand-glow); }
button:disabled, select:disabled { opacity: .55; cursor: not-allowed; }
button:focus-visible { outline: 2px solid var(--brand); outline-offset: 3px; }
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
.sync-dialog { max-height: calc(var(--app-vh) * 0.9); background: var(--bg-surface); color: var(--text-primary); border: 1px solid var(--border); box-shadow: var(--shadow-modal); }
.sync-dialog-body { display: grid; gap: 1rem; padding: 1.5rem 1.5rem 1rem; overflow-y: auto; min-height: 0; }
.sync-dialog-head { display: flex; gap: 1rem; }
.sync-dialog-head h3 { font-size: 1.125rem; font-weight: 700; margin: .25rem 0 0; }
.sync-dialog-head p { margin: .25rem 0 0; font-size: .875rem; color: var(--text-secondary); }
.sync-dialog-icon { display: inline-flex; align-items: center; justify-content: center; width: 2.5rem; height: 2.5rem; flex-shrink: 0; border-radius: 9999px; background: var(--brand-glow); color: var(--brand); }
.sync-dialog-note { margin: 0; font-size: .875rem; color: var(--text-muted); }
.sync-options { display: grid; gap: .5rem; }
.sync-option { display: block; width: 100%; text-align: left; background: var(--bg-inset); }
.sync-option:hover:not(:disabled) { border-color: var(--border-hover); background: var(--bg-raised); }
.sync-option span { display: block; color: var(--text-secondary); font-size: .8125rem; margin-top: .25rem; }
.sync-option .sync-option-risk { display: flex; align-items: flex-start; gap: .375rem; color: var(--danger-text); }
.sync-option .sync-option-risk svg { margin-top: .1rem; }
.sync-secret { position: relative; }
.sync-secret input { width: 100%; padding-right: 2.75rem; }
.sync-reveal { position: absolute; top: 50%; right: .25rem; transform: translateY(-50%); display: inline-flex; align-items: center; justify-content: center; min-height: 0; width: 2.25rem; height: 2.25rem; padding: 0; background: transparent; border-color: transparent; color: var(--text-muted); }
.sync-reveal:hover { color: var(--text-primary); background: var(--bg-overlay); }
.sync-match { margin: 0; font-size: .8125rem; color: var(--text-muted); }
.sync-match-bad { color: var(--danger-text); }
.sync-match-ok { color: var(--success-text); }
.sync-pass[aria-invalid="true"] { border-color: var(--danger-border); }
.sync-fields { display: grid; gap: .5rem; }
.sync-fields input + label { margin-top: .25rem; }
.sync-dialog-foot { display: flex; justify-content: flex-end; gap: .75rem; padding: 1rem 1.5rem; border-top: 1px solid var(--border); background: var(--bg-inset); }
.sync-dialog-foot button { min-height: 0; padding: .625rem 1.25rem; }
.sync-cancel { background: transparent; border-color: transparent; color: var(--text-secondary); font-weight: 500; }
.sync-cancel:hover:not(:disabled) { color: var(--text-primary); background: var(--bg-overlay); }
.sync-dialog-foot .sync-primary { font-weight: 700; padding-inline: 1.5rem; }
@media (max-width: 480px) { .sync-card { padding: 1rem; } .sync-footer { padding-inline: 1rem; } .sync-controls select { width: 100%; } .sync-dialog-body { padding: 1rem; } .sync-dialog-foot { padding: .75rem 1rem; } }
</style>
