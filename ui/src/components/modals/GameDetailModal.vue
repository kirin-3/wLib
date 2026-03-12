<script setup lang="ts">
import { ref, watch, computed } from "vue";
import { api } from "../../services/api";
import type { GameRecord, RunnerInfo, SaveLocation } from "../../services/api";

type PlayStatus = "Playing" | "Completed" | "On Hold" | "Plan to Play";

const props = defineProps<{
  modelValue: boolean;
  game: GameRecord | null;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  updated: [];
  deleted: [];
  launch: [payload: GameRecord];
}>();

// Editable fields
const title = ref("");
const exePath = ref("");
const f95Url = ref("");
const version = ref("");
const commandLineArgs = ref("");
const coverImage = ref("");
const status = ref("");
const playStatus = ref<PlayStatus>("Plan to Play");
const isFavorite = ref(false);
const tags = ref<string[]>([]);
const engine = ref("");
const newTag = ref("");
const latestVersion = ref("");
const runJapaneseLocale = ref(false);
const runWayland = ref(false);
const autoInjectCe = ref(false);
const customPrefix = ref("");
const protonVersion = ref("");
const useCustomPrefix = ref(false);
const availableRunners = ref<RunnerInfo[]>([]);
const loadingRunners = ref(false);
const runnersLoaded = ref(false);
const ceInstalled = ref(false);
const showJapaneseLocaleInfo = ref(false);
const showWaylandInfo = ref(false);
const showCheatEngineInfo = ref(false);
const tagsExpanded = ref(false);
const executableModifiedAt = ref<string | null>(null);
const loadingExecutableModifiedAt = ref(false);
const executableModifiedKnown = ref(false);
const executableModifiedRequestId = ref(0);

// F95Zone rating (read-only, from scraper)
const f95Rating = ref("");

// Personal ratings (0-5 scale)
const ratingGraphics = ref(0);
const ratingStory = ref(0);
const ratingFappability = ref(0);
const ratingGameplay = ref(0);

const saving = ref(false);
const deleting = ref(false);

const statuses: Array<{ value: PlayStatus; label: string }> = [
  { value: "Playing", label: "🎮 Playing" },
  { value: "Completed", label: "✅ Completed" },
  { value: "On Hold", label: "⏸️ On Hold" },
  { value: "Plan to Play", label: "🗓️ Plan to Play" },
];

const isPlayStatus = (value: string | undefined): value is PlayStatus =>
  value === "Playing" ||
  value === "Completed" ||
  value === "On Hold" ||
  value === "Plan to Play";

const averagePersonalRating = computed(() => {
  const sum =
    ratingGraphics.value +
    ratingStory.value +
    ratingFappability.value +
    ratingGameplay.value;
  const avg = sum / 4;
  return avg > 0 ? avg.toFixed(1) : "—";
});

const hasUpdate = computed(() => {
  return (
    latestVersion.value &&
    version.value &&
    latestVersion.value.trim() !== version.value.trim()
  );
});

const lastSyncedId = ref<number | null>(null);

watch(
  () => [props.modelValue, props.game] as const,
  ([isOpen, g]) => {
    if (isOpen && g && typeof g.id === "number") {
      if (g.id !== lastSyncedId.value) {
        lastSyncedId.value = g.id;
        title.value = g.title || "";
        exePath.value = g.exe_path || "";
        f95Url.value = g.f95_url || "";
        version.value = g.version || "";
        commandLineArgs.value = g.command_line_args || "";
        coverImage.value = g.cover_image_path || g.cover_image || "";
        status.value = g.status || "";
        playStatus.value = isPlayStatus(g.play_status)
          ? g.play_status
          : "Plan to Play";
        isFavorite.value = !!g.is_favorite;
        engine.value = g.engine || "";
        runJapaneseLocale.value = g.run_japanese_locale ? true : false;
        runWayland.value = g.run_wayland ? true : false;
        autoInjectCe.value = g.auto_inject_ce ? true : false;
        customPrefix.value = g.custom_prefix || "";
        protonVersion.value = g.proton_version || "";
        useCustomPrefix.value = !!g.custom_prefix || !!g.proton_version;
        showJapaneseLocaleInfo.value = false;
        showWaylandInfo.value = false;
        showCheatEngineInfo.value = false;
        tagsExpanded.value = false;
        if (typeof g.tags === "string" && g.tags) {
          tags.value = g.tags
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean);
        } else if (Array.isArray(g.tags)) {
          tags.value = g.tags
            .map((tag) => String(tag).trim())
            .filter(Boolean);
        } else {
          tags.value = [];
        }
        latestVersion.value = g.latest_version || "";
        f95Rating.value = g.rating || "";

        ratingGraphics.value = g.rating_graphics || 0;
        ratingStory.value = g.rating_story || 0;
        ratingFappability.value = g.rating_fappability || 0;
        ratingGameplay.value = g.rating_gameplay || 0;
      }
    } else if (!isOpen) {
      lastSyncedId.value = null;
    }
  },
  { immediate: true },
);

watch(
  () => props.modelValue,
  async (open) => {
    if (open) {
      availableRunners.value = [];
      runnersLoaded.value = false;
      loadingRunners.value = false;
      void Promise.all([loadCheatEngineStatus(), loadExecutableModifiedTime()]);
    } else {
      executableModifiedAt.value = null;
      executableModifiedKnown.value = false;
      loadingExecutableModifiedAt.value = false;
    }
  },
);

watch(
  () => useCustomPrefix.value,
  async (enabled) => {
    if (!enabled || runnersLoaded.value || loadingRunners.value) {
      return;
    }
    loadingRunners.value = true;
    try {
      const res = await api.getAvailableRunners();
      if (res && res.success === false) {
        console.error("Failed to get runners:", res.error);
      } else if (res?.success) {
        availableRunners.value = res.runners || [];
        runnersLoaded.value = true;
      }
    } catch (e) {
      console.error("Failed to get runners", e);
    } finally {
      loadingRunners.value = false;
    }
  },
);

const close = () => {
  emit("update:modelValue", false);
};

const browseExe = async () => {
  try {
    const p = await api.browseFile(exePath.value || "");
    if (p) {
      exePath.value = p;
    }
  } catch (e) {
    console.error("Failed to browse file", e);
    alert("Error browsing file: " + String(e));
  }
};

const browseCustomPrefix = async () => {
  try {
    const p = await api.browseDirectory(customPrefix.value || "");
    if (p) {
      customPrefix.value = p;
    }
  } catch (e) {
    console.error("Failed to browse directory", e);
    alert("Error browsing directory: " + String(e));
  }
};

const installDepsToPrefix = async () => {
  if (!customPrefix.value && !protonVersion.value) return;
  alert("Dependencies installation has started in the background. It may take several minutes to complete.");
  try {
    const res = await api.installRpgmakerDependencies(customPrefix.value, protonVersion.value);
    if (res && res.success === false) {
      alert("Failed to install dependencies: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to install deps", e);
    alert("Error installing dependencies: " + String(e));
  }
};

const save = async () => {
  if (!props.game || typeof props.game.id !== "number") return;
  saving.value = true;
  try {
    const res = await api.updateGame(props.game.id, {
      title: title.value,
      exe_path: exePath.value,
      f95_url: f95Url.value,
      version: version.value,
      command_line_args: commandLineArgs.value,
      cover_image_path: coverImage.value,
      status: status.value,
      play_status: playStatus.value,
      is_favorite: isFavorite.value ? 1 : 0,
      tags: tags.value.join(", "),
      engine: engine.value,
      run_japanese_locale: runJapaneseLocale.value,
      run_wayland: runWayland.value,
      auto_inject_ce: autoInjectCe.value,
      custom_prefix: useCustomPrefix.value ? customPrefix.value : "",
      proton_version: useCustomPrefix.value ? protonVersion.value : "",
      latest_version: latestVersion.value,
      rating_graphics: ratingGraphics.value,
      rating_story: ratingStory.value,
      rating_fappability: ratingFappability.value,
      rating_gameplay: ratingGameplay.value,
    });
    if (res && res.success === false) {
      alert("Failed to save game: " + (res.error || "Unknown error"));
    } else {
      emit("updated");
      close();
    }
  } catch (e) {
    console.error("Failed to save game", e);
    alert("Error saving game: " + String(e));
  } finally {
    saving.value = false;
  }
};

const deleteGame = async () => {
  if (
    !props.game ||
    typeof props.game.id !== "number" ||
    !confirm("Are you sure you want to remove this game from your library?")
  )
    return;
  deleting.value = true;
  try {
    const res = await api.deleteGame(props.game.id);
    if (res && res.success === false) {
      alert("Failed to delete game: " + (res.error || "Unknown error"));
    } else {
      emit("deleted");
      close();
    }
  } catch (e) {
    console.error("Failed to delete", e);
    alert("Error deleting game: " + String(e));
  } finally {
    deleting.value = false;
  }
};

const launchGame = () => {
  if (props.game) {
    const payload: GameRecord = {
      ...props.game,
      title: title.value,
      exe_path: exePath.value,
      f95_url: f95Url.value,
      version: version.value,
      command_line_args: commandLineArgs.value,
      cover_image_path: coverImage.value,
      play_status: playStatus.value,
      is_favorite: !!isFavorite.value,
      tags: tags.value.join(", "),
      engine: engine.value,
      run_japanese_locale: !!runJapaneseLocale.value,
      run_wayland: !!runWayland.value,
      auto_inject_ce: !!autoInjectCe.value,
      custom_prefix: useCustomPrefix.value ? customPrefix.value : "",
      proton_version: useCustomPrefix.value ? protonVersion.value : "",
    };
    emit("launch", payload);
  }
};

const saveResults = ref<SaveLocation[]>([]);
const searchingSaves = ref(false);
const showSavePanel = ref(false);

const findSaves = async () => {
  if (!props.game) return;
  searchingSaves.value = true;
  showSavePanel.value = true;
  try {
    const results = await api.findSaveFiles(
      props.game.exe_path,
      title.value,
      engine.value,
      useCustomPrefix.value ? customPrefix.value : "",
      useCustomPrefix.value ? protonVersion.value : ""
    );
    saveResults.value = results || [];
  } catch (e) {
    console.error("Failed to find saves", e);
    saveResults.value = [];
  } finally {
    searchingSaves.value = false;
  }
};

const openSaveFolder = async (path: string) => {
  try {
    const res = await api.openFolder(path);
    if (res && res.success === false) {
      alert("Failed to open folder: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to open folder", e);
    alert("Error opening folder: " + String(e));
  }
};

const addTag = () => {
  const t = newTag.value.trim();
  if (t && !tags.value.includes(t)) {
    tags.value.push(t);
  }
  newTag.value = "";
};

const removeTag = (tag: string) => {
  tags.value = tags.value.filter((t) => t !== tag);
};

const formatPlaytime = (seconds: number | null | undefined): string => {
  if (!seconds) return "0.0 hrs";
  return (seconds / 3600).toFixed(1) + " hrs";
};

const formatLastPlayed = (dateString: string | null | undefined): string => {
  if (!dateString) return "Never";
  return new Date(dateString).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
};

const formatTimestamp = (dateString: string | null | undefined): string => {
  if (!dateString) return "Unknown";
  return new Date(dateString).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
};

const loadCheatEngineStatus = async () => {
  try {
    const ceCheck = await api.isCheatEngineInstalled();
    ceInstalled.value = ceCheck.installed;
  } catch (e) {
    console.error("Failed to check cheat engine status", e);
  }
};

const loadExecutableModifiedTime = async () => {
  const requestId = executableModifiedRequestId.value + 1;
  executableModifiedRequestId.value = requestId;
  executableModifiedAt.value = null;
  executableModifiedKnown.value = false;

  const path = exePath.value.trim();
  if (!path) {
    return;
  }

  loadingExecutableModifiedAt.value = true;
  try {
    const result = await api.getExecutableModifiedTime(path);
    if (executableModifiedRequestId.value !== requestId) {
      return;
    }

    executableModifiedKnown.value = true;
    executableModifiedAt.value = result.success ? result.modified_at : null;
  } catch (e) {
    if (executableModifiedRequestId.value !== requestId) {
      return;
    }

    executableModifiedKnown.value = true;
    executableModifiedAt.value = null;
    console.error("Failed to load executable modified time", e);
  } finally {
    if (executableModifiedRequestId.value === requestId) {
      loadingExecutableModifiedAt.value = false;
    }
  }
};

const executableModifiedDisplay = computed(() => {
  if (loadingExecutableModifiedAt.value) return "Loading...";
  if (!executableModifiedKnown.value) return "Unknown";
  if (!executableModifiedAt.value) return "Unavailable";
  return formatTimestamp(executableModifiedAt.value);
});

const threadMainPostEditedDisplay = computed(() => {
  const lastEditAt = props.game?.thread_main_post_last_edit_at || null;
  const checkedAt = props.game?.thread_main_post_checked_at || null;

  if (lastEditAt) return formatTimestamp(lastEditAt);
  if (checkedAt) return "Not edited";
  return "Unknown";
});

const openInBrowser = async () => {
  if (f95Url.value) {
    try {
      const res = await api.openInBrowser(f95Url.value);
      if (res && res.success === false) {
        alert("Failed to open browser: " + (res.error || "Unknown error"));
      }
    } catch (e) {
      console.error("Failed to open browser", e);
      alert("Error opening browser: " + String(e));
    }
  }
};
</script>

<template>
  <div
    v-if="modelValue && game"
    class="fixed inset-0 z-50 flex items-center justify-center p-4"
  >
    <!-- Overlay -->
    <div
      class="absolute inset-0 bg-black/80"
      @click="close"
    ></div>

    <!-- Modal Content -->
    <div
      class="modal-content w-full max-w-3xl rounded-2xl shadow-2xl relative overflow-hidden transform transition-[opacity,transform] flex flex-col max-h-[90vh]"
    >
      <!-- Header with Cover Image Background -->
      <div
        class="relative h-48 overflow-hidden"
        style="
          background: linear-gradient(
            135deg,
            var(--bg-raised),
            var(--bg-inset)
          );
        "
      >
        <img
          v-if="coverImage"
          :src="coverImage"
          class="absolute inset-0 w-full h-full object-cover opacity-60 blur-sm"
        />
        <div
          class="absolute inset-0"
          style="
            background: linear-gradient(
              to top,
              var(--bg-surface),
              transparent,
              transparent
            );
          "
        ></div>

        <div
          class="absolute bottom-4 left-6 right-6 flex items-end justify-between"
        >
          <div>
            <h3 class="text-xl font-bold text-white drop-shadow-lg line-clamp-2 max-w-lg">
              {{ title || "Untitled Game" }}
            </h3>
            <div class="flex items-center gap-2 mt-0.5">
              <span
                v-if="engine"
                class="text-xs font-bold px-2 py-0.5 rounded-md"
                style="
                  background: rgba(90, 57, 104, 0.3);
                  color: #b380cc;
                  border: 1px solid rgba(90, 57, 104, 0.4);
                "
                >{{ engine }}</span
              >
              <span
                v-if="game?.playtime_seconds"
                class="text-xs font-bold px-2 py-0.5 rounded-md text-gray-300"
                style="background: rgba(255, 255, 255, 0.1); border: 1px solid rgba(255, 255, 255, 0.2);"
              >
                ⏱ {{ formatPlaytime(game.playtime_seconds) }}
              </span>
              <span
                v-if="game?.last_played"
                class="text-xs font-bold px-2 py-0.5 rounded-md text-gray-400"
                style="background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1);"
              >
                Last played: {{ formatLastPlayed(game.last_played) }}
              </span>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <div
              v-if="f95Rating"
              class="bg-black/70 text-yellow-400 text-xs font-bold px-2.5 py-1 rounded-lg border border-white/10 flex items-center gap-1"
            >
              <svg class="w-3.5 h-3.5 fill-yellow-400" viewBox="0 0 20 20">
                <path
                  d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"
                />
              </svg>
              F95: {{ f95Rating }}
            </div>
            <div
              class="bg-black/70 text-xs font-bold px-2.5 py-1 rounded-lg border border-white/10"
              style="color: var(--brand)"
            >
              You: {{ averagePersonalRating }}
            </div>
          </div>
        </div>

        <button
          @click="close"
          class="absolute top-3 right-3 text-gray-400 hover:text-white transition-colors bg-black/70 rounded-lg p-1.5"
        >
          <svg
            class="w-5 h-5"
            xmlns="http://www.w3.org/2000/svg"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              stroke-linecap="round"
              stroke-linejoin="round"
              stroke-width="2"
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>

      <!-- Scrollable Body -->
      <div class="modal-scroll-body p-6 overflow-y-auto space-y-6 flex-1">
        <!-- Status Selector & Favorite Toggle -->
        <div class="flex flex-wrap items-center gap-3">
          <div class="flex flex-wrap gap-2">
            <button
              v-for="s in statuses"
              :key="s.value"
              @click="playStatus = s.value"
              class="px-3 py-1.5 rounded-full text-xs font-semibold transition-all"
              :style="
                playStatus === s.value
                  ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                  : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'
              "
            >
              {{ s.label }}
            </button>
          </div>
          
          <button
            @click="isFavorite = !isFavorite"
            class="ml-auto px-4 py-1.5 rounded-full text-xs font-bold flex items-center gap-1.5 transition-all"
            :style="
              isFavorite
                ? 'background: rgba(234, 179, 8, 0.15); border: 1px solid rgba(234, 179, 8, 0.5); color: #eab308; box-shadow: 0 0 10px rgba(234, 179, 8, 0.2)'
                : 'background: var(--bg-raised); border: 1px dashed var(--border); color: var(--text-muted)'
            "
          >
            <svg class="w-4 h-4" viewBox="0 0 24 24" :fill="isFavorite ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
            </svg>
            {{ isFavorite ? 'Favorited' : 'Mark Favorite' }}
          </button>
        </div>

        <!-- Game Info Fields -->
        <div class="grid grid-cols-2 gap-4">
          <div class="col-span-2">
            <label class="modal-label">Title</label>
            <input v-model="title" type="text" class="modal-input w-full" />
          </div>

          <div class="col-span-2">
            <label class="modal-label">Executable Path</label>
            <div class="flex gap-2">
              <input
                v-model="exePath"
                type="text"
                class="modal-input flex-1 font-mono"
              />
              <button @click="browseExe" class="modal-btn">Browse</button>
            </div>
          </div>

          <div>
            <label class="modal-label">Version</label>
            <input
              v-model="version"
              type="text"
              placeholder="v1.0"
              class="modal-input w-full font-mono"
              style="color: var(--brand)"
            />
          </div>

          <div>
            <label class="modal-label">Engine</label>
            <input
              v-model="engine"
              type="text"
              placeholder="Unity, RPGM, Ren'Py..."
              class="modal-input w-full"
              style="color: #b380cc"
            />
          </div>

          <div class="col-span-2">
            <label class="modal-label">F95Zone URL</label>
            <input
              v-model="f95Url"
              type="text"
              placeholder="https://f95zone.to/threads/..."
              class="modal-input w-full"
              style="color: var(--brand)"
            />
          </div>

          <div class="col-span-2 grid gap-2 md:grid-cols-2">
            <div
              class="rounded-md px-3 py-2"
              style="background: var(--bg-raised); border: 1px solid var(--border)"
            >
              <p class="text-[10px] uppercase tracking-[0.14em]" style="color: var(--text-muted)">
                Executable Modified
              </p>
              <p class="mt-0.5 text-xs font-medium leading-tight" style="color: var(--text-primary)">
                {{ executableModifiedDisplay }}
              </p>
            </div>

            <div
              class="rounded-md px-3 py-2"
              style="background: var(--bg-raised); border: 1px solid var(--border)"
            >
              <p class="text-[10px] uppercase tracking-[0.14em]" style="color: var(--text-muted)">
                Thread Main Post Edited
              </p>
              <p class="mt-0.5 text-xs font-medium leading-tight" style="color: var(--text-primary)">
                {{ threadMainPostEditedDisplay }}
              </p>
            </div>
          </div>

          <div class="col-span-2">
            <label class="modal-label">Cover Image URL</label>
            <input
              v-model="coverImage"
              type="text"
              placeholder="https://..."
              class="modal-input w-full"
            />
          </div>

          <div class="col-span-2">
            <label class="modal-label">Command Line Arguments</label>
            <input
              v-model="commandLineArgs"
              type="text"
              placeholder="gamemoderun %command% --fullscreen"
              class="modal-input w-full font-mono"
            />
          </div>

          <div class="col-span-2">
            <label class="modal-label">Launch Options</label>
            <div
              class="rounded-lg p-3 space-y-2"
              style="background: var(--bg-raised); border: 1px solid var(--border)"
            >
              <div class="rounded-md px-2 py-1" style="background: var(--bg-surface)">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-medium" style="color: var(--text-primary)">
                    Run with Japanese Locale
                  </span>
                  <button
                    type="button"
                    @click="showJapaneseLocaleInfo = !showJapaneseLocaleInfo"
                    class="w-4 h-4 rounded-full text-[10px] font-bold flex items-center justify-center"
                    style="background: var(--bg-overlay); color: var(--text-muted); border: 1px solid var(--border)"
                  >
                    i
                  </button>
                  <label class="relative inline-flex items-center cursor-pointer ml-auto">
                    <input
                      type="checkbox"
                      v-model="runJapaneseLocale"
                      class="sr-only peer"
                    />
                    <div
                      class="toggle-track peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--brand)]"
                    ></div>
                  </label>
                </div>
                <p
                  v-show="showJapaneseLocaleInfo"
                  class="text-xs mt-1 pr-12"
                  style="color: var(--text-muted)"
                >
                  Enable this if the game has unreadable text (Mojibake) or crashes due to missing Japanese fonts.
                </p>
              </div>

              <div class="rounded-md px-2 py-1" style="background: var(--bg-surface)">
                <div class="flex items-center gap-2">
                  <span class="text-sm font-medium" style="color: var(--text-primary)">
                    Run in Wayland Compatibility Mode
                  </span>
                  <button
                    type="button"
                    @click="showWaylandInfo = !showWaylandInfo"
                    class="w-4 h-4 rounded-full text-[10px] font-bold flex items-center justify-center"
                    style="background: var(--bg-overlay); color: var(--text-muted); border: 1px solid var(--border)"
                  >
                    i
                  </button>
                  <label class="relative inline-flex items-center cursor-pointer ml-auto">
                    <input
                      type="checkbox"
                      v-model="runWayland"
                      class="sr-only peer"
                    />
                    <div
                      class="toggle-track peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--brand)]"
                    ></div>
                  </label>
                </div>
                <p
                  v-show="showWaylandInfo"
                  class="text-xs mt-1 pr-12"
                  style="color: var(--text-muted)"
                >
                  Enable this if your system uses Wayland and the game crashes or freezes on launch.
                </p>
              </div>

              <div
                class="rounded-md px-2 py-1 transition-colors"
                :style="
                  ceInstalled
                    ? 'background: var(--bg-surface)'
                    : 'background: var(--bg-inset); opacity: 0.7'
                "
              >
                <div class="flex items-center gap-2">
                  <span class="text-sm font-medium" style="color: var(--text-primary)">
                    Auto-Launch & Inject Cheat Engine
                  </span>
                  <span
                    v-if="!ceInstalled"
                    class="text-[10px] uppercase px-1.5 py-0.5 rounded font-bold"
                    style="background: var(--bg-overlay); color: var(--text-muted); border: 1px solid var(--border)"
                  >
                    Not Installed
                  </span>
                  <button
                    type="button"
                    @click="showCheatEngineInfo = !showCheatEngineInfo"
                    class="w-4 h-4 rounded-full text-[10px] font-bold flex items-center justify-center"
                    style="background: var(--bg-overlay); color: var(--text-muted); border: 1px solid var(--border)"
                  >
                    i
                  </button>
                  <label
                    class="relative inline-flex items-center ml-auto"
                    :class="ceInstalled ? 'cursor-pointer' : 'cursor-not-allowed'"
                  >
                    <input
                      type="checkbox"
                      v-model="autoInjectCe"
                      :disabled="!ceInstalled"
                      class="sr-only peer"
                    />
                    <div
                      class="toggle-track peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-600"
                    ></div>
                  </label>
                </div>
                <p
                  v-show="showCheatEngineInfo"
                  class="text-xs mt-1 pr-12"
                  style="color: var(--text-muted)"
                >
                  Spawns Cheat Engine inside the same virtual Wine sandbox immediately after the game starts.
                </p>
              </div>
            </div>
          </div>

          <!-- Advanced Launch Options -->
          <div class="col-span-2 mt-2">
            <div
              class="flex items-center justify-between p-3 rounded-lg"
              style="
                background: var(--bg-raised);
                border: 1px solid var(--border);
              "
            >
              <div>
                <p class="text-sm font-medium" style="color: var(--text-primary)">
                  Use Custom Wine Prefix & Proton Version
                </p>
                <p class="text-xs" style="color: var(--text-muted)">
                  Isolate this game's save files and dependencies from the global prefix.
                </p>
              </div>
              <label class="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  v-model="useCustomPrefix"
                  class="sr-only peer"
                />
                <div
                  class="toggle-track peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--brand)]"
                ></div>
              </label>
            </div>

            <div v-if="useCustomPrefix" class="mt-3 p-4 rounded-lg space-y-4" style="background: var(--bg-inset); border: 1px dashed var(--border);">
              <div>
                <label class="modal-label">Custom Prefix Path</label>
                <div class="flex gap-2">
                  <input
                    v-model="customPrefix"
                    type="text"
                    placeholder="/home/user/games/my_game_prefix"
                    class="modal-input flex-1 font-mono text-xs"
                  />
                  <button @click="browseCustomPrefix" class="modal-btn">Browse</button>
                </div>
              </div>

              <div>
                <label class="modal-label">Proton Version</label>
                <select
                  v-model="protonVersion"
                  class="modal-input w-full"
                >
                  <option v-if="loadingRunners" value="" disabled>
                    Loading runners...
                  </option>
                  <option v-else value="">(Use Global Default)</option>
                  <option
                    v-for="runner in availableRunners"
                    :key="runner.path"
                    :value="runner.path"
                  >
                    {{ runner.name }}
                  </option>
                </select>
              </div>

              <div class="pt-2 border-t border-gray-700/50">
                <button
                  @click="installDepsToPrefix"
                  class="w-full text-xs font-medium px-4 py-2 rounded-lg transition-colors"
                  style="background: rgba(90, 57, 104, 0.2); color: #b380cc; border: 1px solid rgba(90, 57, 104, 0.4);"
                >
                  Install RPGMaker DLLs to this Prefix
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- Personal Ratings Section -->
        <div>
          <h4
            class="text-sm font-bold mb-3 flex items-center gap-2"
            style="color: var(--text-secondary)"
          >
            <svg
              class="w-4 h-4"
              style="color: var(--brand)"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"
              />
            </svg>
            Your Ratings
          </h4>
          <div class="grid grid-cols-2 gap-3">
            <div
              v-for="cat in [
                { label: '🎨 Graphics', model: 'ratingGraphics' },
                { label: '📖 Story', model: 'ratingStory' },
                { label: '🔥 Fappability', model: 'ratingFappability' },
                { label: '🎮 Gameplay', model: 'ratingGameplay' },
              ]"
              :key="cat.model"
              class="flex items-center gap-3 rounded-lg p-2"
              style="background: var(--bg-raised); border: 1px solid var(--border)"
            >
              <span
                class="text-xs w-24 shrink-0"
                style="color: var(--text-secondary)"
                >{{ cat.label }}</span
              >
              <div class="flex gap-1">
                <button
                  v-for="star in 5"
                  :key="star"
                  @click="
                    cat.model === 'ratingGraphics'
                      ? (ratingGraphics = star)
                      : cat.model === 'ratingStory'
                        ? (ratingStory = star)
                        : cat.model === 'ratingFappability'
                          ? (ratingFappability = star)
                          : (ratingGameplay = star)
                  "
                  class="transition-transform hover:scale-110"
                >
                  <svg
                    class="w-5 h-5"
                    :class="[
                      star <=
                      (cat.model === 'ratingGraphics'
                        ? ratingGraphics
                        : cat.model === 'ratingStory'
                          ? ratingStory
                          : cat.model === 'ratingFappability'
                            ? ratingFappability
                            : ratingGameplay)
                        ? 'text-yellow-400 fill-yellow-400'
                        : 'fill-current',
                    ]"
                    :style="
                      star >
                      (cat.model === 'ratingGraphics'
                        ? ratingGraphics
                        : cat.model === 'ratingStory'
                          ? ratingStory
                          : cat.model === 'ratingFappability'
                            ? ratingFappability
                            : ratingGameplay)
                        ? 'color: var(--border)'
                        : ''
                    "
                    viewBox="0 0 20 20"
                  >
                    <path
                      d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"
                    />
                  </svg>
                </button>
              </div>
              <span
                class="text-xs font-mono ml-auto"
                style="color: var(--text-muted)"
                >{{
                  cat.model === "ratingGraphics"
                    ? ratingGraphics
                    : cat.model === "ratingStory"
                      ? ratingStory
                      : cat.model === "ratingFappability"
                        ? ratingFappability
                        : ratingGameplay
                }}/5</span
              >
            </div>
          </div>
        </div>

        <!-- Tags (editable) -->
        <div>
          <button
            type="button"
            class="w-full flex items-center justify-between px-3 py-2 rounded-lg"
            style="background: var(--bg-raised); border: 1px solid var(--border)"
            @click="tagsExpanded = !tagsExpanded"
          >
            <span class="text-xs font-medium" style="color: var(--text-secondary)">
              Tags ({{ tags.length }})
            </span>
            <svg
              class="w-4 h-4 transition-transform"
              :class="tagsExpanded ? 'rotate-180' : ''"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              style="color: var(--text-muted)"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
          <div v-show="tagsExpanded" class="mt-2">
            <div class="flex flex-wrap gap-1.5 mb-2">
              <span
                v-for="tag in tags"
                :key="tag"
                class="text-xs px-2 py-0.5 rounded-full flex items-center gap-1"
                style="
                  background: var(--bg-raised);
                  border: 1px solid var(--border);
                  color: var(--text-secondary);
                "
              >
                {{ tag }}
                <button
                  @click="removeTag(tag)"
                  class="hover:text-red-400 transition-colors ml-0.5"
                  style="color: var(--text-muted)"
                >
                  &times;
                </button>
              </span>
            </div>
            <div class="flex gap-2">
              <input
                v-model="newTag"
                type="text"
                placeholder="Add a tag..."
                @keydown.enter.prevent="addTag"
                class="modal-input flex-1 !py-1.5 !text-xs"
              />
              <button @click="addTag" class="modal-btn">Add</button>
            </div>
          </div>
        </div>
      </div>

      <!-- Save File Finder Panel -->
      <div
        v-if="showSavePanel"
        class="px-6 py-3"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <div class="flex items-center justify-between mb-2">
          <h4
            class="text-xs font-semibold uppercase tracking-wider"
            style="color: var(--text-secondary)"
          >
            Save File Locations
          </h4>
          <button
            @click="showSavePanel = false"
            class="text-xs hover:text-white transition-colors"
            style="color: var(--text-muted)"
          >
            ✕ Close
          </button>
        </div>
        <div
          v-if="searchingSaves"
          class="text-xs py-2 flex items-center gap-2"
          style="color: var(--text-secondary)"
        >
          <svg
            class="w-3 h-3 animate-spin"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <circle cx="12" cy="12" r="10" />
            <path d="M12 2a10 10 0 0 1 10 10" />
          </svg>
          Searching...
        </div>
        <div
          v-else-if="saveResults.length === 0"
          class="text-xs py-2"
          style="color: var(--text-muted)"
        >
          No save files found. The game may store saves in an unexpected
          location.
        </div>
        <div v-else class="space-y-1.5 max-h-32 overflow-y-auto">
          <button
            v-for="(result, i) in saveResults"
            :key="i"
            @click="openSaveFolder(result.path)"
            class="w-full flex items-center gap-3 p-2 rounded-lg transition-all text-left group"
            style="
              background: var(--bg-surface);
              border: 1px solid var(--border);
            "
            onmouseover="this.style.borderColor = 'var(--border-hover)'"
            onmouseout="this.style.borderColor = 'var(--border)'"
          >
            <span class="text-lg">📂</span>
            <div class="flex-1 min-w-0">
              <p
                class="text-xs font-medium truncate"
                style="color: var(--text-primary)"
              >
                {{ result.description }}
              </p>
              <p
                class="text-[10px] truncate font-mono"
                style="color: var(--text-muted)"
              >
                {{ result.path }}
              </p>
            </div>
            <span
              class="text-[10px] px-1.5 py-0.5 rounded shrink-0"
              style="
                background: var(--bg-raised);
                color: var(--text-muted);
                border: 1px solid var(--border);
              "
              >{{ result.type }}</span
            >
          </button>
        </div>
        <p class="text-[10px] mt-2 italic" style="color: var(--text-muted)">
          ⚠ Auto-detection is best-effort. Some games store saves in
          non-standard locations.
        </p>
      </div>

      <!-- Footer Actions -->
      <div
        class="px-6 py-4 flex items-center justify-between"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <div class="flex gap-2">
          <button
            @click="deleteGame"
            :disabled="deleting"
            class="text-red-400 hover:text-red-300 hover:bg-red-500/10 px-3 py-2 rounded-lg text-xs font-medium transition-all"
          >
            {{ deleting ? "Removing..." : "🗑 Remove" }}
          </button>
          <button
            v-if="f95Url"
            @click="openInBrowser"
            class="hover:bg-purple-500/10 px-3 py-2 rounded-lg text-xs font-medium transition-all flex items-center gap-1"
            style="color: #b380cc"
          >
            <svg
              class="w-3.5 h-3.5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path
                d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"
              />
            </svg>
            Open in Browser
          </button>
          <button
            @click="findSaves"
            :disabled="searchingSaves"
            class="text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 px-3 py-2 rounded-lg text-xs font-medium transition-all flex items-center gap-1"
          >
            <svg
              class="w-3.5 h-3.5"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path
                d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"
              />
            </svg>
            {{ searchingSaves ? "Searching..." : "Find Saves" }}
          </button>
        </div>
        <div class="flex gap-3">
          <button
            @click="launchGame"
            class="bg-green-600 hover:bg-green-500 text-white px-5 py-2 rounded-lg text-sm font-bold transition-all shadow-lg shadow-green-900/20 flex items-center gap-2"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="currentColor"
              stroke="none"
            >
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
            Play
          </button>
          <button
            @click="save"
            :disabled="saving"
            class="text-white px-5 py-2 rounded-lg text-sm font-bold transition-all disabled:opacity-50"
            style="background: var(--brand); box-shadow: var(--shadow-brand)"
          >
            {{ saving ? "Saving..." : "Save Changes" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
  contain: layout paint;
  will-change: transform, opacity;
}

.modal-scroll-body {
  contain: content;
  will-change: scroll-position;
}

.modal-label {
  display: block;
  font-size: 0.75rem;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 0.25rem;
}

.modal-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.875rem;
  color: var(--text-primary);
  transition: all 0.15s ease;
}
.modal-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.modal-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 0.75rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  transition: all 0.15s ease;
}
.modal-btn:hover {
  background: var(--border-hover);
}

.toggle-track {
  width: 2.75rem;
  height: 1.5rem;
  background: var(--bg-overlay);
  border-radius: 9999px;
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
