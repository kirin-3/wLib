<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { IconLoader2 } from "@tabler/icons-vue";
import {
  api,
  type GameToolEngine,
  type GameToolStatus,
  type GameToolsResponse,
  type RenpyExtractMode,
} from "../services/api";
import {
  consoleHint,
  formatToolBytes,
  formatToolProgress,
  getDecryptState,
  getExtractState,
  getUseDecryptedState,
  keySourceLabel,
  toolProgressPercent,
} from "../utils/gameTools";
import { notify, notifyError } from "../utils/toast";

const props = defineProps<{
  gameId: number;
  engine: GameToolEngine;
  // True while the Tools tab is showing; the tab loads on first show.
  active: boolean;
}>();

type BusyAction = "" | "console" | "extract" | "decrypt" | "switch" | "urm";

const POLL_MS = 500;

const tools = ref<GameToolsResponse | null>(null);
const loading = ref(false);
const loadError = ref("");
const busy = ref<BusyAction>("");
const urmError = ref("");
const lastResult = ref("");
const developerOption = ref(false);
const extractMode = ref<RenpyExtractMode>("folder");
const outputFolder = ref("");
const progress = ref<GameToolStatus | null>(null);
const cancelling = ref(false);

let loadedFor: number | null = null;
let requestId = 0;
let pollTimer: ReturnType<typeof setInterval> | null = null;

const stopPolling = () => {
  if (pollTimer !== null) clearInterval(pollTimer);
  pollTimer = null;
};

const startPolling = (gameId: number) => {
  stopPolling();
  progress.value = null;
  cancelling.value = false;
  pollTimer = setInterval(async () => {
    try {
      const status = await api.getGameToolStatus(gameId);
      if (props.gameId === gameId && pollTimer !== null && status.running) progress.value = status;
    } catch {
      // the run's own response reports failures
    }
  }, POLL_MS);
};

onBeforeUnmount(stopPolling);

const load = async (gameId: number, keepVisible = false) => {
  const request = ++requestId;
  loading.value = true;
  loadError.value = "";
  if (!keepVisible) tools.value = null;
  try {
    const result = await api.getGameTools(gameId);
    if (request !== requestId || props.gameId !== gameId) return;
    if (result.success === false) {
      tools.value = null;
      loadError.value = result.error || "Could not load the tools";
    } else {
      tools.value = result;
      developerOption.value = result.renpy?.console === "developer";
    }
  } catch (error) {
    if (request !== requestId || props.gameId !== gameId) return;
    tools.value = null;
    loadError.value = String(error);
  } finally {
    if (request === requestId) loading.value = false;
  }
};

watch(
  () => [props.gameId, props.active] as const,
  ([gameId, active]) => {
    if (loadedFor !== gameId) {
      // A different game: drop the old payload, any in-flight response and any polling.
      requestId++;
      stopPolling();
      tools.value = null;
      loadError.value = "";
      loading.value = false;
      busy.value = "";
      urmError.value = "";
      lastResult.value = "";
      progress.value = null;
      extractMode.value = "folder";
      outputFolder.value = "";
      developerOption.value = false;
      loadedFor = null;
    }
    if (active && loadedFor !== gameId) {
      loadedFor = gameId;
      void load(gameId);
    }
  },
  { immediate: true },
);

const isBusy = computed(() => busy.value !== "");
const renpy = computed(() => tools.value?.renpy ?? null);
const rpgm = computed(() => tools.value?.rpgm ?? null);
const consoleOn = computed(() => (renpy.value?.console ?? "off") !== "off");
const longRun = computed(() => busy.value === "extract" || busy.value === "decrypt");

const extractState = computed(() =>
  getExtractState(renpy.value?.archives ?? [], extractMode.value, outputFolder.value, isBusy.value),
);
const decryptState = computed(() => (rpgm.value ? getDecryptState(rpgm.value, isBusy.value) : null));
const switchState = computed(() => (rpgm.value ? getUseDecryptedState(rpgm.value, isBusy.value) : null));

const progressInput = computed(() => ({
  running: true,
  done: progress.value?.done ?? 0,
  total: progress.value?.total ?? 0,
  current: progress.value?.current ?? "",
}));

// Runs one tool call; the tab stays locked until it returns, and a response for a game
// that is no longer shown is dropped.
const run = async <T extends { success?: boolean; error?: string; message?: string }>(
  action: BusyAction,
  call: (gameId: number) => Promise<T>,
): Promise<T | null> => {
  if (busy.value) return null;
  const gameId = props.gameId;
  busy.value = action;
  lastResult.value = "";
  if (action === "extract" || action === "decrypt") startPolling(gameId);
  try {
    const result = await call(gameId);
    if (props.gameId !== gameId) return null;
    if (result.success === false) {
      notifyError(result.error || "The tool failed");
      return null;
    }
    if (result.message) {
      lastResult.value = result.message;
      notify(result.message, "success");
    }
    return result;
  } catch (error) {
    if (props.gameId === gameId) notifyError(String(error));
    return null;
  } finally {
    if (props.gameId === gameId) {
      stopPolling();
      progress.value = null;
      cancelling.value = false;
      busy.value = "";
    }
  }
};

const setConsole = async (enabled: boolean, developer: boolean): Promise<boolean> => {
  const result = await run("console", (id) => api.setRenpyConsole(id, enabled, developer));
  if (result) await load(props.gameId, true);
  return result !== null;
};

const toggleConsole = () => void setConsole(!consoleOn.value, developerOption.value);

const toggleDeveloper = async () => {
  const gameId = props.gameId;
  const next = !developerOption.value;
  developerOption.value = next;
  // Rewrites the script only when the console is already on; a failed rewrite
  // leaves the file as it was, so the checkbox goes back too.
  if (consoleOn.value && !(await setConsole(true, next)) && props.gameId === gameId) {
    developerOption.value = !next;
  }
};

const chooseOutput = async () => {
  if (isBusy.value) return;
  try {
    const picked = await api.browseDirectory(outputFolder.value);
    if (picked) outputFolder.value = picked;
  } catch (error) {
    notifyError("Error browsing folder: " + String(error));
  }
};

const extract = async () => {
  const mode = extractMode.value;
  const output = outputFolder.value;
  const result = await run("extract", (id) =>
    api.extractRenpyArchives(id, mode === "folder" ? { mode, output } : { mode }),
  );
  if (result) await load(props.gameId, true);
};

const decrypt = async () => {
  const result = await run("decrypt", (id) => api.decryptRpgmAssets(id));
  if (result) await load(props.gameId, true);
};

const toggleUseDecrypted = async () => {
  const enabled = !(rpgm.value?.use_decrypted ?? false);
  const result = await run("switch", (id) => api.setRpgmUseDecrypted(id, enabled));
  if (result) await load(props.gameId, true);
};

const cancel = async () => {
  if (cancelling.value) return;
  cancelling.value = true;
  try {
    await api.cancelGameTool(props.gameId);
  } catch (error) {
    notifyError(String(error));
  }
};

const toggleUrm = async () => {
  const urm = renpy.value?.urm;
  if (!urm || isBusy.value) return;
  const gameId = props.gameId;
  busy.value = "urm";
  urmError.value = "";
  try {
    const result = await api.setUrmInstalled(gameId, !urm.installed);
    if (props.gameId !== gameId) return;
    if (result.success === false) urmError.value = result.error || "Could not update URM";
    else if (tools.value?.renpy) tools.value = { ...tools.value, renpy: { ...tools.value.renpy, urm: result } };
  } catch (error) {
    if (props.gameId === gameId) urmError.value = String(error);
  } finally {
    if (props.gameId === gameId) busy.value = "";
  }
};

// v-model re-syncs the checkbox from the state on re-render, so a failed toggle snaps back.
const urmInstalled = computed({
  get: () => !!renpy.value?.urm.installed,
  set: () => void toggleUrm(),
});
const consoleChecked = computed({
  get: () => consoleOn.value,
  set: () => toggleConsole(),
});
const useDecryptedChecked = computed({
  get: () => !!rpgm.value?.use_decrypted,
  set: () => void toggleUseDecrypted(),
});
</script>

<template>
  <div class="tools-tab space-y-4" :aria-busy="isBusy || loading">
    <div v-if="longRun" class="tools-card space-y-2" role="status">
      <div class="flex items-center justify-between gap-3">
        <p class="text-xs flex items-center gap-2 min-w-0" style="color: var(--text-secondary)">
          <IconLoader2 class="ui-action-icon animate-spin shrink-0" />
          <span class="truncate">{{ formatToolProgress(progressInput) }}</span>
        </p>
        <button type="button" class="tools-btn disabled:opacity-50" :disabled="cancelling" @click="cancel">
          {{ cancelling ? "Cancelling..." : "Cancel" }}
        </button>
      </div>
      <div
        class="tools-bar"
        role="progressbar"
        aria-label="Tool progress"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="toolProgressPercent(progressInput)"
      >
        <div class="tools-bar-fill" :style="{ width: toolProgressPercent(progressInput) + '%' }"></div>
      </div>
    </div>
    <p v-if="loading && !tools" class="text-xs flex items-center gap-2" style="color: var(--text-secondary)" role="status">
      <IconLoader2 class="ui-action-icon animate-spin" />
      Looking at the game files...
    </p>
    <p v-else-if="loadError" role="alert" class="text-xs" style="color: var(--danger-text)">{{ loadError }}</p>

    <template v-else-if="tools && engine === 'renpy' && renpy">
      <section class="tools-card space-y-2" aria-labelledby="tools-console-title">
        <div class="flex items-center justify-between gap-3">
          <span id="tools-console-title" class="text-sm font-medium" style="color: var(--text-primary)">Developer console</span>
          <label class="relative inline-flex items-center" :class="isBusy ? 'cursor-not-allowed' : 'cursor-pointer'">
            <input
              v-model="consoleChecked"
              type="checkbox"
              class="sr-only peer"
              aria-label="Developer console"
              :disabled="isBusy"
            />
            <div class="ui-toggle"></div>
          </label>
        </div>
        <label class="flex items-center gap-2 text-xs" style="color: var(--text-secondary)">
          <input type="checkbox" :checked="developerOption" :disabled="isBusy" @change="toggleDeveloper" />
          Also enable developer mode
        </label>
        <p class="text-xs" style="color: var(--text-muted)">
          <template v-if="consoleHint(renpy.console)">{{ consoleHint(renpy.console) }}</template>
          <template v-else>Adds one small script to the game's <code>game</code> folder. Developer mode changes how some games behave.</template>
        </p>
      </section>

      <section class="tools-card space-y-3" aria-labelledby="tools-archives-title">
        <span id="tools-archives-title" class="text-sm font-medium block" style="color: var(--text-primary)">Archives</span>
        <p v-if="!renpy.archives.length" class="text-xs" style="color: var(--text-muted)">There are no archives to extract.</p>
        <template v-else>
          <ul class="space-y-1">
            <li
              v-for="archive in renpy.archives"
              :key="archive.name"
              class="flex items-center gap-2 text-xs"
              style="color: var(--text-secondary)"
            >
              <span class="flex-1 min-w-0 truncate font-mono" :title="archive.name">{{ archive.name }}</span>
              <span style="color: var(--text-muted)">{{ archive.format || "Unsupported" }}</span>
              <span style="color: var(--text-muted)">{{ formatToolBytes(archive.size) }}</span>
            </li>
          </ul>

          <fieldset class="space-y-2" :disabled="isBusy">
            <legend class="sr-only">Where to extract</legend>
            <label class="flex items-center gap-2 text-xs" style="color: var(--text-secondary)">
              <input v-model="extractMode" type="radio" value="folder" name="extract-mode" />
              Extract to a folder (the game is not changed)
            </label>
            <div v-if="extractMode === 'folder'" class="flex items-center gap-2 pl-5">
              <button type="button" class="tools-btn disabled:opacity-50" :disabled="isBusy" @click="chooseOutput">
                Choose output folder…
              </button>
              <span
                class="flex-1 min-w-0 truncate text-xs font-mono"
                style="color: var(--text-muted)"
                :title="outputFolder"
              >{{ outputFolder || "No folder chosen" }}</span>
            </div>
            <label class="flex items-center gap-2 text-xs" style="color: var(--text-secondary)">
              <input v-model="extractMode" type="radio" value="in_place" name="extract-mode" />
              Extract into the game folder
            </label>
            <p v-if="extractMode === 'in_place'" class="text-[0.625rem] pl-5" style="color: var(--text-muted)">
              Script files are left out and existing files are kept. Edited assets then override the archive.
            </p>
          </fieldset>

          <div class="flex items-center gap-3">
            <button
              type="button"
              class="tools-btn ui-action-btn disabled:opacity-50"
              :disabled="!extractState.enabled"
              @click="extract"
            >
              Extract
            </button>
            <p v-if="extractState.message && !isBusy" role="status" class="text-xs" style="color: var(--text-muted)">
              {{ extractState.message }}
            </p>
          </div>
        </template>
      </section>

      <section v-if="renpy.urm.renpy" class="tools-card space-y-2" aria-labelledby="tools-urm-title">
        <div class="flex items-center justify-between gap-3">
          <span id="tools-urm-title" class="text-sm font-medium" style="color: var(--text-primary)">Universal Ren'Py Mod (URM)</span>
          <label
            class="relative inline-flex items-center"
            :class="renpy.urm.source_configured && !isBusy ? 'cursor-pointer' : 'cursor-not-allowed'"
          >
            <input
              v-model="urmInstalled"
              type="checkbox"
              class="sr-only peer"
              aria-label="Universal Ren'Py Mod (URM)"
              :disabled="isBusy || !renpy.urm.source_configured"
            />
            <div class="ui-toggle"></div>
          </label>
        </div>
        <p v-if="!renpy.urm.source_configured" class="text-xs" style="color: var(--text-muted)">
          Configure a URM .rpa source file in Settings to enable this control.
        </p>
        <p v-if="urmError" role="alert" class="text-xs" style="color: var(--danger-text)">{{ urmError }}</p>
      </section>
    </template>

    <template v-else-if="tools && engine === 'rpgm' && rpgm">
      <section class="tools-card space-y-3" aria-labelledby="tools-decrypt-title">
        <span id="tools-decrypt-title" class="text-sm font-medium block" style="color: var(--text-primary)">Decrypt assets</span>
        <p v-if="rpgm.encrypted === 0" class="text-xs" style="color: var(--text-muted)">This game's assets are not encrypted.</p>
        <template v-else>
          <p class="text-xs" style="color: var(--text-secondary)">
            {{ rpgm.encrypted }} encrypted files ({{ rpgm.images }} images, {{ rpgm.audio }} audio).
            {{ rpgm.missing === 0 ? "All have a decrypted copy." : `${rpgm.missing} still need a decrypted copy.` }}
          </p>
          <p class="text-[0.625rem]" style="color: var(--text-muted)">
            {{ keySourceLabel(rpgm.key_source) }}. Decrypted copies are written next to the originals, which are kept.
          </p>
          <div class="flex items-center gap-3">
            <button
              type="button"
              class="tools-btn ui-action-btn disabled:opacity-50"
              :disabled="!decryptState?.enabled"
              @click="decrypt"
            >
              Decrypt
            </button>
            <p v-if="decryptState?.message && !isBusy" role="status" class="text-xs" style="color: var(--text-muted)">
              {{ decryptState.message }}
            </p>
          </div>
        </template>
      </section>

      <section class="tools-card space-y-2" aria-labelledby="tools-switch-title">
        <div class="flex items-center justify-between gap-3">
          <span id="tools-switch-title" class="text-sm font-medium" style="color: var(--text-primary)">Use decrypted files in-game</span>
          <label class="relative inline-flex items-center" :class="switchState?.enabled ? 'cursor-pointer' : 'cursor-not-allowed'">
            <input
              v-model="useDecryptedChecked"
              type="checkbox"
              class="sr-only peer"
              aria-label="Use decrypted files in-game"
              :disabled="!switchState?.enabled"
            />
            <div class="ui-toggle"></div>
          </label>
        </div>
        <p class="text-xs" style="color: var(--text-muted)">
          Sets the game's encryption flags in <code>System.json</code> so it loads the decrypted copies.
          A backup, <code>System.json.wlib-backup</code>, is made the first time.
        </p>
        <p v-if="switchState?.message && !isBusy" role="status" class="text-xs" style="color: var(--text-muted)">
          {{ switchState.message }}
        </p>
      </section>
    </template>

    <p v-else-if="tools" class="text-xs" style="color: var(--text-muted)">No tools are available for this game.</p>

    <p v-if="lastResult && !longRun" role="status" class="text-xs" style="color: var(--text-secondary)">{{ lastResult }}</p>
  </div>
</template>

<style scoped>
.tools-card {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 1rem;
}

.tools-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  transition: all 0.15s ease;
}

.tools-btn:hover:not(:disabled) {
  background: var(--border-hover);
}

.tools-btn:disabled {
  cursor: not-allowed;
}

.tools-bar {
  height: 0.375rem;
  border-radius: 9999px;
  background: var(--bg-surface);
  border: 1px solid var(--border);
  overflow: hidden;
}

.tools-bar-fill {
  height: 100%;
  background: var(--brand);
  transition: width 0.2s ease;
}

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
  color: var(--text-secondary);
}
</style>
