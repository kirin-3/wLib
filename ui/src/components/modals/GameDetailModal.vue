<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { ref, watch, computed, onMounted, onBeforeUnmount } from "vue";
import { useRouter } from "vue-router";
import { sandboxSwitchState } from "../../utils/sandbox";
import type { SandboxStatus } from "../../services/api";
import GameCompanionTools from "../GameCompanionTools.vue";
import TagColorPicker from "../TagColorPicker.vue";
import { colorForTag, loadTagColors } from "../../utils/tagColors";
import {
  IconBookFilled,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconDeviceFloppyFilled,
  IconExternalLinkFilled,
  IconFlameFilled,
  IconFolderOpenFilled,
  IconPaletteFilled,
  IconPlayerPlayFilled,
  IconPlayerStopFilled,
  IconDownload,
  IconRefresh,
  IconStarFilled,
  IconTrashX,
  IconX,
  IconDeviceGamepad2Filled,
} from "@tabler/icons-vue";
import { storeLabel } from "../../utils/storeLinks";
import { api, onWebviewReady } from "../../services/api";
import { notify, notifyError } from "../../utils/toast";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { getSafeNotesLinkUrl } from "../../utils/notesLinks";
import { openInstallArchive } from "../../utils/installArchive";
import { useModalKeyboard } from "../../utils/modalKeyboard";
import ScreenshotLightbox from "../ScreenshotLightbox.vue";
import { hasAvailableUpdate, isUpdateSkipped, isWishlisted, normalizeVersion } from "../../utils/libraryGames";
import type {
  GameAttachment,
  GameRecord,
  LaunchTarget,
  PlatformCapabilities,
  PlaySession,
  PlaytimeTickDetail,
  RpgmakerLinuxRunnerStatus,
  RunnerInfo,
  UpdateGameResponse,
  GameToolEngine,
} from "../../services/api";
import { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../../services/api";
import {
  getLaunchModeOptions,
  isClearlyNativeTarget,
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
import PathStatus from "../PathStatus.vue";
import GameSavesSection from "../GameSavesSection.vue";
import GameToolsTab from "../GameToolsTab.vue";

type UpdateCheckState = {
  running: boolean;
  type: "" | "success" | "error";
  message: string;
};

const ENGINE_OPTIONS = [
  "ADRIFT",
  "Flash",
  "Godot",
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
  Godot: "71, 140, 191",
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

const sandboxRouter = useRouter();
const sandboxEnabled = ref(false);
const sandboxStatus = ref<SandboxStatus | null>(null);

// Editable fields
const title = ref("");
const exePath = ref("");
const f95Url = ref("");
const version = ref("");
const finishedVersion = ref("");
const commandLineArgs = ref("");
const coverImage = ref("");
const playStatus = ref<PlayStatus>(DEFAULT_PLAY_STATUS);
const isFavorite = ref(false);
const tags = ref<string[]>([]);
const engine = ref<EngineOption>("Others");
const newTag = ref("");
const latestVersion = ref("");
const skippedVersion = ref("");
const savingSkip = ref(false);
const engineMenuOpen = ref(false);
const baseTabs = ["Overview", "Journal", "Launch", "Info", "Notes"] as const;
type DetailTab = (typeof baseTabs)[number] | "Tools";
// "Tools" exists only for games whose folder is a supported engine.
const toolEngine = ref<GameToolEngine>("");
const detailTabs = computed<DetailTab[]>(() => (toolEngine.value ? [...baseTabs, "Tools"] : [...baseTabs]));
const activeTab = ref<DetailTab>("Overview");
const notes = ref("");
const notesEditing = ref(false);
const attachments = ref<GameAttachment[]>([]);
const loadingAttachments = ref(false);
const attachingPdf = ref(false);
const removingAttachment = ref<string | null>(null);
let attachmentsRequestId = 0;
const coverImageFailed = ref(false);
const lightboxIndex = ref<number | null>(null);
const failedScreenshots = ref<Set<string>>(new Set());
const gameScreenshots = computed(() => props.game?.screenshots ?? []);
const hasCoverImage = computed(() => !!coverImage.value && !coverImageFailed.value);
const galleryImages = computed(() => [
  ...(hasCoverImage.value ? [coverImage.value] : []),
  ...gameScreenshots.value,
]);
const hasGallery = computed(() => galleryImages.value.length > 0);
const openLightboxAt = (index: number) => {
  lightboxIndex.value = index;
};
const closeLightbox = () => {
  lightboxIndex.value = null;
};
const markScreenshotFailed = (url: string) => {
  failedScreenshots.value = new Set([...failedScreenshots.value, url]);
};
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
// Collections: chips toggle local state; membership is written on Save.
const collectionOptions = ref<string[]>([]);
const collectionMembership = ref<string[]>([]);
const newCollectionName = ref("");
const collectionError = ref("");
const collectionBusy = ref(false);
// Bumped when the modal closes so a late createCollection answer is dropped.
let collectionRequestId = 0;

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
// Visibility only: saved prefix/runner/CE values are untouched (see isNativeLaunchMode).
const hideWineControlsForAuto = computed(() => {
  if (!platformCapabilities.value.wine_proton || launchMode.value !== "auto")
    return false;
  const targets = [exePath.value, ...launchTargets.value.map((t) => t.exe_path)];
  return targets.every(isClearlyNativeTarget);
});
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

// The footer Play launches the primary executable, so it needs a path to launch.
const isCurrentlyWishlisted = computed(() => !exePath.value.trim());

const hasUpdate = computed(() => hasAvailableUpdate({
  exe_path: exePath.value.trim(),
  version: version.value,
  latest_version: latestVersion.value,
  skipped_version: skippedVersion.value,
}));
const updateSkipped = computed(() => isUpdateSkipped({
  exe_path: exePath.value,
  latest_version: latestVersion.value,
  skipped_version: skippedVersion.value,
}));
const toggleSkippedVersion = async () => {
  const gameId = props.game?.id;
  if (gameId == null || savingSkip.value) return;
  const next = updateSkipped.value ? "" : latestVersion.value;
  savingSkip.value = true;
  try {
    const result = await api.updateGame(gameId, { skipped_version: next });
    if (!result.success) throw new Error(result.error || "Could not change skipped version");
    if (props.game?.id === gameId) skippedVersion.value = next;
    notify(next ? `Skipping version ${next}.` : "Stopped skipping this version.", "success");
  } catch (error) {
    notifyError(String(error));
  } finally {
    savingSkip.value = false;
  }
};

const sessions = ref<PlaySession[]>([]);
const loadingJournal = ref(false);
const journalError = ref("");
let journalRequestId = 0;
const loadJournal = async () => {
  const gameId = props.game?.id;
  if (gameId == null) return;
  const requestId = ++journalRequestId;
  loadingJournal.value = true;
  journalError.value = "";
  try {
    const result = await api.getGamePlaySessions(gameId);
    if (requestId !== journalRequestId || props.game?.id !== gameId) return;
    if (!result.success) throw new Error(result.error || "Could not load play sessions");
    sessions.value = result.sessions || [];
  } catch (error) {
    if (requestId === journalRequestId) journalError.value = String(error);
  } finally {
    if (requestId === journalRequestId) loadingJournal.value = false;
  }
};
const journalGroups = computed(() => {
  const groups = new Map<string, { version: string; sessions: PlaySession[]; duration: number; lastPlayed: string }>();
  const newest = [...sessions.value].sort((a, b) => b.started_at.localeCompare(a.started_at) || b.id - a.id);
  for (const session of newest) {
    const key = normalizeVersion(session.version);
    let group = groups.get(key);
    if (!group) {
      group = { version: session.version, sessions: [], duration: 0, lastPlayed: session.started_at };
      groups.set(key, group);
    }
    group.sessions.push(session);
    group.duration += session.duration_seconds;
  }
  return [...groups.entries()];
});
const beforeJournal = computed(() => Math.max(0,
  (props.game?.playtime_seconds || 0) - sessions.value.reduce((sum, session) => sum + session.duration_seconds, 0),
));
const newestSessionId = computed(() => [...sessions.value]
  .sort((a, b) => b.started_at.localeCompare(a.started_at) || b.id - a.id)[0]?.id);
const journalDuration = (seconds: number) => {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor(seconds % 3600 / 60);
  return hours ? `${hours}h ${minutes}m` : minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
};
const handleJournalTick = (event: Event) => {
  const detail = (event as CustomEvent<PlaytimeTickDetail>).detail;
  if (detail?.gameId === props.game?.id && props.modelValue && activeTab.value === "Journal") {
    void loadJournal();
  }
};

watch(() => [props.modelValue, props.game?.id, activeTab.value, props.isRunning] as const, ([open, , tab], previous) => {
  if (!open || props.game?.id !== previous?.[1]) {
    journalRequestId++;
    sessions.value = [];
    journalError.value = "";
  }
  if (open && tab === "Journal") void loadJournal();
});

const syncReadonlyMetadata = (game: GameRecord | null) => {
  latestVersion.value = game?.latest_version || "";
  skippedVersion.value = game?.skipped_version || "";
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
// Object keys keep the dirty-check string stable and let one field's baseline
// be patched without wiping the dirty state of the others.
const formSnapshot = (): string =>
  JSON.stringify({
    sandbox_enabled: sandboxEnabled.value,
    title: title.value,
    exe_path: exePath.value,
    f95_url: f95Url.value,
    version: version.value,
    finished_version: finishedVersion.value,
    command_line_args: commandLineArgs.value,
    cover_image: coverImage.value,
    engine: engine.value,
    run_japanese_locale: runJapaneseLocale.value,
    run_wayland: runWayland.value,
    auto_inject_ce: autoInjectCe.value,
    use_custom_prefix: useCustomPrefix.value,
    custom_prefix: customPrefix.value,
    proton_version: protonVersion.value,
    launch_mode: launchMode.value,
    collections: collectionMembership.value,
    progress: notes.value,
  });
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
        finishedVersion.value = g.finished_version || "";
        commandLineArgs.value = g.command_line_args || "";
        coverImage.value = g.cover_image_path || g.cover_image || "";
        playStatus.value = normalizePlayStatus(g.play_status, g.status);
        isFavorite.value = !!g.is_favorite;
        engine.value = normalizeEngine(g.engine);
        runJapaneseLocale.value = g.run_japanese_locale ? true : false;
        runWayland.value = g.run_wayland ? true : false;
        autoInjectCe.value = g.auto_inject_ce ? true : false;
        sandboxEnabled.value = !!g.sandbox_enabled;
        sandboxStatus.value = null;
        void api.getSandboxStatus().then(status => { sandboxStatus.value = status; }).catch(console.error);
        customPrefix.value = g.custom_prefix || "";
        protonVersion.value = g.proton_version || "";
        launchMode.value = normalizeLaunchMode(g.launch_mode);
        useCustomPrefix.value = !!g.custom_prefix;
        activeTab.value = "Overview";
        notes.value = g.progress || "";
        notesEditing.value = false;
        newTag.value = "";
        collectionMembership.value = [...(g.collections || [])];
        newCollectionName.value = "";
        collectionError.value = "";
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
        attachments.value = [];
        attachmentsRequestId++;
        loadingAttachments.value = false;
        attachingPdf.value = false;
        removingAttachment.value = null;
        savedSnapshot.value = formSnapshot();
        savedQuickFields.value = quickFields();
        lightboxIndex.value = null;
        failedScreenshots.value = new Set();
      }
    } else if (!isOpen) {
      lastSyncedId.value = null;
      lightboxIndex.value = null;
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
      toolEngine.value = "";
      void api
        .getCollections()
        .then((collections) => {
          collectionOptions.value = collections;
        })
        .catch(console.error);
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
      executableModifiedRequestId.value++;
      engineMenuOpen.value = false;
      newTag.value = "";
      activeTab.value = "Overview";
      notesEditing.value = false;
      executableModifiedAt.value = null;
      executableModifiedKnown.value = false;
      loadingExecutableModifiedAt.value = false;
      loadingLaunchTargets.value = false;
      launchTargets.value = [];
      attachments.value = [];
      attachmentsRequestId++;
      loadingAttachments.value = false;
      attachingPdf.value = false;
      removingAttachment.value = null;
      collectionRequestId++;
      collectionOptions.value = [];
      newCollectionName.value = "";
      collectionError.value = "";
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
  () => [props.game?.id, props.game?.latest_version, props.game?.rating, props.game?.skipped_version] as const,
  ([gameId]) => {
    if (props.game && typeof gameId === "number" && gameId === lastSyncedId.value) {
      syncReadonlyMetadata(props.game);
    }
  },
  { immediate: true },
);

watch(
  () => [activeTab.value, props.game?.id] as const,
  ([tab, gameId]) => {
    if (props.modelValue && tab === "Notes" && typeof gameId === "number") {
      void loadAttachments();
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
  onWebviewReady(() => { void loadTagColors().catch(console.error); });
  document.addEventListener("mousedown", handleOutsideClick);
  window.addEventListener("wlib-playtime-tick", handleJournalTick);
});

onBeforeUnmount(() => {
  document.removeEventListener("mousedown", handleOutsideClick);
  window.removeEventListener("wlib-playtime-tick", handleJournalTick);
  journalRequestId++;
});

const openSandboxFolder = async () => {
  if (!props.game) return;
  const result = await api.openGameSandboxFolder(props.game.id);
  if (!result.success) notifyError(result.error || "Could not open sandbox folder.");
};

const close = () => {
  engineMenuOpen.value = false;
  newTag.value = "";
  activeTab.value = "Overview";
  notesEditing.value = false;
  attachments.value = [];
  attachmentsRequestId++;
  loadingAttachments.value = false;
  attachingPdf.value = false;
  removingAttachment.value = null;
  emit("update:modelValue", false);
};

// User-initiated close (X, backdrop, Esc): don't drop unsaved form edits silently.
const requestClose = () => {
  if (saving.value || deleting.value) return;
  if (hasUnsavedChanges.value && !confirm("Discard unsaved changes to this game?")) {
    return;
  }
  close();
};

const modalRef = ref<HTMLElement | null>(null);
const tagColorPicker = ref<{ tag: string; anchor: HTMLElement } | null>(null);
function openTagColorPicker(event: MouseEvent | KeyboardEvent, tag: string) {
  event.preventDefault();
  const target = event.currentTarget as HTMLElement;
  tagColorPicker.value = { tag, anchor: target.querySelector<HTMLButtonElement>(".tag-name-button") || target };
}
// A game without tools (or no game) has no Tools tab: show Overview instead of an empty panel.
watch(toolEngine, (engine) => {
  if (!engine && activeTab.value === "Tools") activeTab.value = "Overview";
});

const selectTab = (tab: DetailTab) => {
  activeTab.value = tab;
  engineMenuOpen.value = false;
  modalRef.value?.querySelector(".modal-scroll-body")?.scrollTo(0, 0);
};

const onTabKeydown = (event: KeyboardEvent, index: number) => {
  let nextIndex: number;
  if (event.key === "ArrowRight") nextIndex = (index + 1) % detailTabs.value.length;
  else if (event.key === "ArrowLeft") nextIndex = (index + detailTabs.value.length - 1) % detailTabs.value.length;
  else if (event.key === "Home") nextIndex = 0;
  else if (event.key === "End") nextIndex = detailTabs.value.length - 1;
  else return;
  event.preventDefault();
  selectTab(detailTabs.value[nextIndex]!);
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

const saveQuickFields = async (
  fields: Record<string, unknown>,
): Promise<UpdateGameResponse | undefined> => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  try {
    const res = await api.updateGame(gameId, fields);
    if (res && res.success === false) {
      notifyError("Failed to save: " + (res.error || "Unknown error"));
      return res;
    }
    if (isCurrentGame(gameId)) {
      Object.assign(savedQuickFields.value, fields);
    }
    return res;
  } catch (e) {
    console.error("Failed to save game", e);
    notifyError("Error saving: " + String(e));
  }
};

// A backend-filled finished version (move to Completed) must show up at once
// without marking the form dirty or discarding other fields' dirty state.
const applyAutoFilledFinishedVersion = (value: string) => {
  finishedVersion.value = value;
  try {
    const baseline = JSON.parse(savedSnapshot.value || "{}") as Record<
      string,
      unknown
    >;
    baseline.finished_version = value;
    savedSnapshot.value = JSON.stringify(baseline);
  } catch {
    savedSnapshot.value = formSnapshot();
  }
};

const setPlayStatus = (value: PlayStatus) => {
  if (playStatus.value === value) return;
  playStatus.value = value;
  const gameId = props.game?.id;
  void saveQuickFields({ play_status: value }).then((res) => {
    if (
      typeof res?.finished_version === "string" &&
      typeof gameId === "number" &&
      isCurrentGame(gameId)
    ) {
      applyAutoFilledFinishedVersion(res.finished_version);
    }
  });
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
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  try {
    const p = await api.browseFile(exePath.value || "");
    if (p && isCurrentGame(gameId)) {
      exePath.value = p;
      void loadExecutableModifiedTime();
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
  // Settled separately so a failed tool check never drops the custom statuses.
  const [settings, support] = await Promise.allSettled([api.getSettings(), api.getGameToolSupport(gameId)]);
  if (!isCurrentGame(gameId)) return;
  if (settings.status === "fulfilled") customStatuses.value = settings.value.custom_play_statuses || [];
  else console.error("Failed to load game settings", settings.reason);
  if (support.status === "fulfilled") toolEngine.value = support.value.engine ?? "";
  else console.error("Failed to check game tools", support.reason);
};

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
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  try {
    const p = await api.browseFile(newTargetPath.value || exePath.value || "");
    if (p && isCurrentGame(gameId)) newTargetPath.value = p;
  } catch (e) {
    console.error("Failed to browse launch target", e);
    notifyError("Error browsing file: " + String(e));
  }
};

const browseEditTargetPath = async () => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  try {
    const p = await api.browseFile(editTargetPath.value || exePath.value || "");
    if (p && isCurrentGame(gameId)) editTargetPath.value = p;
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
  if (addingLaunchTarget.value) return;
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
  if (savingTargetId.value !== null) return;
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
  if (deletingTargetId.value !== null) return;
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
  if (reorderingLaunchTargets.value) return;
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
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  try {
    const p = await api.browseDirectory(customPrefix.value || "");
    if (p && isCurrentGame(gameId)) {
      customPrefix.value = p;
    }
  } catch (e) {
    console.error("Failed to browse directory", e);
    if (isCurrentGame(gameId)) {
      notifyError("Error browsing directory: " + String(e));
    }
  }
};

const installRtpsToPrefix = async () => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  notify("RTP installation has started in the background. It may take several minutes to complete.");
  try {
    const res = await api.installRpgmakerRtp(customPrefix.value, protonVersion.value);
    if (!isCurrentGame(gameId)) return;
    if (res && res.success === false) {
      notifyError("Failed to install RTPs: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to install RTPs", e);
    if (isCurrentGame(gameId)) {
      notifyError("Error installing RTPs: " + String(e));
    }
  }
};

// Membership is matched case-insensitively, like everywhere else.
const gameInCollection = (name: string): boolean => {
  const key = name.toLowerCase();
  return collectionMembership.value.some((entry) => entry.toLowerCase() === key);
};

const toggleModalCollection = (name: string) => {
  collectionError.value = "";
  const key = name.toLowerCase();
  collectionMembership.value = gameInCollection(name)
    ? collectionMembership.value.filter((entry) => entry.toLowerCase() !== key)
    : [...collectionMembership.value, name];
};

// Creates the collection now (empty) and selects its chip; the game joins it
// when the modal is saved, like every other membership change here.
const createModalCollection = async () => {
  if (collectionBusy.value) return;
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  const requestId = ++collectionRequestId;
  const name = newCollectionName.value.trim();
  if (!name) {
    collectionError.value = "Enter a collection name.";
    return;
  }
  collectionBusy.value = true;
  try {
    const result = await api.createCollection(name);
    // The collection list is global, so keep it fresh in any open modal.
    if (result.success && result.collections && props.modelValue) {
      collectionOptions.value = result.collections;
    }
    // Closing the modal mid-request is allowed, so the answer can arrive after it
    // was reopened (on this or another game): never touch that form's membership.
    if (requestId !== collectionRequestId || !isCurrentGame(gameId)) return;
    if (!result.success) {
      collectionError.value = result.error || "Could not create the collection.";
      return;
    }
    newCollectionName.value = "";
    collectionError.value = "";
    if (!gameInCollection(name)) collectionMembership.value = [...collectionMembership.value, name];
  } catch (e) {
    console.error("Failed to create collection", e);
    if (requestId === collectionRequestId && isCurrentGame(gameId)) {
      collectionError.value = "Error creating collection: " + String(e);
    }
  } finally {
    collectionBusy.value = false;
  }
};

const save = async () => {
  if (!props.game || typeof props.game.id !== "number" || saving.value) return;
  if (!title.value.trim()) {
    notifyError("Game title is required.");
    return;
  }
  const gameId = props.game.id;
  // Snapshot before awaiting: the sync watcher replaces these if props.game changes mid-save.
  const previousCollections = [...(props.game.collections || [])];
  const desiredCollections = [...collectionMembership.value];
  saving.value = true;
  try {
    const res = await api.updateGame(gameId, {
      title: title.value.trim(),
      exe_path: exePath.value.trim(),
      f95_url: f95Url.value.trim(),
      version: version.value,
      finished_version: finishedVersion.value,
      command_line_args: commandLineArgs.value,
      cover_image_path: coverImage.value,
      ...quickFields(),
      progress: notes.value,
      engine: normalizeEngine(engine.value),
      sandbox_enabled: sandboxEnabled.value,
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
      const membershipChanged =
        JSON.stringify(previousCollections) !==
        JSON.stringify(desiredCollections);
      if (membershipChanged) {
        const collectionsResult = await api.setGameCollections(
          gameId,
          desiredCollections,
        );
        if (collectionsResult && collectionsResult.success === false) {
          notifyError(
            "Failed to save collections: " +
              (collectionsResult.error || "Unknown error"),
          );
        }
      }
      if (isCurrentGame(gameId)) {
        savedSnapshot.value = formSnapshot();
        savedQuickFields.value = quickFields();
      }
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
    deleting.value ||
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
  if (!path || !props.modelValue) {
    loadingExecutableModifiedAt.value = false;
    executableModifiedKnown.value = !path;
    return;
  }

  loadingExecutableModifiedAt.value = true;
  try {
    const result = await api.getExecutableModifiedTime(path);
    if (executableModifiedRequestId.value !== requestId || !props.modelValue) {
      return;
    }

    executableModifiedKnown.value = true;
    executableModifiedAt.value = result.success ? result.modified_at : null;
  } catch (e) {
    if (executableModifiedRequestId.value !== requestId || !props.modelValue) {
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
  const url = f95Url.value.trim() || props.game?.links?.[0]?.url || "";
  if (url) {
    try {
      const res = await api.openInBrowser(url);
      if (res && res.success === false) {
        notifyError("Failed to open browser: " + (res.error || "Unknown error"));
      }
    } catch (e) {
      console.error("Failed to open browser", e);
      notifyError("Error opening browser: " + String(e));
    }
  }
};

const notesHtml = computed(() => {
  const rendered = marked.parse(notes.value || "", { async: false });
  const html = typeof rendered === "string" ? rendered : "";
  return DOMPurify.sanitize(html);
});

const onNotesLinkClick = async (event: MouseEvent) => {
  const target = event.target as HTMLElement | null;
  const anchor = target?.closest?.("a") as HTMLAnchorElement | null;
  if (!anchor) return;
  event.preventDefault();
  const safeUrl = getSafeNotesLinkUrl(anchor.getAttribute("href"));
  if (!safeUrl) return;
  try {
    const res = await api.openInBrowser(safeUrl);
    if (res && res.success === false) {
      notifyError("Failed to open browser: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to open notes link", e);
    notifyError("Error opening browser: " + String(e));
  }
};

const formatAttachmentSize = (bytes: number): string => {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = units[0]!;
  for (const next of units) {
    unit = next;
    if (value < 1024 || next === "GB") break;
    value /= 1024;
  }
  return `${value >= 100 ? Math.round(value) : value.toFixed(1)} ${unit}`;
};

const loadAttachments = async () => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  const requestId = ++attachmentsRequestId;
  loadingAttachments.value = true;
  try {
    const res = await api.listGameAttachments(gameId);
    if (requestId !== attachmentsRequestId || !isCurrentGame(gameId)) return;
    if (res && res.success === false) {
      notifyError("Failed to load attachments: " + (res.error || "Unknown error"));
      attachments.value = [];
    } else {
      attachments.value = res?.attachments || [];
    }
  } catch (e) {
    console.error("Failed to load attachments", e);
    if (requestId === attachmentsRequestId && isCurrentGame(gameId)) {
      attachments.value = [];
    }
  } finally {
    if (requestId === attachmentsRequestId && isCurrentGame(gameId)) {
      loadingAttachments.value = false;
    }
  }
};

const attachPdf = async () => {
  if (attachingPdf.value || !props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  attachingPdf.value = true;
  try {
    const picked = await api.browsePdfFile("");
    if (!picked) return;
    if (!isCurrentGame(gameId)) return;
    const res = await api.addGameAttachment(gameId, picked);
    if (!isCurrentGame(gameId)) return;
    if (res && res.success === false) {
      notifyError("Failed to attach PDF: " + (res.error || "Unknown error"));
      if ((res as { error_code?: string }).error_code === "attachment_missing") {
        await loadAttachments();
      }
      return;
    }
    attachments.value = res?.attachments || res?.attachment
      ? res.attachments || (res.attachment ? [res.attachment] : [])
      : attachments.value;
    await loadAttachments();
  } catch (e) {
    console.error("Failed to attach PDF", e);
    if (isCurrentGame(gameId)) {
      notifyError("Error attaching PDF: " + String(e));
    }
  } finally {
    attachingPdf.value = false;
  }
};

const openAttachment = async (name: string) => {
  if (!props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  try {
    const res = await api.openGameAttachment(gameId, name);
    if (!isCurrentGame(gameId)) return;
    if (res && res.success === false) {
      notifyError("Failed to open attachment: " + (res.error || "Unknown error"));
      if ((res as { error_code?: string }).error_code === "attachment_missing") {
        await loadAttachments();
      }
    }
  } catch (e) {
    console.error("Failed to open attachment", e);
    if (isCurrentGame(gameId)) {
      notifyError("Error opening attachment: " + String(e));
    }
  }
};

const removeAttachment = async (name: string) => {
  if (removingAttachment.value !== null || !props.game || typeof props.game.id !== "number") return;
  const gameId = props.game.id;
  if (!confirm(`Remove ${name}? The attached copy will be deleted.`)) return;
  removingAttachment.value = name;
  try {
    const res = await api.removeGameAttachment(gameId, name);
    if (!isCurrentGame(gameId)) return;
    if (res && res.success === false) {
      notifyError("Failed to remove attachment: " + (res.error || "Unknown error"));
      if ((res as { error_code?: string }).error_code === "attachment_missing") {
        await loadAttachments();
      }
      return;
    }
    attachments.value = res?.attachments || [];
  } catch (e) {
    console.error("Failed to remove attachment", e);
    if (isCurrentGame(gameId)) {
      notifyError("Error removing attachment: " + String(e));
    }
  } finally {
    removingAttachment.value = null;
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
      class="modal-content relative flex h-[min(calc(var(--app-vh)*0.9),52rem)] w-full max-w-[56rem] flex-col overflow-hidden rounded-xl"
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
          <button
            v-if="hasCoverImage && hasGallery"
            type="button"
            class="game-cover game-cover-button"
            aria-label="View cover and screenshots"
            @click="openLightboxAt(0)"
          >
            <img :src="coverImage" :alt="`${title || game.title} cover`" @error="coverImageFailed = true" />
          </button>
          <div v-else class="game-cover">
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
              <span v-else-if="updateSkipped" class="text-sm" style="color: var(--text-secondary)">Version {{ skippedVersion }} skipped</span>
            </div>
          </div>
        </div>
        <div class="modal-toolbar">
          <div class="modal-toolbar-group">
            <button v-if="hasUpdate || updateSkipped" @click="toggleSkippedVersion"
              :disabled="savingSkip" class="modal-toolbar-action ui-action-btn min-h-11"
              style="color: var(--text-primary)">
              {{ updateSkipped ? `Stop skipping ${skippedVersion}` : `Skip version ${latestVersion}` }}
            </button>
            <button
              v-if="f95Url.trim() || game.links?.length"
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
            <button
              v-if="hasUpdate && props.game"
              @click="openInstallArchive({ gameId: props.game.id })"
              class="modal-toolbar-action ui-action-btn"
              style="color: var(--text-primary)"
              title="Install the new version from a downloaded archive"
            >
              <IconDownload class="ui-action-icon" />
              Install update
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

          <div v-if="game.links?.length" class="space-y-2">
            <h3 class="modal-label">Store links</h3>
            <div class="flex flex-wrap gap-2">
              <button v-for="link in game.links" :key="`${link.source}:${link.id}`" type="button" class="modal-toolbar-action ui-action-btn" @click="api.openInBrowser(link.url)">
                <IconExternalLinkFilled class="ui-action-icon" />{{ storeLabel(link.source) }} · {{ link.id }}
              </button>
            </div>
          </div>
          <div v-if="game.f95_url">
            <h3 class="modal-label mb-3">Screenshots</h3>
            <div v-if="gameScreenshots.length" class="screenshot-strip" role="group" aria-label="Game screenshots">
              <button
                v-for="(url, index) in gameScreenshots"
                v-show="!failedScreenshots.has(url)"
                :key="url"
                type="button"
                class="screenshot-thumb-button"
                :aria-label="`View screenshot ${index + 1}`"
                @click="openLightboxAt(index + (hasCoverImage ? 1 : 0))"
              >
                <img
                  :src="url"
                  :alt="`Screenshot ${index + 1}`"
                  loading="lazy"
                  class="screenshot-thumb"
                  @error="markScreenshotFailed(url)"
                />
              </button>
            </div>
            <div v-else class="screenshot-empty">
              <p class="text-xs" style="color: var(--text-muted)">Screenshots load from the F95 thread.</p>
              <button
                type="button"
                class="modal-btn"
                :disabled="updateCheckState.running"
                @click="requestUpdateCheck"
              >
                {{ updateCheckState.running ? "Checking..." : "Check thread now" }}
              </button>
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
            <h3 class="modal-label mb-3">Collections</h3>
            <div>
              <p
                v-if="!collectionOptions.length"
                class="text-xs mb-2"
                style="color: var(--text-muted)"
              >
                No collections yet.
              </p>
              <div v-else class="flex flex-wrap gap-1.5 mb-2">
                <button
                  v-for="name in collectionOptions"
                  :key="name"
                  type="button"
                  :aria-pressed="gameInCollection(name)"
                  class="collection-chip"
                  :class="{ 'collection-chip-active': gameInCollection(name) }"
                  @click="toggleModalCollection(name)"
                >
                  {{ name }}
                </button>
              </div>
              <div class="flex gap-2">
                <input
                  v-model="newCollectionName"
                  aria-label="New collection name"
                  type="text"
                  placeholder="New collection"
                  class="modal-input flex-1 !py-1.5 !text-xs"
                  @keydown.enter.prevent="createModalCollection"
                />
                <button
                  class="modal-btn"
                  :disabled="collectionBusy"
                  @click="createModalCollection"
                >
                  Add
                </button>
              </div>
              <p
                v-if="collectionError"
                role="alert"
                class="text-xs mt-2"
                style="color: var(--danger-text)"
              >
                {{ collectionError }}
              </p>
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
                  class="tag-pill text-xs px-2 py-0.5 rounded-full flex items-center gap-1"
                  @contextmenu="openTagColorPicker($event, tag)"
                  :data-tag-color="colorForTag(tag)"
                  :style="colorForTag(tag) ? undefined : { background: 'var(--bg-raised)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }"
                >
                  <button type="button" :aria-label="`Set color for ${tag}`" @click="openTagColorPicker($event, tag)" @keydown.shift.f10.prevent="openTagColorPicker($event, tag)" class="tag-name-button">{{ tag }}</button>
                  <button
                    @click="removeTag(tag)"
                    :aria-label="`Remove tag ${tag}`"
                    class="hover:text-red-400 transition-colors ml-0.5"
                    style="color: inherit"
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
          <GameCompanionTools v-if="modelValue && game && platformCapabilities.companion_tools" :key="game.id" :game-id="game.id" @settings="emit('update:modelValue', false); sandboxRouter.push('/settings#custom-tools')" />
          <div class="space-y-2">
            <label class="flex items-center justify-between text-sm">
              <span>Run in sandbox</span>
              <input v-model="sandboxEnabled" type="checkbox" role="switch" class="sandbox-switch" :disabled="sandboxSwitchState(exePath, sandboxStatus).disabled" />
            </label>
            <p class="text-xs" style="color:var(--text-secondary)">{{ sandboxSwitchState(exePath, sandboxStatus).note }}</p>
            <div class="flex flex-wrap gap-2">
              <button v-if="sandboxSwitchState(exePath, sandboxStatus).needsSettings" class="modal-btn" @click="emit('update:modelValue', false); sandboxRouter.push('/settings#sandbox')">Open Sandbox settings</button>
              <button class="modal-btn" @click="openSandboxFolder">Open sandbox folder</button>
            </div>
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div class="col-span-2">
              <label for="detail-exe" class="modal-label">Executable Path</label>
              <div class="flex gap-2">
                <input
                  v-model="exePath" id="detail-exe"
                  type="text"
                  class="modal-input flex-1 font-mono"
                  @change="loadExecutableModifiedTime"
                />
                <button @click="browseExe" class="modal-btn ui-action-btn">
                  <IconFolderOpenFilled class="ui-action-icon" />
                  Browse
                </button>
              </div>
              <PathStatus :path="exePath" kind="file" />
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
                  <button v-if="!isRunning && game && exePath.trim()" @click="launchGame" class="launch-target-action ui-play-btn ui-action-btn">
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
                      <PathStatus :path="editTargetPath" kind="file" />
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
                      <button v-if="!isRunning && game && target.exe_path.trim()" @click="launchTarget(target)" class="launch-target-action ui-play-btn ui-action-btn">
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
                      <PathStatus :path="newTargetPath" kind="file" />
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
                placeholder="e.g. gamemoderun %command% --fullscreen"
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
                    !isNativeLaunchMode &&
                    !hideWineControlsForAuto
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
                      class="text-[0.625rem] uppercase px-1.5 py-0.5 rounded font-bold"
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
            <div
              v-if="showWineProtonControls && !hideWineControlsForAuto"
              class="col-span-2 mt-2"
            >
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
                  <PathStatus :path="customPrefix" kind="dir" />
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

          <GameSavesSection
            v-if="game && typeof game.id === 'number'"
            :game-id="game.id"
            :active="modelValue && activeTab === 'Launch'"
          />
        </section>

        <section v-show="activeTab === 'Journal'" id="game-detail-panel-Journal" role="tabpanel" aria-labelledby="game-detail-tab-Journal" tabindex="0" class="journal-panel space-y-6">
          <p v-if="loadingJournal && !sessions.length" role="status">Loading play sessions...</p>
          <div v-else-if="journalError" role="alert">
            <p>Could not load play sessions: {{ journalError }}</p>
            <button class="modal-btn mt-3 min-h-11" @click="loadJournal">Retry</button>
          </div>
          <template v-else>
            <p v-if="!sessions.length" style="color: var(--text-secondary)">No sessions have been recorded yet. Launch this game from wLib to start its journal.</p>
            <div v-if="beforeJournal > 0" class="journal-before">
              <span>Before journal</span><span>{{ journalDuration(beforeJournal) }}</span>
            </div>
            <div v-for="[key, group] in journalGroups" :key="key" class="journal-version">
              <h3 class="font-semibold break-words">{{ key ? group.version : 'Unknown version' }}</h3>
              <p class="text-sm mt-1 mb-3" style="color: var(--text-secondary)">
                {{ journalDuration(group.duration) }} · {{ group.sessions.length }} {{ group.sessions.length === 1 ? 'session' : 'sessions' }} · Last played {{ formatLastPlayed(group.lastPlayed) }}
              </p>
              <ul class="journal-sessions">
                <li v-for="session in group.sessions" :key="session.id" class="journal-session">
                  <time :datetime="session.started_at">{{ formatTimestamp(session.started_at) }}</time>
                  <span v-if="isRunning && !session.ended_at && session.id === newestSessionId" class="text-sm">In progress</span>
                  <span class="journal-duration">{{ journalDuration(session.duration_seconds) }}</span>
                </li>
              </ul>
            </div>
          </template>
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

            <div>
              <label for="detail-finished-version" class="modal-label">Finished version</label>
              <input
                v-model="finishedVersion" id="detail-finished-version"
                type="text"
                placeholder="Not finished"
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

        <section v-show="activeTab === 'Notes'" id="game-detail-panel-Notes" role="tabpanel" aria-labelledby="game-detail-tab-Notes" tabindex="0" class="space-y-6">
          <div>
            <div class="flex items-center justify-between mb-3">
              <h3 class="modal-label !mb-0">Notes</h3>
              <button
                v-if="!notesEditing"
                type="button"
                class="modal-btn"
                @click="notesEditing = true"
              >
                Edit
              </button>
              <button
                v-else
                type="button"
                class="modal-btn"
                @click="notesEditing = false"
              >
                Preview
              </button>
            </div>
            <div v-if="!notesEditing">
              <div
                v-if="notes.trim()"
                class="notes-markdown"
                v-html="notesHtml"
                @click="onNotesLinkClick"
              ></div>
              <div
                v-else
                class="rounded-lg p-4 text-sm"
                style="background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-muted)"
              >
                <p class="mb-3">No notes yet. Track routes, choices, or progress for this game.</p>
                <button type="button" class="modal-btn" @click="notesEditing = true">
                  Start writing
                </button>
              </div>
            </div>
            <div v-else>
              <textarea
                v-model="notes"
                aria-label="Game notes"
                rows="10"
                placeholder="# Notes&#10;&#10;Track routes, choices, and progress here. Markdown is supported."
                class="modal-input w-full font-mono notes-editor"
              ></textarea>
              <p class="text-xs mt-2" style="color: var(--text-muted)">
                Markdown is supported. Changes save with Save Changes.
              </p>
            </div>
          </div>

          <div>
            <div class="flex items-center justify-between mb-3">
              <h3 class="modal-label !mb-0">Attachments</h3>
              <button
                type="button"
                class="modal-btn ui-action-btn disabled:opacity-50"
                :disabled="attachingPdf"
                @click="attachPdf"
              >
                {{ attachingPdf ? "Attaching..." : "Attach PDF" }}
              </button>
            </div>
            <div
              v-if="loadingAttachments"
              class="text-xs"
              style="color: var(--text-muted)"
            >
              Loading attachments...
            </div>
            <p
              v-else-if="!attachments.length"
              class="text-xs"
              style="color: var(--text-muted)"
            >
              No PDFs attached.
            </p>
            <ul v-else class="space-y-2">
              <li
                v-for="item in attachments"
                :key="item.name"
                class="flex items-center gap-2 rounded-lg p-2"
                style="background: var(--bg-raised); border: 1px solid var(--border)"
              >
                <button
                  type="button"
                  class="flex-1 min-w-0 text-left"
                  :title="`Open ${item.name}`"
                  @click="openAttachment(item.name)"
                >
                  <span class="block truncate text-sm" style="color: var(--text-primary)">{{ item.name }}</span>
                  <span class="block text-xs" style="color: var(--text-muted)">{{ formatAttachmentSize(item.size) }}</span>
                </button>
                <button
                  type="button"
                  class="modal-btn !py-1 !text-xs disabled:opacity-50"
                  :disabled="removingAttachment === item.name"
                  @click="removeAttachment(item.name)"
                >
                  {{ removingAttachment === item.name ? "Removing..." : "Remove" }}
                </button>
              </li>
            </ul>
          </div>
        </section>

        <section
          v-if="toolEngine && game && typeof game.id === 'number'"
          v-show="activeTab === 'Tools'"
          id="game-detail-panel-Tools"
          role="tabpanel"
          aria-labelledby="game-detail-tab-Tools"
          tabindex="0"
          class="space-y-6"
        >
          <GameToolsTab :game-id="game.id" :engine="toolEngine" :active="modelValue && activeTab === 'Tools'" />
        </section>
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
          </div>
          <div class="ml-auto flex flex-wrap items-center justify-end gap-3">
            <template v-if="hasUnsavedChanges">
              <span class="text-xs" style="color: var(--text-muted)">Unsaved changes</span>
              <button
                @click="save"
                :disabled="saving || !title.trim()"
                class="save-btn ui-action-btn disabled:opacity-50"
              >
                <IconDeviceFloppyFilled class="ui-action-icon" />
                {{ saving ? "Saving..." : "Save Changes" }}
              </button>
            </template>
            <button
              @click="game && isCurrentlyWishlisted ? selectTab('Launch') : isRunning && game ? emit('stop', game.id) : launchGame()"
              :title="game && isCurrentlyWishlisted ? 'Choose an executable in the Launch tab' : isRunning ? 'Stop the game and all processes in its Wine prefix.' : 'Play game'"
              class="launch-btn footer-launch-btn ui-play-btn ui-action-btn"
            >
              <IconPlayerStopFilled v-if="isRunning && game && !isCurrentlyWishlisted" class="ui-action-icon" />
              <IconPlayerPlayFilled v-else-if="game && !isCurrentlyWishlisted" class="ui-action-icon" />
              {{ game && isCurrentlyWishlisted ? 'Set up' : isRunning ? 'Stop' : 'Play' }}
            </button>
          </div>
        </div>
      </div>
    </div>
    <ScreenshotLightbox
      :images="galleryImages"
      :start-index="lightboxIndex"
      @close="closeLightbox"
    />
    <TagColorPicker v-if="tagColorPicker" :key="tagColorPicker.tag" :tag="tagColorPicker.tag" :anchor="tagColorPicker.anchor" @close="tagColorPicker = null" />
  </div>
</template>

<style scoped>
.journal-panel {
  color: var(--text-primary);
}
.journal-before,
.journal-session {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}
.journal-before {
  justify-content: space-between;
}
.journal-duration {
  margin-left: auto;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
.journal-sessions {
  list-style: none;
  padding: 0;
}
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

.footer-launch-btn {
  justify-content: center;
  min-width: 6.5rem;
}

.detail-header .modal-toolbar {
  margin-top: 1rem;
}

.detail-tabs {
  display: flex;
  gap: 1.5rem;
  overflow-x: auto;
  scrollbar-width: thin;
}

.detail-tab {
  flex-shrink: 0;
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

.game-cover-button {
  cursor: pointer;
  padding: 0;
}

.game-cover-button:hover {
  border-color: var(--brand);
}

.game-cover-button:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.screenshot-strip {
  display: flex;
  gap: 0.5rem;
  height: 6rem;
  overflow-x: auto;
  overflow-y: hidden;
  padding-bottom: 0.25rem;
}

.screenshot-thumb-button {
  flex: 0 0 auto;
  height: 6rem;
  border-radius: 0.5rem;
  overflow: hidden;
  border: 1px solid var(--border);
  background: var(--bg-inset);
  padding: 0;
  cursor: pointer;
}

.screenshot-thumb-button:hover {
  border-color: var(--brand);
}

.screenshot-thumb-button:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

.screenshot-thumb {
  height: 6rem;
  width: auto;
  display: block;
  object-fit: cover;
}

.screenshot-empty {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  height: 6rem;
  padding: 0.75rem;
  border-radius: 0.5rem;
  border: 1px dashed var(--border);
  background: var(--bg-inset);
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

.launch-target-action.ui-play-btn,
.launch-target-action.ui-play-btn:hover:not(:disabled) {
  background: var(--play);
  border-color: var(--play);
  color: var(--on-play);
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
  border-radius: 0.55rem;
}
.collection-chip {
  padding: 0.3rem 0.7rem;
  border: 1px solid var(--border);
  border-radius: 9999px;
  background: var(--bg-raised);
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
  transition: background-color 0.15s ease, color 0.15s ease,
    border-color 0.15s ease;
}
.collection-chip:hover {
  background: var(--bg-overlay);
  color: var(--text-primary);
}
.collection-chip:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.collection-chip-active {
  background: var(--brand);
  border-color: var(--brand);
  color: var(--on-brand);
}
.collection-chip-active:hover {
  background: var(--brand);
  color: var(--on-brand);
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
  border: 1px solid var(--play);
}

.save-btn {
  background: var(--brand);
  border: 1px solid var(--brand);
  box-shadow: var(--shadow-brand);
  color: var(--on-brand);
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.notes-editor {
  min-height: 12rem;
  resize: vertical;
}

.notes-markdown {
  color: var(--text-primary);
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.625rem;
  padding: 1rem 1.25rem;
  max-height: 24rem;
  overflow-y: auto;
  line-height: 1.6;
  font-size: 0.875rem;
  overflow-wrap: anywhere;
}

.notes-markdown :deep(h1),
.notes-markdown :deep(h2),
.notes-markdown :deep(h3),
.notes-markdown :deep(h4) {
  color: var(--text-primary);
  font-weight: 700;
  margin: 1rem 0 0.5rem;
  line-height: 1.3;
}

.notes-markdown :deep(h1:first-child),
.notes-markdown :deep(h2:first-child),
.notes-markdown :deep(h3:first-child),
.notes-markdown :deep(p:first-child),
.notes-markdown :deep(ul:first-child),
.notes-markdown :deep(ol:first-child) {
  margin-top: 0;
}

.notes-markdown :deep(h1) { font-size: 1.25rem; }
.notes-markdown :deep(h2) {
  font-size: 1.125rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.25rem;
}
.notes-markdown :deep(h3) { font-size: 1rem; }

.notes-markdown :deep(p) {
  margin: 0.5rem 0;
  color: var(--text-primary);
}

.notes-markdown :deep(ul) {
  list-style-type: disc;
  margin: 0.5rem 0 0.5rem 1.5rem;
}

.notes-markdown :deep(ol) {
  list-style-type: decimal;
  margin: 0.5rem 0 0.5rem 1.5rem;
}

.notes-markdown :deep(li) { margin-bottom: 0.25rem; }
.notes-markdown :deep(li::marker) { color: var(--text-muted); }

.notes-markdown :deep(a) {
  color: var(--brand);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.notes-markdown :deep(a:hover) { filter: brightness(1.2); }

.notes-markdown :deep(code) {
  background: var(--bg-overlay);
  border: 1px solid var(--border);
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  font-family: monospace;
  font-size: 0.85em;
  color: var(--text-primary);
}

.notes-markdown :deep(pre) {
  background: var(--bg-inset);
  border: 1px solid var(--border);
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
  margin: 0.75rem 0;
}

.notes-markdown :deep(pre code) {
  background: transparent;
  border: none;
  padding: 0;
}

.notes-markdown :deep(blockquote) {
  border-left: 2px solid var(--brand);
  padding-left: 0.75rem;
  margin: 0.75rem 0;
  color: var(--text-secondary);
}

.notes-markdown :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75rem 0;
  font-size: 0.8125rem;
}

.notes-markdown :deep(th),
.notes-markdown :deep(td) {
  border: 1px solid var(--border);
  padding: 0.4rem 0.6rem;
  text-align: left;
}

.notes-markdown :deep(th) {
  background: var(--bg-overlay);
  color: var(--text-primary);
  font-weight: 600;
}

.notes-markdown :deep(td) { color: var(--text-secondary); }

.notes-markdown :deep(hr) {
  border: none;
  border-top: 1px solid var(--border);
  margin: 1rem 0;
}

.notes-markdown :deep(img) { max-width: 100%; }
</style>
