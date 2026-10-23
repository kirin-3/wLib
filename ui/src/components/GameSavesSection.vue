<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { IconFolderOpenFilled, IconLoader2, IconRefresh } from "@tabler/icons-vue";
import { api, type GameSavesResponse } from "../services/api";
import { formatBackupLabel, getSaveActionState } from "../utils/gameSaves";
import { notify, notifyError } from "../utils/toast";
import PathStatus from "./PathStatus.vue";

const props = defineProps<{
  gameId: number;
  // True while the Launch tab is showing; the section loads on first show.
  active: boolean;
}>();

type BusyAction = "" | "choose" | "backup" | "import" | "restore";

const saves = ref<GameSavesResponse | null>(null);
const loading = ref(false);
const loadError = ref("");
const busy = ref<BusyAction>("");
const restoring = ref("");

let loadedFor: number | null = null;
let requestId = 0;

const load = async (gameId: number, keepVisible = false) => {
  const request = ++requestId;
  loading.value = true;
  loadError.value = "";
  if (!keepVisible) saves.value = null;
  try {
    const result = await api.getGameSaves(gameId);
    if (request !== requestId || props.gameId !== gameId) return;
    if (result.success === false || !("detected" in result)) {
      saves.value = null;
      loadError.value = result.error || "Could not load saves";
    } else {
      saves.value = result;
    }
  } catch (error) {
    if (request !== requestId || props.gameId !== gameId) return;
    saves.value = null;
    loadError.value = String(error);
  } finally {
    if (request === requestId) loading.value = false;
  }
};

watch(
  () => [props.gameId, props.active] as const,
  ([gameId, active]) => {
    if (loadedFor !== gameId) {
      // A different game: drop the old payload and any in-flight response.
      requestId++;
      saves.value = null;
      loadError.value = "";
      loading.value = false;
      busy.value = "";
      restoring.value = "";
      loadedFor = null;
    }
    if (active && loadedFor !== gameId) {
      loadedFor = gameId;
      void load(gameId);
    }
  },
  { immediate: true },
);

const eligibleDetected = computed(
  () => saves.value?.detected.filter((entry) => entry.eligible).length ?? 0,
);

const actions = computed(() =>
  getSaveActionState({
    effective: saves.value?.effective ?? "",
    effectiveExists: saves.value?.effective_exists ?? false,
    eligibleDetected: eligibleDetected.value,
    detected: saves.value?.detected.length ?? 0,
    busy: busy.value !== "",
  }),
);

const keepLabel = computed(() => {
  const keep = saves.value?.keep ?? "all";
  return keep === "all" ? "All backups are kept." : `Keeping the newest ${keep} backups.`;
});

const open = async (path: string) => {
  try {
    const res = await api.openFolder(path);
    if (res && res.success === false) notifyError("Failed to open folder: " + (res.error || "Unknown error"));
  } catch (error) {
    notifyError("Error opening folder: " + String(error));
  }
};

const openBackup = async (name: string) => {
  try {
    const res = await api.openGameSaveBackup(props.gameId, name);
    if (res && res.success === false) notifyError("Failed to open backup: " + (res.error || "Unknown error"));
  } catch (error) {
    notifyError("Error opening backup: " + String(error));
  }
};

// Runs one save call; the section stays locked until it returns, and a
// response for a game that is no longer shown is dropped.
const run = async <T extends { success?: boolean; error?: string; message?: string }>(
  action: BusyAction,
  call: (gameId: number) => Promise<T>,
  successMessage?: (result: T) => string,
): Promise<T | null> => {
  if (busy.value) return null;
  const gameId = props.gameId;
  busy.value = action;
  try {
    const result = await call(gameId);
    if (props.gameId !== gameId) return null;
    if (result.success === false) {
      notifyError(result.error || "Save operation failed");
      return null;
    }
    const text = successMessage ? successMessage(result) : result.message;
    if (text) notify(text, "success");
    return result;
  } catch (error) {
    if (props.gameId === gameId) notifyError(String(error));
    return null;
  } finally {
    if (props.gameId === gameId) {
      busy.value = "";
      restoring.value = "";
    }
  }
};

const refresh = (gameId: number) => load(gameId, true);

const setFolder = async (path: string) => {
  const result = await run("choose", (id) => api.setGameSaveFolder(id, path));
  if (result) await refresh(props.gameId);
};

const chooseFolder = async () => {
  if (busy.value) return;
  try {
    const picked = await api.browseDirectory(saves.value?.override || saves.value?.effective || "");
    if (picked) await setFolder(picked);
  } catch (error) {
    notifyError("Error browsing folder: " + String(error));
  }
};

const backup = async () => {
  const result = await run("backup", (id) => api.backupGameSaves(id));
  if (result) await refresh(props.gameId);
};

const importFolder = async () => {
  if (busy.value) return;
  try {
    const picked = await api.browseDirectory("");
    if (!picked) return;
    const result = await run("import", (id) => api.importGameSaves(id, picked));
    if (result) await refresh(props.gameId);
  } catch (error) {
    notifyError("Error importing saves: " + String(error));
  }
};

const restore = async (name: string) => {
  if (busy.value) return;
  restoring.value = name;
  const result = await run("restore", (id) => api.restoreGameSavesBackup(id, name));
  if (result) await refresh(props.gameId);
};

const busyText = computed(() => {
  switch (busy.value) {
    case "backup":
      return "Backing up saves...";
    case "import":
      return "Importing saves...";
    case "restore":
      return "Restoring backup...";
    case "choose":
      return "Saving folder...";
    default:
      return "";
  }
});
</script>

<template>
  <div class="saves-section" :aria-busy="busy !== '' || loading">
    <div class="flex items-center justify-between mb-3">
      <h3 class="saves-label !mb-0">Saves</h3>
      <button
        type="button"
        class="saves-btn ui-action-btn !py-1 !text-xs disabled:opacity-50"
        :disabled="loading || busy !== ''"
        @click="load(gameId, true)"
      >
        <IconRefresh class="ui-action-icon" />
        Search again
      </button>
    </div>

    <p v-if="loading && !saves" class="text-xs flex items-center gap-2" style="color: var(--text-secondary)" role="status">
      <IconLoader2 class="ui-action-icon animate-spin" />
      Looking for saves...
    </p>
    <p v-else-if="loadError" role="alert" class="text-xs" style="color: var(--danger-text)">{{ loadError }}</p>

    <div v-else-if="saves" class="space-y-3">
      <div
        v-if="saves.override"
        class="rounded-lg p-3"
        style="background: var(--bg-raised); border: 1px solid var(--border)"
      >
        <p class="text-xs mb-1" style="color: var(--text-muted)">Saves folder (chosen by you)</p>
        <p class="text-xs font-mono break-all" style="color: var(--text-primary)">{{ saves.override }}</p>
        <PathStatus :path="saves.override" kind="dir" />
      </div>

      <template v-else>
        <p v-if="!saves.detected.length" class="text-xs" style="color: var(--text-muted)">
          No saves folder was found. The game may store saves in an unexpected location.
        </p>
        <ul v-else class="space-y-2">
          <li
            v-for="entry in saves.detected"
            :key="entry.path"
            class="flex items-center gap-2 rounded-lg p-2"
            style="background: var(--bg-raised); border: 1px solid var(--border)"
            :style="entry.path === saves.effective ? 'border-color: var(--brand)' : ''"
          >
            <div class="flex-1 min-w-0">
              <p class="truncate text-xs font-medium" style="color: var(--text-primary)">
                {{ entry.description }}
                <span
                  v-if="entry.path === saves.effective"
                  class="ml-1 text-[0.625rem]"
                  style="color: var(--brand)"
                  >In use</span
                >
              </p>
              <p class="truncate text-[0.625rem] font-mono" style="color: var(--text-muted)" :title="entry.path">
                {{ entry.path }}
              </p>
            </div>
            <span
              class="text-[0.625rem] px-1.5 py-0.5 rounded shrink-0"
              style="background: var(--bg-surface); color: var(--text-muted); border: 1px solid var(--border)"
              >{{ entry.type }}</span
            >
            <button
              type="button"
              class="saves-btn !py-1 !text-xs disabled:opacity-50"
              :disabled="busy !== ''"
              @click="open(entry.path)"
            >
              Open
            </button>
            <button
              type="button"
              class="saves-btn !py-1 !text-xs disabled:opacity-50"
              :disabled="busy !== ''"
              @click="setFolder(entry.path)"
            >
              Use this
            </button>
          </li>
        </ul>
        <p v-if="saves.detected.some((entry) => !entry.eligible)" class="text-[0.625rem] italic" style="color: var(--text-muted)">
          The game's own folder is only listed as a lead: choose "Use this" to treat it as the saves folder.
        </p>
      </template>

      <div class="flex flex-wrap gap-2">
        <button
          type="button"
          class="saves-btn ui-action-btn disabled:opacity-50"
          :disabled="!actions.canOpen"
          @click="open(saves.effective)"
        >
          <IconFolderOpenFilled class="ui-action-icon" />
          Open
        </button>
        <button
          type="button"
          class="saves-btn ui-action-btn disabled:opacity-50"
          :disabled="!actions.canBackup"
          @click="backup"
        >
          Backup
        </button>
        <button
          type="button"
          class="saves-btn ui-action-btn disabled:opacity-50"
          :disabled="!actions.canImport"
          @click="importFolder"
        >
          Import…
        </button>
        <button
          type="button"
          class="saves-btn ui-action-btn disabled:opacity-50"
          :disabled="busy !== ''"
          @click="chooseFolder"
        >
          Choose folder…
        </button>
        <button
          v-if="saves.override"
          type="button"
          class="saves-btn ui-action-btn disabled:opacity-50"
          :disabled="busy !== ''"
          @click="setFolder('')"
        >
          Use detected folder
        </button>
      </div>

      <p v-if="busyText" role="status" class="text-xs flex items-center gap-2" style="color: var(--text-secondary)">
        <IconLoader2 class="ui-action-icon animate-spin" />
        {{ busyText }}
      </p>
      <p v-else-if="actions.message" role="status" class="text-xs" style="color: var(--text-muted)">
        {{ actions.message }}
      </p>

      <div>
        <h4 class="saves-label">Backups</h4>
        <p v-if="!saves.backups.length" class="text-xs" style="color: var(--text-muted)">No backups yet.</p>
        <ul v-else class="space-y-2">
          <li
            v-for="item in saves.backups"
            :key="item.name"
            class="flex items-center gap-2 rounded-lg p-2"
            style="background: var(--bg-raised); border: 1px solid var(--border)"
          >
            <span class="flex-1 min-w-0 truncate text-sm" style="color: var(--text-primary)" :title="item.name">
              {{ formatBackupLabel(item.name) }}
            </span>
            <button
              type="button"
              class="saves-btn !py-1 !text-xs disabled:opacity-50"
              :disabled="busy !== ''"
              @click="openBackup(item.name)"
            >
              Open
            </button>
            <button
              type="button"
              class="saves-btn !py-1 !text-xs disabled:opacity-50"
              :disabled="!actions.canRestore"
              :title="actions.canRestore ? 'Merge this backup into the saves folder' : actions.message"
              @click="restore(item.name)"
            >
              {{ restoring === item.name ? "Restoring..." : "Restore" }}
            </button>
          </li>
        </ul>
        <p class="text-[0.625rem] mt-2" style="color: var(--text-muted)">
          {{ keepLabel }} Change this in Settings. Restore and Import add to your current saves and back them up first.
        </p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.saves-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 0.25rem;
}

.saves-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  transition: all 0.15s ease;
}

.saves-btn:hover:not(:disabled) {
  background: var(--border-hover);
}

.saves-btn:disabled {
  cursor: not-allowed;
}
</style>
