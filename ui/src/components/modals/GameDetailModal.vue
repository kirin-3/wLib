<script setup lang="ts">
import { ref, watch, computed, onMounted, onBeforeUnmount } from "vue";
import {
  IconBookFilled,
  IconCheck,
  IconChevronDown,
  IconExternalLinkFilled,
  IconFlameFilled,
  IconFolderOpenFilled,
  IconPaletteFilled,
  IconPlayerPlayFilled,
  IconRefresh,
  IconStarFilled,
  IconX,
  IconDeviceGamepad2Filled,
} from "@tabler/icons-vue";
import { api } from "../../services/api";
import type { GameRecord, RunnerInfo, SaveLocation } from "../../services/api";
import {
  DEFAULT_PLAY_STATUS,
  PLAY_STATUS_OPTIONS,
  normalizePlayStatus,
  type PlayStatus,
} from "../../utils/playStatus";

type UpdateCheckState = {
  running: boolean;
  type: "" | "success" | "error";
  message: string;
};

const ENGINE_OPTIONS = [
  "ADRIFT",
  "Flash",
  "HTML",
  "Java",
  "Others",
  "QSP",
  "RAGS",
  "RPGM",
  "Ren'Py",
  "Tads",
  "Unity",
  "Unreal Engine",
  "WebGL",
  "Wolf RPG",
] as const;

type EngineOption = (typeof ENGINE_OPTIONS)[number];

const ENGINE_STYLES: Record<EngineOption, string> = {
  ADRIFT: "background: rgba(109, 128, 145, 0.16); border: 1px solid rgba(109, 128, 145, 0.36); color: #bfd2e0",
  Flash: "background: rgba(189, 93, 56, 0.16); border: 1px solid rgba(189, 93, 56, 0.36); color: #f3b28f",
  HTML: "background: rgba(191, 125, 54, 0.16); border: 1px solid rgba(191, 125, 54, 0.36); color: #e8c08b",
  Java: "background: rgba(177, 120, 45, 0.16); border: 1px solid rgba(177, 120, 45, 0.36); color: #f1ca78",
  Others: "background: var(--bg-overlay); border: 1px solid var(--border); color: var(--text-secondary)",
  QSP: "background: rgba(125, 82, 156, 0.16); border: 1px solid rgba(125, 82, 156, 0.36); color: #cfb0f0",
  RAGS: "background: rgba(150, 80, 72, 0.16); border: 1px solid rgba(150, 80, 72, 0.36); color: #e1afa7",
  RPGM: "background: rgba(179, 128, 204, 0.16); border: 1px solid rgba(179, 128, 204, 0.36); color: #d7b6ea",
  "Ren'Py": "background: rgba(176, 90, 112, 0.16); border: 1px solid rgba(176, 90, 112, 0.36); color: #e6b0c0",
  Tads: "background: rgba(104, 127, 83, 0.16); border: 1px solid rgba(104, 127, 83, 0.36); color: #c8ddaf",
  Unity: "background: rgba(93, 141, 147, 0.16); border: 1px solid rgba(93, 141, 147, 0.36); color: #add9dd",
  "Unreal Engine": "background: rgba(119, 119, 132, 0.16); border: 1px solid rgba(119, 119, 132, 0.36); color: #d6d6dc",
  WebGL: "background: rgba(74, 148, 161, 0.16); border: 1px solid rgba(74, 148, 161, 0.36); color: #9fdbe5",
  "Wolf RPG": "background: rgba(145, 91, 53, 0.16); border: 1px solid rgba(145, 91, 53, 0.36); color: #e2ba93",
};

const props = defineProps<{
  modelValue: boolean;
  game: GameRecord | null;
  updateCheckState: UpdateCheckState;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  updated: [];
  deleted: [];
  launch: [payload: GameRecord];
  "check-updates": [gameId: number];
}>();

// Editable fields
const title = ref("");
const exePath = ref("");
const f95Url = ref("");
const version = ref("");
const commandLineArgs = ref("");
const coverImage = ref("");
const status = ref("");
const playStatus = ref<PlayStatus>(DEFAULT_PLAY_STATUS);
const isFavorite = ref(false);
const tags = ref<string[]>([]);
const engine = ref<EngineOption>("Others");
const newTag = ref("");
const latestVersion = ref("");
const engineMenuOpen = ref(false);
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
const engineDropdownRef = ref<HTMLElement | null>(null);

const statuses: Array<{ value: PlayStatus; label: string }> = PLAY_STATUS_OPTIONS;

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

const syncReadonlyMetadata = (game: GameRecord | null) => {
  latestVersion.value = game?.latest_version || "";
  f95Rating.value = game?.rating || "";
};

const normalizeEngine = (value: string | null | undefined): EngineOption => {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized || normalized === "unknown" || normalized === "null") {
    return "Others";
  }

  const match = ENGINE_OPTIONS.find(
    (option) => option.toLowerCase() === normalized,
  );
  return match || "Others";
};

const getEngineBadgeStyle = (value: EngineOption): string => ENGINE_STYLES[value];

const selectedEngineStyle = computed(() => getEngineBadgeStyle(engine.value));

const statusButtonStyle = (value: PlayStatus) => {
  if (playStatus.value !== value) {
    return "background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)";
  }

  switch (value) {
    case "Not Started":
      return "background: rgba(107, 114, 128, 0.16); border: 1px solid rgba(156, 163, 175, 0.28); color: #d1d5db";
    case "Plan to Play":
      return "background: rgba(120, 113, 46, 0.18); border: 1px solid rgba(202, 138, 4, 0.28); color: #fcd34d";
    case "Playing":
      return "background: rgba(234, 179, 8, 0.16); border: 1px solid rgba(250, 204, 21, 0.3); color: #facc15";
    case "Waiting For Update":
      return "background: rgba(59, 130, 246, 0.16); border: 1px solid rgba(96, 165, 250, 0.28); color: #93c5fd";
    case "Abandoned":
      return "background: rgba(239, 68, 68, 0.16); border: 1px solid rgba(248, 113, 113, 0.28); color: #fca5a5";
    case "Completed":
      return "background: rgba(47, 106, 73, 0.18); border: 1px solid rgba(74, 222, 128, 0.28); color: #86efac";
    case "On Hold":
      return "background: rgba(180, 83, 9, 0.18); border: 1px solid rgba(251, 146, 60, 0.3); color: #fdba74";
    default:
      return "background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)";
  }
};

const summaryItems = computed(() => [
  {
    label: "F95 Rating",
    value: f95Rating.value || "Unavailable",
    accent: f95Rating.value ? "color: var(--rating-accent)" : "",
    showStar: true,
  },
  {
    label: "Your Rating",
    value: averagePersonalRating.value === "—" ? "Not rated" : `${averagePersonalRating.value}/5`,
    accent: averagePersonalRating.value === "—" ? "" : "color: var(--rating-accent)",
    showStar: true,
  },
  {
    label: "Playtime",
    value: formatPlaytime(props.game?.playtime_seconds),
    accent: "",
    showStar: false,
  },
  {
    label: "Last Played",
    value: formatLastPlayed(props.game?.last_played),
    accent: "",
    showStar: false,
  },
  {
    label: "Exe Modified",
    value: executableModifiedDisplay.value,
    accent: "",
    showStar: false,
  },
  {
    label: "Thread Updated",
    value: threadMainPostEditedDisplay.value,
    accent: hasUpdate.value ? "color: #facc15" : "",
    showStar: false,
  },
]);

const ratingCategories = [
  { label: "Graphics", model: "ratingGraphics", icon: IconPaletteFilled },
  { label: "Story", model: "ratingStory", icon: IconBookFilled },
  { label: "Fappability", model: "ratingFappability", icon: IconFlameFilled },
  { label: "Gameplay", model: "ratingGameplay", icon: IconDeviceGamepad2Filled },
] as const;

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
        playStatus.value = normalizePlayStatus(g.play_status, g.status);
        isFavorite.value = !!g.is_favorite;
        engine.value = normalizeEngine(g.engine);
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
        syncReadonlyMetadata(g);

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
      engineMenuOpen.value = false;
      executableModifiedAt.value = null;
      executableModifiedKnown.value = false;
      loadingExecutableModifiedAt.value = false;
    }
  },
);

watch(
  () => [props.game?.id, props.game?.latest_version, props.game?.rating] as const,
  ([gameId]) => {
    if (props.game && typeof gameId === "number" && gameId === lastSyncedId.value) {
      syncReadonlyMetadata(props.game);
    }
  },
  { immediate: true },
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

const selectEngine = (value: EngineOption) => {
  engine.value = value;
  engineMenuOpen.value = false;
};

const handleOutsideClick = (event: MouseEvent) => {
  if (!engineMenuOpen.value) return;
  const target = event.target;
  if (!(target instanceof Node)) return;
  if (!engineDropdownRef.value?.contains(target)) {
    engineMenuOpen.value = false;
  }
};

onMounted(() => {
  document.addEventListener("mousedown", handleOutsideClick);
});

onBeforeUnmount(() => {
  document.removeEventListener("mousedown", handleOutsideClick);
});

const close = () => {
  engineMenuOpen.value = false;
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
      engine: normalizeEngine(engine.value),
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
      engine: normalizeEngine(engine.value),
      run_japanese_locale: !!runJapaneseLocale.value,
      run_wayland: !!runWayland.value,
      auto_inject_ce: !!autoInjectCe.value,
      custom_prefix: useCustomPrefix.value ? customPrefix.value : "",
      proton_version: useCustomPrefix.value ? protonVersion.value : "",
    };
    emit("launch", payload);
  }
};

const requestUpdateCheck = () => {
  if (props.game?.f95_url) {
    emit("check-updates", props.game.id);
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
    <div
      class="absolute inset-0 bg-black/80"
      @click="close"
    ></div>

    <div
      class="modal-content relative flex max-h-[90vh] w-full max-w-[49rem] flex-col overflow-hidden rounded-xl"
    >
      <div class="absolute right-4 top-4 z-10 flex items-center gap-2">
        <button
          @click="isFavorite = !isFavorite"
          class="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors"
          :style="
            isFavorite
              ? 'background: rgba(234, 179, 8, 0.14); border: 1px solid rgba(234, 179, 8, 0.42); color: #facc15'
              : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-muted)'
          "
        >
          <IconStarFilled class="w-4 h-4" :style="isFavorite ? '' : 'opacity: 0.7'" />
          {{ isFavorite ? 'Favorited' : 'Mark Favorite' }}
        </button>

        <button
          @click="close"
          class="rounded-md px-3 py-1.5 inline-flex items-center justify-center transition-colors"
          style="color: var(--text-muted); background: var(--bg-raised); border: 1px solid var(--border)"
        >
          <IconX class="w-4 h-4" />
        </button>
      </div>

      <div class="modal-scroll-body overflow-y-auto p-6 pt-14 space-y-6 flex-1">
        <div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <div
            v-for="item in summaryItems"
            :key="item.label"
            class="summary-card"
          >
            <p class="summary-label">{{ item.label }}</p>
            <p class="summary-value" :style="item.accent">
              <IconStarFilled
                v-if="item.showStar"
                class="summary-star"
              />
              {{ item.value }}
            </p>
          </div>
        </div>

        <div class="status-strip flex flex-wrap items-center gap-3 pr-36 sm:pr-44">
          <div class="flex flex-wrap gap-2">
            <button
              v-for="s in statuses"
              :key="s.value"
              @click="playStatus = s.value"
              class="status-chip px-3 py-1.5 text-xs font-medium transition-colors"
              :style="statusButtonStyle(s.value)"
            >
              {{ s.label }}
            </button>
          </div>
        </div>

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

          <div class="relative" ref="engineDropdownRef">
            <label class="modal-label">Engine</label>
            <button
              type="button"
              class="engine-select modal-input w-full"
              @click="engineMenuOpen = !engineMenuOpen"
            >
              <span class="identity-badge" :style="selectedEngineStyle">
                {{ engine }}
              </span>
              <IconChevronDown
                class="h-4 w-4 shrink-0 transition-transform"
                :class="engineMenuOpen ? 'rotate-180' : ''"
                style="color: var(--text-muted)"
              />
            </button>
            <div
              v-if="engineMenuOpen"
              class="engine-menu"
            >
              <button
                v-for="option in ENGINE_OPTIONS"
                :key="option"
                type="button"
                class="engine-menu-item"
                @click="selectEngine(option)"
              >
                <span class="identity-badge" :style="getEngineBadgeStyle(option)">
                  {{ option }}
                </span>
                <IconCheck
                  v-if="engine === option"
                  class="h-4 w-4 shrink-0"
                  style="color: var(--text-secondary)"
                />
              </button>
            </div>
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
                    <div class="ui-toggle"></div>
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
                    <div class="ui-toggle"></div>
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
                    <div class="ui-toggle"></div>
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
                <div class="ui-toggle"></div>
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
              v-for="cat in ratingCategories"
              :key="cat.model"
              class="flex items-center gap-3 rounded-lg p-2"
              style="background: var(--bg-raised); border: 1px solid var(--border)"
            >
              <span class="rating-label" style="color: var(--text-secondary)">
                <component :is="cat.icon" class="w-4 h-4 shrink-0" style="color: var(--brand)" />
                {{ cat.label }}
              </span>
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
                  <IconStarFilled
                    class="w-5 h-5"
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
                        : 'color: var(--rating-accent)'
                    "
                  />
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

      <div
        class="px-6 py-4"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <p
          v-if="updateCheckState.message"
          class="mb-3 rounded-md border px-3 py-2 text-xs"
          :style="
            updateCheckState.type === 'error'
              ? 'background: rgba(239, 68, 68, 0.12); border-color: rgba(239, 68, 68, 0.28); color: #fca5a5'
              : 'background: var(--success-bg); border-color: var(--success-border); color: var(--success-text)'
          "
        >
          {{ updateCheckState.message }}
        </p>
        <div class="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
          <div class="flex flex-wrap gap-2">
            <button
              @click="deleteGame"
              :disabled="deleting"
              class="footer-action text-red-400"
            >
              {{ deleting ? "Removing..." : "Remove" }}
            </button>
            <button
              v-if="f95Url"
              @click="openInBrowser"
              class="footer-action inline-flex items-center gap-2"
              style="color: var(--text-primary)"
            >
              <IconExternalLinkFilled class="w-4 h-4" />
              Open in Browser
            </button>
            <button
              @click="findSaves"
              :disabled="searchingSaves"
              class="footer-action inline-flex items-center gap-2"
              style="color: var(--text-primary)"
            >
              <IconFolderOpenFilled class="w-4 h-4" />
              {{ searchingSaves ? "Searching..." : "Find Saves" }}
            </button>
            <button
              v-if="game.f95_url"
              @click="requestUpdateCheck"
              :disabled="updateCheckState.running"
              class="footer-action inline-flex items-center gap-2"
              style="color: var(--text-primary)"
            >
              <IconRefresh class="w-4 h-4" :class="updateCheckState.running ? 'animate-spin' : ''" />
              {{ updateCheckState.running ? "Checking..." : "Check Updates" }}
            </button>
          </div>
          <div class="flex flex-wrap gap-3">
            <button
              @click="launchGame"
              class="launch-btn"
            >
              <IconPlayerPlayFilled class="w-4 h-4" />
              Play
            </button>
            <button
              @click="save"
              :disabled="saving"
              class="save-btn disabled:opacity-50"
            >
              {{ saving ? "Saving..." : "Save Changes" }}
            </button>
          </div>
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

.summary-card {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.625rem;
  padding: 0.75rem 0.875rem;
}

.summary-label {
  margin: 0;
  font-size: 0.75rem;
  color: var(--text-secondary);
}

.summary-value {
  margin: 0.25rem 0 0;
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text-primary);
}

.summary-star {
  width: 0.85rem;
  height: 0.85rem;
  color: var(--rating-accent);
  flex-shrink: 0;
}

.rating-label {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  width: 7.5rem;
  flex-shrink: 0;
  font-size: 0.75rem;
}

.identity-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  border-radius: 0.5rem;
  padding: 0.3rem 0.55rem;
  font-size: 0.75rem;
  font-weight: 600;
}

.engine-select {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  text-align: left;
}

.engine-menu {
  position: absolute;
  z-index: 20;
  top: calc(100% + 0.5rem);
  left: 0;
  right: 0;
  max-height: 16rem;
  overflow-y: auto;
  border: 1px solid var(--border-hover);
  border-radius: 0.75rem;
  background: var(--bg-surface);
  box-shadow: var(--shadow-card);
  padding: 0.4rem;
}

.engine-menu-item {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  border-radius: 0.5rem;
  padding: 0.4rem;
}

.engine-menu-item:hover {
  background: var(--bg-raised);
}

.status-strip {
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: linear-gradient(180deg, var(--bg-raised) 0%, var(--bg-surface) 100%);
  padding: 0.75rem;
}

.status-chip {
  border-radius: 0.55rem;
}

.footer-action {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
  font-weight: 500;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.footer-action:hover:not(:disabled) {
  background: var(--bg-overlay);
  border-color: var(--border-hover);
}

.footer-action:disabled {
  opacity: 0.6;
}

.launch-btn,
.save-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  border-radius: 0.5rem;
  padding: 0.5rem 1rem;
  font-size: 0.875rem;
  font-weight: 600;
  transition: background-color 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
}

.launch-btn {
  background: var(--toggle-active);
  border: 1px solid var(--toggle-active);
  color: #f8fafc;
}

.launch-btn:hover {
  filter: brightness(1.06);
}

.save-btn {
  background: var(--brand);
  border: 1px solid var(--brand);
  box-shadow: var(--shadow-brand);
  color: var(--text-inverse);
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
