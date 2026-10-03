<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { ref, watch, computed, onMounted, onBeforeUnmount } from "vue";
import {
  IconBookFilled,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconDeviceFloppyFilled,
  IconExternalLinkFilled,
  IconFlameFilled,
  IconFolderOpenFilled,
  IconLoader2,
  IconPaletteFilled,
  IconPlayerPlayFilled,
  IconPlayerStopFilled,
  IconRefresh,
  IconStarFilled,
  IconTrashX,
  IconX,
  IconDeviceGamepad2Filled,
} from "@tabler/icons-vue";
import { api } from "../../services/api";
import { notify, notifyError } from "../../utils/toast";
import { useModalKeyboard } from "../../utils/modalKeyboard";
import { hasAvailableUpdate } from "../../utils/libraryGames";
import type {
  GameRecord,
  LaunchTarget,
  PlatformCapabilities,
  RpgmakerLinuxRunnerStatus,
  RunnerInfo,
  SaveLocation,
  UrmStatusResponse,
} from "../../services/api";
import { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../../services/api";
import {
  getLaunchModeOptions,
  normalizeLaunchMode,
  resolveLaunchRuntimeOverrides,
  usesWineProtonControls,
} from "../../utils/launchMode";
import type { LaunchMode } from "../../utils/launchMode";
import { loadPlatformCapabilities } from "../../utils/platformCapabilities";
import {
  DEFAULT_PLAY_STATUS,
  getPlayStatusMeta,
  getPlayStatusOptions,
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

// One hue per engine; the text mixes toward --text-primary so it stays readable in both themes.
const ENGINE_HUES: Record<Exclude<EngineOption, "Others">, string> = {
  ADRIFT: "109, 128, 145",
  Flash: "189, 93, 56",
  HTML: "191, 125, 54",
  Java: "177, 120, 45",
  QSP: "125, 82, 156",
  RAGS: "150, 80, 72",
  RPGM: "179, 128, 204",
  "Ren'Py": "176, 90, 112",
  Tads: "104, 127, 83",
  Unity: "93, 141, 147",
  "Unreal Engine": "119, 119, 132",
  WebGL: "74, 148, 161",
  "Wolf RPG": "145, 91, 53",
};

const props = defineProps<{
  modelValue: boolean;
  game: GameRecord | null;
  updateCheckState: UpdateCheckState;
  isRunning?: boolean;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
  updated: [];
  deleted: [];
  launch: [payload: GameRecord];
  "check-updates": [gameId: number];
  "targets-changed": [];
  stop: [gameId: number];
}>();

// Editable fields
const title = ref("");
const exePath = ref("");
const f95Url = ref("");
const version = ref("");
const commandLineArgs = ref("");
const coverImage = ref("");
const playStatus = ref<PlayStatus>(DEFAULT_PLAY_STATUS);
const isFavorite = ref(false);
const tags = ref<string[]>([]);
const engine = ref<EngineOption>("Others");
const newTag = ref("");
const latestVersion = ref("");
const engineMenuOpen = ref(false);
const detailTabs = ["Overview", "Launch", "Info"] as const;
const activeTab = ref<(typeof detailTabs)[number]>("Overview");
const coverImageFailed = ref(false);
const runJapaneseLocale = ref(false);
const runWayland = ref(false);
const autoInjectCe = ref(false);
const customPrefix = ref("");
const protonVersion = ref("");
const launchMode = ref<LaunchMode>("auto");
const platformCapabilities = ref<PlatformCapabilities>({
  ...CONSERVATIVE_PLATFORM_CAPABILITIES,
});
const useCustomPrefix = ref(false);
const availableRunners = ref<RunnerInfo[]>([]);
const loadingRunners = ref(false);
const runnersLoaded = ref(false);
const rpgmakerLinuxRunnerAvailable = ref(false);
const rpgmakerLinuxRunnerLoaded = ref(false);
const rpgmakerLinuxRunnerError = ref("");
const ceInstalled = ref(false);
const executableModifiedAt = ref<string | null>(null);
const loadingExecutableModifiedAt = ref(false);
const executableModifiedKnown = ref(false);
const executableModifiedRequestId = ref(0);
const launchTargets = ref<LaunchTarget[]>([]);
const newTargetLabel = ref("");
const newTargetPath = ref("");
const showAddLaunchTarget = ref(false);
const editingTargetId = ref<number | null>(null);
const editTargetLabel = ref("");
const editTargetPath = ref("");
const loadingLaunchTargets = ref(false);
const savingTargetId = ref<number | null>(null);
const deletingTargetId = ref<number | null>(null);
const addingLaunchTarget = ref(false);
const reorderingLaunchTargets = ref(false);

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

const customStatuses = ref<string[]>([]);
const statuses = computed(() => getPlayStatusOptions([...customStatuses.value, playStatus.value]));
const urmStatus = ref<UrmStatusResponse>({});
const urmBusy = ref(false);
const urmError = ref("");
const runnerOptions = computed(() => protonVersion.value && !availableRunners.value.some((runner) => runner.path === protonVersion.value)
  ? [...availableRunners.value, { path: protonVersion.value, name: `${protonVersion.value} (custom)` }]
  : availableRunners.value);
const isNativeLaunchMode = computed(
  () => !usesWineProtonControls(launchMode.value),
);
const showWineProtonControls = computed(
  () =>
    platformCapabilities.value.wine_proton &&
    usesWineProtonControls(launchMode.value),
);
const launchModeOptions = computed(() =>
  getLaunchModeOptions(
    platformCapabilities.value,
    rpgmakerLinuxRunnerAvailable.value,
    launchMode.value,
  ),
);
const rpgmakerLinuxModeUnavailable = computed(
  () =>
    launchMode.value === "rpgmaker_linux" &&
    rpgmakerLinuxRunnerLoaded.value &&
    !rpgmakerLinuxRunnerAvailable.value,
);
const launchModeUnavailableOnPlatform = computed(
  () => !platformCapabilities.value.launch_modes.includes(launchMode.value),
);

const averagePersonalRating = computed(() => {
  const sum =
    ratingGraphics.value +
    ratingStory.value +
    ratingFappability.value +
    ratingGameplay.value;
  const avg = sum / 4;
  return avg > 0 ? avg.toFixed(1) : "—";
});

const hasUpdate = computed(() => hasAvailableUpdate({
  version: version.value,
  latest_version: latestVersion.value,
}));

const syncReadonlyMetadata = (game: GameRecord | null) => {
  latestVersion.value = game?.latest_version || "";
  f95Rating.value = game?.rating || "";
};

const sortLaunchTargets = (targets: LaunchTarget[]): LaunchTarget[] => {
  return targets
    .map((target) => ({ ...target }))
    .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
};

const syncLaunchTargets = (game: GameRecord | null) => {
  launchTargets.value = sortLaunchTargets(game?.launch_targets || []);
};

const resetLaunchTargetForms = () => {
  showAddLaunchTarget.value = false;
  editingTargetId.value = null;
  newTargetLabel.value = "";
  newTargetPath.value = "";
  editTargetLabel.value = "";
  editTargetPath.value = "";
};

const isCurrentGame = (gameId: number): boolean => {
  return props.modelValue && props.game?.id === gameId;
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

const getEngineBadgeStyle = (value: EngineOption): string => {
  if (value === "Others") {
    return "background: var(--bg-overlay); border: 1px solid var(--border); color: var(--text-secondary)";
  }
  const rgb = ENGINE_HUES[value];
  return `background: rgba(${rgb}, 0.16); border: 1px solid rgba(${rgb}, 0.36); color: color-mix(in srgb, rgb(${rgb}) 55%, var(--text-primary))`;
};

const selectedEngineStyle = computed(() => getEngineBadgeStyle(engine.value));

const statusButtonClasses = (value: PlayStatus) => {
  const meta = getPlayStatusMeta(value);
  return [
    "status-chip px-3 py-1.5 text-xs font-medium transition-colors",
    playStatus.value === value ? ["ui-status-chip", meta.toneClass, "status-chip-active"] : ["ui-status-chip", "ui-status-chip--idle", "status-chip-idle"],
  ];
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
]);

const ratingCategories = [
  { label: "Graphics", field: "rating_graphics", rating: ratingGraphics, icon: IconPaletteFilled },
  { label: "Story", field: "rating_story", rating: ratingStory, icon: IconBookFilled },
  { label: "Fappability", field: "rating_fappability", rating: ratingFappability, icon: IconFlameFilled },
  { label: "Gameplay", field: "rating_gameplay", rating: ratingGameplay, icon: IconDeviceGamepad2Filled },
] as const;

// Launch and info fields save explicitly; Overview fields save on click.
const formSnapshot = (): string =>
  JSON.stringify([
    title.value,
    exePath.value,
    f95Url.value,
    version.value,
    commandLineArgs.value,
    coverImage.value,
    engine.value,
    runJapaneseLocale.value,
    runWayland.value,
    autoInjectCe.value,
    useCustomPrefix.value,
    customPrefix.value,
    protonVersion.value,
    launchMode.value,
  ]);
const savedSnapshot = ref("");
const quickFields = (): Record<string, unknown> => ({
  play_status: playStatus.value,
  is_favorite: isFavorite.value ? 1 : 0,
  tags: tags.value.join(", "),
  rating_graphics: ratingGraphics.value,
  rating_story: ratingStory.value,
  rating_fappability: ratingFappability.value,
  rating_gameplay: ratingGameplay.value,
});
const savedQuickFields = ref<Record<string, unknown>>({});
const hasUnsavedChanges = computed(() =>
  formSnapshot() !== savedSnapshot.value ||
  JSON.stringify(quickFields()) !== JSON.stringify(savedQuickFields.value),
);

watch(coverImage, () => { coverImageFailed.value = false; });

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
        playStatus.value = normalizePlayStatus(g.play_status, g.status);
        isFavorite.value = !!g.is_favorite;
        engine.value = normalizeEngine(g.engine);
        runJapaneseLocale.value = g.run_japanese_locale ? true : false;
        runWayland.value = g.run_wayland ? true : false;
        autoInjectCe.value = g.auto_inject_ce ? true : false;
        customPrefix.value = g.custom_prefix || "";
        protonVersion.value = g.proton_version || "";
        launchMode.value = normalizeLaunchMode(g.launch_mode);
        useCustomPrefix.value = !!g.custom_prefix;
        activeTab.value = "Overview";
        resetLaunchTargetForms();
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
        syncLaunchTargets(g);

        ratingGraphics.value = g.rating_graphics || 0;
        ratingStory.value = g.rating_story || 0;
        ratingFappability.value = g.rating_fappability || 0;
        ratingGameplay.value = g.rating_gameplay || 0;
        savedSnapshot.value = formSnapshot();
        savedQuickFields.value = quickFields();
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
      platformCapabilities.value = await loadPlatformCapabilities();
      availableRunners.value = [];
      runnersLoaded.value = false;
      loadingRunners.value = false;
      rpgmakerLinuxRunnerLoaded.value = false;
      rpgmakerLinuxRunnerError.value = "";
      urmStatus.value = {};
      urmError.value = "";
      const loaders = [loadExecutableModifiedTime(), loadLaunchTargets(), loadGameExtras()];
      if (platformCapabilities.value.cheat_engine_injection) {
        loaders.push(loadCheatEngineStatus());
      } else {
        ceInstalled.value = false;
      }
      if (platformCapabilities.value.rpgmaker_linux) {
        loaders.push(loadRpgmakerLinuxRunnerStatus());
      } else {
        syncRpgmakerLinuxRunnerStatus({
          available: false,
          path: "",
          source: "",
          configured_path: "",
          error: "RPGMaker Linux is unavailable on this platform.",
        });
      }
      void Promise.all(loaders);
    } else {
      engineMenuOpen.value = false;
      executableModifiedAt.value = null;
      executableModifiedKnown.value = false;
      loadingExecutableModifiedAt.value = false;
      loadingLaunchTargets.value = false;
      launchTargets.value = [];
      resetLaunchTargetForms();
    }
  },
);

watch(
  () => props.game?.launch_targets,
  () => {
    if (props.modelValue) {
      syncLaunchTargets(props.game);
    }
  },
  { deep: true },
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
  () => [props.modelValue, showWineProtonControls.value] as const,
  async ([open, showControls]) => {
    if (
      !platformCapabilities.value.wine_proton ||
      !open ||
      !showControls ||
      runnersLoaded.value ||
      loadingRunners.value
    ) {
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

// User-initiated close (X, backdrop, Esc): don't drop unsaved form edits silently.
const requestClose = () => {
  if (hasUnsavedChanges.value && !confirm("Discard unsaved changes to this game?")) {
    return;
  }
  close();
};

const modalRef = ref<HTMLElement | null>(null);
const selectTab = (tab: (typeof detailTabs)[number]) => {
  activeTab.value = tab;
  engineMenuOpen.value = false;
  modalRef.value?.querySelector(".modal-scroll-body")?.scrollTo(0, 0);
};

const onTabKeydown = (event: KeyboardEvent, index: number) => {
  let nextIndex: number;
  if (event.key === "ArrowRight") nextIndex = (index + 1) % detailTabs.length;
  else if (event.key === "ArrowLeft") nextIndex = (index + detailTabs.length - 1) % detailTabs.length;
  else if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = detailTabs.length - 1;
  else return;
  event.preventDefault();
  selectTab(detailTabs[nextIndex]!);
  (event.currentTarget as HTMLElement).parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[nextIndex]?.focus();
};
useModalKeyboard(
  modalRef,
  () => props.modelValue && !!props.game,
  () => {
    if (engineMenuOpen.value) engineMenuOpen.value = false;
    else requestClose();
  },
);

const saveQuickFields = async (fields: Record<string, unknown>) => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  try {
    const res = await api.updateGame(gameId, fields);
    if (res && res.success === false) {
      notifyError("Failed to save: " + (res.error || "Unknown error"));
      return;
    }
    if (isCurrentGame(gameId)) {
      Object.assign(savedQuickFields.value, fields);
    }
  } catch (e) {
    console.error("Failed to save game", e);
    notifyError("Error saving: " + String(e));
  }
};

const setPlayStatus = (value: PlayStatus) => {
  if (playStatus.value === value) return;
  playStatus.value = value;
  void saveQuickFields({ play_status: value });
};

const toggleFavorite = () => {
  isFavorite.value = !isFavorite.value;
  void saveQuickFields({ is_favorite: isFavorite.value ? 1 : 0 });
};

const setRating = (category: (typeof ratingCategories)[number], star: number) => {
  category.rating.value = star;
  void saveQuickFields({ [category.field]: star });
};

const browseExe = async () => {
  try {
    const p = await api.browseFile(exePath.value || "");
    if (p) {
      exePath.value = p;
    }
  } catch (e) {
    console.error("Failed to browse file", e);
    notifyError("Error browsing file: " + String(e));
  }
};

const syncRpgmakerLinuxRunnerStatus = (status: RpgmakerLinuxRunnerStatus | undefined) => {
  rpgmakerLinuxRunnerAvailable.value = !!status?.available;
  rpgmakerLinuxRunnerError.value = status?.error || "";
  rpgmakerLinuxRunnerLoaded.value = true;
};

const loadRpgmakerLinuxRunnerStatus = async () => {
  try {
    const settings = await api.getSettings();
    syncRpgmakerLinuxRunnerStatus(settings?.rpgmaker_linux_runner_status);
  } catch (e) {
    console.error("Failed to load RPGMaker Linux runner status", e);
    rpgmakerLinuxRunnerAvailable.value = false;
    rpgmakerLinuxRunnerError.value = "Failed to check RPGMaker Linux runner status.";
    rpgmakerLinuxRunnerLoaded.value = true;
  }
};

const loadGameExtras = async () => {
  if (!props.game) return;
  const gameId = props.game.id;
  try {
    const [settings, status] = await Promise.all([api.getSettings(), api.getUrmStatus(gameId)]);
    if (isCurrentGame(gameId)) {
      customStatuses.value = settings.custom_play_statuses || [];
      urmStatus.value = status;
    }
  } catch (error) {
    console.error("Failed to load game settings", error);
  }
};

const toggleUrm = async () => {
  if (!props.game || urmBusy.value) return;
  const gameId = props.game.id;
  urmBusy.value = true;
  urmError.value = "";
  try {
    const result = await api.setUrmInstalled(gameId, !urmStatus.value.installed);
    if (!isCurrentGame(gameId)) return;
    if (result.success === false) urmError.value = result.error || "Could not update URM";
    else urmStatus.value = result;
  } catch (error) {
    if (isCurrentGame(gameId)) urmError.value = String(error);
  } finally {
    urmBusy.value = false;
  }
};

// v-model re-syncs the checkbox from urmStatus on re-render, so a failed toggle snaps back.
const urmInstalled = computed({
  get: () => !!urmStatus.value.installed,
  set: () => void toggleUrm(),
});

const loadLaunchTargets = async () => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  loadingLaunchTargets.value = true;
  try {
    const targets = await api.getLaunchTargets(gameId);
    if (isCurrentGame(gameId)) {
      launchTargets.value = sortLaunchTargets(targets || []);
    }
  } catch (e) {
    console.error("Failed to load launch targets", e);
    if (isCurrentGame(gameId)) {
      syncLaunchTargets(props.game);
    }
  } finally {
    if (isCurrentGame(gameId)) {
      loadingLaunchTargets.value = false;
    }
  }
};

const browseNewTargetPath = async () => {
  try {
    const p = await api.browseFile(newTargetPath.value || exePath.value || "");
    if (p) newTargetPath.value = p;
  } catch (e) {
    console.error("Failed to browse launch target", e);
    notifyError("Error browsing file: " + String(e));
  }
};

const browseEditTargetPath = async () => {
  try {
    const p = await api.browseFile(editTargetPath.value || exePath.value || "");
    if (p) editTargetPath.value = p;
  } catch (e) {
    console.error("Failed to browse launch target", e);
    notifyError("Error browsing file: " + String(e));
  }
};

const startAddLaunchTarget = () => {
  editingTargetId.value = null;
  editTargetLabel.value = "";
  editTargetPath.value = "";
  showAddLaunchTarget.value = true;
};

const cancelAddLaunchTarget = () => {
  showAddLaunchTarget.value = false;
  newTargetLabel.value = "";
  newTargetPath.value = "";
};

const startEditLaunchTarget = (target: LaunchTarget) => {
  showAddLaunchTarget.value = false;
  editingTargetId.value = target.id;
  editTargetLabel.value = target.label;
  editTargetPath.value = target.exe_path;
};

const cancelEditLaunchTarget = () => {
  editingTargetId.value = null;
  editTargetLabel.value = "";
  editTargetPath.value = "";
};

const addLaunchTarget = async () => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  const label = newTargetLabel.value.trim();
  const path = newTargetPath.value.trim();
  if (!label || !path) {
    notifyError("Launch target label and executable path are required.");
    return;
  }

  addingLaunchTarget.value = true;
  try {
    const res = await api.createLaunchTarget(
      gameId,
      label,
      path,
      launchTargets.value.length,
    );
    if (res && res.success === false) {
      notifyError("Failed to add launch target: " + (res.error || "Unknown error"));
      return;
    }
    if (res.target && isCurrentGame(gameId)) {
      launchTargets.value = sortLaunchTargets([...launchTargets.value, res.target]);
      cancelAddLaunchTarget();
    }
    emit("targets-changed");
  } catch (e) {
    console.error("Failed to add launch target", e);
    notifyError("Error adding launch target: " + String(e));
  } finally {
    addingLaunchTarget.value = false;
  }
};

const saveLaunchTarget = async (target: LaunchTarget) => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  const label = editTargetLabel.value.trim();
  const path = editTargetPath.value.trim();
  if (!label || !path) {
    notifyError("Launch target label and executable path are required.");
    return;
  }

  savingTargetId.value = target.id;
  try {
    const res = await api.updateLaunchTarget(target.id, {
      label,
      exe_path: path,
      sort_order: target.sort_order,
    });
    if (res && res.success === false) {
      notifyError("Failed to save launch target: " + (res.error || "Unknown error"));
      return;
    }
    if (res.target && isCurrentGame(gameId)) {
      launchTargets.value = sortLaunchTargets(
        launchTargets.value.map((item) => (item.id === res.target?.id ? res.target : item)),
      );
      cancelEditLaunchTarget();
    }
    emit("targets-changed");
  } catch (e) {
    console.error("Failed to save launch target", e);
    notifyError("Error saving launch target: " + String(e));
  } finally {
    savingTargetId.value = null;
  }
};

const deleteLaunchTarget = async (target: LaunchTarget) => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  if (!confirm(`Remove launch target "${target.label}"?`)) return;
  deletingTargetId.value = target.id;
  try {
    const res = await api.deleteLaunchTarget(target.id);
    if (res && res.success === false) {
      notifyError("Failed to remove launch target: " + (res.error || "Unknown error"));
      return;
    }
    if (isCurrentGame(gameId)) {
      launchTargets.value = launchTargets.value.filter((item) => item.id !== target.id);
      if (editingTargetId.value === target.id) {
        cancelEditLaunchTarget();
      }
    }
    emit("targets-changed");
  } catch (e) {
    console.error("Failed to remove launch target", e);
    notifyError("Error removing launch target: " + String(e));
  } finally {
    deletingTargetId.value = null;
  }
};

const moveLaunchTarget = async (index: number, direction: -1 | 1) => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  const nextIndex = index + direction;
  if (nextIndex < 0 || nextIndex >= launchTargets.value.length) return;

  const reordered = [...launchTargets.value];
  const [target] = reordered.splice(index, 1);
  if (!target) return;
  reordered.splice(nextIndex, 0, target);
  launchTargets.value = reordered.map((item, sortOrder) => ({
    ...item,
    sort_order: sortOrder,
  }));

  reorderingLaunchTargets.value = true;
  try {
    const res = await api.reorderLaunchTargets(
      gameId,
      launchTargets.value.map((item) => item.id),
    );
    if (res && res.success === false) {
      notifyError("Failed to reorder launch targets: " + (res.error || "Unknown error"));
      if (isCurrentGame(gameId)) {
        await loadLaunchTargets();
      }
      return;
    }
    if (isCurrentGame(gameId)) {
      launchTargets.value = sortLaunchTargets(res.targets || []);
    }
    emit("targets-changed");
  } catch (e) {
    console.error("Failed to reorder launch targets", e);
    notifyError("Error reordering launch targets: " + String(e));
    if (isCurrentGame(gameId)) {
      await loadLaunchTargets();
    }
  } finally {
    reorderingLaunchTargets.value = false;
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
    notifyError("Error browsing directory: " + String(e));
  }
};

const installRtpsToPrefix = async () => {
  notify("RTP installation has started in the background. It may take several minutes to complete.");
  try {
    const res = await api.installRpgmakerRtp(customPrefix.value, protonVersion.value);
    if (res && res.success === false) {
      notifyError("Failed to install RTPs: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to install RTPs", e);
    notifyError("Error installing RTPs: " + String(e));
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
      ...quickFields(),
      engine: normalizeEngine(engine.value),
      run_japanese_locale: platformCapabilities.value.wine_proton
        ? runJapaneseLocale.value
        : !!props.game.run_japanese_locale,
      run_wayland: platformCapabilities.value.wayland
        ? runWayland.value
        : !!props.game.run_wayland,
      auto_inject_ce: platformCapabilities.value.cheat_engine_injection
        ? autoInjectCe.value
        : !!props.game.auto_inject_ce,
      ...resolveLaunchRuntimeOverrides({
        wineProtonSupported: platformCapabilities.value.wine_proton,
        // Keep hidden prefix/runner values so switching to a non-Wine mode doesn't erase them;
        // the launcher already ignores them outside Wine/Proton launches.
        usesWineProtonRuntime: true,
        useCustomPrefix: useCustomPrefix.value,
        customPrefix: customPrefix.value,
        protonVersion: protonVersion.value,
        storedCustomPrefix: props.game.custom_prefix || "",
        storedProtonVersion: props.game.proton_version || "",
      }),
      launch_mode: launchMode.value,
      latest_version: latestVersion.value,
    });
    if (res && res.success === false) {
      notifyError("Failed to save game: " + (res.error || "Unknown error"));
    } else {
      savedSnapshot.value = formSnapshot();
      savedQuickFields.value = quickFields();
      emit("updated");
      close();
    }
  } catch (e) {
    console.error("Failed to save game", e);
    notifyError("Error saving game: " + String(e));
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
      notifyError("Failed to delete game: " + (res.error || "Unknown error"));
    } else {
      emit("deleted");
      close();
    }
  } catch (e) {
    console.error("Failed to delete", e);
    notifyError("Error deleting game: " + String(e));
  } finally {
    deleting.value = false;
  }
};

const buildLaunchPayload = (targetPath: string): GameRecord | null => {
  if (props.game) {
    const runtimeOverrides = resolveLaunchRuntimeOverrides({
      wineProtonSupported: platformCapabilities.value.wine_proton,
      usesWineProtonRuntime: !isNativeLaunchMode.value,
      useCustomPrefix: useCustomPrefix.value,
      customPrefix: customPrefix.value,
      protonVersion: protonVersion.value,
      storedCustomPrefix: props.game.custom_prefix || "",
      storedProtonVersion: props.game.proton_version || "",
    });
    return {
      ...props.game,
      title: title.value,
      exe_path: targetPath,
      f95_url: f95Url.value,
      version: version.value,
      command_line_args: commandLineArgs.value,
      cover_image_path: coverImage.value,
      play_status: playStatus.value,
      is_favorite: !!isFavorite.value,
      tags: tags.value.join(", "),
      engine: normalizeEngine(engine.value),
      run_japanese_locale: platformCapabilities.value.wine_proton
        ? !!runJapaneseLocale.value
        : !!props.game.run_japanese_locale,
      run_wayland: platformCapabilities.value.wayland
        ? !!runWayland.value
        : !!props.game.run_wayland,
      auto_inject_ce: platformCapabilities.value.cheat_engine_injection
        ? !isNativeLaunchMode.value && !!autoInjectCe.value
        : !!props.game.auto_inject_ce,
      custom_prefix: runtimeOverrides.custom_prefix,
      proton_version: runtimeOverrides.proton_version,
      launch_mode: launchMode.value,
    };
  }
  return null;
};

const launchGame = () => {
  if (props.isRunning) return;
  const payload = buildLaunchPayload(exePath.value);
  if (payload) emit("launch", payload);
};

const launchTarget = (target: LaunchTarget) => {
  if (props.isRunning) return;
  const payload = buildLaunchPayload(target.exe_path);
  if (payload) emit("launch", payload);
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
      !isNativeLaunchMode.value && useCustomPrefix.value ? customPrefix.value : "",
      !isNativeLaunchMode.value ? protonVersion.value : ""
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
      notifyError("Failed to open folder: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to open folder", e);
    notifyError("Error opening folder: " + String(e));
  }
};

const addTag = () => {
  const t = newTag.value.trim();
  newTag.value = "";
  if (t && !tags.value.includes(t)) {
    tags.value.push(t);
    void saveQuickFields({ tags: tags.value.join(", ") });
  }
};

const removeTag = (tag: string) => {
  tags.value = tags.value.filter((t) => t !== tag);
  void saveQuickFields({ tags: tags.value.join(", ") });
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
        notifyError("Failed to open browser: " + (res.error || "Unknown error"));
      }
    } catch (e) {
      console.error("Failed to open browser", e);
      notifyError("Error opening browser: " + String(e));
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
      @click="requestClose"
    ></div>

    <div
      ref="modalRef"
      role="dialog"
      aria-modal="true"
      aria-labelledby="game-detail-title"
      tabindex="-1"
      class="modal-content relative flex max-h-[90vh] w-full max-w-[56rem] flex-col overflow-hidden rounded-xl"
    >
      <div class="absolute right-4 top-4 z-10">
        <button
          @click="requestClose"
          aria-label="Close"
          title="Close (Esc)"
          class="modal-close-btn transition-colors"
          style="color: var(--text-muted); background: var(--bg-raised); border: 1px solid var(--border)"
        >
          <IconX class="ui-action-icon" />
        </button>
      </div>

      <header class="detail-header px-6 py-5">
        <div class="game-header">
          <div class="game-cover">
            <img v-if="coverImage && !coverImageFailed" :src="coverImage" :alt="`${title || game.title} cover`" @error="coverImageFailed = true" />
            <IconDeviceGamepad2Filled v-else class="h-10 w-10" role="img" aria-label="No cover image" />
          </div>
          <div class="min-w-0">
            <h2 id="game-detail-title" class="game-title" :title="title || game.title">{{ title || game.title || 'Untitled game' }}</h2>
            <p v-if="game.developer" class="text-sm mt-1 break-words" style="color: var(--text-secondary)">by {{ game.developer }}</p>
            <div class="flex flex-wrap items-center gap-2 mt-3">
              <span class="identity-badge" :style="selectedEngineStyle">{{ engine }}</span>
              <span v-if="version" class="identity-badge version-badge">{{ version }}</span>
              <span v-if="hasUpdate" class="identity-badge update-badge">Update: {{ latestVersion }}</span>
            </div>
          </div>
        </div>
        <div class="modal-toolbar">
          <div class="modal-toolbar-group">
            <button
              @click="isRunning && game ? emit('stop', game.id) : launchGame()"
              :title="isRunning ? 'Stop the game and all processes in its Wine prefix.' : 'Play game'"
              class="launch-btn header-launch-btn ui-action-btn"
            >
              <IconPlayerStopFilled v-if="isRunning" class="ui-action-icon" />
              <IconPlayerPlayFilled v-else class="ui-action-icon" />
              {{ isRunning ? 'Stop' : 'Play' }}
            </button>
            <button
              v-if="f95Url"
              @click="openInBrowser"
              class="modal-toolbar-action ui-action-btn"
              style="color: var(--text-primary)"
            >
              <IconExternalLinkFilled class="ui-action-icon" />
              Open in Browser
            </button>
            <button
              v-if="game.f95_url"
              @click="requestUpdateCheck"
              :disabled="updateCheckState.running"
              class="modal-toolbar-action ui-action-btn"
              style="color: var(--text-primary)"
            >
              <IconRefresh class="ui-action-icon" :class="updateCheckState.running ? 'animate-spin' : ''" />
              {{ updateCheckState.running ? "Checking..." : "Check Updates" }}
            </button>
          </div>

          <div class="modal-toolbar-group modal-toolbar-group--end">
            <button
              @click="toggleFavorite"
              class="modal-toolbar-action ui-action-btn"
              :style="
                isFavorite
                  ? 'background: rgba(234, 179, 8, 0.14); border: 1px solid rgba(234, 179, 8, 0.42); color: var(--rating-accent)'
                  : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-muted)'
              "
            >
              <IconStarFilled class="ui-action-icon" :style="isFavorite ? '' : 'opacity: 0.7'" />
              {{ isFavorite ? 'Favorited' : 'Mark Favorite' }}
            </button>
          </div>
        </div>
      </header>

      <div class="detail-tabs px-6" role="tablist" aria-label="Game details">
        <button
          v-for="(tab, index) in detailTabs"
          :key="tab"
          :id="`game-detail-tab-${tab}`"
          role="tab"
          :aria-selected="activeTab === tab"
          :aria-controls="`game-detail-panel-${tab}`"
          :tabindex="activeTab === tab ? 0 : -1"
          class="detail-tab"
          @click="selectTab(tab)"
          @keydown="onTabKeydown($event, index)"
        >{{ tab }}</button>
      </div>

      <div class="modal-scroll-body min-h-0 overflow-y-auto p-6 flex-1">
        <section v-show="activeTab === 'Overview'" id="game-detail-panel-Overview" role="tabpanel" aria-labelledby="game-detail-tab-Overview" tabindex="0" class="space-y-6">
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
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

          <div class="status-strip">
            <h3 class="modal-label mb-3">Play Status</h3>
            <div class="status-grid">
              <button
                v-for="s in statuses"
                :key="s.value"
                @click="setPlayStatus(s.value)"
                :aria-pressed="playStatus === s.value"
                :class="[statusButtonClasses(s.value), 'w-full']"
              >
                <component :is="s.icon" class="ui-status-icon" />
                <span>{{ s.label }}</span>
              </button>
            </div>
          </div>

          <div>
            <h3
              class="ui-section-heading text-sm font-bold mb-3"
              style="color: var(--text-secondary)"
            >
              <IconStarFilled class="ui-section-icon" />
              Your Ratings
            </h3>
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div
                v-for="cat in ratingCategories"
                :key="cat.field"
                class="grid grid-cols-[1fr_auto] items-center gap-2 rounded-lg p-3"
                style="background: var(--bg-raised); border: 1px solid var(--border)"
              >
                <span class="rating-label col-span-2" style="color: var(--text-secondary)">
                  <component :is="cat.icon" class="w-4 h-4 shrink-0" style="color: var(--brand)" />
                  {{ cat.label }}
                </span>
                <div class="flex gap-1">
                  <button
                    v-for="star in 5"
                    :key="star"
                    @click="setRating(cat, star)"
                    :aria-label="`Rate ${cat.label} ${star} of 5`"
                    class="transition-transform hover:scale-110"
                  >
                    <IconStarFilled
                      class="w-5 h-5"
                      :style="
                        star > cat.rating.value
                          ? 'color: var(--border)'
                          : 'color: var(--rating-accent)'
                      "
                    />
                  </button>
                </div>
                <span
                  class="text-xs font-mono ml-auto"
                  style="color: var(--text-muted)"
                  >{{ cat.rating.value }}/5</span
                >
              </div>
            </div>
          </div>

          <div>
            <h3 class="modal-label mb-3">Tags ({{ tags.length }})</h3>
            <div>
              <p v-if="!tags.length" class="text-xs mb-2" style="color: var(--text-muted)">No tags yet.</p>
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
                    :aria-label="`Remove tag ${tag}`"
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
                  aria-label="New tag"
                  type="text"
                  placeholder="Add a tag..."
                  @keydown.enter.prevent="addTag"
                  class="modal-input flex-1 !py-1.5 !text-xs"
                />
                <button @click="addTag" class="modal-btn">Add</button>
              </div>
            </div>
          </div>
          <p class="detail-timestamps">
            <span>Exe modified: {{ executableModifiedDisplay }}</span>
            <span>Thread updated: {{ threadMainPostEditedDisplay }}</span>
          </p>
        </section>

        <section v-show="activeTab === 'Launch'" id="game-detail-panel-Launch" role="tabpanel" aria-labelledby="game-detail-tab-Launch" tabindex="0" class="space-y-6">
          <div class="grid grid-cols-2 gap-4">
            <div class="col-span-2">
              <label for="detail-exe" class="modal-label">Executable Path</label>
              <div class="flex gap-2">
                <input
                  v-model="exePath" id="detail-exe"
                  type="text"
                  class="modal-input flex-1 font-mono"
                />
                <button @click="browseExe" class="modal-btn ui-action-btn">
                  <IconFolderOpenFilled class="ui-action-icon" />
                  Browse
                </button>
              </div>
            </div>

            <div class="col-span-2">
              <label class="modal-label">Launch Targets</label>
              <div class="launch-target-panel">
                <div class="launch-target-row launch-target-card">
                  <div class="launch-target-main">
                    <div class="launch-target-name">Default</div>
                    <div class="launch-target-path" :title="exePath">
                      {{ exePath || "No executable selected" }}
                    </div>
                  </div>
                  <button v-if="!isRunning" @click="launchGame" class="launch-target-action ui-action-btn">
                    <IconPlayerPlayFilled class="ui-action-icon" />
                    Play
                  </button>
                </div>

                <div v-if="loadingLaunchTargets" class="launch-target-empty">
                  Loading launch targets...
                </div>

                <div
                  v-for="(target, index) in launchTargets"
                  :key="target.id"
                  class="launch-target-card"
                >
                  <div v-if="editingTargetId === target.id" class="launch-target-form">
                    <label class="launch-target-field">
                      <span class="launch-target-field-label">Label</span>
                      <input
                        v-model="editTargetLabel"
                        type="text"
                        class="modal-input w-full"
                        placeholder="Part 2"
                      />
                    </label>
                    <div class="launch-target-field launch-target-field-path">
                      <span class="launch-target-field-label">Path</span>
                      <div class="launch-target-path-input">
                        <input
                          v-model="editTargetPath"
                          type="text"
                          class="modal-input flex-1 min-w-0 font-mono"
                          placeholder="/path/to/part2.exe"
                        />
                        <button @click="browseEditTargetPath" class="modal-btn">
                          Browse
                        </button>
                      </div>
                    </div>
                    <div class="launch-target-form-actions">
                      <button @click="cancelEditLaunchTarget" class="modal-btn">
                        Cancel
                      </button>
                      <button
                        @click="saveLaunchTarget(target)"
                        :disabled="savingTargetId === target.id"
                        class="modal-btn ui-action-btn disabled:opacity-50"
                      >
                        {{ savingTargetId === target.id ? "Saving..." : "Save Target" }}
                      </button>
                    </div>
                  </div>

                  <div v-else class="launch-target-row">
                    <div class="launch-target-main">
                      <div class="launch-target-name">{{ target.label }}</div>
                      <div class="launch-target-path" :title="target.exe_path">
                        {{ target.exe_path }}
                      </div>
                    </div>
                    <div class="launch-target-actions">
                      <div class="launch-target-order-actions">
                        <button
                          @click="moveLaunchTarget(index, -1)"
                          :disabled="index === 0 || reorderingLaunchTargets"
                          class="launch-target-order-btn disabled:opacity-50"
                          :aria-label="`Move ${target.label} up`"
                          title="Move up"
                        >
                          <IconChevronUp class="ui-action-icon" />
                        </button>
                        <button
                          @click="moveLaunchTarget(index, 1)"
                          :disabled="index === launchTargets.length - 1 || reorderingLaunchTargets"
                          class="launch-target-order-btn disabled:opacity-50"
                          :aria-label="`Move ${target.label} down`"
                          title="Move down"
                        >
                          <IconChevronDown class="ui-action-icon" />
                        </button>
                      </div>
                      <button v-if="!isRunning" @click="launchTarget(target)" class="launch-target-action ui-action-btn">
                        <IconPlayerPlayFilled class="ui-action-icon" />
                        Play
                      </button>
                      <button @click="startEditLaunchTarget(target)" class="launch-target-action">
                        Edit
                      </button>
                      <button
                        @click="deleteLaunchTarget(target)"
                        :disabled="deletingTargetId === target.id"
                        class="launch-target-action text-red-400 disabled:opacity-50"
                      >
                        {{ deletingTargetId === target.id ? "Removing..." : "Remove" }}
                      </button>
                    </div>
                  </div>
                </div>

                <div v-if="showAddLaunchTarget" class="launch-target-card">
                  <div class="launch-target-form">
                    <label class="launch-target-field">
                      <span class="launch-target-field-label">Label</span>
                      <input
                        v-model="newTargetLabel"
                        type="text"
                        class="modal-input w-full"
                        placeholder="Part 2"
                      />
                    </label>
                    <div class="launch-target-field launch-target-field-path">
                      <span class="launch-target-field-label">Path</span>
                      <div class="launch-target-path-input">
                        <input
                          v-model="newTargetPath"
                          type="text"
                          class="modal-input flex-1 min-w-0 font-mono"
                          placeholder="/path/to/part2.exe"
                        />
                        <button @click="browseNewTargetPath" class="modal-btn">
                          Browse
                        </button>
                      </div>
                    </div>
                    <div class="launch-target-form-actions">
                      <button @click="cancelAddLaunchTarget" class="modal-btn">
                        Cancel
                      </button>
                      <button
                        @click="addLaunchTarget"
                        :disabled="addingLaunchTarget"
                        class="modal-btn ui-action-btn disabled:opacity-50"
                      >
                        {{ addingLaunchTarget ? "Adding..." : "Add Target" }}
                      </button>
                    </div>
                  </div>
                </div>

                <button
                  v-else
                  @click="startAddLaunchTarget"
                  class="launch-target-add-button"
                >
                  + Add launch target
                </button>
              </div>
            </div>

            <div class="col-span-2">
              <label for="detail-command" class="modal-label">{{ launchMode === 'custom' ? 'Command' : 'Command Line Arguments' }}</label>
              <input
                v-model="commandLineArgs"
                id="detail-command"
                type="text"
                placeholder="gamemoderun %command% --fullscreen"
                class="modal-input w-full font-mono"
              />
              <p v-if="launchMode === 'custom'" class="text-xs mt-2" style="color: var(--text-muted)">Use %command% for the target path, or run a command as written in the game folder.</p>
            </div>

            <div class="col-span-2">
              <label class="modal-label">Launch Options</label>
              <div
                class="rounded-lg p-3 space-y-2"
                style="background: var(--bg-raised); border: 1px solid var(--border)"
              >
                <div class="rounded-md px-2 py-2" style="background: var(--bg-surface)">
                  <label for="detail-runtime" class="modal-label">Runtime Mode</label>
                  <select v-model="launchMode" id="detail-runtime" class="modal-input w-full">
                    <option
                      v-for="option in launchModeOptions"
                      :key="option.value"
                      :value="option.value"
                    >
                      {{ option.label }}
                    </option>
                  </select>
                  <p class="text-xs mt-2" style="color: var(--text-muted)">
                    {{
                      platformCapabilities.platform === "windows"
                        ? "Auto Detect launches Windows games directly without Wine or Proton."
                        : "Linux Native runs the selected file directly. Custom Command runs your command in the game folder. RPGMaker Linux uses the external native-port runner when installed."
                    }}
                  </p>
                  <p
                    v-if="launchModeUnavailableOnPlatform"
                    class="text-xs mt-2 text-yellow-400"
                  >
                    This stored launch mode is unavailable on
                    {{ platformCapabilities.platform }}. Choose Auto Detect to
                    launch on this platform; the value is preserved until you save.
                  </p>
                  <p
                    v-if="rpgmakerLinuxModeUnavailable"
                    class="text-xs mt-2 text-yellow-400"
                  >
                    This game is set to RPGMaker Linux, but the runner is not detected.
                    Install it or set the runner path in Settings before launching.
                    {{ rpgmakerLinuxRunnerError }}
                  </p>
                </div>

                <div
                  v-if="platformCapabilities.wine_proton"
                  class="rounded-md px-2 py-1"
                  style="background: var(--bg-surface)"
                >
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-medium" style="color: var(--text-primary)">
                      Run with Japanese Locale
                    </span>
                    <label class="relative inline-flex items-center cursor-pointer ml-auto">
                      <input
                        type="checkbox"
                        v-model="runJapaneseLocale"
                        aria-label="Run with Japanese Locale"
                        class="sr-only peer"
                      />
                      <div class="ui-toggle"></div>
                    </label>
                  </div>
                  <p
                    class="text-xs mt-1 pr-12"
                    style="color: var(--text-muted)"
                  >
                    Enable this if the game has unreadable text (Mojibake) or crashes due to missing Japanese fonts.
                  </p>
                </div>

                <div
                  v-if="platformCapabilities.wayland"
                  class="rounded-md px-2 py-1"
                  style="background: var(--bg-surface)"
                >
                  <div class="flex items-center gap-2">
                    <span class="text-sm font-medium" style="color: var(--text-primary)">
                      Run in Wayland Compatibility Mode
                    </span>
                    <label class="relative inline-flex items-center cursor-pointer ml-auto">
                      <input
                        type="checkbox"
                        v-model="runWayland"
                        aria-label="Run in Wayland Compatibility Mode"
                        class="sr-only peer"
                      />
                      <div class="ui-toggle"></div>
                    </label>
                  </div>
                  <p
                    class="text-xs mt-1 pr-12"
                    style="color: var(--text-muted)"
                  >
                    Enable this if your system uses Wayland and the game crashes or freezes on launch.
                  </p>
                </div>

                <div
                  v-if="
                    platformCapabilities.cheat_engine_injection &&
                    !isNativeLaunchMode
                  "
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
                    <label
                      class="relative inline-flex items-center ml-auto"
                      :class="ceInstalled ? 'cursor-pointer' : 'cursor-not-allowed'"
                    >
                      <input
                        type="checkbox"
                        v-model="autoInjectCe"
                        aria-label="Auto-Launch & Inject Cheat Engine"
                        :disabled="!ceInstalled"
                        class="sr-only peer"
                      />
                      <div class="ui-toggle"></div>
                    </label>
                  </div>
                  <p
                    class="text-xs mt-1 pr-12"
                    style="color: var(--text-muted)"
                  >
                    Spawns Cheat Engine inside the same virtual Wine sandbox immediately after the game starts.
                  </p>
                </div>
              </div>
            </div>

            <!-- Advanced Launch Options -->
            <div v-if="showWineProtonControls" class="col-span-2 mt-2">
              <div
                class="flex items-center justify-between p-3 rounded-lg"
                style="
                  background: var(--bg-raised);
                  border: 1px solid var(--border);
                "
              >
                <div>
                  <p class="text-sm font-medium" style="color: var(--text-primary)">
                    Use Custom Wine Prefix
                  </p>
                  <p class="text-xs" style="color: var(--text-muted)">
                    Isolate this game's save files and dependencies from the global prefix.
                  </p>
                </div>
                <label class="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    v-model="useCustomPrefix"
                    aria-label="Use Custom Wine Prefix"
                    class="sr-only peer"
                  />
                  <div class="ui-toggle"></div>
                </label>
              </div>

              <div class="mt-3">
                <label for="game-runner" class="modal-label">Runner (Wine / Proton)</label>
                <select id="game-runner" v-model="protonVersion" class="modal-input w-full">
                  <option value="">{{ loadingRunners ? 'Loading runners...' : '(Use Global Default)' }}</option>
                  <option v-for="runner in runnerOptions" :key="runner.path" :value="runner.path">{{ runner.name }}</option>
                </select>
              </div>
              <div v-if="useCustomPrefix" class="mt-3 p-4 rounded-lg space-y-4" style="background: var(--bg-inset); border: 1px dashed var(--border);">
                <div>
                  <label for="detail-prefix" class="modal-label">Custom Prefix Path</label>
                  <div class="flex gap-2">
                    <input
                      v-model="customPrefix" id="detail-prefix"
                      type="text"
                      placeholder="/home/user/games/my_game_prefix"
                      class="modal-input flex-1 font-mono text-xs"
                    />
                    <button @click="browseCustomPrefix" class="modal-btn ui-action-btn">
                      <IconFolderOpenFilled class="ui-action-icon" />
                      Browse
                    </button>
                  </div>
                </div>

                <div class="pt-2 border-t border-gray-700/50">
                  <button
                    @click="installRtpsToPrefix"
                    class="w-full text-xs font-medium px-4 py-2 rounded-lg transition-colors"
                    style="background: var(--brand-glow); color: var(--brand); border: 1px solid var(--brand-deep);"
                  >
                    Install RTPs to this Prefix
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div v-if="urmStatus.renpy" class="rounded-lg p-4 space-y-2" style="background: var(--bg-raised); border: 1px solid var(--border)">
            <div class="flex items-center justify-between gap-3">
              <span class="text-sm font-medium" style="color: var(--text-primary)">Universal Ren'Py Mod (URM)</span>
              <label
                class="relative inline-flex items-center"
                :class="urmStatus.source_configured && !urmBusy ? 'cursor-pointer' : 'cursor-not-allowed'"
              >
                <input
                  type="checkbox"
                  v-model="urmInstalled"
                  :disabled="urmBusy || !urmStatus.source_configured"
                  class="sr-only peer"
                  aria-label="Universal Ren'Py Mod (URM)"
                />
                <div class="ui-toggle"></div>
              </label>
            </div>
            <p v-if="!urmStatus.source_configured" class="text-xs" style="color: var(--text-muted)">Configure a URM .rpa source file in Settings to enable this control.</p>
            <p v-if="urmError" role="alert" class="text-xs text-red-400">{{ urmError }}</p>
          </div>
        </section>

        <section v-show="activeTab === 'Info'" id="game-detail-panel-Info" role="tabpanel" aria-labelledby="game-detail-tab-Info" tabindex="0" class="space-y-6">
          <div class="grid grid-cols-2 gap-4">
            <div class="col-span-2">
              <label for="detail-title" class="modal-label">Title</label>
              <input v-model="title" id="detail-title" type="text" class="modal-input w-full" />
            </div>

            <div>
              <label for="detail-version" class="modal-label">Version</label>
              <input
                v-model="version" id="detail-version"
                type="text"
                placeholder="v1.0"
                class="modal-input w-full font-mono"
                style="color: var(--brand)"
              />
            </div>

            <div class="relative" ref="engineDropdownRef">
              <label for="detail-engine" class="modal-label">Engine</label>
              <button
                id="detail-engine"
                type="button"
                :aria-expanded="engineMenuOpen"
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
              <label for="detail-url" class="modal-label">F95Zone URL</label>
              <input
                v-model="f95Url" id="detail-url"
                type="text"
                placeholder="https://f95zone.to/threads/..."
                class="modal-input w-full"
                style="color: var(--brand)"
              />
            </div>

            <div class="col-span-2">
              <label for="detail-cover" class="modal-label">Cover Image URL</label>
              <input
                v-model="coverImage" id="detail-cover"
                type="text"
                placeholder="https://..."
                class="modal-input w-full"
              />
            </div>
          </div>
        </section>
      </div>

      <div
        v-if="showSavePanel"
        class="px-6 py-3"
        style="border-top: 1px solid var(--border); background: var(--bg-inset)"
      >
        <div class="flex items-center justify-between mb-2">
          <h4
            class="ui-section-heading text-xs font-semibold uppercase tracking-wider"
            style="color: var(--text-secondary)"
          >
            <IconFolderOpenFilled class="ui-section-icon" />
            Save File Locations
          </h4>
          <button
            @click="showSavePanel = false"
            class="text-xs hover:text-[var(--text-primary)] transition-colors"
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
          <IconLoader2 class="ui-action-icon animate-spin" />
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
              ? 'background: var(--danger-bg); border-color: var(--danger-border); color: var(--danger-text)'
              : 'background: var(--success-bg); border-color: var(--success-border); color: var(--success-text)'
          "
        >
          {{ updateCheckState.message }}
        </p>
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex flex-wrap gap-2">
            <button
              @click="deleteGame"
              :disabled="deleting"
              class="footer-action ui-action-btn text-red-400"
            >
              <IconTrashX class="ui-action-icon" />
              {{ deleting ? "Removing..." : "Remove" }}
            </button>
            <button
              @click="findSaves"
              :disabled="searchingSaves"
              class="footer-action ui-action-btn"
              style="color: var(--text-primary)"
            >
              <IconFolderOpenFilled class="ui-action-icon" />
              {{ searchingSaves ? "Searching..." : "Find Saves" }}
            </button>
          </div>
          <div v-if="hasUnsavedChanges" class="flex flex-wrap items-center gap-3">
            <span class="text-xs" style="color: var(--text-muted)">Unsaved changes</span>
            <button
              @click="save"
              :disabled="saving"
              class="save-btn ui-action-btn disabled:opacity-50"
            >
              <IconDeviceFloppyFilled class="ui-action-icon" />
              {{ saving ? "Saving..." : "Save Changes" }}
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.detail-header,
.detail-tabs {
  flex-shrink: 0;
  border-bottom: 1px solid var(--border);
}

.game-header {
  display: grid;
  grid-template-columns: 6rem minmax(0, 1fr);
  align-items: center;
  gap: 1rem;
  padding-right: 2.75rem;
}

.game-cover {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 6rem;
  height: 7.5rem;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 0.625rem;
  background: var(--bg-inset);
  color: var(--text-muted);
}

.game-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.game-title {
  font-size: 1.375rem;
  font-weight: 700;
  line-height: 1.3;
  color: var(--text-primary);
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.version-badge {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}

.update-badge {
  background: color-mix(in srgb, var(--warning-text) 12%, var(--bg-surface));
  border: 1px solid color-mix(in srgb, var(--warning-text) 35%, transparent);
  color: var(--warning-text);
}

.header-launch-btn {
  justify-content: center;
  min-width: 6.5rem;
}

.detail-header .modal-toolbar {
  margin-top: 1rem;
}

.detail-tabs {
  display: flex;
  gap: 1.5rem;
}

.detail-tab {
  padding: 0.875rem 0.25rem;
  border-bottom: 2px solid transparent;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text-muted);
}

.detail-tab:hover {
  color: var(--text-primary);
}

.detail-tab[aria-selected="true"] {
  border-bottom-color: var(--brand);
  color: var(--brand);
}

.detail-tab:focus-visible,
.launch-target-order-btn:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.detail-timestamps {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 1rem;
  font-size: 0.6875rem;
  color: var(--text-muted);
}

@media (max-width: 640px) {
  .game-header {
    grid-template-columns: 4.5rem minmax(0, 1fr);
    gap: 0.75rem;
    padding-right: 1.5rem;
  }

  .game-cover {
    width: 4.5rem;
    height: 6rem;
  }

  .game-title {
    font-size: 1.125rem;
  }

}

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
  min-width: 0;
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

.launch-target-panel {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
  padding: 0.5rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--bg-surface);
}

.launch-target-card {
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--bg-raised);
}

.launch-target-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  min-height: 3.25rem;
  padding: 0.55rem 0.6rem;
}

.launch-target-main {
  min-width: 0;
  flex: 1;
}

.launch-target-form {
  display: grid;
  grid-template-columns: minmax(8rem, 0.8fr) minmax(14rem, 1.6fr);
  gap: 0.6rem;
  padding: 0.65rem;
}

.launch-target-field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 0.25rem;
}

.launch-target-field-label {
  font-size: 0.7rem;
  color: var(--text-secondary);
}

.launch-target-field-path {
  min-width: 0;
}

.launch-target-path-input {
  display: flex;
  min-width: 0;
  gap: 0.4rem;
}

.launch-target-form-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  gap: 0.4rem;
}

.launch-target-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 0.8rem;
  font-weight: 600;
  color: var(--text-primary);
}

.launch-target-path,
.launch-target-empty {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
  font-size: 0.75rem;
  color: var(--text-muted);
}

.launch-target-empty {
  padding: 0.4rem 0.6rem;
}

.launch-target-actions {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  flex-shrink: 0;
}

.launch-target-action,
.launch-target-order-btn,
.launch-target-add-button {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  border-radius: 0.45rem;
  font-size: 0.75rem;
  font-weight: 500;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.launch-target-action {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  min-height: 2rem;
  padding: 0.4rem 0.6rem;
}

.launch-target-order-actions {
  display: flex;
  gap: 0.25rem;
}

.launch-target-order-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 2rem;
  padding: 0.35rem 0.45rem;
  color: var(--text-secondary);
}

.launch-target-add-button {
  align-self: flex-start;
  min-height: 2rem;
  padding: 0.4rem 0.65rem;
  color: var(--text-secondary);
}

.launch-target-action:hover:not(:disabled),
.launch-target-order-btn:hover:not(:disabled),
.launch-target-add-button:hover:not(:disabled) {
  background: var(--border-hover);
  color: var(--text-primary);
}

@media (max-width: 768px) {
  .launch-target-row {
    flex-direction: column;
    align-items: stretch;
  }

  .launch-target-form {
    grid-template-columns: 1fr;
  }

  .launch-target-actions {
    justify-content: flex-start;
    flex-wrap: wrap;
  }

  .launch-target-form-actions {
    justify-content: flex-start;
  }
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
  min-height: 2.375rem;
  padding-top: 0.375rem;
  padding-bottom: 0.375rem;
  text-align: left;
}

.engine-select .identity-badge {
  padding: 0.25rem 0.5rem;
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

.status-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(9.75rem, 1fr));
  gap: 0.5rem;
}

.modal-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}

.modal-toolbar-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.modal-toolbar-group--end {
  margin-left: auto;
}

.modal-toolbar-action,
.footer-action {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  min-height: 2.375rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
  font-weight: 500;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}

.modal-toolbar-action:hover:not(:disabled),
.footer-action:hover:not(:disabled) {
  background: var(--bg-overlay);
  border-color: var(--border-hover);
}

.modal-toolbar-action:disabled,
.footer-action:disabled {
  opacity: 0.6;
}

.modal-close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.375rem;
  height: 2.375rem;
  border-radius: 0.5rem;
}

.status-chip {
  border-radius: 0.625rem;
}

.status-chip-active {
  box-shadow: inset 0 0 0 1px color-mix(in srgb, currentColor 24%, transparent);
}

.status-chip-idle:hover {
  background: var(--bg-overlay);
  border-color: var(--border-hover);
  color: var(--text-primary);
}

.launch-btn,
.save-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  border-radius: 0.5rem;
  min-height: 2.375rem;
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
