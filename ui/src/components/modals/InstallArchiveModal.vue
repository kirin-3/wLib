<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { IconCheck, IconFolderOpen, IconLoader2, IconX } from "@tabler/icons-vue";
import {
  api,
  type ArchiveInstallResponse,
  type GameRecord,
} from "../../services/api";
import { notifyError } from "../../utils/toast";
import { useModalKeyboard } from "../../utils/modalKeyboard";
import PathStatus from "../PathStatus.vue";

const props = defineProps<{
  modelValue: boolean;
  game?: GameRecord | null;
  preselectedArchive?: string;
}>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

type Phase = "pick" | "inspecting" | "ready" | "installing" | "done";

const phase = ref<Phase>("pick");
const archive = ref("");
const title = ref("");
const version = ref("");
const creator = ref("");
const f95Url = ref("");
const destination = ref("");
const toolError = ref("");
const oldFolder = ref("");
const removable = ref(false);
const removeReason = ref("");
const removeOld = ref(true);
const deleteArchive = ref(false);
const libraryFolder = ref("");
const libraryFolderMissing = ref(false);
const inspectError = ref("");
const installError = ref("");
const installStage = ref("");
const result = ref<ArchiveInstallResponse | null>(null);
const duplicateGameId = ref<number | null>(null);
const inspecting = ref(false);

const isUpdate = computed(() => !!props.game);
const canInstall = computed(
  () =>
    phase.value === "ready" &&
    archive.value.trim() !== "" &&
    title.value.trim() !== "" &&
    !toolError.value &&
    !libraryFolderMissing.value &&
    !inspecting.value,
);

const close = () => {
  if (phase.value === "installing" || inspecting.value) return;
  emit("update:modelValue", false);
};

const modalRef = ref<HTMLElement | null>(null);
useModalKeyboard(modalRef, () => props.modelValue, close);

let inspectRequestId = 0;

const reset = () => {
  inspectRequestId++;
  phase.value = "pick";
  archive.value = "";
  title.value = "";
  version.value = "";
  creator.value = "";
  f95Url.value = "";
  destination.value = "";
  toolError.value = "";
  oldFolder.value = "";
  removable.value = false;
  removeReason.value = "";
  removeOld.value = true;
  deleteArchive.value = false;
  libraryFolder.value = "";
  libraryFolderMissing.value = false;
  inspectError.value = "";
  installError.value = "";
  installStage.value = "";
  result.value = null;
  duplicateGameId.value = null;
  inspecting.value = false;
};

const loadLibraryFolder = async () => {
  try {
    const settings = await api.getSettings();
    if (!props.modelValue) return;
    libraryFolder.value = settings?.library_folder || "";
    libraryFolderMissing.value = !libraryFolder.value.trim();
  } catch (e) {
    console.error("Failed to load library folder", e);
  }
};

const inspect = async () => {
  const path = archive.value.trim();
  // A newer inspect supersedes any in-flight one via inspectRequestId.
  if (!path) return;
  const requestId = ++inspectRequestId;
  inspecting.value = true;
  phase.value = "inspecting";
  inspectError.value = "";
  installError.value = "";
  duplicateGameId.value = null;
  try {
    const info = await api.inspectArchive(path, props.game?.id ?? null);
    if (requestId !== inspectRequestId || !props.modelValue) return;
    // The field was edited without committing; wait for its change event.
    if (archive.value.trim() !== path) {
      phase.value = "pick";
      return;
    }
    if (!info?.success) {
      inspectError.value = info?.error || "Could not read the archive.";
      phase.value = "pick";
      return;
    }
    if (!title.value.trim()) title.value = isUpdate.value ? (props.game?.title || info.title || "") : (info.title || "");
    if (props.game?.latest_version?.trim()) {
      version.value = props.game.latest_version.trim();
    } else if (!version.value.trim()) {
      version.value = info.version || "";
    }
    if (!creator.value.trim()) creator.value = isUpdate.value ? (props.game?.developer || info.creator || "") : (info.creator || "");
    destination.value = info.destination || "";
    toolError.value = info.tool_error || "";
    oldFolder.value = info.old_folder || "";
    removable.value = !!info.removable;
    removeReason.value = info.remove_reason || "";
    if (!removable.value) removeOld.value = false;
    else if (isUpdate.value) removeOld.value = true;
    phase.value = "ready";
  } catch (e) {
    if (requestId !== inspectRequestId || !props.modelValue) return;
    inspectError.value = e instanceof Error ? e.message : String(e);
    phase.value = "pick";
  } finally {
    if (requestId === inspectRequestId) {
      inspecting.value = false;
    }
  }
};

const browseArchive = async () => {
  try {
    const path = await api.browseArchiveFile(archive.value);
    if (path && props.modelValue) {
      archive.value = path;
      version.value = "";
      if (!isUpdate.value) {
        title.value = "";
        creator.value = "";
      }
      await inspect();
    }
  } catch (e) {
    console.error("Failed to browse archive", e);
  }
};

const browseLibraryFolder = async () => {
  try {
    const path = await api.browseDirectory(libraryFolder.value);
    if (!path || !props.modelValue) return;
    const res = await api.saveSettings({ library_folder: path });
    if (!props.modelValue) return;
    if (res?.success === false) throw new Error(res.error || "Could not save library folder");
    libraryFolder.value = path;
    libraryFolderMissing.value = false;
    if (archive.value.trim()) await inspect();
  } catch (e) {
    if (props.modelValue) {
      notifyError(e instanceof Error ? e.message : String(e));
    }
  }
};

// Recursive setTimeout so status requests never overlap; bumping pollId cancels the loop.
let pollId = 0;
let pollTimer: ReturnType<typeof setTimeout> | null = null;

const stopPolling = () => {
  pollId++;
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
};

const pollStatus = () => {
  stopPolling();
  const id = pollId;
  const tick = async () => {
    try {
      const status = await api.getArchiveInstallStatus();
      if (id !== pollId || phase.value !== "installing") return;
      if (status?.stage) installStage.value = status.stage;
    } catch (e) {
      console.error("Failed to poll install status", e);
    }
    if (id !== pollId || phase.value !== "installing") return;
    pollTimer = setTimeout(() => {
      pollTimer = null;
      if (id === pollId && phase.value === "installing") void tick();
    }, 500);
  };
  void tick();
};

onBeforeUnmount(() => {
  stopPolling();
});

const install = async () => {
  if (!canInstall.value) return;
  phase.value = "installing";
  installError.value = "";
  installStage.value = "starting";
  duplicateGameId.value = null;
  pollStatus();
  try {
    const res = await api.installArchive({
      archive_path: archive.value.trim(),
      game_id: props.game?.id ?? undefined,
      title: title.value.trim(),
      version: version.value.trim(),
      creator: creator.value.trim(),
      f95_url: f95Url.value.trim(),
      library_folder: libraryFolder.value || undefined,
      remove_old: removeOld.value,
      delete_archive: deleteArchive.value,
    });
    if (!res?.success) {
      installError.value = res?.error || "Install failed.";
      const payload = res as unknown as Record<string, unknown>;
      duplicateGameId.value =
        typeof payload?.game_id === "number" ? payload.game_id : null;
      phase.value = "ready";
      return;
    }
    result.value = res;
    phase.value = "done";
  } catch (e) {
    installError.value = e instanceof Error ? e.message : String(e);
    phase.value = "ready";
  } finally {
    stopPolling();
    installStage.value = "";
  }
};

watch(
  () => props.modelValue,
  (open) => {
    if (!open) {
      stopPolling();
      reset();
      return;
    }
    reset();
    if (props.game) {
      title.value = props.game.title || "";
      creator.value = props.game.developer || "";
      f95Url.value = props.game.f95_url || "";
      // The installed version is never the update's version; let the archive name fill it.
      version.value = props.game.latest_version?.trim() || "";
    }
    void loadLibraryFolder().then(() => {
      if (!props.modelValue) return;
      if (props.preselectedArchive) {
        archive.value = props.preselectedArchive;
        void inspect();
      }
    });
  },
);
</script>

<template>
  <div v-if="modelValue" class="fixed inset-0 z-50 flex items-center justify-center p-4">
    <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>

    <div
      ref="modalRef"
      role="dialog"
      aria-modal="true"
      aria-labelledby="install-archive-title"
      tabindex="-1"
      class="modal-content w-full max-w-2xl rounded-2xl relative overflow-hidden flex flex-col max-h-[90vh] outline-none"
    >
      <div
        class="px-6 py-5 flex items-center justify-between"
        style="border-bottom: 1px solid var(--border)"
      >
        <div>
          <h3 id="install-archive-title" class="text-xl font-bold" style="color: var(--text-primary)">
            {{ isUpdate ? `Install update for ${props.game?.title}` : "Install from archive" }}
          </h3>
          <p class="text-xs mt-1" style="color: var(--text-muted)">
            {{
              isUpdate
                ? "Extracts the archive into your library folder, carries saves over, and points the game at the new version."
                : "Extracts a downloaded .zip, .7z or .rar into your library folder and adds it as a game."
            }}
          </p>
        </div>
        <button
          @click="close"
          aria-label="Close"
          title="Close (Esc)"
          class="close-btn shrink-0 ml-4 p-1 rounded-lg"
        >
          <IconX class="w-5 h-5" />
        </button>
      </div>

      <div class="p-6 space-y-4 overflow-y-auto flex-1 min-h-0">
        <div
          v-if="libraryFolderMissing"
          class="rounded-lg px-4 py-3 text-sm"
          style="background: var(--warning-bg); color: var(--warning-text); border: 1px solid var(--warning-border)"
        >
          Choose a library folder first — installs are placed inside it.
          <button @click="browseLibraryFolder" class="link-btn ml-2 font-semibold">Choose folder</button>
        </div>

        <div class="flex gap-2">
          <input
            v-model="archive"
            type="text"
            placeholder="/path/to/Game-1.0-pc.zip"
            aria-label="Archive file"
            class="modal-input flex-1 text-sm font-mono"
            :disabled="phase === 'installing'"
            @change="inspect"
          />
          <button @click="browseArchive" :disabled="phase === 'installing'" class="modal-btn">
            <IconFolderOpen class="w-4 h-4 mr-1" />
            Browse
          </button>
        </div>
        <PathStatus :path="archive" kind="file" />

        <p v-if="inspectError" class="text-sm" style="color: var(--error-text)">{{ inspectError }}</p>
        <p v-if="toolError" class="text-sm" style="color: var(--error-text)">{{ toolError }}</p>

        <div v-if="phase !== 'pick'" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label class="block">
            <span class="field-label">Title</span>
            <input v-model="title" type="text" class="modal-input w-full text-sm" :disabled="phase === 'installing'" @keydown.enter="canInstall && install()" />
          </label>
          <label class="block">
            <span class="field-label">Version</span>
            <input v-model="version" type="text" class="modal-input w-full text-sm font-mono" :disabled="phase === 'installing'" @keydown.enter="canInstall && install()" />
          </label>
          <label class="block">
            <span class="field-label">Creator</span>
            <input v-model="creator" type="text" class="modal-input w-full text-sm" :disabled="phase === 'installing'" @keydown.enter="canInstall && install()" />
          </label>
          <label class="block">
            <span class="field-label">F95Zone thread URL</span>
            <input v-model="f95Url" type="text" placeholder="https://f95zone.to/threads/…" class="modal-input w-full text-sm font-mono" :disabled="phase === 'installing'" @keydown.enter="canInstall && install()" />
          </label>
        </div>

        <p v-if="destination" class="text-xs font-mono break-all" style="color: var(--text-muted)">
          Installs to: {{ destination }}
        </p>

        <div v-if="isUpdate && oldFolder" class="space-y-2">
          <label class="flex items-start gap-2 text-sm" :class="{ 'opacity-60': !removable }" style="color: var(--text-primary)">
            <input v-model="removeOld" type="checkbox" :disabled="!removable || phase === 'installing'" class="mt-1" />
            <span>
              Remove old version
              <span class="block text-xs font-mono" style="color: var(--text-muted)">{{ oldFolder }}</span>
              <span v-if="!removable && removeReason" class="block text-xs" style="color: var(--warning-text)">
                Kept automatically: {{ removeReason }}
              </span>
            </span>
          </label>
        </div>

        <label class="flex items-center gap-2 text-sm" style="color: var(--text-primary)">
          <input v-model="deleteArchive" type="checkbox" :disabled="phase === 'installing'" />
          Delete archive after install
        </label>

        <div v-if="phase === 'installing'" class="flex items-center gap-2 text-sm" style="color: var(--text-secondary)">
          <IconLoader2 class="w-4 h-4 animate-spin" />
          {{ installStage || "Working…" }} — the rest of the app stays usable.
        </div>

        <p v-if="installError" class="text-sm" style="color: var(--error-text)">
          {{ installError }}
          <span v-if="duplicateGameId !== null"> (Already in library as game #{{ duplicateGameId }} — install the archive as an update to that game instead.)</span>
        </p>

        <div
          v-if="phase === 'done' && result"
          class="rounded-lg px-4 py-3 text-sm space-y-1"
          style="background: var(--success-bg); color: var(--success-text); border: 1px solid var(--success-border)"
        >
          <p class="font-semibold flex items-center gap-2">
            <IconCheck class="w-4 h-4" />
            Installed to {{ String(result.destination || "") }}
          </p>
          <p v-if="result.kept_old_folder_reason" class="text-xs">
            Old folder kept: {{ String(result.kept_old_folder_reason) }}
          </p>
          <div v-if="Array.isArray(result.unmoved_targets) && result.unmoved_targets.length" class="text-xs">
            <p>These launch targets were not found in the new version and need attention:</p>
            <ul class="list-disc ml-5">
              <li v-for="(target, index) in result.unmoved_targets" :key="index" class="font-mono">
                {{ String((target as Record<string, unknown>).label || (target as Record<string, unknown>).exe_path) }}
              </li>
            </ul>
          </div>
        </div>
      </div>

      <div
        class="px-6 py-4 flex justify-end gap-3"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <button
          v-if="phase !== 'done'"
          @click="close"
          :disabled="phase === 'installing'"
          class="px-5 py-2.5 rounded-lg text-sm font-medium"
          style="color: var(--text-secondary)"
        >
          Cancel
        </button>
        <button
          v-if="phase === 'done'"
          @click="close"
          class="px-6 py-2.5 rounded-lg text-sm font-bold"
          style="background: var(--brand); color: var(--on-brand); box-shadow: var(--shadow-brand)"
        >
          Close
        </button>
        <button
          v-else
          @click="install"
          :disabled="!canInstall"
          class="px-6 py-2.5 rounded-lg text-sm font-bold disabled:opacity-50 disabled:cursor-not-allowed"
          style="background: var(--brand); color: var(--on-brand); box-shadow: var(--shadow-brand)"
        >
          {{ phase === "installing" ? "Installing…" : isUpdate ? "Install update" : "Install game" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
}

.modal-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.5rem 1rem;
  color: var(--text-primary);
}
.modal-input::placeholder {
  color: var(--text-muted);
}
.modal-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}
.modal-input:disabled {
  opacity: 0.6;
}

.modal-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 4.5rem;
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.875rem;
  font-weight: 500;
}
.modal-btn:hover:not(:disabled) {
  background: var(--border-hover);
}
.modal-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.close-btn {
  color: var(--text-muted);
}
.close-btn:hover {
  color: var(--text-primary);
}

.link-btn {
  color: var(--brand);
}
.link-btn:hover {
  text-decoration: underline;
}

.field-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 600;
  margin-bottom: 0.25rem;
  color: var(--text-secondary);
}
</style>
