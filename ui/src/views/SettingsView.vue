<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useFieldAutosave } from "../composables/useFieldAutosave";
import FieldSaveFeedback from "../components/FieldSaveFeedback.vue";
import type { SettingsPayload } from "../services/api";
import { mergeSettingsFields } from "../utils/settingsMerge";
import { confirmAction } from "../utils/confirm";
import { gamingMode, setTvPreference, tvModeActive, tvPreference } from "../utils/tvMode";
import { formatLibraryFreeSpace } from "../utils/installArchive";
import type { LibraryFolder } from "../services/api";
import { computed, ref, onMounted, onUnmounted, watch, nextTick, provide } from "vue";
import { onBeforeRouteLeave, useRoute } from "vue-router";
import { settingsVisibleRows } from "../utils/settingsVisibility";
import { matchSettings, type SettingsSearchSection } from "../utils/settingsSearch";
import { controllerFocusText, useControllerActions } from "../utils/controllerActions";
import { focusCandidates, pickNext } from "../utils/spatialFocus";
import {
  IconAdjustments,
  IconAutomation,
  IconBook2,
  IconDownloadFilled,
  IconFolderOpen,
  IconKeyboard,
  IconLoader2,
  IconPalette,
  IconSettings,
  IconTool,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import { notify, notifyError } from "../utils/toast";
import { motionEnabled, saveMotionPreference } from "../utils/motionPreference";
import { coverAnimation, saveCoverAnimation } from "../utils/coverAnimation";
import {
  accentPreference,
  previewAccentPreference,
  setAccentPreference,
  type AccentPreference,
  type AccentPreset,
} from "../utils/accentPreference";
import {
  isDarkTheme,
  setThemePreference,
  themePreference,
  type ThemePreference,
} from "../utils/themePreference";
import { INTERFACE_SCALE_OPTIONS, interfaceScale, setInterfaceScale } from "../utils/interfaceScalePreference";
import {
  TEXT_SIZE_OPTIONS,
  setTextSizePreference,
  textSizePreference,
} from "../utils/textSizePreference";
import type {
  CollectionsResponse,
  InstallProgressStatus,
  ProtonDownloadStatus,
  PlatformCapabilities,
  RpgmakerLinuxRunnerStatus,
  SaveBackupKeep,
  SettingsResponse,
  SystemDepsCommandResponse,
  RunnerInfo,
} from "../services/api";
import { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../services/api";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { validateCustomPlayStatuses } from "../utils/playStatus";
import PathStatus from "../components/PathStatus.vue";
import CompanionToolsSettings from "../components/CompanionToolsSettings.vue";
import SandboxSettings from "../components/SandboxSettings.vue";
import UmuSettings from "../components/UmuSettings.vue";
import CatalogSettings from "../components/CatalogSettings.vue";
import SteamSettings from "../components/SteamSettings.vue";
import FileHostAccounts from "../components/FileHostAccounts.vue";

const coverAnimationOptions = [
  { value: "always", label: "Always" },
  { value: "hover", label: "On hover" },
  { value: "never", label: "Never" },
] as const;

const RPGMAKER_LINUX_REPO_URL =
  "https://github.com/bakustarver/rpgmakermlinux-cicpoffs";
const URM_PAGE_URL = "https://0x52.dev/mods/Universal-Ren-Py-Mod-1000";

const protonPath = ref("");
const availableRunners = ref<RunnerInfo[]>([]);
const customStatuses = ref<string[]>([]);
const newStatus = ref("");
const statusError = ref("");
// Collections apply immediately through their own API.
const collectionList = ref<string[]>([]);
const newCollectionName = ref("");
const collectionError = ref("");
const renamingCollection = ref<string | null>(null);
const renameCollectionDraft = ref("");
const urmPath = ref("");
const prefixPath = ref("");
const rpgmakerLinuxRunnerPath = ref("");
const libraryFolder = ref("");
const libraryFolders = ref<string[]>([]);
const libraryFolderStatus = ref<LibraryFolder[]>([]);
const refreshFolderStatus = async () => {
  libraryFolderStatus.value = await api.listLibraryFolders();
};
const folderFreeSpace = (path: string) => {
  const status = libraryFolderStatus.value.find((entry) => entry.path === path);
  return status ? formatLibraryFreeSpace(status.available ? status.free_bytes : null) : "Save to check free space";
};
const removeLibraryFolder = (path: string) => {
  if (path === libraryFolder.value && libraryFolders.value.length > 1) return;
  libraryFolders.value = libraryFolders.value.filter((entry) => entry !== path);
  if (!libraryFolders.value.length) libraryFolder.value = "";
  void foldersSave.commit();
};
const libraryFolderPattern = ref("{creator}/{title}/{version}");
const downloadsFolder = ref("");
const downloadDetectionEnabled = ref(true);
const enableLogging = ref(false);
const gamemodeEnabled = ref(false);
const mangohudEnabled = ref(false);
const performanceTools = ref({ gamemode: false, mangohud: false });
const savePerformanceTool = async (tool: "gamemode" | "mangohud", event: Event) => {
  const value = (event.target as HTMLInputElement).checked;
  const state = tool === "gamemode" ? gamemodeEnabled : mangohudEnabled;
  const previous = state.value;
  state.value = value;
  try {
    const result = await api.saveSettings({ [`${tool}_enabled`]: value });
    if (!result.success) throw new Error(result.error || "Unknown error");
  } catch (error) {
    state.value = previous;
    notifyError(`Failed to save ${tool}: ${String(error)}`);
  }
};
const openPrefixTool = async (tool: "winecfg" | "winetricks") => {
  try {
    const result = await api.openWinePrefixTool(tool, prefixPath.value, protonPath.value);
    if (!result.success) throw new Error(result.error || "Unknown error");
    notify(`Opening ${tool === "winecfg" ? "Winecfg" : "Winetricks"}…`, "success");
  } catch (error) { notifyError(String(error)); }
};
// Save backups to keep per game: a preset, or "custom" with its own number.
const KEEP_PRESETS = [1, 3, 5, 10, 20];
const keepChoice = ref("all");
const keepCustom = ref(10);
const savingKeep = ref(false);
const savingLogging = ref(false);
const protonDownload = ref<ProtonDownloadStatus | null>(null);
let protonDownloadTimer: ReturnType<typeof setInterval> | null = null;
const installingDeps = ref(false);
const installError = ref("");
const downloadingProton = ref(false);
const protonError = ref("");
const installingRtps = ref(false);
const rtpError = ref("");
const ceInstalled = ref(false);
const cePath = ref("");
const installingCe = ref(false);
const ceError = ref("");
const dllsInstalled = ref(false);
const rtpsInstalled = ref(false);
const depsProgress = ref<InstallProgressStatus>({
  running: false,
  done: 0,
  total: 0,
  current: "",
  error: "",
});
const rtpProgress = ref<InstallProgressStatus>({
  running: false,
  done: 0,
  total: 0,
  current: "",
  error: "",
});
const systemDeps = ref<SystemDepsCommandResponse | null>(null);
const copiedCommand = ref(false);
const settingsLoaded = ref(false);
const settingsLoading = ref(false);
const settingsLoadError = ref("");
// The form as it was before the first settings read. Keeping it across failed
// attempts lets Retry preserve edits typed after that first attempt.
const settingsLoadBaseline = ref<ReturnType<typeof formValues> | null>(null);
const platformCapabilities = ref<PlatformCapabilities>({
  ...CONSERVATIVE_PLATFORM_CAPABILITIES,
});
const supportsLinuxRuntimes = computed(
  () => platformCapabilities.value.runtime_installers,
);
let pollTimer: ReturnType<typeof setTimeout> | null = null;
let statusRefreshTimer: ReturnType<typeof setTimeout> | null = null;
// Set when this page closes, so a late status read cannot arm a poll that
// onUnmounted has already run past.
let pageDisposed = false;
// The proton path this page attached with, so a download that finishes while
// it is open never overwrites an edit made in the meantime.
let trackedProtonPath = "";
// True while this page's own downloadProton() call is pending; its finally
// block then owns resetting the button.
let protonRequestPending = false;

const defaultRpgmakerLinuxRunnerStatus: RpgmakerLinuxRunnerStatus = {
  available: false,
  path: "",
  source: "",
  configured_path: "",
  error: "RPGMaker Linux runner is not installed or configured.",
};
const rpgmakerLinuxRunnerStatus = ref<RpgmakerLinuxRunnerStatus>({
  ...defaultRpgmakerLinuxRunnerStatus,
});
const rpgmakerLinuxRunnerAvailable = computed(
  () => rpgmakerLinuxRunnerStatus.value.available,
);
const rpgmakerLinuxRunnerStatusText = computed(() => {
  if (rpgmakerLinuxRunnerStatus.value.available) {
    const detected = `Detected: ${rpgmakerLinuxRunnerStatus.value.path}`;
    // A stale saved path falls back to discovery and reports why.
    return rpgmakerLinuxRunnerStatus.value.error
      ? `${detected}. ${rpgmakerLinuxRunnerStatus.value.error}`
      : detected;
  }
  return rpgmakerLinuxRunnerStatus.value.error || "Not detected";
});

const applyKeepSetting = (keep: SaveBackupKeep | undefined) => {
  if (typeof keep !== "number") {
    keepChoice.value = "all";
  } else if (KEEP_PRESETS.includes(keep)) {
    keepChoice.value = String(keep);
  } else {
    keepChoice.value = "custom";
    keepCustom.value = keep;
  }
};

const persistKeep = async (value: SaveBackupKeep, previous: { choice: string; custom: number }) => {
  savingKeep.value = true;
  try {
    const res = await api.saveSettings({ save_backup_keep: value });
    if (!res || res.success === false) throw new Error(res?.error || "Unknown error");
  } catch (e) {
    keepChoice.value = previous.choice;
    keepCustom.value = previous.custom;
    notifyError("Failed to save backup limit: " + (e instanceof Error ? e.message : String(e)));
  } finally {
    savingKeep.value = false;
  }
};

const onKeepChoice = (event: Event) => {
  const previous = { choice: keepChoice.value, custom: keepCustom.value };
  const choice = (event.target as HTMLSelectElement).value;
  keepChoice.value = choice;
  void persistKeep(choice === "all" ? "all" : choice === "custom" ? keepCustom.value : Number(choice), previous);
};

const onKeepCustom = (event: Event) => {
  const previous = { choice: keepChoice.value, custom: keepCustom.value };
  const input = event.target as HTMLInputElement;
  const parsed = Math.trunc(Number(input.value));
  const next = Number.isFinite(parsed) ? Math.min(99, Math.max(1, parsed)) : previous.custom;
  keepCustom.value = next;
  input.value = String(next);
  if (next !== previous.custom) void persistKeep(next, previous);
};

const applySettings = (data: SettingsResponse) => {
  customStatuses.value = [...(data.custom_play_statuses || [])];
  collectionList.value = [...(data.collections || [])];
  urmPath.value = data.urm_rpa_path || "";
  protonPath.value = data.proton_path || "";
  prefixPath.value = data.wine_prefix_path || "";
  libraryFolder.value = data.library_folder || "";
  libraryFolders.value = [...(data.library_folders || (libraryFolder.value ? [libraryFolder.value] : []))];
  void refreshFolderStatus();
  libraryFolderPattern.value = data.library_folder_pattern || "{creator}/{title}/{version}";
  downloadsFolder.value = data.downloads_folder || "";
  downloadDetectionEnabled.value = data.download_detection_enabled !== false;
  rpgmakerLinuxRunnerPath.value = data.rpgmaker_linux_runner_path || "";
  rpgmakerLinuxRunnerStatus.value =
    data.rpgmaker_linux_runner_status || defaultRpgmakerLinuxRunnerStatus;
  enableLogging.value = !!data.enable_logging;
  gamemodeEnabled.value = !!data.gamemode_enabled;
  mangohudEnabled.value = !!data.mangohud_enabled;
  applyKeepSetting(data.save_backup_keep);
  savedValues.value = formValues();
};

// Values which need protection while settings are reloaded.
const formValues = () => ({
  protonPath: protonPath.value,
  prefixPath: prefixPath.value,
  rpgmakerLinuxRunnerPath: rpgmakerLinuxRunnerPath.value,
  customStatuses: [...customStatuses.value],
  urmPath: urmPath.value,
  libraryFolder: libraryFolder.value,
  libraryFolders: [...libraryFolders.value],
  libraryFolderPattern: libraryFolderPattern.value,
  downloadsFolder: downloadsFolder.value,
  downloadDetectionEnabled: downloadDetectionEnabled.value,
});
const savedValues = ref(formValues());

const applyFormValues = (values: ReturnType<typeof formValues>) => {
  protonPath.value = values.protonPath;
  prefixPath.value = values.prefixPath;
  rpgmakerLinuxRunnerPath.value = values.rpgmakerLinuxRunnerPath;
  customStatuses.value = [...values.customStatuses];
  urmPath.value = values.urmPath;
  libraryFolder.value = values.libraryFolder;
  libraryFolders.value = [...values.libraryFolders];
  libraryFolderPattern.value = values.libraryFolderPattern;
  downloadsFolder.value = values.downloadsFolder;
  downloadDetectionEnabled.value = values.downloadDetectionEnabled;
};

// Coalesce overlapping reads while preserving focused and subsequently edited fields.
let persistedRead: Promise<SettingsResponse | null> | null = null;
let persistedGeneration = 0;
const reloadPersisted = async (submitted: ReturnType<typeof formValues>) => {
  if (!persistedRead) {
    persistedRead = (async () => {
      let data: SettingsResponse | null;
      let generation: number;
      do {
        generation = persistedGeneration;
        data = await api.getSettings();
      } while (generation !== persistedGeneration);
      return data;
    })().finally(() => { persistedRead = null; });
  }
  const data = await persistedRead;
  if (!data) throw new Error("Settings were saved but could not be reloaded.");
  const current = formValues();
  const focused = (document.activeElement as HTMLElement | null)?.dataset.settingsField as keyof typeof current | undefined;
  applySettings(data);
  applyFormValues(mergeSettingsFields(formValues(), current, submitted, focused));
  return data;
};


const themeOptions: { value: ThemePreference; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

// Swatch colors mirror the preset values in style.css for each theme.
const accentOptions: { value: AccentPreset; label: string; dark: string; light: string }[] = [
  { value: "magenta", label: "Magenta", dark: "#d152d1", light: "#b93ab9" },
  { value: "violet", label: "Violet", dark: "#a78bfa", light: "#7c3aed" },
  { value: "blue", label: "Blue", dark: "#60a5fa", light: "#2563eb" },
  { value: "cyan", label: "Cyan", dark: "#22d3ee", light: "#0e7490" },
  { value: "rose", label: "Rose", dark: "#fb7185", light: "#e11d48" },
  { value: "orange", label: "Orange", dark: "#fb923c", light: "#c2410c" },
];

const isCustomAccent = computed(() => accentPreference.value.startsWith("#"));
// Keeps the picker's last color while a preset is selected.
const customAccentColor = ref("#d152d1");
watch(
  accentPreference,
  (value) => {
    if (value.startsWith("#")) customAccentColor.value = value;
  },
  { immediate: true },
);

const onAccentColorInput = (event: Event, save: boolean): void => {
  const value = (event.target as HTMLInputElement).value as AccentPreference;
  if (save) setAccentPreference(value);
  else previewAccentPreference(value);
};

const shortcuts = [
  { keys: ["/", "Ctrl+F"], action: "Search the library" },
  { keys: ["Ctrl+N"], action: "Add a game" },
  { keys: ["Enter"], action: "Open the focused game" },
  { keys: ["Shift+F10"], action: "Game actions menu" },
  { keys: ["Esc"], action: "Close a window or menu" },
];

const toggleLogging = async () => {
  if (savingLogging.value) return;
  const enabled = !enableLogging.value;
  enableLogging.value = enabled;
  savingLogging.value = true;
  try {
    const res = await api.saveSettings({ enable_logging: enabled });
    if (!res || res.success === false) throw new Error(res?.error || "Unknown error");
  } catch (e) {
    enableLogging.value = !enabled;
    notifyError("Failed to save debug logging: " + (e instanceof Error ? e.message : String(e)));
  } finally {
    savingLogging.value = false;
  }
};

const loadSettings = async () => {
  if (settingsLoading.value) return;
  settingsLoading.value = true;
  settingsLoadError.value = "";
  // Values typed before the settings read lands must survive it, like edits
  // typed during a save. Keep them and mark them unsaved instead of replacing
  // them with the persisted values. A failed attempt keeps the original
  // baseline so Retry can still tell those edits apart from the defaults.
  const beforeLoad = settingsLoadBaseline.value ?? formValues();
  if (!settingsLoadBaseline.value) settingsLoadBaseline.value = beforeLoad;
  let settingsApplied = false;
  try {
    platformCapabilities.value = await loadPlatformCapabilities();
    // Read the GE-Proton status before the settings snapshot: a download that
    // finishes in between then shows up in the snapshot (or in the poll's own
    // settings read), so a stale proton_path is never loaded and saved back.
    const protonStatus = supportsLinuxRuntimes.value
      ? await api.getProtonDownloadStatus()
      : null;
    const data = await api.getSettings();
    if (!data) throw new Error("Settings response was empty");
    const current = formValues();
    applySettings(data);
    resetAutosaveBaselines();
    applyFormValues(mergeSettingsFields(formValues(), current, beforeLoad));
    settingsLoadBaseline.value = null;
    settingsApplied = true;

    if (supportsLinuxRuntimes.value) {
      performanceTools.value = await api.getPerformanceTools().catch(() => ({ gamemode: false, mangohud: false }));
      const runners = await api.getAvailableRunners();
      availableRunners.value = runners.runners || [];
      const ceCheck = await api.isCheatEngineInstalled();
      ceInstalled.value = !!ceCheck?.installed;
      cePath.value = ceCheck?.path || "";

      await pollInstallStatus();
      // An install started before leaving this page is still running; keep tracking it.
      // If this page closed while that read was in flight, do not arm a timer
      // that onUnmounted has already run past.
      if (!pageDisposed && (installingDeps.value || installingRtps.value)) startPolling();

      // The same applies to a GE-Proton download: it keeps running in the
      // backend after this page is closed, so re-attach to its progress
      // instead of offering a button the backend would refuse. Leaving again
      // before this read lands must not arm a timer nobody will clear.
      if (protonStatus?.running && !pageDisposed) {
        downloadingProton.value = true;
        trackedProtonPath = protonPath.value;
        protonDownload.value = protonStatus;
        startProtonDownloadPolling();
      }

      const sysDeps = await api.getSystemDepsCommand();
      if (sysDeps) systemDeps.value = sysDeps;
    }
  } catch (e) {
    console.error("Failed to load settings", e);
    // A later failure (runners, install status, system deps) leaves the loaded
    // form valid. Only a read that never produced the settings makes saving
    // the default/empty form dangerous.
    if (!settingsApplied) {
      settingsLoadError.value =
        "Could not load settings: " +
        (e instanceof Error ? e.message : String(e)) +
        ". Retry before changing them.";
    }
  } finally {
    settingsLoaded.value = true;
    settingsLoading.value = false;
  }
};

const pollInstallStatus = async () => {
  if (!supportsLinuxRuntimes.value || pageDisposed) return;
  try {
    const s = await api.getInstallStatus(prefixPath.value, protonPath.value);
    if (s) {
      dllsInstalled.value = !!s.dlls_installed;
      rtpsInstalled.value = !!s.rtps_installed;
      if (s.deps) {
        depsProgress.value = s.deps;
        installingDeps.value = !!s.deps.running;
        installError.value = s.deps.error || "";
      }
      if (s.rtps) {
        rtpProgress.value = s.rtps;
        installingRtps.value = !!s.rtps.running;
        rtpError.value = s.rtps.error || "";
      }
    }
  } catch (e) {}
};

const browseProton = async () => {
  try {
    const p = await api.browseRunnerFile(protonPath.value || "");
    if (p) {
      protonPath.value = p;
      void protonSave.commit();
    }
  } catch (e) {
    console.error("Browse proton error", e);
    notifyError("Error browsing file: " + String(e));
  }
};

const browsePrefix = async () => {
  try {
    const p = await api.browseDirectory(prefixPath.value || "");
    if (p) {
      prefixPath.value = p;
      void prefixSave.commit();
    }
  } catch (e) {
    console.error("Browse prefix error", e);
    notifyError("Error browsing directory: " + String(e));
  }
};

const browseLibraryFolder = async () => {
  try {
    const p = await api.browseDirectory(libraryFolder.value || "");
    if (p) {
      if (libraryFolders.value.includes(p)) {
        notifyError("That folder is already a library folder.");
        return;
      }
      libraryFolders.value = [...libraryFolders.value, p];
      if (!libraryFolder.value) libraryFolder.value = p;
      void foldersSave.commit();
    }
  } catch (e) {
    console.error("Browse library folder error", e);
    notifyError("Error browsing directory: " + String(e));
  }
};

const browseDownloadsFolder = async () => {
  try {
    const p = await api.browseDirectory(downloadsFolder.value || "");
    if (p) {
      downloadsFolder.value = p;
      void downloadsSave.commit();
    }
  } catch (e) {
    console.error("Browse downloads folder error", e);
    notifyError("Error browsing directory: " + String(e));
  }
};

// Live example for the folder pattern, using a sample game.
const patternExample = computed(() => {
  const samples: Record<string, string> = {
    creator: "Caribdis",
    title: "Eternum",
    version: "0.8",
    engine: "Ren'Py",
  };
  const clean = (segment: string) =>
    segment.replace(/[<>:"/\\|?*]/g, "_").replace(/[. ]+$/, "").trim() || "_";
  const parts = (libraryFolderPattern.value || "").split("/");
  const segments: string[] = [];
  for (const part of parts) {
    let substituted = part;
    for (const [token, sample] of Object.entries(samples)) {
      substituted = substituted.split(`{${token}}`).join(sample);
    }
    substituted = substituted.replace(/\{[^{}]*\}/g, "");
    const cleaned = clean(substituted);
    if (cleaned && cleaned !== "_") segments.push(cleaned);
  }
  const base = (libraryFolder.value || "~/games").replace(/[/\\]+$/, "");
  return `${base}/${segments.length ? segments.join("/") : "Eternum"}`;
});

const browseRpgmakerLinuxRunner = async () => {
  try {
    const p = await api.browseRunnerFile(rpgmakerLinuxRunnerPath.value || "");
    if (p) {
      rpgmakerLinuxRunnerPath.value = p;
      void runnerSave.commit();
    }
  } catch (e) {
    console.error("Browse RPGMaker Linux runner error", e);
    notifyError("Error browsing file: " + String(e));
  }
};

const openExternalPage = async (url: string) => {
  try {
    const result = await api.openInBrowser(url);
    if (result && result.success === false) {
      window.open(url, "_blank", "noopener");
    }
  } catch (_error) {
    window.open(url, "_blank", "noopener");
  }
};
const openRpgmakerLinuxRepo = () => openExternalPage(RPGMAKER_LINUX_REPO_URL);
const openUrmPage = () => openExternalPage(URM_PAGE_URL);

const stopProtonDownloadPolling = () => {
  if (protonDownloadTimer) clearInterval(protonDownloadTimer);
  protonDownloadTimer = null;
};

const pollProtonDownload = async () => {
  if (!downloadingProton.value) return;
  try {
    const status = await api.getProtonDownloadStatus();
    if (!downloadingProton.value) return;
    if (status?.running) {
      protonDownload.value = status;
      return;
    }
    // The download finished (or failed) while this page was open: drop the
    // progress UI and adopt the path the backend just saved. Only when the
    // field still holds what this page attached with, so an edit made while
    // the download ran is never overwritten (downloadProton reports success
    // and failure itself).
    downloadingProton.value = false;
    protonDownload.value = null;
    stopProtonDownloadPolling();
    const settings = await api.getSettings();
    if (
      settings?.proton_path &&
      settings.proton_path !== trackedProtonPath &&
      protonPath.value === trackedProtonPath
    ) {
      protonPath.value = settings.proton_path;
      savedValues.value = { ...savedValues.value, protonPath: settings.proton_path };
      protonSave.resetSaved(settings.proton_path);
    }
  } catch (e) {
    // Without a readable status the loop cannot see the download end, so stop
    // it and free the button instead of polling (and spinning) forever.
    console.error("Failed to read GE-Proton download status", e);
    stopProtonDownloadPolling();
    // A pending downloadProton() call still tracks the download and resets
    // the button when it returns; only a re-attached view has nothing left.
    if (!protonRequestPending) {
      downloadingProton.value = false;
      protonDownload.value = null;
    }
    protonError.value = "Could not read the GE-Proton download status: " + String(e);
  }
};

const startProtonDownloadPolling = () => {
  if (protonDownloadTimer) return;
  protonDownloadTimer = setInterval(pollProtonDownload, 500);
};

const coverCache = ref({ count: 0, bytes: 0 });
const coverCacheBusy = ref(false);
const coverCacheError = ref("");
const loadCoverCache = async () => {
  try {
    const result = await api.getCoverCacheStatus();
    if (!result.success) throw new Error(result.error || "Could not read cover cache");
    coverCache.value = { count: result.count || 0, bytes: result.bytes || 0 };
  } catch (error) { coverCacheError.value = String(error); }
};
const purgeCoverCache = async () => {
  if (!(await confirmAction({ title: "Delete cached covers", message: "Delete all cached covers? Cover downloads will resume next launch. Your local covers will be kept.", confirmLabel: "Delete", danger: true }))) return;
  coverCacheBusy.value = true;
  coverCacheError.value = "";
  try {
    const result = await api.purgeCoverCache();
    if (!result.success) throw new Error(result.error || "Could not purge cover cache");
    await loadCoverCache();
  } catch (error) { coverCacheError.value = String(error); }
  finally { coverCacheBusy.value = false; }
};
onMounted(() => { void loadCoverCache(); });
const formatCoverCacheSize = (bytes: number) => bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : formatMegabytes(bytes);
const formatMegabytes = (bytes: number) => `${Math.round(bytes / 1024 / 1024)} MB`;

const downloadProton = async () => {
  downloadingProton.value = true;
  protonError.value = "";
  protonDownload.value = { running: true, stage: "Starting", done: 0, total: 0 };
  trackedProtonPath = protonPath.value;
  protonRequestPending = true;
  startProtonDownloadPolling();
  try {
    const result = await api.downloadProtonGe();
    if (result && result.success && result.path) {
      // The backend already saved proton_path; other unsaved edits stay unsaved.
      protonPath.value = result.path;
      savedValues.value = { ...savedValues.value, protonPath: result.path };
      protonSave.resetSaved(result.path);
      protonError.value = "";
      notify(`GE-Proton installed to ${result.path}.`, "success");
    } else {
      protonError.value = result?.error || "Failed to download Proton.";
    }
  } catch (e) {
    protonError.value = String(e);
  } finally {
    protonRequestPending = false;
    downloadingProton.value = false;
    protonDownload.value = null;
    stopProtonDownloadPolling();
  }
};

const installRtps = async () => {
  installingRtps.value = true;
  rtpError.value = "";
  try {
    const result = await api.installRpgmakerRtp(prefixPath.value, protonPath.value);
    if (result && result.success) {
      startPolling();
    } else {
      rtpError.value = result?.error || "Unknown error occurred";
      installingRtps.value = false;
    }
  } catch (e) {
    rtpError.value = String(e);
    installingRtps.value = false;
  }
};

const installDeps = async () => {
  installingDeps.value = true;
  installError.value = "";
  try {
    const result = await api.installRpgmakerDependencies(prefixPath.value, protonPath.value);
    if (result && result.success) {
      startPolling();
    } else {
      installError.value = result?.error || "Unknown error occurred";
      installingDeps.value = false;
    }
  } catch (e) {
    installError.value = String(e);
    installingDeps.value = false;
  }
};

const startPolling = () => {
  if (pageDisposed || pollTimer) return;
  const tick = async () => {
    await pollInstallStatus();
    if (!pageDisposed && (installingDeps.value || installingRtps.value)) {
      pollTimer = setTimeout(tick, 2000);
    } else {
      pollTimer = null;
    }
  };
  pollTimer = setTimeout(tick, 2000);
};

const downloadCe = async () => {
  installingCe.value = true;
  ceError.value = "";
  try {
    const result = await api.downloadCheatEngine();
    if (result && result.success) {
      ceInstalled.value = true;
      cePath.value = result.path || "";
    } else {
      ceError.value = result?.error || "Unknown error occurred";
    }
  } catch (e) {
    ceError.value = String(e);
  } finally {
    installingCe.value = false;
  }
};

const copyDepsCommand = () => {
  if (!systemDeps.value) return;
  const text = systemDeps.value.command;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
  copiedCommand.value = true;
  setTimeout(() => (copiedCommand.value = false), 2000);
};

onMounted(() => {
  onWebviewReady(() => {
    loadSettings();
  });
});

onUnmounted(() => {
  pageDisposed = true;
  stopProtonDownloadPolling();
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  if (statusRefreshTimer) {
    clearTimeout(statusRefreshTimer);
    statusRefreshTimer = null;
  }
});

// Each setting owns its request queue. Reload using the last persisted values as
// the baseline so a different field's pending edit is never overwritten.
const libraryValue = computed({
  get: () => ({ folder: libraryFolder.value, folders: [...libraryFolders.value] }),
  set: value => { libraryFolder.value = value.folder; libraryFolders.value = value.folders; },
});
const makeField = <T,>(value: import("vue").Ref<T>, payload: (value: T) => SettingsPayload,
  keys: (keyof ReturnType<typeof formValues>)[], canonical: (values: ReturnType<typeof formValues>) => T,
  after?: () => Promise<void>) => useFieldAutosave({
  value,
  enabled: () => settingsLoaded.value && !settingsLoading.value && !settingsLoadError.value,
  save: async (submitted) => {
    const baseline = { ...savedValues.value };
    const current = formValues();
    for (const key of keys) Object.assign(baseline, { [key]: current[key] });
    const body = payload(submitted);
    const result = await api.saveSettings(body);
    if (!result?.success) throw new Error(result?.error || "Could not save setting.");
    persistedGeneration++;
    const persisted = await reloadPersisted(baseline);
    if (body.library_folders && persisted.library_folders.length < body.library_folders.length) {
      notifyError("A duplicate folder was removed: that folder is already a library folder.");
    }
    if (after && !pageDisposed) await after();
    return canonical(savedValues.value);
  },
});
const refreshInstall = async () => { if (supportsLinuxRuntimes.value) await pollInstallStatus(); };
const protonSave = makeField(protonPath, value => ({ proton_path: value }), ["protonPath"], values => values.protonPath, refreshInstall);
const prefixSave = makeField(prefixPath, value => ({ wine_prefix_path: value }), ["prefixPath"], values => values.prefixPath, refreshInstall);
const runnerSave = makeField(rpgmakerLinuxRunnerPath, value => ({ rpgmaker_linux_runner_path: value }), ["rpgmakerLinuxRunnerPath"], values => values.rpgmakerLinuxRunnerPath);
const urmSave = makeField(urmPath, value => ({ urm_rpa_path: value }), ["urmPath"], values => values.urmPath);
const foldersSave = makeField(libraryValue, value => ({ library_folder: value.folder, library_folders: value.folders }), ["libraryFolder", "libraryFolders"], values => ({ folder: values.libraryFolder, folders: values.libraryFolders }));
const patternSave = makeField(libraryFolderPattern, value => ({ library_folder_pattern: value }), ["libraryFolderPattern"], values => values.libraryFolderPattern);
const downloadsSave = makeField(downloadsFolder, value => ({ downloads_folder: value }), ["downloadsFolder"], values => values.downloadsFolder);
const statusesSave = makeField(customStatuses, value => ({ custom_play_statuses: value }), ["customStatuses"], values => values.customStatuses);
const detectionSave = makeField(downloadDetectionEnabled, value => ({ download_detection_enabled: value }), ["downloadDetectionEnabled"], values => values.downloadDetectionEnabled);
const autosaves = [protonSave, prefixSave, runnerSave, urmSave, foldersSave, patternSave, downloadsSave, statusesSave, detectionSave];
const resetAutosaveBaselines = () => {
  protonSave.resetSaved(protonPath.value); prefixSave.resetSaved(prefixPath.value);
  runnerSave.resetSaved(rpgmakerLinuxRunnerPath.value); urmSave.resetSaved(urmPath.value);
  foldersSave.resetSaved(libraryValue.value); patternSave.resetSaved(libraryFolderPattern.value);
  downloadsSave.resetSaved(downloadsFolder.value); statusesSave.resetSaved(customStatuses.value);
  detectionSave.resetSaved(downloadDetectionEnabled.value);
};
const commitPattern = () => {
  libraryFolderPattern.value = libraryFolderPattern.value.trim() || "{creator}/{title}/{version}";
  void patternSave.commit();
};
onBeforeRouteLeave(() => {
  commitPattern();
  for (const field of autosaves) {
    void field.commit().then(() => {
      if (field.state.value === "error") notifyError(`Could not save setting: ${field.error.value}`);
    });
  }
});

watch([protonPath, prefixPath], () => {
  if (!settingsLoaded.value || !supportsLinuxRuntimes.value) return;
  if (pollTimer) return;
  if (statusRefreshTimer) clearTimeout(statusRefreshTimer);
  statusRefreshTimer = setTimeout(() => {
    statusRefreshTimer = null;
    void pollInstallStatus();
  }, 300);
});

const addStatus = () => {
  const name = newStatus.value.trim();
  statusError.value = validateCustomPlayStatuses([...customStatuses.value, name]);
  if (statusError.value) return;
  customStatuses.value = [...customStatuses.value, name];
  newStatus.value = "";
  void statusesSave.commit();
};

const removeStatus = async (name: string) => {
  try {
    const games = await api.getGames();
    const count = games.filter((game) => game.play_status?.toLowerCase() === name.toLowerCase()).length;
    if (!(await confirmAction({ title: 'Remove status', message: `Remove '${name}'? This resets ${count} game${count === 1 ? '' : 's'} to Not Started.`, confirmLabel: 'Remove', danger: true }))) return;
    customStatuses.value = customStatuses.value.filter((status) => status !== name);
    statusError.value = "";
    void statusesSave.commit();
  } catch (error) {
    statusError.value = String(error);
  }
};

// Collections: each action goes straight to the backend in one transaction,
// so the library tabs follow this list immediately.
const applyCollectionsResult = (result: CollectionsResponse) => {
  if (result.collections) collectionList.value = [...result.collections];
};

const addCollection = async () => {
  const name = newCollectionName.value.trim();
  if (!name) {
    collectionError.value = "Enter a collection name.";
    return;
  }
  const result = await api.createCollection(name);
  if (!result.success) {
    collectionError.value = result.error || "Could not create the collection.";
    return;
  }
  applyCollectionsResult(result);
  newCollectionName.value = "";
  collectionError.value = "";
};

const startCollectionRename = (name: string) => {
  collectionError.value = "";
  renamingCollection.value = name;
  renameCollectionDraft.value = name;
};

const cancelCollectionRename = () => {
  renamingCollection.value = null;
  renameCollectionDraft.value = "";
};

const commitCollectionRename = async () => {
  const oldName = renamingCollection.value;
  const newName = renameCollectionDraft.value.trim();
  if (!oldName) return;
  if (!newName || newName === oldName) {
    cancelCollectionRename();
    return;
  }
  const result = await api.renameCollection(oldName, newName);
  if (!result.success) {
    collectionError.value = result.error || "Could not rename the collection.";
    return;
  }
  applyCollectionsResult(result);
  cancelCollectionRename();
};

const moveCollection = async (name: string, offset: -1 | 1) => {
  const index = collectionList.value.indexOf(name);
  const target = index + offset;
  if (index === -1 || target < 0 || target >= collectionList.value.length) return;
  const next = [...collectionList.value];
  const moved = next[index];
  const swapped = next[target];
  if (moved === undefined || swapped === undefined) return;
  next[index] = swapped;
  next[target] = moved;
  const result = await api.reorderCollections(next);
  if (!result.success) {
    collectionError.value = result.error || "Could not move the collection.";
    return;
  }
  applyCollectionsResult(result);
};

const removeCollection = async (name: string) => {
  let count = 0;
  try {
    const games = await api.getGames();
    count = games.filter((game) =>
      (game.collections || []).some((entry) => entry.toLowerCase() === name.toLowerCase()),
    ).length;
  } catch (_error) {
    // The confirm wording works without the count too.
  }
  const note = count
    ? ` Its ${count} game${count === 1 ? "" : "s"} will stay in your library.`
    : " Games stay in your library.";
  if (!(await confirmAction({ title: 'Remove collection', message: `Remove the collection '${name}'?${note}`, confirmLabel: 'Remove', danger: true }))) return;
  const result = await api.deleteCollection(name);
  if (!result.success) {
    collectionError.value = result.error || "Could not remove the collection.";
    return;
  }
  applyCollectionsResult(result);
  collectionError.value = "";
};

const settingsRoot = ref<HTMLElement | null>(null);
const desktopSearch = ref<HTMLInputElement | null>(null);
const mobileSearch = ref<HTMLInputElement | null>(null);
const searchQuery = ref("");
const visibleRows = ref<Set<string> | null>(null);
provide(settingsVisibleRows, visibleRows);
const settingsRowVisible = (id: string) => !visibleRows.value || visibleRows.value.has(id);
const activeSection = ref("general");
const shownSectionIds = ref<string[]>([]);
const route = useRoute();
const sectionRegistry = computed(() => [
  { id: "general", title: "General", keywords: "logging statuses collections" },
  { id: "library-downloads", title: "Library & Downloads", keywords: "folders archives" },
  { id: "file-hosts", title: "File host accounts", keywords: "pixeldrain mega account api key premium download" },
  { id: "appearance", title: "Appearance", keywords: "theme dark light color" },
  { id: "wine-proton", title: supportsLinuxRuntimes.value ? "Wine / Proton" : platformCapabilities.value.platform === "windows" ? "Windows Environment" : "Platform Environment", keywords: "wine prefix runner" },
  ...(supportsLinuxRuntimes.value ? [{ id: "umu", title: "Proton (umu)", keywords: "runtime" }] : []),
  ...(platformCapabilities.value.platform === "linux" ? [{ id: "steam", title: "Steam", keywords: "account sync" }] : []),
  ...(platformCapabilities.value.companion_tools ? [{ id: "custom-tools", title: "Custom tools", keywords: "companion" }] : []),
  { id: "sandbox", title: "Sandbox", keywords: "isolation" },
  { id: "catalog", title: "Catalog", keywords: "game catalog" },
  ...(supportsLinuxRuntimes.value ? [{ id: "tools", title: "Tools", keywords: "winetricks" }] : []),
  { id: "renpy", title: "Ren'Py", keywords: "urm mod" },
]);
const shownSections = computed(() => sectionRegistry.value.filter(section => shownSectionIds.value.includes(section.id)));
const sectionVisible = (id: string) => !searchQuery.value.trim() || shownSectionIds.value.includes(id);
const scrollToSection = (id: string, focus = false) => {
  const section = document.getElementById(id);
  const main = settingsRoot.value?.closest<HTMLElement>("main");
  if (!section || !main || !shownSectionIds.value.includes(id)) return;
  main.scrollTo({ top: main.scrollTop + section.getBoundingClientRect().top - main.getBoundingClientRect().top, behavior: "instant" });
  activeSection.value = id;
  if (focus) focusCandidates(section)[0]?.focus({ preventScroll: true });
};
const cycleSection = (step: number) => {
  const list = shownSections.value;
  if (!list.length) return;
  const index = Math.max(0, list.findIndex(section => section.id === activeSection.value));
  const target = list[Math.max(0, Math.min(list.length - 1, index + step))];
  if (target) scrollToSection(target.id, true);
};
useControllerActions(settingsRoot, {
  search: () => {
    const input = [desktopSearch.value, mobileSearch.value].find(item => item?.getClientRects().length);
    if (input) controllerFocusText(input);
  },
  prevTab: () => cycleSection(-1), nextTab: () => cycleSection(1),
  move: direction => {
    const active = document.activeElement as HTMLElement | null;
    if (!active) return false;
    if (direction === "right" && active.closest("[data-settings-nav]")) {
      scrollToSection(activeSection.value, true);
      return true;
    }
    const content = active.closest<HTMLElement>("[data-settings-content]");
    if (direction !== "left" || !content) return false;
    const box = (el: HTMLElement) => (el.matches('input[type="checkbox"], input[type="radio"], input.sr-only') ? el.closest("label") || el : el).getBoundingClientRect();
    const rect = box(active);
    const candidates = focusCandidates(content).filter(el => el !== active).map(el => ({ element: el, ...box(el).toJSON() })).filter(el => el.bottom > rect.top && el.top < rect.bottom);
    if (pickNext(rect, candidates, "left")) return false;
    const entry = settingsRoot.value?.querySelector<HTMLElement>('[data-settings-nav] [aria-current="location"]');
    if (!entry?.getClientRects().length) return false;
    entry.focus({ preventScroll: true });
    return true;
  },
  hints: [{ button: "A", label: "Select" }, { button: "B", label: "Back" }, { button: "Y", label: "Search" }, { button: "LB/RB", label: "Sections" }],
});

// Tag each top-level setting group and keep it mounted when filtering. This
// also covers conditionally rendered rows supplied by child sections.
let searchObserver: MutationObserver | null = null;
let intersectionObserver: IntersectionObserver | null = null;
let searchScheduled = false;
const originalDisplay = new WeakMap<HTMLElement, string>();
const refreshSearch = () => {
  const root = settingsRoot.value;
  if (!root) return;
  const sections: SettingsSearchSection[] = [];
  const elements = new Map<string, HTMLElement>();
  for (const section of sectionRegistry.value) {
    const container = root.querySelector<HTMLElement>(`#${section.id}`);
    if (!container) continue;
    const rows: SettingsSearchSection["rows"] = [];
    let index = 0;
    for (const child of Array.from(container.children)) {
      if (!(child instanceof HTMLElement) || child.matches("h3") || child.querySelector("h3")) continue;
      const id = child.dataset.settingsRow || `${section.id}-dynamic-${index}`;
      index++;
      child.dataset.settingsRow = id;
      if (!child.dataset.keywords) child.dataset.keywords = section.id === "appearance" && child.textContent?.includes("Theme") ? "dark light system" : "";
      if (!originalDisplay.has(child)) originalDisplay.set(child, child.style.display);
      elements.set(id, child);
      rows.push({ id, label: child.textContent || "", keywords: child.dataset.keywords || "" });
    }
    sections.push({ id: section.id, title: section.title, rows });
  }
  const matched = matchSettings(sections, searchQuery.value);
  shownSectionIds.value = matched.sectionIds;
  visibleRows.value = new Set(matched.rowIds);
  for (const [id, element] of elements) {
    if (!element.hasAttribute("data-settings-bound")) element.style.display = matched.rowIds.includes(id) ? originalDisplay.get(element) || "" : "none";
  }
  if (!shownSectionIds.value.includes(activeSection.value)) activeSection.value = shownSectionIds.value[0] || "";
  void nextTick(observeSections);
};
const scheduleSearch = () => {
  if (searchScheduled) return;
  searchScheduled = true;
  void nextTick(() => { searchScheduled = false; refreshSearch(); });
};
const observeSections = () => {
  intersectionObserver?.disconnect();
  const main = settingsRoot.value?.closest<HTMLElement>("main");
  if (!main || typeof IntersectionObserver === "undefined") return;
  intersectionObserver = new IntersectionObserver(() => {
    const top = main.getBoundingClientRect().top + 20;
    const visible = shownSections.value.map(item => document.getElementById(item.id)).filter((el): el is HTMLElement => !!el && el.getClientRects().length > 0);
    const passed = visible.filter(el => el.getBoundingClientRect().top <= top);
    const atTop = passed[passed.length - 1] || visible[0];
    if (atTop) activeSection.value = atTop.id;
  }, { root: main, rootMargin: "-1px 0px -70% 0px", threshold: [0, 1] });
  for (const item of shownSections.value) { const el = document.getElementById(item.id); if (el) intersectionObserver.observe(el); }
};
const followSettingsHash = async () => {
  await nextTick();
  refreshSearch();
  const id = route.hash.slice(1);
  if (id && shownSectionIds.value.includes(id)) { scrollToSection(id); pinSection(id); }
  else settingsRoot.value?.closest<HTMLElement>("main")?.scrollTo({ top: 0, behavior: "instant" });
};
// Sections above a deep-link target grow or shrink as settings and child
// sections finish loading. Keep the target at the top until the user scrolls.
let unpinSection: (() => void) | null = null;
const pinSection = (id: string) => {
  unpinSection?.();
  const root = settingsRoot.value;
  const main = root?.closest<HTMLElement>("main");
  if (!root || !main || typeof ResizeObserver === "undefined") return;
  let pinnedTop = main.scrollTop;
  const resize = new ResizeObserver(() => { scrollToSection(id); pinnedTop = main.scrollTop; });
  // Any scroll the pin did not make (wheel, keys, controller, section list) ends it.
  const onScroll = () => { if (Math.abs(main.scrollTop - pinnedTop) > 2) stop(); };
  const stop = () => {
    resize.disconnect();
    clearTimeout(timer);
    main.removeEventListener("scroll", onScroll);
    unpinSection = null;
  };
  const timer = setTimeout(stop, 3000);
  main.addEventListener("scroll", onScroll, { passive: true });
  resize.observe(root);
  unpinSection = stop;
};
watch(searchQuery, scheduleSearch);
watch(sectionRegistry, () => { scheduleSearch(); void followSettingsHash(); });
watch(() => route.hash, () => { void followSettingsHash(); });
onMounted(() => {
  searchObserver = new MutationObserver(scheduleSearch);
  if (settingsRoot.value) searchObserver.observe(settingsRoot.value, { childList: true, subtree: true });
  scheduleSearch();
  void followSettingsHash();
});
onUnmounted(() => { searchObserver?.disconnect(); intersectionObserver?.disconnect(); unpinSection?.(); });

const browseUrm = async () => {
  const path = await api.browseUrmFile(urmPath.value);
  if (path) { urmPath.value = path; void urmSave.commit(); }
};
</script>

<template>
  <div ref="settingsRoot" class="allow-text-selection mx-auto w-full p-8 max-w-7xl pb-12">
    <header class="mb-8">
      <h2
        class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
        style="color: var(--text-primary)"
      >
        <IconSettings class="ui-page-heading-icon" />
        <span>Settings</span>
      </h2>
      <p
        class="text-sm pl-3"
        style="
          color: var(--text-secondary);
          border-left: 2px solid var(--brand);
        "
      >
        Configure launch paths and application behavior. Changes save automatically.
      </p>
    </header>

    <div
      v-if="settingsLoadError"
      class="settings-card rounded-xl p-4 mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
      role="alert"
    >
      <p class="copyable-feedback text-sm" style="color: var(--danger-text)">
        {{ settingsLoadError }}
      </p>
      <button
        type="button"
        class="settings-btn shrink-0 disabled:opacity-50"
        :disabled="settingsLoading"
        @click="loadSettings"
      >
        {{ settingsLoading ? "Retrying..." : "Retry" }}
      </button>
    </div>

    <div class="lg:grid lg:grid-cols-[13rem_minmax(0,1fr)] lg:gap-8 lg:items-start">
      <aside class="hidden lg:block sticky top-4 space-y-3" aria-label="Settings navigation">
        <label class="block text-sm" for="settings-search-desktop">Search settings</label>
        <input id="settings-search-desktop" ref="desktopSearch" v-model="searchQuery" type="search" class="settings-input w-full" placeholder="Search settings" />
        <nav data-settings-nav aria-label="Settings sections" class="flex flex-col gap-1">
          <button v-for="section in shownSections" :key="section.id" :data-section-id="section.id" type="button" class="settings-nav-item text-left rounded-lg px-3 py-2 text-sm" :class="{ 'settings-nav-active': activeSection === section.id }" :aria-current="activeSection === section.id ? 'location' : undefined" @click="scrollToSection(section.id, $event.detail === 0)">{{ section.title }}</button>
        </nav>
      </aside>
      <div data-settings-content class="min-w-0 max-w-4xl w-full">
        <div class="lg:hidden space-y-3 mb-6">
          <label class="block text-sm" for="settings-search-mobile">Search settings</label>
          <input id="settings-search-mobile" ref="mobileSearch" v-model="searchQuery" type="search" class="settings-input w-full" placeholder="Search settings" />
          <label class="block text-sm" for="settings-section-select">Section</label>
          <select id="settings-section-select" class="settings-input w-full" :value="activeSection" @change="scrollToSection(($event.target as HTMLSelectElement).value)">
            <option v-for="section in shownSections" :key="section.id" :value="section.id">{{ section.title }}</option>
          </select>
        </div>
        <div v-if="!shownSections.length && searchQuery.trim()" class="settings-card rounded-xl p-6 mb-6" role="status">No settings match. <button class="underline" @click="searchQuery = ''">Clear search</button></div>
        <fieldset :disabled="!!settingsLoadError || settingsLoading" class="space-y-6 w-full min-w-0 border-0 p-0">
      <!-- General -->
      <section v-show="sectionVisible('general')" id="general" class="settings-card rounded-xl p-6 space-y-5">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconAdjustments class="ui-section-icon" />
          General
        </h3>

        <div data-settings-row="general-static-0" data-settings-bound v-show="settingsRowVisible('general-static-0')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">
              Enable Debug Logging
            </h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              {{
                supportsLinuxRuntimes
                  ? "Saves Wine/Proton launch output to a .log file next to the game executable."
                  : "Saves native launch output to a .log file next to the game executable."
              }}
            </p>
          </div>
          <label class="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              type="checkbox"
              :checked="enableLogging"
              :disabled="savingLogging"
              @change="toggleLogging"
              class="sr-only peer"
              aria-label="Enable debug logging"
            />
            <div class="ui-toggle"></div>
          </label>
        </div>

        <div data-settings-row="general-static-1" data-settings-bound v-show="settingsRowVisible('general-static-1')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <label for="save-backup-keep" class="text-sm font-medium" style="color: var(--text-primary)">
              Save backups to keep per game
            </label>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Older backups are deleted after a new one is made. Changes apply right away.
            </p>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <select
              id="save-backup-keep"
              :value="keepChoice"
              :disabled="savingKeep"
              @change="onKeepChoice"
              class="settings-input"
            >
              <option value="all">Keep all</option>
              <option v-for="count in KEEP_PRESETS" :key="count" :value="String(count)">
                Newest {{ count }}
              </option>
              <option value="custom">Custom…</option>
            </select>
            <input
              v-if="keepChoice === 'custom'"
              type="number"
              min="1"
              max="99"
              step="1"
              :value="keepCustom"
              :disabled="savingKeep"
              @change="onKeepCustom"
              class="settings-input w-20"
              aria-label="Number of save backups to keep"
            />
          </div>
        </div>

        <div data-settings-row="general-static-2" data-settings-bound v-show="settingsRowVisible('general-static-2')" class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Play Statuses</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Add custom statuses alongside the built-in choices.
            </p>
          </div>
          <div class="flex gap-3">
            <input
              v-model="newStatus"
              @input="statusError = ''"
              :disabled="!!settingsLoadError || settingsLoading"
              @keydown.enter.prevent="addStatus"
              type="text"
              class="settings-input flex-1"
              placeholder="Custom status name"
              aria-label="Custom status name"
            />
            <button @click="addStatus" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn">Add Status</button>
          </div>
          <FieldSaveFeedback :state="statusesSave.state.value" :error="statusesSave.error.value" />
          <p v-if="statusError" role="alert" class="text-xs text-red-400">{{ statusError }}</p>
          <div
            v-for="name in customStatuses"
            :key="name"
            class="setting-row flex items-center justify-between gap-3 px-4 py-2 rounded-lg"
          >
            <span class="text-sm" style="color: var(--text-primary)">{{ name }}</span>
            <button @click="removeStatus(name)" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn" :aria-label="`Remove ${name}`">
              Remove
            </button>
          </div>
        </div>

        <div data-settings-row="general-static-3" data-settings-bound v-show="settingsRowVisible('general-static-3')" class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Collections</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Your own groups of games, shown as tabs above the library. Changes apply right away.
            </p>
          </div>
          <div class="flex gap-3">
            <input
              v-model="newCollectionName"
              @keydown.enter.prevent="addCollection"
              type="text"
              class="settings-input flex-1"
              placeholder="Collection name"
              aria-label="New collection name"
            />
            <button @click="addCollection" class="settings-btn">Add Collection</button>
          </div>
          <p v-if="collectionError" role="alert" class="text-xs text-red-400">{{ collectionError }}</p>
          <p
            v-if="!collectionList.length"
            class="text-xs"
            style="color: var(--text-muted)"
          >
            No collections yet.
          </p>
          <div
            v-for="(name, index) in collectionList"
            :key="name"
            class="setting-row flex items-center justify-between gap-3 px-4 py-2 rounded-lg"
          >
            <form
              v-if="renamingCollection === name"
              class="flex items-center gap-2 min-w-0 flex-1"
              @submit.prevent="commitCollectionRename"
            >
              <input
                v-model="renameCollectionDraft"
                type="text"
                class="settings-input flex-1"
                :aria-label="`Rename ${name}`"
                @keydown.escape.prevent="cancelCollectionRename"
              />
              <button type="submit" class="settings-btn" title="Save the new name">Save</button>
              <button type="button" class="settings-btn" @click="cancelCollectionRename">Cancel</button>
            </form>
            <template v-else>
              <span class="text-sm truncate" style="color: var(--text-primary)">{{ name }}</span>
              <div class="flex items-center gap-1.5 shrink-0">
                <button
                  class="settings-btn"
                  :disabled="index === 0"
                  :aria-label="`Move ${name} up`"
                  title="Move up"
                  @click="moveCollection(name, -1)"
                >
                  ↑
                </button>
                <button
                  class="settings-btn"
                  :disabled="index === collectionList.length - 1"
                  :aria-label="`Move ${name} down`"
                  title="Move down"
                  @click="moveCollection(name, 1)"
                >
                  ↓
                </button>
                <button class="settings-btn" @click="startCollectionRename(name)">Rename</button>
                <button class="settings-btn" :aria-label="`Remove ${name}`" @click="removeCollection(name)">
                  Remove
                </button>
              </div>
            </template>
          </div>
        </div>

        <div data-settings-row="general-static-4" data-settings-bound v-show="settingsRowVisible('general-static-4')" class="space-y-3">
          <h4 class="ui-section-heading text-sm font-medium" style="color: var(--text-primary)">
            <IconKeyboard class="w-4 h-4" style="color: var(--text-muted)" />
            Keyboard Shortcuts
          </h4>
          <dl class="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2">
            <div
              v-for="shortcut in shortcuts"
              :key="shortcut.action"
              class="flex items-center justify-between gap-3"
            >
              <dt style="color: var(--text-secondary)">{{ shortcut.action }}</dt>
              <dd class="flex gap-1 shrink-0">
                <kbd v-for="key in shortcut.keys" :key="key">{{ key }}</kbd>
              </dd>
            </div>
          </dl>
        </div>
      </section>

      <!-- Library & Downloads -->
      <section v-show="sectionVisible('library-downloads')" id="library-downloads" class="settings-card rounded-xl p-6 space-y-5">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconDownloadFilled class="ui-section-icon" />
          Library &amp; Downloads
        </h3>
        <div data-settings-row="library-downloads-static-0" data-settings-bound v-show="settingsRowVisible('library-downloads-static-0')" class="flex items-center justify-between gap-3 mb-4">
          <div>
            <p class="text-sm">Cover cache: {{ coverCache.count ? `${coverCache.count} covers, ${formatCoverCacheSize(coverCache.bytes)}` : 'Empty' }}</p>
            <p class="text-xs mt-1" style="color: var(--text-muted)">Cached covers stay on this device. After purging, downloads resume next launch.</p>
            <p v-if="coverCacheError" class="text-xs" role="alert">{{ coverCacheError }}</p>
          </div>
          <button class="settings-btn disabled:opacity-50" :disabled="coverCacheBusy || !coverCache.count" @click="purgeCoverCache">Purge</button>
        </div>

        <div data-settings-row="library-downloads-static-1" data-settings-bound v-show="settingsRowVisible('library-downloads-static-1')" class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Library folders</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Choose the folders available for archive installs. New installs preselect the default.
            </p>
          </div>
          <div v-for="path in libraryFolders" :key="path" class="space-y-2 rounded-lg p-3" style="border: 1px solid var(--border-color)">
            <div class="flex items-center gap-3">
              <span class="font-mono text-sm break-all flex-1" :aria-label="path">{{ path }}</span>
              <span v-if="path === libraryFolder" class="text-xs">Default</span>
              <button v-else @click="libraryFolder = path; foldersSave.commit()" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn">Make default</button>
              <button @click="removeLibraryFolder(path)" :disabled="!!settingsLoadError || settingsLoading || (path === libraryFolder && libraryFolders.length > 1)" :title="path === libraryFolder && libraryFolders.length > 1 ? 'Choose another default first' : 'Remove from list'" class="settings-btn disabled:opacity-50">Remove</button>
            </div>
            <div class="flex gap-3 text-xs" style="color: var(--text-muted)">
              <PathStatus :path="path" kind="dir" />
              <span>{{ folderFreeSpace(path) }}</span>
            </div>
          </div>
          <p v-if="libraryFolders.length > 1" class="text-xs" style="color: var(--text-muted)">Choose another default before removing the default folder.</p>
          <button @click="browseLibraryFolder" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn ui-action-btn">Add library folder</button>
          <FieldSaveFeedback :state="foldersSave.state.value" :error="foldersSave.error.value" />
        </div>

        <div data-settings-row="library-downloads-static-2" data-settings-bound v-show="settingsRowVisible('library-downloads-static-2')" class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Folder pattern</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Tokens: <code>{creator}</code> <code>{title}</code> <code>{version}</code> <code>{engine}</code>.
              Empty parts are skipped.
            </p>
          </div>
          <input
            v-model="libraryFolderPattern"
              @blur="commitPattern()"
              @keydown.enter.prevent="commitPattern()"
              :disabled="!!settingsLoadError || settingsLoading"
              data-settings-field="libraryFolderPattern"
            type="text"
            class="settings-input w-full font-mono"
            placeholder="{creator}/{title}/{version}"
            aria-label="Library folder pattern"
          />
            <FieldSaveFeedback :state="patternSave.state.value" :error="patternSave.error.value" />
          <p class="text-xs font-mono break-all" style="color: var(--text-muted)">
            Example: {{ patternExample }}
          </p>
        </div>

        <div data-settings-row="library-downloads-static-3" data-settings-bound v-show="settingsRowVisible('library-downloads-static-3')" class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Downloads folder</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              wLib looks here for archives matching games with pending updates.
            </p>
          </div>
          <div class="flex gap-3">
            <input
              v-model="downloadsFolder"
              @blur="downloadsSave.commit()"
              @keydown.enter.prevent="downloadsSave.commit()"
              :disabled="!!settingsLoadError || settingsLoading"
              data-settings-field="downloadsFolder"
              type="text"
              class="settings-input flex-1 font-mono"
              placeholder="~/Downloads"
              aria-label="Downloads folder"
            />
            <FieldSaveFeedback :state="downloadsSave.state.value" :error="downloadsSave.error.value" />
            <button @click="browseDownloadsFolder" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn ui-action-btn">Browse</button>
          </div>
          <PathStatus :path="downloadsFolder" kind="dir" />
        </div>

        <div data-settings-row="library-downloads-static-4" data-settings-bound v-show="settingsRowVisible('library-downloads-static-4')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">
              Detect downloaded updates
            </h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Offer to install matching archives when the app starts, after update checks, and when the window regains focus.
            </p>
          </div>
          <FieldSaveFeedback :state="detectionSave.state.value" :error="detectionSave.error.value" />
          <label class="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              type="checkbox"
              v-model="downloadDetectionEnabled"
              @change="detectionSave.commit()"
              :disabled="!!settingsLoadError || settingsLoading"
              class="sr-only peer"
              aria-label="Detect downloaded updates"
            />
            <div class="ui-toggle"></div>
          </label>
        </div>
      </section>

      <FileHostAccounts v-show="sectionVisible('file-hosts')" />

      <!-- Appearance -->
      <section v-show="sectionVisible('appearance')" id="appearance" class="settings-card rounded-xl p-6 space-y-4">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconPalette class="ui-section-icon" />
          Appearance
        </h3>

        <div data-settings-row="appearance-static-0" data-keywords="dark light system" data-settings-bound v-show="settingsRowVisible('appearance-static-0')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Theme</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              System follows your desktop's light or dark setting.
            </p>
          </div>
          <div class="motion-toggle inline-flex rounded-lg p-1 shrink-0" role="group" aria-label="Theme">
            <button
              v-for="option in themeOptions"
              :key="option.value"
              type="button"
              class="motion-option px-3 py-1.5 rounded-md text-xs font-semibold"
              :class="themePreference === option.value ? 'motion-option-active' : ''"
              :aria-pressed="themePreference === option.value"
              @click="setThemePreference(option.value)"
            >
              {{ option.label }}
            </button>
          </div>
        </div>

        <div data-settings-row="appearance-static-1" data-settings-bound v-show="settingsRowVisible('appearance-static-1')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 id="accent-label" class="text-sm font-medium" style="color: var(--text-primary)">Accent</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Highlight color for buttons, tabs, and active items. Play buttons stay green.
            </p>
          </div>
          <div class="accent-swatches flex flex-wrap justify-end gap-2 shrink-0" role="radiogroup" aria-labelledby="accent-label">
            <button
              v-for="option in accentOptions"
              :key="option.value"
              type="button"
              role="radio"
              class="accent-swatch"
              :class="accentPreference === option.value ? 'accent-swatch-selected' : ''"
              :style="{ background: isDarkTheme ? option.dark : option.light }"
              :aria-checked="accentPreference === option.value"
              :aria-label="option.label"
              :title="option.label"
              @click="setAccentPreference(option.value)"
            />
            <label
              class="accent-swatch accent-swatch-custom"
              :class="isCustomAccent ? 'accent-swatch-selected' : ''"
              :style="isCustomAccent ? { background: accentPreference } : undefined"
              title="Custom"
            >
              <input
                type="color"
                class="accent-color-input"
                :value="customAccentColor"
                :aria-label="isCustomAccent ? 'Custom color (selected)' : 'Custom color'"
                @input="onAccentColorInput($event, false)"
                @change="onAccentColorInput($event, true)"
              />
            </label>
          </div>
        </div>

        <div data-settings-row="appearance-static-2" data-settings-bound v-show="settingsRowVisible('appearance-static-2')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Text size</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Scales text and spacing across the whole interface. Kept on this device.
            </p>
          </div>
          <div class="motion-toggle inline-flex rounded-lg p-1 shrink-0" role="group" aria-label="Text size">
            <button
              v-for="option in TEXT_SIZE_OPTIONS"
              :key="option.value"
              type="button"
              class="motion-option px-3 py-1.5 rounded-md text-xs font-semibold"
              :class="textSizePreference === option.value ? 'motion-option-active' : ''"
              :aria-pressed="textSizePreference === option.value"
              @click="setTextSizePreference(option.value)"
            >
              {{ option.label }}
            </button>
          </div>
        </div>

        <div data-settings-row="appearance-static-3" data-settings-bound v-show="settingsRowVisible('appearance-static-3')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">TV mode</h4>
            <label class="sr-only" for="tv-mode">TV mode</label>
            <p class="text-xs" style="color: var(--text-muted)">Larger controls for a TV or Steam Deck. Kept on this device. <span v-if="tvPreference === 'auto'">{{ gamingMode ? 'Active (Gaming Mode)' : 'Inactive' }}</span></p>
          </div>
          <select id="tv-mode" class="rounded-lg border px-3 py-1.5 text-sm" style="background: var(--bg-surface); color: var(--text-primary); border-color: var(--border)" :value="tvPreference" @change="setTvPreference(($event.target as HTMLSelectElement).value)">
              <option value="auto">Auto</option><option value="on">On</option><option value="off">Off</option>
            </select>
        </div>

        <div data-settings-row="appearance-static-4" data-settings-bound v-show="settingsRowVisible('appearance-static-4')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <label for="interface-scale" class="text-sm font-medium" style="color: var(--text-primary)">Interface scale</label>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Scales the whole interface evenly. Kept on this device. <span v-if="tvModeActive">TV mode uses 150%.</span>
            </p>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <select id="interface-scale" class="rounded-lg border px-3 py-1.5 text-sm"
              style="background: var(--bg-surface); color: var(--text-primary); border-color: var(--border)"
              :value="interfaceScale" @change="setInterfaceScale(($event.target as HTMLSelectElement).value)">
              <option v-for="option in INTERFACE_SCALE_OPTIONS" :key="option" :value="option">{{ option }}%</option>
            </select>
            <button v-if="interfaceScale !== 100" type="button" class="motion-option px-3 py-1.5 rounded-md text-xs font-semibold"
              @click="setInterfaceScale(100)">Reset</button>
          </div>
        </div>

        <div data-settings-row="appearance-static-5" data-settings-bound v-show="settingsRowVisible('appearance-static-5')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Animations</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Turn non-essential UI motion on or off. Loading indicators stay visible either way.
            </p>
          </div>
          <div class="motion-toggle inline-flex rounded-lg p-1 shrink-0" role="group" aria-label="Animations">
            <button
              type="button"
              class="motion-option px-3 py-1.5 rounded-md text-xs font-semibold"
              :class="motionEnabled ? 'motion-option-active' : ''"
              :aria-pressed="motionEnabled"
              @click="saveMotionPreference(true)"
            >
              On
            </button>
            <button
              type="button"
              class="motion-option px-3 py-1.5 rounded-md text-xs font-semibold"
              :class="!motionEnabled ? 'motion-option-active' : ''"
              :aria-pressed="!motionEnabled"
              @click="saveMotionPreference(false)"
            >
              Off
            </button>
          </div>
        </div>

        <div data-settings-row="appearance-static-6" data-keywords="gif cover covers animate animated hover" data-settings-bound v-show="settingsRowVisible('appearance-static-6')" class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Animate covers</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              {{ motionEnabled
                ? "When animated covers play. They always stay still while a game is running."
                : "Covers stay still while Animations are off." }}
            </p>
          </div>
          <div class="motion-toggle inline-flex rounded-lg p-1 shrink-0" :class="motionEnabled ? '' : 'opacity-50'" role="group" aria-label="Animate covers">
            <button
              v-for="option in coverAnimationOptions"
              :key="option.value"
              type="button"
              class="motion-option px-3 py-1.5 rounded-md text-xs font-semibold"
              :class="coverAnimation === option.value ? 'motion-option-active' : ''"
              :aria-pressed="coverAnimation === option.value"
              :disabled="!motionEnabled"
              @click="saveCoverAnimation(option.value)"
            >
              {{ option.label }}
            </button>
          </div>
        </div>
      </section>

      <!-- Wine / Proton -->
      <section v-show="sectionVisible('wine-proton')" id="wine-proton" class="settings-card rounded-xl p-6 space-y-5">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconAutomation class="ui-section-icon" />
          {{ supportsLinuxRuntimes ? "Wine / Proton" : platformCapabilities.platform === 'windows' ? "Windows Environment" : "Platform Environment" }}
        </h3>

        <div data-settings-row="wine-proton-static-0" data-settings-bound v-show="settingsRowVisible('wine-proton-static-0')"
          v-if="platformCapabilities.platform === 'windows'"
          class="setting-row p-4 rounded-lg text-sm"
          style="color: var(--text-secondary)"
        >
          Windows games launch directly. Proton, Wine, Winetricks, Wine
          prefixes, and Linux runtime installers are not used on Windows.
        </div>
        <div data-settings-row="wine-proton-static-1" data-settings-bound v-show="settingsRowVisible('wine-proton-static-1')"
          v-else-if="!supportsLinuxRuntimes"
          class="setting-row p-4 rounded-lg text-sm"
          style="color: var(--text-secondary)"
        >
          Wine, Proton, and Linux runtime installers are not supported on this platform.
        </div>

        <div data-settings-row="wine-proton-static-2" data-settings-bound v-show="settingsRowVisible('wine-proton-static-2')" v-if="supportsLinuxRuntimes">
          <div class="mb-1.5 flex justify-between items-center">
            <label
              for="settings-proton-path"
              class="block text-sm font-medium"
              style="color: var(--text-secondary)"
            >
              Proton / Wine Executable Path
            </label>
            <button
              @click="downloadProton"
              :disabled="downloadingProton"
              class="ui-action-btn text-xs font-medium disabled:opacity-50"
              style="color: var(--brand)"
            >
              <IconLoader2 v-if="downloadingProton" class="ui-action-icon animate-spin" />
              <IconDownloadFilled v-else class="ui-action-icon" />
              {{ downloadingProton ? "Downloading GE-Proton…" : "Auto Download Latest GE-Proton" }}
            </button>
          </div>
          <div class="flex gap-3">
            <input
              id="settings-proton-path"
              v-model="protonPath"
              @blur="protonSave.commit()"
              @keydown.enter.prevent="protonSave.commit()"
              :disabled="!!settingsLoadError || settingsLoading"
              data-settings-field="protonPath"
              list="runner-paths"
              aria-describedby="settings-proton-help"
              type="text"
              placeholder="/usr/bin/wine or /path/to/GE-Proton/proton"
              class="settings-input flex-1"
            />
            <FieldSaveFeedback :state="protonSave.state.value" :error="protonSave.error.value" />
            <datalist id="runner-paths">
              <option v-for="runner in availableRunners" :key="runner.path" :value="runner.path">{{ runner.name }}</option>
            </datalist>
            <button @click="browseProton" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn ui-action-btn">
              <IconFolderOpen class="ui-action-icon" />
              Browse
            </button>
          </div>
          <PathStatus :path="protonPath" kind="runner" />
          <div v-if="protonDownload" class="mt-3 space-y-1.5" role="status">
            <div class="flex justify-between text-xs" style="color: var(--text-secondary)">
              <span>{{ protonDownload.stage }}…</span>
              <span v-if="protonDownload.total" class="font-mono">
                {{ formatMegabytes(protonDownload.done) }} / {{ formatMegabytes(protonDownload.total) }}
              </span>
              <span v-else-if="protonDownload.done" class="font-mono">
                {{ formatMegabytes(protonDownload.done) }}
              </span>
            </div>
            <div class="w-full rounded-full h-1.5 overflow-hidden" style="background: var(--bg-raised)">
              <div
                class="h-1.5 rounded-full transition-all duration-300"
                :class="{ 'animate-pulse': !protonDownload.total }"
                style="background: var(--brand)"
                :style="{
                  width: protonDownload.total
                    ? (protonDownload.done / protonDownload.total) * 100 + '%'
                    : '100%',
                }"
              ></div>
            </div>
          </div>
          <p id="settings-proton-help" class="text-xs mt-2" style="color: var(--text-muted)">
            Leave empty for system Wine, or UMU-Proton if Wine is unavailable. GE-Proton (latest) may check for updates when launching.
          </p>
          <p v-if="protonError" class="copyable-feedback text-xs text-red-400 mt-1">
            {{ protonError }}
          </p>
        </div>

        <div data-settings-row="wine-proton-static-3" data-settings-bound v-show="settingsRowVisible('wine-proton-static-3')" v-if="supportsLinuxRuntimes">
          <label
            for="settings-prefix-path"
            class="block text-sm font-medium mb-1.5"
            style="color: var(--text-secondary)"
          >
            Default Wine Prefix Path (WINEPREFIX)
          </label>
          <div class="flex gap-3">
            <input
              id="settings-prefix-path"
              v-model="prefixPath"
              @blur="prefixSave.commit()"
              @keydown.enter.prevent="prefixSave.commit()"
              :disabled="!!settingsLoadError || settingsLoading"
              data-settings-field="prefixPath"
              aria-describedby="settings-prefix-help"
              type="text"
              placeholder="Auto-managed (~/.local/share/wLib/prefix) if left empty"
              class="settings-input flex-1"
            />
            <FieldSaveFeedback :state="prefixSave.state.value" :error="prefixSave.error.value" />
            <button @click="browsePrefix" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn ui-action-btn">
              <IconFolderOpen class="ui-action-icon" />
              Browse
            </button>
          </div>
          <PathStatus :path="prefixPath" kind="dir" />
          <p id="settings-prefix-help" class="text-xs mt-2" style="color: var(--text-muted)">
            The location where game dependencies and save files will be isolated.
          </p>
        </div>

        <div data-settings-row="wine-proton-static-4" data-settings-bound v-show="settingsRowVisible('wine-proton-static-4')" v-if="supportsLinuxRuntimes" class="setting-row p-4 rounded-lg space-y-3">
          <div class="flex flex-wrap gap-2">
            <button class="settings-btn" @click="openPrefixTool('winecfg')">Winecfg</button>
            <button class="settings-btn" @click="openPrefixTool('winetricks')">Winetricks</button>
          </div>
          <div class="flex items-center justify-between text-sm" :class="{ 'opacity-50': !performanceTools.gamemode }">
            <span>GameMode <span v-if="!performanceTools.gamemode" style="color: var(--text-muted)">(not installed)</span></span>
            <label class="relative inline-flex items-center shrink-0" :class="performanceTools.gamemode ? 'cursor-pointer' : 'cursor-not-allowed'">
              <input type="checkbox" role="switch" aria-label="GameMode" class="sr-only peer" :checked="gamemodeEnabled" :disabled="!performanceTools.gamemode" @change="savePerformanceTool('gamemode', $event)" />
              <div class="ui-toggle"></div>
            </label>
          </div>
          <div class="flex items-center justify-between text-sm" :class="{ 'opacity-50': !performanceTools.mangohud }">
            <span>MangoHud <span v-if="!performanceTools.mangohud" style="color: var(--text-muted)">(not installed)</span></span>
            <label class="relative inline-flex items-center shrink-0" :class="performanceTools.mangohud ? 'cursor-pointer' : 'cursor-not-allowed'">
              <input type="checkbox" role="switch" aria-label="MangoHud" class="sr-only peer" :checked="mangohudEnabled" :disabled="!performanceTools.mangohud" @change="savePerformanceTool('mangohud', $event)" />
              <div class="ui-toggle"></div>
            </label>
          </div>
        </div>

        <div data-settings-row="wine-proton-static-5" data-settings-bound v-show="settingsRowVisible('wine-proton-static-5')" v-if="platformCapabilities.rpgmaker_linux" class="setting-row p-4 rounded-lg">
          <div class="flex flex-col gap-3">
            <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h4 id="settings-rpgm-label" class="text-sm font-medium" style="color: var(--text-primary)">
                  RPGMaker Linux Runner
                </h4>
                <p id="settings-rpgm-help" class="text-xs mt-1" style="color: var(--text-muted)">
                  Optional native-port runner for RPG Maker, TyranoBuilder,
                  Godot, Construct, and Nscripter games. Install it manually
                  from the upstream project.
                </p>
              </div>
              <button
                type="button"
                @click="openRpgmakerLinuxRepo"
                class="settings-btn ui-action-btn shrink-0"
              >
                Open GitHub
              </button>
            </div>

            <div class="flex gap-3">
              <input
                v-model="rpgmakerLinuxRunnerPath"
              @blur="runnerSave.commit()"
              @keydown.enter.prevent="runnerSave.commit()"
              :disabled="!!settingsLoadError || settingsLoading"
              data-settings-field="rpgmakerLinuxRunnerPath"
                aria-labelledby="settings-rpgm-label"
                aria-describedby="settings-rpgm-help"
                type="text"
                placeholder="Auto-detect or choose /path/to/rpgmaker-linux"
                class="settings-input flex-1"
              />
            <FieldSaveFeedback :state="runnerSave.state.value" :error="runnerSave.error.value" />
              <button @click="browseRpgmakerLinuxRunner" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn ui-action-btn">
                <IconFolderOpen class="ui-action-icon" />
                Browse
              </button>
            </div>
            <PathStatus :path="rpgmakerLinuxRunnerPath" kind="runner" />

            <p
              class="copyable-feedback text-xs"
              :class="rpgmakerLinuxRunnerAvailable ? 'text-green-400' : 'text-yellow-400'"
            >
              {{ rpgmakerLinuxRunnerStatusText }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">
              wLib only launches the external tool. It does not run the
              upstream installer, updater, exporter, bug reporter, or patch
              commands.
            </p>
          </div>
        </div>
      </section>

      <UmuSettings v-show="sectionVisible('umu')" v-if="supportsLinuxRuntimes" />

      <SteamSettings v-show="sectionVisible('steam')" v-if="platformCapabilities.platform === 'linux'" />
      <CompanionToolsSettings v-show="sectionVisible('custom-tools')" v-if="platformCapabilities.companion_tools" />

      <SandboxSettings v-show="sectionVisible('sandbox')" />

      <CatalogSettings v-show="sectionVisible('catalog')" />

      <!-- Tools -->
      <section v-show="sectionVisible('tools')" id="tools" v-if="supportsLinuxRuntimes" class="settings-card rounded-xl p-6 space-y-4">
        <div>
          <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
            <IconTool class="ui-section-icon" />
            Tools
          </h3>
          <p class="text-xs mt-1" style="color: var(--text-secondary)">
            Fix common issues with certain game engines. Installs go into the default prefix above.
          </p>
        </div>

        <div data-settings-row="tools-static-0" data-settings-bound v-show="settingsRowVisible('tools-static-0')" class="flex flex-col gap-3">
          <!-- DLL Install Card -->
          <div class="tool-card p-4 rounded-lg relative overflow-hidden">
            <div
              class="absolute inset-y-0 left-0 w-1"
              :class="dllsInstalled ? 'bg-green-500' : ''"
              :style="!dllsInstalled ? 'background: var(--text-muted)' : ''"
            ></div>
            <div class="flex items-center justify-between gap-4">
              <div class="pl-3">
                <h5 class="text-sm font-medium" style="color: var(--text-primary)">
                  RPGMaker / Unity Fix (Winetricks)
                </h5>
                <p
                  v-if="!dllsInstalled && !installingDeps"
                  class="text-xs mt-1"
                  style="color: var(--text-muted)"
                >
                  Installs corefonts, d3d, quartz, wmp9, directshow. Fixes
                  video decoding black screens.
                </p>
                <p v-if="installingDeps" class="text-xs text-yellow-400 mt-1 font-mono">
                  Installing {{ depsProgress.current }} ({{ depsProgress.done }}/{{
                    depsProgress.total
                  }})... This may take 30+ minutes.
                </p>
                <p v-if="dllsInstalled && !installingDeps" class="text-xs text-green-400 mt-1 font-mono">
                  Installed
                </p>
              </div>
              <button
                @click="installDeps"
                :disabled="installingDeps || dllsInstalled"
                class="settings-btn ui-action-btn shrink-0 disabled:opacity-50"
              >
                <IconLoader2 v-if="installingDeps" class="ui-action-icon animate-spin" />
                {{ dllsInstalled ? "Installed" : installingDeps ? "Installing..." : "Install DLLs" }}
              </button>
            </div>
            <div v-if="installingDeps && depsProgress.total > 0" class="mt-3 ml-3">
              <div class="w-full rounded-full h-1.5 overflow-hidden" style="background: var(--bg-surface)">
                <div
                  class="h-1.5 rounded-full transition-all duration-500"
                  style="background: var(--brand)"
                  :style="{ width: (depsProgress.done / depsProgress.total) * 100 + '%' }"
                ></div>
              </div>
            </div>
          </div>

          <!-- RTP Install Card -->
          <div class="tool-card p-4 rounded-lg relative overflow-hidden">
            <div
              class="absolute inset-y-0 left-0 w-1"
              :class="rtpsInstalled ? 'bg-green-500' : ''"
              :style="!rtpsInstalled ? 'background: var(--text-muted)' : ''"
            ></div>
            <div class="flex items-center justify-between gap-4">
              <div class="pl-3">
                <h5 class="text-sm font-medium" style="color: var(--text-primary)">
                  RPGMaker RTP (VX Ace, VX, XP, 2003)
                </h5>
                <p
                  v-if="!rtpsInstalled && !installingRtps"
                  class="text-xs mt-1"
                  style="color: var(--text-muted)"
                >
                  Downloads and verifies the official RTP packages for VX
                  Ace, VX, XP, and 2003.
                </p>
                <p v-if="installingRtps" class="text-xs text-yellow-400 mt-1 font-mono">
                  Installing {{ rtpProgress.current }} ({{ rtpProgress.done }}/{{
                    rtpProgress.total
                  }})...
                </p>
                <p v-if="rtpsInstalled && !installingRtps" class="text-xs text-green-400 mt-1 font-mono">
                  Installed
                </p>
              </div>
              <button
                @click="installRtps"
                :disabled="installingRtps || rtpsInstalled"
                class="settings-btn ui-action-btn shrink-0 disabled:opacity-50"
              >
                <IconLoader2 v-if="installingRtps" class="ui-action-icon animate-spin" />
                {{ rtpsInstalled ? "Installed" : installingRtps ? "Processing..." : "Install RTPs" }}
              </button>
            </div>
            <div v-if="installingRtps && rtpProgress.total > 0" class="mt-3 ml-3">
              <div class="w-full rounded-full h-1.5 overflow-hidden" style="background: var(--bg-surface)">
                <div
                  class="h-1.5 rounded-full transition-all duration-500"
                  style="background: var(--brand)"
                  :style="{ width: (rtpProgress.done / rtpProgress.total) * 100 + '%' }"
                ></div>
              </div>
            </div>
          </div>

          <!-- Cheat Engine Card -->
          <div class="tool-card p-4 rounded-lg relative overflow-hidden">
            <div
              class="absolute inset-y-0 left-0 w-1"
              :class="ceInstalled ? 'bg-green-500' : ''"
              :style="!ceInstalled ? 'background: var(--text-muted)' : ''"
            ></div>
            <div class="flex items-center justify-between gap-4">
              <div class="pl-3">
                <h5 class="text-sm font-medium" style="color: var(--text-primary)">
                  Cheat Engine (Lunar Engine)
                </h5>
                <p v-if="!ceInstalled" class="text-xs mt-1" style="color: var(--text-muted)">
                  Provides the ability to auto-inject CE when launching
                  games to modify values.
                </p>
                <p v-else class="text-xs text-green-400 mt-1 font-mono">Installed</p>
              </div>
              <button
                @click="downloadCe"
                :disabled="installingCe || ceInstalled"
                class="settings-btn ui-action-btn shrink-0 disabled:opacity-50"
              >
                <IconLoader2 v-if="installingCe" class="ui-action-icon animate-spin" />
                {{ ceInstalled ? "Installed" : installingCe ? "Downloading..." : "Install CE" }}
              </button>
            </div>
          </div>
        </div>

        <div data-settings-row="tools-static-1" data-settings-bound v-show="settingsRowVisible('tools-static-1')" class="space-y-1">
          <p v-if="installError" class="copyable-feedback text-xs text-red-400">{{ installError }}</p>
          <p v-if="rtpError" class="copyable-feedback text-xs text-red-400">{{ rtpError }}</p>
          <p v-if="ceError" class="copyable-feedback text-xs text-red-400">{{ ceError }}</p>
        </div>
      </section>

      <!-- Ren'Py -->
      <section v-show="sectionVisible('renpy')" id="renpy" class="settings-card rounded-xl p-6 space-y-3">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconBook2 class="ui-section-icon" />
          Ren'Py
        </h3>
        <div data-settings-row="renpy-static-0" data-settings-bound v-show="settingsRowVisible('renpy-static-0')" class="space-y-3">
        <div class="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <label for="urm-source" class="block text-sm font-medium" style="color: var(--text-secondary)">
              Universal Ren'Py Mod (URM) source file (.rpa)
            </label>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Download URM from its official page on 0x52.dev.
            </p>
          </div>
          <button type="button" @click="openUrmPage" class="settings-btn ui-action-btn shrink-0">
            Open official page
          </button>
        </div>
        <div class="flex gap-3">
          <input
            id="urm-source"
            v-model="urmPath"
              @blur="urmSave.commit()"
              @keydown.enter.prevent="urmSave.commit()"
              :disabled="!!settingsLoadError || settingsLoading"
              data-settings-field="urmPath"
            aria-describedby="settings-urm-help"
            type="text"
            class="settings-input flex-1"
            placeholder="/path/to/0x52_URM.rpa"
          />
            <FieldSaveFeedback :state="urmSave.state.value" :error="urmSave.error.value" />
          <button @click="browseUrm" :disabled="!!settingsLoadError || settingsLoading" class="settings-btn ui-action-btn">
            <IconFolderOpen class="ui-action-icon" />Browse
          </button>
        </div>
        <PathStatus :path="urmPath" kind="file" />
        <p id="settings-urm-help" class="text-xs" style="color: var(--text-muted)">
          Select the .rpa file you downloaded, then enable URM in a Ren'Py game's details.
        </p>
        </div>
      </section>
        </fieldset>
      </div>
    </div>

    <footer class="mt-6">
      <RouterLink to="/diagnostics" class="inline-flex min-h-11 items-center underline underline-offset-4" style="color: var(--text-primary)">Diagnostics</RouterLink>
    </footer>


  </div>
</template>

<style scoped>
.settings-nav-item { color: var(--text-secondary); }
.settings-nav-item:hover, .settings-nav-active { color: var(--text-primary); background: var(--bg-raised); }
.settings-nav-active { box-shadow: inset 3px 0 var(--brand); }
.settings-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}

.accent-swatch {
  position: relative;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 9999px;
  border: 2px solid transparent;
  cursor: pointer;
  box-shadow: inset 0 0 0 1px var(--overlay-border);
}

.accent-swatch:focus-visible,
.accent-swatch:focus-within {
  outline: 2px solid var(--text-primary);
  outline-offset: 2px;
}

.accent-swatch-selected {
  border-color: var(--bg-surface);
  box-shadow: 0 0 0 2px var(--text-primary);
}

.accent-swatch-custom {
  background: conic-gradient(#f87171, #facc15, #4ade80, #22d3ee, #818cf8, #e879f9, #f87171);
  overflow: hidden;
}

.accent-color-input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}

.setting-row,
.motion-toggle {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}

kbd {
  font-family: ui-monospace, monospace;
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 0.3rem;
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
}

.settings-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.625rem 1rem;
  font-size: 0.875rem;
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
.settings-input::placeholder {
  color: var(--text-muted);
}
.settings-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.settings-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 600;
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}
.settings-btn:hover {
  background: var(--border-hover);
}

.motion-option {
  color: var(--text-secondary);
  transition: background-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}

.motion-option-active {
  background: var(--bg-surface);
  color: var(--text-primary);
  box-shadow: var(--shadow-card);
}


.tool-card {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}
</style>
