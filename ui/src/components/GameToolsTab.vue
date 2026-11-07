<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { confirmAction } from "../utils/confirm";
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { IconLoader2 } from "@tabler/icons-vue";
import {
  api,
  type BepInExBuild,
  type GameToolEngine,
  type GameToolStatus,
  type GameToolsResponse,
  type RenpyExtractMode,
  type RpgmSaveSlot,
} from "../services/api";
import {
  consoleHint,
  formatToolBytes,
  formatToolProgress,
  getDecryptState,
  getExtractState,
  getResumableToolAction,
  getUrmToggleState,
  getUseDecryptedState,
  keySourceLabel,
  shouldSyncDeveloperOption,
  toolProgressPercent,
} from "../utils/gameTools";
import { unityActions, unityWarnings, switchWarning } from "../utils/unityTools";
import { notify, notifyError } from "../utils/toast";
import RpgmSaveEditor from "./modals/RpgmSaveEditor.vue";

const props = defineProps<{
  gameId: number;
  engine: GameToolEngine;
  // True while the Tools tab is showing; the tab loads on first show.
  active: boolean;
}>();

type BusyAction = "" | "console" | "extract" | "decrypt" | "switch" | "urm" | "bepinex_install" | "bepinex_uninstall" | "bepinex_restore";

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
const builds = ref<BepInExBuild[]>([]);
const buildId = ref("");
const major = ref(5);
const versionsLoaded = ref(false);
const versionLoading = ref(false);
const versionWarnings = ref<string[]>([]);
const removeUserData = ref(false);

// True when polling belongs to a run that was already going before the tab loaded.
const resumedRun = ref(false);

let loadedFor: number | null = null;
let requestId = 0;
let pollTimer: ReturnType<typeof setInterval> | null = null;

const stopPolling = () => {
  if (pollTimer !== null) clearInterval(pollTimer);
  pollTimer = null;
};

const startPolling = (gameId: number, watchCompletion = false) => {
  stopPolling();
  progress.value = null;
  cancelling.value = false;
  resumedRun.value = watchCompletion;
  pollTimer = setInterval(async () => {
    try {
      const status = await api.getGameToolStatus(gameId);
      if (props.gameId !== gameId || pollTimer === null) return;
      if (status.running) {
        progress.value = status;
        return;
      }
      if (!resumedRun.value) return;
      // A run started before this tab was shown ended: unlock the tab and reload.
      resumedRun.value = false;
      stopPolling();
      progress.value = null;
      busy.value = "";
      lastResult.value = "The tool run finished.";
      await load(gameId, true);
    } catch {
      // the run's own response reports failures
    }
  }, POLL_MS);
};

onBeforeUnmount(() => {
  // Drop in-flight loads too, so a late status response cannot start polling
  // for a tab that is already gone.
  requestId++;
  stopPolling();
});

const load = async (gameId: number, keepVisible = false) => {
  const request = ++requestId;
  loading.value = true;
  loadError.value = "";
  const previousConsole = tools.value?.renpy?.console;
  if (!keepVisible) tools.value = null;
  try {
    const result = await api.getGameTools(gameId);
    if (request !== requestId || props.gameId !== gameId) return;
    if (result.success === false) {
      tools.value = null;
      loadError.value = result.error || "Could not load the tools";
      return;
    }
    tools.value = result;
    const nextConsole = result.renpy?.console;
    if (shouldSyncDeveloperOption(previousConsole, nextConsole)) {
      developerOption.value = nextConsole === "developer";
    }
    // Re-attach a long run that started before this tab was (re)opened, so its
    // progress and Cancel are still reachable and no action is offered twice.
    try {
      const status = await api.getGameToolStatus(gameId);
      if (request !== requestId || props.gameId !== gameId || busy.value) return;
      const action = getResumableToolAction(status);
      if (action) {
        busy.value = action;
        startPolling(gameId, true);
        progress.value = status;
      }
    } catch {
      // The status is optional; the tab still works without it.
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
      resumedRun.value = false;
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
      builds.value = [];
      buildId.value = "";
      major.value = 5;
      versionsLoaded.value = false;
      versionLoading.value = false;
      versionWarnings.value = [];
      removeUserData.value = false;
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

const saveSlots = ref<RpgmSaveSlot[]>([]);
const saveSlotsLoaded = ref(false);
const saveSlotsError = ref("");
const editorFile = ref("");
const editorOpen = ref(false);
let saveSlotsToken = 0;

const loadSaveSlots = async (): Promise<void> => {
  const token = ++saveSlotsToken;
  saveSlotsError.value = "";
  try {
    const result = await api.listRpgmSaves(props.gameId);
    if (token !== saveSlotsToken) return;
    if (result.success === false || !("slots" in result)) {
      saveSlotsError.value = ("error" in result && result.error) || "Could not list the save files";
      saveSlots.value = [];
    } else {
      saveSlots.value = result.slots;
    }
  } catch (error) {
    if (token === saveSlotsToken) saveSlotsError.value = error instanceof Error ? error.message : "Could not list the save files";
  } finally {
    if (token === saveSlotsToken) saveSlotsLoaded.value = true;
  }
};

watch(
  () => [props.gameId, props.active, !!rpgm.value] as const,
  ([, active, hasRpgm]) => {
    if (active && hasRpgm) void loadSaveSlots();
  },
  { immediate: true },
);

const openEditor = (file: string): void => {
  editorFile.value = file;
  editorOpen.value = true;
};

const formatSlotTime = (iso: string): string => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString();
};
const unity = computed(() => tools.value?.unity ?? null);
const selectedBuild = computed(() => builds.value.find((build) => build.id === buildId.value) ?? null);
const filteredBuilds = computed(() => builds.value.filter((build) => build.major === major.value));
const unityState = computed(() => unity.value ? unityActions(unity.value, selectedBuild.value, isBusy.value) : null);
const pickBuild = () => {
  const options = filteredBuilds.value;
  const preferred = options.find((b) => b.channel === (major.value === 5 ? "stable" : "be")) ?? options[0];
  buildId.value = preferred?.id ?? "";
};
watch(major, pickBuild);
const loadVersions = async () => {
  const id = props.gameId;
  versionLoading.value = true;
  try {
    const result = await api.listBepinexVersions(id);
    if (props.gameId !== id) return;
    if (result.success === false) { notifyError(result.error || "Could not load versions"); return; }
    builds.value = result.builds ?? [];
    versionWarnings.value = result.warnings ?? [];
    major.value = result.recommended?.major ?? (unity.value?.build.backend === "il2cpp" ? 6 : 5);
    buildId.value = result.recommended?.id ?? "";
    if (!buildId.value) pickBuild();
    versionsLoaded.value = true;
  } catch (error) { notifyError(String(error)); }
  finally { if (props.gameId === id) versionLoading.value = false; }
};
// Load the version list once a Windows Unity game with a known build is shown,
// so the best-fitting build is preselected without an extra click.
watch(unity, (state) => {
  if (!state || versionsLoaded.value || versionLoading.value) return;
  if (state.platform === "windows" && state.build.backend && state.build.arch) void loadVersions();
});
const installBepinex = async () => {
  if (!unity.value || !selectedBuild.value) return;
  const warning = switchWarning(unity.value, selectedBuild.value);
  if (warning && !(await confirmAction({ title: "Install BepInEx", message: warning, confirmLabel: "Install" }))) return;
  const result = await run("bepinex_install", (id) => api.installBepinex(id, buildId.value));
  if (result) { if (result.warning) lastResult.value = result.warning; await load(props.gameId, true); }
};
const uninstallBepinex = async () => {
  const result = await run("bepinex_uninstall", (id) => api.uninstallBepinex(id, removeUserData.value));
  if (result) await load(props.gameId, true);
};
const restoreBepinex = async () => {
  const result = await run("bepinex_restore", (id) => api.restoreBepinex(id));
  if (result) await load(props.gameId, true);
};
const consoleOn = computed(() => (renpy.value?.console ?? "off") !== "off");
const longRun = computed(() => busy.value === "extract" || busy.value === "decrypt" || busy.value === "bepinex_install");

const extractState = computed(() =>
  getExtractState(renpy.value?.archives ?? [], extractMode.value, outputFolder.value, isBusy.value),
);
const decryptState = computed(() => (rpgm.value ? getDecryptState(rpgm.value, isBusy.value) : null));
const switchState = computed(() => (rpgm.value ? getUseDecryptedState(rpgm.value, isBusy.value) : null));
const urmState = computed(() => (renpy.value ? getUrmToggleState(renpy.value.urm, isBusy.value) : null));

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
  resumedRun.value = false;
  busy.value = action;
  lastResult.value = "";
  if (action === "extract" || action === "decrypt" || action === "bepinex_install") startPolling(gameId);
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
            :class="urmState?.enabled ? 'cursor-pointer' : 'cursor-not-allowed'"
          >
            <input
              v-model="urmInstalled"
              type="checkbox"
              class="sr-only peer"
              aria-label="Universal Ren'Py Mod (URM)"
              :disabled="!urmState?.enabled"
            />
            <div class="ui-toggle"></div>
          </label>
        </div>
        <p v-if="urmState?.message && !isBusy" class="text-xs" style="color: var(--text-muted)">
          {{ urmState.message }}
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

      <section class="tools-card space-y-3" aria-labelledby="tools-saveeditor-title">
        <span id="tools-saveeditor-title" class="text-sm font-medium block" style="color: var(--text-primary)">Save editor</span>
        <p v-if="saveSlotsError" role="alert" class="text-xs" style="color: var(--danger-text)">{{ saveSlotsError }}</p>
        <p v-else-if="saveSlotsLoaded && saveSlots.length === 0" class="text-xs" style="color: var(--text-muted)">No save files were found. Save in the game first.</p>
        <ul v-else class="space-y-1">
          <li v-for="slot in saveSlots" :key="slot.file" class="flex items-center gap-3">
            <div class="flex-1 min-w-0">
              <p class="text-sm" style="color: var(--text-primary)">
                Slot {{ slot.slot }}<span v-if="slot.title" class="text-xs" style="color: var(--text-muted)"> · {{ slot.title }}</span>
              </p>
              <p class="text-xs" style="color: var(--text-muted)">
                {{ formatSlotTime(slot.modified) }}<span v-if="slot.playtime"> · {{ slot.playtime }}</span>
              </p>
            </div>
            <button type="button" class="tools-btn ui-action-btn" :disabled="isBusy" @click="openEditor(slot.file)">Open</button>
          </li>
        </ul>
        <p class="text-[0.625rem]" style="color: var(--text-muted)">
          Edit gold, items, actors, switches and variables. Saving needs the game closed, and the saves folder is backed up first.
        </p>
      </section>
      <RpgmSaveEditor v-model="editorOpen" :game-id="gameId" :file="editorFile" @saved="loadSaveSlots" />
    </template>

    <template v-else-if="tools && engine === 'unity' && unity">
      <section class="tools-card space-y-3" aria-labelledby="tools-unity-title">
        <h3 id="tools-unity-title" class="text-sm font-medium" style="color: var(--text-primary)">BepInEx</h3>
        <p class="text-xs" style="color: var(--text-secondary)">Backend: {{ unity.build.backend || "Unknown" }} · Architecture: {{ unity.build.arch || "Unknown" }} · Unity: {{ unity.build.version || "Unknown" }}</p>
        <p class="text-xs" style="color: var(--text-secondary)">{{ unity.installed ? `BepInEx ${unity.installed.version || "Unknown"} (${unity.installed.backend}) ${unity.installed.complete ? "installed" : "incomplete"}` : "BepInEx is not installed." }}</p>
        <p v-for="warning in unityWarnings(unity)" :key="warning" class="text-xs" style="color: var(--text-muted)">{{ warning }}</p>
        <p v-if="unity.platform === 'linux'" class="text-xs" style="color: var(--text-muted)">{{ unityState?.install.message }}</p>
        <template v-else>
          <button v-if="!versionsLoaded" type="button" class="tools-btn disabled:opacity-50" :disabled="versionLoading || isBusy" @click="loadVersions">{{ versionLoading ? "Loading versions…" : "Load versions" }}</button>
          <template v-else>
            <div class="flex flex-wrap gap-3 items-center text-xs" style="color: var(--text-secondary)">
              <label v-if="unity.build.backend !== 'il2cpp'"><input v-model="major" type="radio" :value="5" :disabled="isBusy" /> BepInEx 5</label>
              <label><input v-model="major" type="radio" :value="6" :disabled="isBusy" /> BepInEx 6</label>
              <select v-model="buildId" class="tools-btn" :disabled="isBusy" aria-label="BepInEx version">
                <option v-for="build in filteredBuilds" :key="build.id" :value="build.id">{{ build.id }}{{ build.channel === 'be' ? ' (bleeding edge)' : '' }}</option>
              </select>
            </div>
            <button type="button" class="tools-btn disabled:opacity-50" :disabled="!unityState?.install.enabled" @click="installBepinex">{{ unity.installed ? "Update / Reinstall" : "Install" }}</button>
          </template>
          <p v-if="unityState?.install.message && !isBusy" class="text-xs" style="color: var(--text-muted)">{{ unityState.install.message }}</p>
          <p v-for="warning in versionWarnings" :key="warning" class="text-xs" style="color: var(--text-muted)">{{ warning }}</p>
          <label class="flex items-center gap-2 text-xs" style="color: var(--text-secondary)"><input v-model="removeUserData" type="checkbox" :disabled="isBusy" /> Also remove plugins and config</label>
          <div class="flex gap-2"><button type="button" class="tools-btn disabled:opacity-50" :disabled="!unityState?.uninstall.enabled" @click="uninstallBepinex">Uninstall</button><button type="button" class="tools-btn disabled:opacity-50" :disabled="!unityState?.restore.enabled" @click="restoreBepinex">Restore</button></div>
        </template>
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
