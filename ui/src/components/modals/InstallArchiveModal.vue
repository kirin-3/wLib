<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { IconCheck, IconFolderOpen, IconLoader2, IconX } from "@tabler/icons-vue";
import {
  api,
  type ArchiveInstallResponse,
  type GameRecord,
  type LibraryFolder,
  type InstallDestinationOptions,
} from "../../services/api";
import { notifyError } from "../../utils/toast";
import { useModalKeyboard } from "../../utils/modalKeyboard";
import { createInstallPreview, mergeArchiveFields, preselectLibraryFolder, formatLibraryFreeSpace, type InstallArchiveSeed } from "../../utils/installArchive";
import PathStatus from "../PathStatus.vue";

const props = defineProps<{
  modelValue: boolean;
  game?: GameRecord | null;
  preselectedArchive?: string;
  seed?: InstallArchiveSeed | undefined;
}>();
const emit = defineEmits<{ "update:modelValue": [value: boolean] }>();

type Phase = "pick" | "inspecting" | "ready" | "installing" | "done";

const phase = ref<Phase>("pick");
const archive = ref("");
const title = ref("");
const version = ref("");
const creator = ref("");
const engine = ref("");
const coverImage = ref("");
const tags = ref<string | string[]>("");
const rating = ref("");
const enginePending = ref(false);
const lookupMessage = ref("");
const lookupLoading = ref(false);
const previewError = ref("");
type DetailField = "title" | "version" | "creator" | "engine";
const edited = new Set<DetailField>();
let lookupRequestId = 0;
let lastLookupUrl = "";
const requestSeed = ref<InstallArchiveSeed | undefined>();
const engineOptions = ["ADRIFT", "Flash", "Godot", "HTML", "Java", "Others", "QSP", "RAGS", "Ren'Py", "RPGM", "Tads", "Unity", "Unreal Engine", "WebGL", "Wolf RPG"];

const f95Url = ref("");
const destination = ref("");
const toolError = ref("");
const oldFolder = ref("");
const removable = ref(false);
const removeReason = ref("");
const removeOld = ref(true);
const deleteArchive = ref(false);
const libraryFolder = ref("");
const libraryFolders = ref<LibraryFolder[]>([]);
const folderEdited = ref(false);
const targetId = ref<number | null>(null);
const ownTargetsInside = ref<string[]>([]);
const libraryFolderMissing = ref(false);
const inspectError = ref("");
const installError = ref("");
const installStage = ref("");
const result = ref<ArchiveInstallResponse | null>(null);
const duplicateGameId = ref<number | null>(null);
const inspecting = ref(false);

// The game the form was seeded for. Hosted once in App.vue, a new request can
// change props.game while the dialog is open (or installing); every read below
// goes through this snapshot so the header, inspect and install never mix games.
const target = ref<GameRecord | null>(null);
const seededArchive = ref("");
const isUpdate = computed(() => !!target.value);
const selectedFolderUnavailable = computed(() =>
  libraryFolders.value.some((folder) => folder.path === libraryFolder.value && !folder.available),
);
const canInstall = computed(
  () =>
    phase.value === "ready" &&
    archive.value.trim() !== "" &&
    title.value.trim() !== "" &&
    !toolError.value &&
    !libraryFolderMissing.value &&
    libraryFolders.value.some((folder) => folder.path === libraryFolder.value && folder.available) &&
    !inspecting.value &&
    !lookupLoading.value,
);

const close = () => {
  if (phase.value === "installing" || inspecting.value) return;
  emit("update:modelValue", false);
};

const modalRef = ref<HTMLElement | null>(null);
useModalKeyboard(modalRef, () => props.modelValue, close);

let inspectRequestId = 0;
// Title and version guessed from the current archive's file name.
let archiveGuess = { title: "", version: "" };

const reset = () => {
  inspectRequestId++;
  archiveGuess = { title: "", version: "" };
  lookupRequestId++;
  lastLookupUrl = "";
  preview.cancel();
  edited.clear();
  engine.value = "";
  coverImage.value = "";
  tags.value = "";
  rating.value = "";
  enginePending.value = false;
  lookupMessage.value = "";
  lookupLoading.value = false;
  previewError.value = "";
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
  libraryFolders.value = [];
  folderEdited.value = false;
  targetId.value = null;
  ownTargetsInside.value = [];
  libraryFolderMissing.value = false;
  inspectError.value = "";
  installError.value = "";
  installStage.value = "";
  result.value = null;
  duplicateGameId.value = null;
  inspecting.value = false;
};

const preview = createInstallPreview(
  (options: InstallDestinationOptions) => api.previewInstallDestination(options),
  (info) => {
    destination.value = info.destination;
    enginePending.value = info.engine_pending;
    previewError.value = "";
  },
  () => {
    destination.value = "";
    previewError.value = "Could not preview the destination folder.";
  },
);
const updatePreview = () => {
  if (!props.modelValue || phase.value === "installing" || phase.value === "done") return;
  preview.schedule({
    title: title.value, version: version.value, creator: creator.value,
    engine: engine.value, game_id: target.value?.id, library_folder: libraryFolder.value,
  });
};
watch([title, version, creator, engine, libraryFolder], updatePreview);

const lookupDetails = async () => {
  const url = f95Url.value.trim();
  if (isUpdate.value || requestSeed.value || !url || url === lastLookupUrl || phase.value === "installing") return;
  lastLookupUrl = url;
  const requestId = ++lookupRequestId;
  lookupLoading.value = true;
  lookupMessage.value = "";
  try {
    const info = await api.lookupThreadDetails(url);
    if (requestId !== lookupRequestId || !props.modelValue || f95Url.value.trim() !== url) return;
    if (!info.success) throw new Error(info.error || "Thread unavailable");
    const fields = mergeArchiveFields(
      { title: title.value, version: version.value, creator: creator.value, engine: engine.value },
      { title: info.title || "", version: info.version || "", creator: info.creator || "", engine: info.engine || "" },
      edited,
    );
    title.value = fields.title;
    version.value = fields.version;
    creator.value = fields.creator;
    engine.value = fields.engine;
    coverImage.value = info.cover_image || "";
    tags.value = info.tags || "";
  } catch (error) {
    if (requestId !== lookupRequestId || !props.modelValue || f95Url.value.trim() !== url) return;
    lookupMessage.value = "Thread details could not be loaded. You can still install using the details above.";
    console.error("Failed to load thread details", error);
  } finally {
    if (requestId === lookupRequestId) lookupLoading.value = false;
  }
};
// Invalidate as soon as the URL is edited, before a new commit starts a lookup.
watch(f95Url, () => {
  lookupRequestId++;
  lookupLoading.value = false;
  lookupMessage.value = "";
  lastLookupUrl = "";
  if (!requestSeed.value && !isUpdate.value) {
    coverImage.value = "";
    tags.value = "";
  }
}, { flush: "sync" });

const loadLibraryFolder = async () => {
  try {
    const [settings, folders] = await Promise.all([api.getSettings(), api.listLibraryFolders()]);
    if (!props.modelValue) return;
    libraryFolders.value = folders;
    if (!libraryFolder.value) libraryFolder.value = settings?.library_folder || "";
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
    const info = await api.inspectArchive(path, target.value?.id ?? null, targetId.value, folderEdited.value ? libraryFolder.value : null);
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
    // A guess from a previously picked archive is replaced; thread, seed and typed values are kept.
    const replaceable = (field: "title" | "version", value: string) =>
      !edited.has(field) && (!value.trim() || value === archiveGuess[field]);
    if (replaceable("title", title.value)) title.value = isUpdate.value ? (target.value?.title || info.title || "") : (info.title || "");
    if (target.value?.latest_version?.trim() && !edited.has("version")) {
      version.value = target.value.latest_version.trim();
    } else if (replaceable("version", version.value)) {
      version.value = info.version || "";
    }
    archiveGuess = { title: info.title || "", version: info.version || "" };
    if (!engine.value.trim() && !edited.has("engine")) engine.value = info.engine || "";
    if (!creator.value.trim() && !edited.has("creator")) creator.value = isUpdate.value ? (target.value?.developer || info.creator || "") : (info.creator || "");
    libraryFolder.value = preselectLibraryFolder(libraryFolder.value, info.library_folder, folderEdited.value);
    ownTargetsInside.value = info.own_targets_inside || [];
    destination.value = info.destination || "";
    enginePending.value = !!info.engine_pending;
    toolError.value = info.tool_error || "";
    oldFolder.value = info.old_folder || "";
    removable.value = !!info.removable;
    removeReason.value = info.remove_reason || "";
    if (!removable.value) removeOld.value = false;
    else if (isUpdate.value) removeOld.value = true;
    phase.value = "ready";
    updatePreview();
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
    const res = await api.saveSettings({ library_folder: path, library_folders: [path] });
    if (!props.modelValue) return;
    if (res?.success === false) throw new Error(res.error || "Could not save library folder");
    libraryFolder.value = path;
    libraryFolders.value = await api.listLibraryFolders();
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
  preview.cancel();
  lookupRequestId++;
});

const install = async () => {
  if (!canInstall.value) return;
  preview.cancel();
  lookupRequestId++;
  phase.value = "installing";
  installError.value = "";
  installStage.value = "starting";
  duplicateGameId.value = null;
  pollStatus();
  try {
    const res = await api.installArchive({
      archive_path: archive.value.trim(),
      game_id: target.value?.id ?? undefined,
      title: title.value.trim(),
      version: version.value.trim(),
      creator: creator.value.trim(),
      engine: engine.value.trim(),
      cover_image: coverImage.value,
      tags: tags.value,
      rating: rating.value,
      f95_url: f95Url.value.trim(),
      links: requestSeed.value?.links,
      library_folder: libraryFolder.value || undefined,
      target_id: targetId.value,
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

const seedForm = () => {
  reset();
  target.value = props.game ?? null;
  seededArchive.value = props.preselectedArchive || "";
  requestSeed.value = props.seed;
  if (target.value) {
    title.value = target.value.title || "";
    creator.value = target.value.developer || "";
    engine.value = target.value.engine || "";
    f95Url.value = target.value.f95_url || "";
    // The installed version is never the update's version; let the archive name fill it.
    version.value = target.value.latest_version?.trim() || "";
  }
  else if (requestSeed.value) {
    const values = requestSeed.value;
    title.value = values.title;
    version.value = values.version;
    creator.value = values.creator;
    engine.value = values.engine;
    f95Url.value = values.f95Url;
    coverImage.value = values.coverImage;
    tags.value = values.tags;
    rating.value = values.rating;
  }
  const requestId = inspectRequestId;
  void loadLibraryFolder().then(() => {
    if (!props.modelValue || requestId !== inspectRequestId) return;
    void lookupDetails();
    if (seededArchive.value) {
      archive.value = seededArchive.value;
      void inspect();
    }
  });
};

const hasPendingRequest = () =>
  (props.game?.id ?? null) !== (target.value?.id ?? null) ||
  (props.preselectedArchive || "") !== seededArchive.value ||
  props.seed !== requestSeed.value;

// Re-seed when the dialog opens or a new request (another game or archive) lands
// while it is open; a superseded inspection is cheap to cancel. A running install
// owns the form, so the request waits until the install ends (see below).
watch(
  () => [props.modelValue, props.game?.id ?? null, props.preselectedArchive, props.seed] as const,
  ([open]) => {
    if (!open) {
      stopPolling();
      reset();
      target.value = null;
      seededArchive.value = "";
      requestSeed.value = undefined;
      return;
    }
    if (phase.value !== "installing") seedForm();
  },
);

// A failed install returns the form to "ready"; apply a request that arrived
// meanwhile instead of letting a retry reuse the old game's fields. A finished
// install keeps showing its result for the game it installed.
watch(phase, (next, prev) => {
  if (prev !== "installing" || next !== "ready" || !props.modelValue || !hasPendingRequest()) return;
  if (installError.value) notifyError(`Install for ${target.value?.title || "archive"} failed: ${installError.value}`);
  seedForm();
});
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
      class="modal-content w-full max-w-2xl rounded-2xl relative overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.9)] outline-none"
    >
      <div
        class="px-6 py-5 flex items-center justify-between"
        style="border-bottom: 1px solid var(--border)"
      >
        <div>
          <h3 id="install-archive-title" class="text-xl font-bold" style="color: var(--text-primary)">
            {{ isUpdate ? `Install update for ${target?.title}` : "Install from archive" }}
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

        <div v-if="libraryFolders.length" class="space-y-2">
          <label for="install-library-folder" class="text-sm">Library folder</label>
          <select v-if="libraryFolders.length > 1" id="install-library-folder" v-model="libraryFolder" @change="folderEdited = true; inspect()" :disabled="phase === 'installing'" class="modal-input w-full text-sm">
            <option v-for="folder in libraryFolders" :key="folder.path" :value="folder.path" :disabled="!folder.available">{{ folder.path }} — {{ formatLibraryFreeSpace(folder.available ? folder.free_bytes : null) }}</option>
          </select>
          <p v-else class="text-sm font-mono break-all">{{ libraryFolders[0]?.path }} — {{ formatLibraryFreeSpace(libraryFolders[0]?.available ? libraryFolders[0]?.free_bytes ?? null : null) }}</p>
          <p v-if="selectedFolderUnavailable" class="text-xs" style="color: var(--warning-text)">This library folder is unavailable. Mount its drive or choose another folder.</p>
        </div>
        <div v-if="target?.launch_targets?.length" class="space-y-2">
          <label for="install-part" class="text-sm">Part to update</label>
          <select id="install-part" v-model="targetId" @change="inspect()" :disabled="phase === 'installing'" class="modal-input w-full text-sm">
            <option :value="null">Default</option>
            <option v-for="part in target.launch_targets" :key="part.id" :value="part.id">{{ part.label }}</option>
          </select>
        </div>
        <p v-if="ownTargetsInside.length" class="text-sm" style="color: var(--warning-text)">The old folder is kept if these targets are not found in the new version: {{ ownTargetsInside.join(', ') }}.</p>

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
            <input v-model="title" @input="edited.add('title')" type="text" class="modal-input w-full text-sm" :disabled="phase === 'installing'" @keydown.enter="canInstall && install()" />
          </label>
          <label class="block">
            <span class="field-label">Version</span>
            <input v-model="version" @input="edited.add('version')" type="text" class="modal-input w-full text-sm font-mono" :disabled="phase === 'installing'" @keydown.enter="canInstall && install()" />
          </label>
          <label class="block">
            <span class="field-label">Creator</span>
            <input v-model="creator" @input="edited.add('creator')" type="text" class="modal-input w-full text-sm" :disabled="phase === 'installing'" @keydown.enter="canInstall && install()" />
          </label>
          <label class="block">
            <span class="field-label">Engine</span>
            <input v-model="engine" @input="edited.add('engine')" list="install-engine-options" type="text" class="modal-input w-full text-sm" :disabled="phase === 'installing'" />
            <datalist id="install-engine-options">
              <option v-for="option in engineOptions" :key="option" :value="option" />
            </datalist>
          </label>
          <label class="block">
            <span class="field-label">F95Zone thread URL</span>
            <input v-model="f95Url" @change="lookupDetails" type="text" placeholder="https://f95zone.to/threads/…" class="modal-input w-full text-sm font-mono" :disabled="phase === 'installing'" @keydown.enter.prevent="lookupDetails" />
          </label>
        </div>

        <p v-if="lookupLoading" class="text-xs" style="color: var(--text-muted)">Loading thread details…</p>
        <p v-if="lookupMessage" class="text-xs" style="color: var(--warning-text)">{{ lookupMessage }}</p>
        <p v-if="previewError" class="text-xs" style="color: var(--warning-text)">{{ previewError }}</p>
        <p v-if="enginePending" class="text-xs" style="color: var(--text-muted)">The engine will be detected from the files before choosing the final folder.</p>
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
