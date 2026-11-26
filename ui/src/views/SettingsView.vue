<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { useFieldAutosave } from "../composables/useFieldAutosave";
import { useGamescopeSettings } from "../composables/useGamescopeSettings";
import { useOnboarding } from "../composables/useOnboarding";
import { FILTER_OPTIONS, SCALER_OPTIONS, WINDOW_MODE_OPTIONS } from "../utils/gamescope";
import type { GamescopeFilter, GamescopeScaler, GamescopeWindowMode } from "../services/api";
import FieldSaveFeedback from "../components/FieldSaveFeedback.vue";
import SettingRow from "../components/settings/SettingRow.vue";
import SettingsGroup from "../components/settings/SettingsGroup.vue";
import SettingsHelpLink from "../components/settings/SettingsHelpLink.vue";
import RunningGamesReadiness from "../components/settings/RunningGamesReadiness.vue";
import { isRunnerPath, optionalToolsSummary, windowsReadiness, type OptionalTool, type ReadinessTarget, type UmuReadiness } from "../utils/settingsReadiness";
import { useSaveStatus } from "../composables/useSaveStatus";
import type { LcPlusMode, LcPlusStatus, SettingsPayload } from "../services/api";
import { lcPlusCheckedAt, lcPlusCheckReason, lcPlusDetectedText } from "../utils/lewdcorner";
import { mergeSettingsFields } from "../utils/settingsMerge";
import { confirmAction } from "../utils/confirm";
import { gamingMode, setTvPreference, tvModeActive, tvPreference } from "../utils/tvMode";
import { formatLibraryFreeSpace } from "../utils/installArchive";
import type { LibraryFolder } from "../services/api";
import { computed, reactive, ref, useTemplateRef, onMounted, onUnmounted, watch, nextTick, provide } from "vue";
import { onBeforeRouteLeave, useRoute } from "vue-router";
import { settingsVisibleRows } from "../utils/settingsVisibility";
import { matchSettings, type SettingsSearchSection } from "../utils/settingsSearch";
import { pickActiveSection } from "../utils/settingsNav";
import { useEventListener } from "../composables/useEventListener";
import { controllerFocusText, useControllerActions } from "../utils/controllerActions";
import { focusCandidates, pickNext } from "../utils/spatialFocus";
import {
  IconArrowDown,
  IconArrowUp,
  IconCircleCheck,
  IconDatabase,
  IconDownload,
  IconDownloadFilled,
  IconExternalLink,
  IconFolderOpen,
  IconLayout2,
  IconLoader2,
  IconPalette,
  IconPlayerPlay,
  IconSettings,
  IconTool,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import { gamesStore } from "../composables/useGamesStore";
const onboarding = useOnboarding();
import { errorReason, notify, notifyError } from "../utils/toast";
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
  RpgmakerRunnerInstallStatus,
  SaveBackupKeep,
  SettingsResponse,
  SystemDepsCommandResponse,
  RunnerInfo,
} from "../services/api";
import { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../services/api";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { getRpgmakerRunnerUiState } from "../utils/rpgmakerRunner";
import { validateCustomPlayStatuses } from "../utils/playStatus";
import PathStatus from "../components/PathStatus.vue";
import CompanionToolsSettings from "../components/CompanionToolsSettings.vue";
import SandboxSettings from "../components/SandboxSettings.vue";
import LocaleEmulatorSettings from "../components/LocaleEmulatorSettings.vue";
import UmuSettings from "../components/UmuSettings.vue";
import CatalogSettings from "../components/CatalogSettings.vue";
import SteamSettings from "../components/SteamSettings.vue";
import FileHostAccounts from "../components/FileHostAccounts.vue";
import TorrentSettings from "../components/TorrentSettings.vue";
import { MAX_ACTIVE_DOWNLOADS, clampLimitKib, limitInMegabytes } from "../utils/downloadQueue";
import type { SandboxStatus } from "../services/api";

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
const runnersLoaded = ref(false);
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
const lcSettings = [
  { key: "lc_enabled", label: "Use LewdCorner", description: "Show LewdCorner thread fields and fill in LewdCorner threads for your games from the catalog." },
] as const;
// Save one payload at once and show "Saving…", "Saved" or the error on the row's status.
const saveNow = (status: ReturnType<typeof useSaveStatus>, payload: SettingsPayload) => status.run(async () => {
  const result = await api.saveSettings(payload);
  if (!result || result.success === false) throw new Error(result?.error || "Unknown error");
});
const lcValues = reactive({ lc_enabled: false });
const lcSaving = reactive({ lc_enabled: false });
const lcStatus = { lc_enabled: useSaveStatus() };
const saveLcSetting = async (key: typeof lcSettings[number]["key"], event: Event) => {
  const previous = lcValues[key];
  lcValues[key] = (event.target as HTMLInputElement).checked;
  lcSaving[key] = true;
  if (await saveNow(lcStatus[key], { [key]: lcValues[key] })) {
    // Turning LC on can start an LC Plus check; read the new state back.
    void refreshLcPlus();
  } else {
    lcValues[key] = previous;
  }
  lcSaving[key] = false;
};
// "LC Plus": Auto, Yes or No. In Auto, the detected tier and "Check now".
const lcPlus = reactive<LcPlusStatus>({
  lc_plus: false, lc_plus_mode: "auto", lc_plus_detected: "", lc_plus_detected_at: "", lc_plus_check: "ready",
});
const lcPlusSaving = ref(false);
const lcPlusStatus = useSaveStatus();
const lcPlusChecking = ref(false);
const lcPlusMessage = ref("");
const lcPlusCheckDisabled = computed(() => lcPlusCheckReason(lcPlus.lc_plus_check, lcValues.lc_enabled));
const applyLcPlus = (data: Partial<LcPlusStatus>) => {
  if (data.lc_plus_mode) lcPlus.lc_plus_mode = data.lc_plus_mode;
  if (typeof data.lc_plus === "boolean") lcPlus.lc_plus = data.lc_plus;
  if (data.lc_plus_detected !== undefined) lcPlus.lc_plus_detected = data.lc_plus_detected;
  if (data.lc_plus_detected_at !== undefined) lcPlus.lc_plus_detected_at = data.lc_plus_detected_at;
  if (data.lc_plus_check) lcPlus.lc_plus_check = data.lc_plus_check;
};
const refreshLcPlus = async () => {
  try {
    const data = await api.getSettings();
    if (!lcPlusSaving.value) applyLcPlus(data);
  } catch {
    // The row keeps its last state; the next settings load reads it again.
  }
};
const saveLcPlusMode = async (event: Event) => {
  const previous = lcPlus.lc_plus_mode;
  const mode = (event.target as HTMLSelectElement).value as LcPlusMode;
  lcPlus.lc_plus_mode = mode;
  lcPlusMessage.value = "";
  lcPlusSaving.value = true;
  if (!(await saveNow(lcPlusStatus, { lc_plus_mode: mode }))) lcPlus.lc_plus_mode = previous;
  lcPlusSaving.value = false;
  void refreshLcPlus();
};
const checkLcPlus = async () => {
  lcPlusChecking.value = true;
  lcPlusMessage.value = "";
  try {
    const result = await api.checkLcPlus();
    applyLcPlus(result);
    if (!result.success) lcPlusMessage.value = result.error || "Could not check LC Plus.";
    else if (result.checked_without_result) lcPlusMessage.value = "LC Plus could not be checked. Set LC Plus to Yes or No if you know it.";
  } catch (error) {
    lcPlusMessage.value = `Could not check LC Plus: ${String(error)}`;
  } finally {
    lcPlusChecking.value = false;
  }
};
const libraryFolderPattern = ref("{creator}/{title}/{version}");
const downloadsFolder = ref("");
const downloadDetectionEnabled = ref(true);
const storageRecountAfterChanges = ref(false);
// The download queue settings; the Downloads page has the same four (DownloadQuickSettings.vue).
const downloadLimitKib = ref(0);
const downloadMaxActive = ref(2);
const downloadPauseWhilePlaying = ref(true);
const downloadPauseOnBattery = ref(false);
const enableLogging = ref(false);
const gamemodeEnabled = ref(false);
const mangohudEnabled = ref(false);
const performanceTools = ref({ gamemode: false, mangohud: false });
const performanceToolsLoaded = ref(false);
const gamescope = useGamescopeSettings();
const gamescopeInstalled = computed(() => gamescope.status.value?.installed === true);
const htmlBuiltinPlayer = ref(true);
const htmlPlayerStatus = useSaveStatus();
const saveHtmlBuiltinPlayer = async (event: Event) => {
  const value = (event.target as HTMLInputElement).checked;
  const previous = htmlBuiltinPlayer.value;
  htmlBuiltinPlayer.value = value;
  if (!(await saveNow(htmlPlayerStatus, { html_builtin_player: value }))) htmlBuiltinPlayer.value = previous;
};
const webPlayerDevtools = ref(true);
const devtoolsStatus = useSaveStatus();
const saveWebPlayerDevtools = async (event: Event) => {
  const value = (event.target as HTMLInputElement).checked;
  const previous = webPlayerDevtools.value;
  webPlayerDevtools.value = value;
  if (!(await saveNow(devtoolsStatus, { web_player_devtools: value }))) webPlayerDevtools.value = previous;
};
const performanceToolStatus = { gamemode: useSaveStatus(), mangohud: useSaveStatus() };
const savePerformanceTool = async (tool: "gamemode" | "mangohud", event: Event) => {
  const value = (event.target as HTMLInputElement).checked;
  const state = tool === "gamemode" ? gamemodeEnabled : mangohudEnabled;
  const previous = state.value;
  state.value = value;
  if (!(await saveNow(performanceToolStatus[tool], { [`${tool}_enabled`]: value }))) state.value = previous;
};
const openPrefixTool = async (tool: "winecfg" | "winetricks") => {
  try {
    const result = await api.openWinePrefixTool(tool, prefixPath.value, protonPath.value);
    if (!result.success) throw new Error(result.error || "Unknown error");
    notify(`Opening ${tool === "winecfg" ? "Winecfg" : "Winetricks"}…`, "success");
  } catch (error) { notifyError(errorReason(error), `Could not open ${tool === "winecfg" ? "Winecfg" : "Winetricks"}`); }
};
// Save backups to keep per game: a preset, or "custom" with its own number.
const KEEP_PRESETS = [1, 3, 5, 10, 20];
const keepChoice = ref("all");
const keepCustom = ref(10);
const savingKeep = ref(false);
const keepStatus = useSaveStatus();
const savingLogging = ref(false);
const loggingStatus = useSaveStatus();
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
  mkxpz: { installed: false, path: "" },
  installable: {
    supported: false,
    can_install: false,
    missing_tools: [],
    reason: "",
  },
};
const rpgmakerLinuxRunnerStatus = ref<RpgmakerLinuxRunnerStatus>({
  ...defaultRpgmakerLinuxRunnerStatus,
});
const rpgmakerRunnerInstallStatus = ref<RpgmakerRunnerInstallStatus>({
  success: true,
  running: false,
  target: "",
  stage: "",
  done: 0,
  total: 0,
  error: "",
});
const rpgmakerRunnerUi = computed(() =>
  getRpgmakerRunnerUiState(
    rpgmakerLinuxRunnerStatus.value,
    rpgmakerRunnerInstallStatus.value,
  ),
);
let rpgmakerRunnerPollTimer: ReturnType<typeof setTimeout> | null = null;
const stopRpgmakerRunnerPolling = () => {
  if (rpgmakerRunnerPollTimer) {
    clearTimeout(rpgmakerRunnerPollTimer);
    rpgmakerRunnerPollTimer = null;
  }
};
const refreshRpgmakerRunnerStatus = async () => {
  try {
    const data = await api.getSettings();
    if (!pageDisposed && data?.rpgmaker_linux_runner_status) {
      rpgmakerLinuxRunnerStatus.value = data.rpgmaker_linux_runner_status;
    }
  } catch (e) {
    console.error("Failed to refresh RPGMaker Linux runner status", e);
  }
};
const pollRpgmakerRunnerInstallStatus = async (wasRunning = false) => {
  if (pageDisposed) return;
  try {
    const status = await api.getRpgmakerRunnerInstallStatus();
    if (pageDisposed) return;
    if (status && status.success !== false) {
      rpgmakerRunnerInstallStatus.value = status;
      if (status.running) {
        stopRpgmakerRunnerPolling();
        rpgmakerRunnerPollTimer = setTimeout(() => {
          rpgmakerRunnerPollTimer = null;
          void pollRpgmakerRunnerInstallStatus(true);
        }, 1000);
        return;
      }
      if (wasRunning) {
        await refreshRpgmakerRunnerStatus();
      }
    }
  } catch (e) {
    if (!pageDisposed) {
      rpgmakerRunnerInstallStatus.value = {
        ...rpgmakerRunnerInstallStatus.value,
        running: false,
        stage: "",
        error: e instanceof Error ? e.message : String(e),
      };
    }
  }
};
const startRpgmakerRunnerInstall = async (target: "runner" | "mkxpz") => {
  if (rpgmakerRunnerInstallStatus.value.running) return;
  const initialStage =
    target === "runner"
      ? "Starting RPGMaker Linux runner installation"
      : "Starting mkxp-z installation";
  rpgmakerRunnerInstallStatus.value = {
    success: true,
    running: true,
    target,
    stage: initialStage,
    done: 0,
    total: 0,
    error: "",
  };
  try {
    const res =
      target === "runner"
        ? await api.installRpgmakerLinuxRunner()
        : await api.installRpgmakerMkxpz();
    if (!res || !res.success) {
      rpgmakerRunnerInstallStatus.value = {
        success: true,
        running: false,
        target,
        stage: "",
        done: 0,
        total: 0,
        error: res?.error || "Installation failed.",
      };
      return;
    }
    await pollRpgmakerRunnerInstallStatus(true);
  } catch (e) {
    rpgmakerRunnerInstallStatus.value = {
      success: true,
      running: false,
      target,
      stage: "",
      done: 0,
      total: 0,
      error: e instanceof Error ? e.message : String(e),
    };
  }
};
const rpgmakerLinuxRunnerAvailable = computed(
  () => rpgmakerLinuxRunnerStatus.value.available,
);
// Not installed and no path typed: the runner is optional, so no warning colour.
const rpgmakerRunnerOptional = computed(() => !rpgmakerLinuxRunnerStatus.value.available
  && !rpgmakerLinuxRunnerStatus.value.configured_path && !rpgmakerLinuxRunnerPath.value.trim());
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

// The setup summary at the top of Running games (Linux only). It reads the
// statuses this page and its groups already load; see utils/settingsReadiness.
const umuReadiness = ref<UmuReadiness | null>(null);
const umuSettings = useTemplateRef<{ refresh: () => Promise<void> }>("umuSettings");
const sandboxStatus = ref<SandboxStatus | null>(null);
const sandboxLoaded = ref(false);
const onSandboxStatus = (status: SandboxStatus | null) => { sandboxStatus.value = status; sandboxLoaded.value = true; };
// Whether the saved runner path exists. Named runners (Wine, GE-Proton, UMU-Proton) need no check.
const runnerPathOk = ref<boolean | null>(null);
let runnerPathRequest = 0;
const checkSavedRunnerPath = async (path: string) => {
  const request = ++runnerPathRequest;
  runnerPathOk.value = null;
  if (!isRunnerPath(path)) return;
  try {
    const result = await api.checkPath(path.trim(), "runner");
    if (request === runnerPathRequest) runnerPathOk.value = !!result?.ok;
  } catch {
    // Unknown: the summary keeps saying "checking", and the field shows its own status.
  }
};
watch(() => [settingsLoaded.value, savedValues.value.protonPath] as const, ([loaded, path]) => {
  if (loaded && supportsLinuxRuntimes.value) void checkSavedRunnerPath(path);
});
const windowsSetup = computed(() => windowsReadiness({
  runner: savedValues.value.protonPath,
  runners: runnersLoaded.value ? availableRunners.value : null,
  runnerPathOk: runnerPathOk.value,
  umu: umuReadiness.value,
}));
const optionalTools = computed(() => {
  const tools: OptionalTool[] = [
    { name: "GameMode", installed: performanceToolsLoaded.value ? performanceTools.value.gamemode : null },
    { name: "MangoHud", installed: performanceToolsLoaded.value ? performanceTools.value.mangohud : null },
    { name: "Gamescope", installed: gamescope.status.value ? gamescope.status.value.installed : null },
  ];
  if (platformCapabilities.value.rpgmaker_linux) {
    const known = settingsLoaded.value && !settingsLoadError.value;
    tools.push(
      { name: "RPG Maker runner", installed: known ? rpgmakerLinuxRunnerStatus.value.available : null },
      { name: "mkxp-z", installed: known ? rpgmakerRunnerUi.value.mkxpzInstalled : null },
    );
  }
  if (platformCapabilities.value.sandbox) tools.push({ name: "Sandbox tool", installed: sandboxLoaded.value ? sandboxStatus.value?.status === "ready" : null });
  return optionalToolsSummary(tools);
});
// Jump to the group that does the next step, and focus its main button or field.
const goToSetup = async (target: ReadinessTarget) => {
  scrollToSection("running-games", false, target);
  await nextTick();
  const control = target === "wine-proton"
    ? document.getElementById("settings-proton-path")
    : document.querySelector<HTMLElement>(`#${target} [data-readiness-target]`);
  control?.focus({ preventScroll: true });
};

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
  if (!(await saveNow(keepStatus, { save_backup_keep: value }))) {
    keepChoice.value = previous.choice;
    keepCustom.value = previous.custom;
  }
  savingKeep.value = false;
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
  for (const { key } of lcSettings) if (!lcSaving[key]) lcValues[key] = !!data[key];
  if (!lcPlusSaving.value) applyLcPlus(data);
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
  storageRecountAfterChanges.value = data.storage_recount_after_changes === true;
  downloadLimitKib.value = data.download_limit_kib ?? 0;
  downloadMaxActive.value = data.download_max_active ?? 2;
  downloadPauseWhilePlaying.value = data.download_pause_while_playing !== false;
  downloadPauseOnBattery.value = data.download_pause_on_battery === true;
  rpgmakerLinuxRunnerPath.value = data.rpgmaker_linux_runner_path || "";
  rpgmakerLinuxRunnerStatus.value =
    data.rpgmaker_linux_runner_status || defaultRpgmakerLinuxRunnerStatus;
  enableLogging.value = !!data.enable_logging;
  gamemodeEnabled.value = !!data.gamemode_enabled;
  mangohudEnabled.value = !!data.mangohud_enabled;
  gamescope.apply(data);
  htmlBuiltinPlayer.value = data.html_builtin_player !== false;
  webPlayerDevtools.value = data.web_player_devtools !== false;
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
  storageRecountAfterChanges: storageRecountAfterChanges.value,
  downloadLimitKib: downloadLimitKib.value,
  downloadMaxActive: downloadMaxActive.value,
  downloadPauseWhilePlaying: downloadPauseWhilePlaying.value,
  downloadPauseOnBattery: downloadPauseOnBattery.value,
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
  storageRecountAfterChanges.value = values.storageRecountAfterChanges;
  downloadLimitKib.value = values.downloadLimitKib;
  downloadMaxActive.value = values.downloadMaxActive;
  downloadPauseWhilePlaying.value = values.downloadPauseWhilePlaying;
  downloadPauseOnBattery.value = values.downloadPauseOnBattery;
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
  { keys: ["/", "Ctrl+F"], action: "Search the library or settings" },
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
  if (!(await saveNow(loggingStatus, { enable_logging: enabled }))) enableLogging.value = !enabled;
  savingLogging.value = false;
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
      performanceToolsLoaded.value = true;
      void gamescope.loadStatus();
      const runners = await api.getAvailableRunners();
      availableRunners.value = runners.runners || [];
      runnersLoaded.value = true;
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
    if (platformCapabilities.value.rpgmaker_linux && !pageDisposed) {
      await pollRpgmakerRunnerInstallStatus(false);
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
      void umuSettings.value?.refresh();
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
  stopRpgmakerRunnerPolling();
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
// The Steam Linux Runtime that umu needs follows the runner, so read the umu status again too.
const afterRunnerSave = async () => { await refreshInstall(); await umuSettings.value?.refresh(); };
const protonSave = makeField(protonPath, value => ({ proton_path: value }), ["protonPath"], values => values.protonPath, afterRunnerSave);
const prefixSave = makeField(prefixPath, value => ({ wine_prefix_path: value }), ["prefixPath"], values => values.prefixPath, refreshInstall);
const runnerSave = makeField(rpgmakerLinuxRunnerPath, value => ({ rpgmaker_linux_runner_path: value }), ["rpgmakerLinuxRunnerPath"], values => values.rpgmakerLinuxRunnerPath);
const urmSave = makeField(urmPath, value => ({ urm_rpa_path: value }), ["urmPath"], values => values.urmPath);
const foldersSave = makeField(libraryValue, value => ({ library_folder: value.folder, library_folders: value.folders }), ["libraryFolder", "libraryFolders"], values => ({ folder: values.libraryFolder, folders: values.libraryFolders }));
const patternSave = makeField(libraryFolderPattern, value => ({ library_folder_pattern: value }), ["libraryFolderPattern"], values => values.libraryFolderPattern);
const downloadsSave = makeField(downloadsFolder, value => ({ downloads_folder: value }), ["downloadsFolder"], values => values.downloadsFolder);
const statusesSave = makeField(customStatuses, value => ({ custom_play_statuses: value }), ["customStatuses"], values => values.customStatuses);
const detectionSave = makeField(downloadDetectionEnabled, value => ({ download_detection_enabled: value }), ["downloadDetectionEnabled"], values => values.downloadDetectionEnabled);
const storageRecountSave = makeField(storageRecountAfterChanges, value => ({ storage_recount_after_changes: value }), ["storageRecountAfterChanges"], values => values.storageRecountAfterChanges);
const limitSave = makeField(downloadLimitKib, value => ({ download_limit_kib: clampLimitKib(value) }), ["downloadLimitKib"], values => values.downloadLimitKib);
const slotsSave = makeField(downloadMaxActive, value => ({ download_max_active: value }), ["downloadMaxActive"], values => values.downloadMaxActive);
const playingSave = makeField(downloadPauseWhilePlaying, value => ({ download_pause_while_playing: value }), ["downloadPauseWhilePlaying"], values => values.downloadPauseWhilePlaying);
const batterySave = makeField(downloadPauseOnBattery, value => ({ download_pause_on_battery: value }), ["downloadPauseOnBattery"], values => values.downloadPauseOnBattery);
const autosaves = [protonSave, prefixSave, runnerSave, urmSave, foldersSave, patternSave, downloadsSave, statusesSave, detectionSave, storageRecountSave, limitSave, slotsSave, playingSave, batterySave];
const resetAutosaveBaselines = () => {
  protonSave.resetSaved(protonPath.value); prefixSave.resetSaved(prefixPath.value);
  runnerSave.resetSaved(rpgmakerLinuxRunnerPath.value); urmSave.resetSaved(urmPath.value);
  foldersSave.resetSaved(libraryValue.value); patternSave.resetSaved(libraryFolderPattern.value);
  downloadsSave.resetSaved(downloadsFolder.value); statusesSave.resetSaved(customStatuses.value);
  detectionSave.resetSaved(downloadDetectionEnabled.value);
  storageRecountSave.resetSaved(storageRecountAfterChanges.value);
  limitSave.resetSaved(downloadLimitKib.value); slotsSave.resetSaved(downloadMaxActive.value);
  playingSave.resetSaved(downloadPauseWhilePlaying.value); batterySave.resetSaved(downloadPauseOnBattery.value);
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
    await gamesStore.refresh();
    const count = gamesStore.games.value.filter((game) => game.play_status?.toLowerCase() === name.toLowerCase()).length;
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
    if (!(await gamesStore.refresh())) throw new Error("Library could not be loaded");
    count = gamesStore.games.value.filter((game) =>
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

const settingsRoot = useTemplateRef<HTMLElement>("settingsRoot");
const desktopSearch = useTemplateRef<HTMLInputElement>("desktopSearch");
const mobileSearch = useTemplateRef<HTMLInputElement>("mobileSearch");
// The search and section bar that stays at the top of narrow windows.
const narrowBar = useTemplateRef<HTMLElement>("narrowBar");
const narrowBarHeight = () => (narrowBar.value?.getClientRects().length ? narrowBar.value.getBoundingClientRect().height : 0);
const searchQuery = ref("");
const visibleRows = ref<Set<string> | null>(null);
provide(settingsVisibleRows, visibleRows);
const settingsRowVisible = (id: string) => !visibleRows.value || visibleRows.value.has(id);
const activeSection = ref("library");
const shownSectionIds = ref<string[]>([]);
const route = useRoute();
// The top-level sections, in page order. Smaller groups inside a section
// (SettingsGroup) keep the ids that were sections before, so old links such
// as /settings#sandbox still open the right place.
const sectionRegistry = computed(() => [
  { id: "library", title: "Library" },
  { id: "downloads", title: "Downloads" },
  { id: "running-games", title: "Running games" },
  { id: "sources", title: "Sources" },
  { id: "appearance", title: "Appearance" },
  ...(platformCapabilities.value.platform === "linux" ? [{ id: "steam", title: "Steam" }] : []),
  { id: "advanced", title: "Advanced" },
]);
// Old section ids whose rows now live in a section with another id.
const SECTION_ALIASES: Record<string, string> = { general: "library", "library-downloads": "downloads" };
const shownSections = computed(() => sectionRegistry.value.filter(section => shownSectionIds.value.includes(section.id)));
const sectionVisible = (id: string) => !searchQuery.value.trim() || shownSectionIds.value.includes(id);
// Scroll a section, or a group or row inside it, to the top of <main>. A
// target inside a section stops under the section's sticky heading.
const scrollToSection = (id: string, focus = false, targetId = id) => {
  const section = document.getElementById(id);
  const target = targetId === id ? section : document.getElementById(targetId);
  const main = settingsRoot.value?.closest<HTMLElement>("main");
  if (!section || !target || !main || !shownSectionIds.value.includes(id)) return;
  // -1px: the target's own top line hides under the heading's bottom line.
  const heading = target === section ? 0 : Math.max(0, (section.firstElementChild?.getBoundingClientRect().height || 0) - 1);
  const top = main.scrollTop + target.getBoundingClientRect().top - main.getBoundingClientRect().top - heading - narrowBarHeight();
  main.scrollTo({ top, behavior: "instant" });
  // Short sections at the end cannot scroll to the top. Keep the chosen
  // section current until a scroll moves the page away from this jump.
  jumpTop = main.scrollTop;
  activeSection.value = id;
  // The quiet Help links are not where a jump should land.
  if (focus) focusCandidates(target).find(el => !el.hasAttribute("data-settings-help"))?.focus({ preventScroll: true });
};
// Find the section that holds a deep-link id: a section, an old section id,
// or a group or row inside a section.
const resolveSettingsHash = (id: string): { section: string; target: string } | null => {
  if (!id) return null;
  const alias = SECTION_ALIASES[id];
  if (alias) return shownSectionIds.value.includes(alias) ? { section: alias, target: alias } : null;
  if (shownSectionIds.value.includes(id)) return { section: id, target: id };
  const element = document.getElementById(id);
  if (!element || !settingsRoot.value?.contains(element)) return null;
  const section = shownSections.value.find(item => document.getElementById(item.id)?.contains(element));
  return section ? { section: section.id, target: id } : null;
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
// Where the last jump to a section left <main>. While <main> stays there the
// jumped-to section stays current; any other scroll (wheel, keys, touch, the
// scrollbar, controller, focus moves) clears it. Only the jump itself sets it,
// so there is no timer.
let jumpTop: number | null = null;
let scrollFrame = 0;
let narrowBarObserver: ResizeObserver | null = null;
// Template refs are gone by onUnmounted; keep the scroller to remove its listener.
let scroller: HTMLElement | null = null;
let searchScheduled = false;
const originalDisplay = new WeakMap<HTMLElement, string>();
const refreshSearch = () => {
  const root = settingsRoot.value;
  if (!root) return;
  const sections: SettingsSearchSection[] = [];
  const elements = new Map<string, HTMLElement>();
  // Each group's rows, so a group with no matching row hides with its heading.
  const groups = new Map<HTMLElement, string[]>();
  const addRow = (child: HTMLElement, fallbackId: string, rows: SettingsSearchSection["rows"], extraKeywords = "") => {
    const id = child.dataset.settingsRow || fallbackId;
    child.dataset.settingsRow = id;
    if (!child.dataset.keywords) child.dataset.keywords = "";
    if (!originalDisplay.has(child)) originalDisplay.set(child, child.style.display);
    elements.set(id, child);
    rows.push({ id, label: child.textContent || "", keywords: `${child.dataset.keywords} ${extraKeywords}` });
    return id;
  };
  for (const section of sectionRegistry.value) {
    const container = root.querySelector<HTMLElement>(`#${section.id}`);
    if (!container) continue;
    const rows: SettingsSearchSection["rows"] = [];
    let index = 0;
    for (const child of Array.from(container.children)) {
      if (!(child instanceof HTMLElement)) continue;
      if (child.hasAttribute("data-settings-group")) {
        // The group title and its data-keywords count as keywords of every row in it.
        const keywords = `${child.querySelector("[data-settings-subheading] h4")?.textContent || ""} ${child.dataset.keywords || ""}`;
        const ids: string[] = [];
        let groupIndex = 0;
        for (const row of Array.from(child.children)) {
          if (!(row instanceof HTMLElement) || row.hasAttribute("data-settings-subheading")) continue;
          ids.push(addRow(row, `${child.id}-dynamic-${groupIndex++}`, rows, keywords));
        }
        groups.set(child, ids);
        continue;
      }
      if (child.matches("h3") || child.querySelector("h3")) continue;
      addRow(child, `${section.id}-dynamic-${index++}`, rows);
    }
    sections.push({ id: section.id, title: section.title, rows });
  }
  const matched = matchSettings(sections, searchQuery.value);
  shownSectionIds.value = matched.sectionIds;
  visibleRows.value = new Set(matched.rowIds);
  for (const [id, element] of elements) {
    if (!element.hasAttribute("data-settings-bound")) element.style.display = matched.rowIds.includes(id) ? originalDisplay.get(element) || "" : "none";
  }
  for (const [group, ids] of groups) {
    if (!originalDisplay.has(group)) originalDisplay.set(group, group.style.display);
    group.style.display = ids.some(id => matched.rowIds.includes(id)) ? originalDisplay.get(group) || "" : "none";
  }
  // A match inside a closed "What it installs" disclosure opens it, and closes it again when the search ends.
  const term = searchQuery.value.trim().toLocaleLowerCase();
  for (const details of Array.from(root.querySelectorAll<HTMLDetailsElement>("details[data-settings-disclosure]"))) {
    if (term && (details.textContent || "").toLocaleLowerCase().includes(term)) {
      if (!details.open) { details.open = true; details.dataset.searchOpened = ""; }
    } else if (details.hasAttribute("data-search-opened")) {
      details.open = false;
      delete details.dataset.searchOpened;
    }
  }
  if (!shownSectionIds.value.includes(activeSection.value)) activeSection.value = shownSectionIds.value[0] || "";
  void nextTick(updateActiveSection);
};
const scheduleSearch = () => {
  if (searchScheduled) return;
  searchScheduled = true;
  void nextTick(() => { searchScheduled = false; refreshSearch(); });
};
// Mark the section at the top of <main> (under the narrow bar) as current.
const updateActiveSection = () => {
  scrollFrame = 0;
  const main = settingsRoot.value?.closest<HTMLElement>("main");
  if (!main) return;
  if (jumpTop !== null) {
    if (Math.abs(main.scrollTop - jumpTop) <= 2) return;
    jumpTop = null;
  }
  const probe = main.getBoundingClientRect().top + narrowBarHeight() + 20;
  const tops = shownSections.value
    .map(item => document.getElementById(item.id))
    .filter((el): el is HTMLElement => !!el && el.getClientRects().length > 0)
    .map(el => ({ id: el.id, top: el.getBoundingClientRect().top }));
  const id = pickActiveSection(tops, probe, main);
  if (id) activeSection.value = id;
};
const onMainScroll = () => {
  if (!scrollFrame) scrollFrame = requestAnimationFrame(updateActiveSection);
};
const followSettingsHash = async () => {
  await nextTick();
  refreshSearch();
  const found = resolveSettingsHash(route.hash.slice(1));
  if (found) { scrollToSection(found.section, false, found.target); pinSection(found.section, found.target); }
  else settingsRoot.value?.closest<HTMLElement>("main")?.scrollTo({ top: 0, behavior: "instant" });
};
// Sections above a deep-link target grow or shrink as settings and child
// sections finish loading. Keep the target at the top until the user scrolls.
let unpinSection: (() => void) | null = null;
const pinSection = (id: string, targetId = id) => {
  unpinSection?.();
  const root = settingsRoot.value;
  const main = root?.closest<HTMLElement>("main");
  if (!root || !main || typeof ResizeObserver === "undefined") return;
  let pinnedTop = main.scrollTop;
  const resize = new ResizeObserver(() => { scrollToSection(id, false, targetId); pinnedTop = main.scrollTop; });
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
// A new search shows other sections: mark the one at the top again.
watch(searchQuery, () => { jumpTop = null; scheduleSearch(); });
watch(sectionRegistry, () => { scheduleSearch(); void followSettingsHash(); });
watch(() => route.hash, () => { void followSettingsHash(); });
// "/" and Ctrl+F search the library on the Library page; here they search settings.
useEventListener(document, "keydown", event => {
  if (event.defaultPrevented || event.altKey || document.querySelector('[aria-modal="true"]')) return;
  const typing = event.target instanceof Element && event.target.closest("input, textarea, select, [contenteditable]:not([contenteditable='false'])");
  const find = (event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === "f";
  if (!find && !(event.key === "/" && !typing && !event.ctrlKey && !event.metaKey)) return;
  const input = [desktopSearch.value, mobileSearch.value].find(item => item?.getClientRects().length);
  if (!input) return;
  event.preventDefault();
  input.focus();
  input.select();
});
onMounted(() => {
  searchObserver = new MutationObserver(scheduleSearch);
  if (settingsRoot.value) searchObserver.observe(settingsRoot.value, { childList: true, subtree: true });
  scroller = settingsRoot.value?.closest("main") || null;
  scroller?.addEventListener("scroll", onMainScroll, { passive: true });
  // Section headings stick under the narrow bar. Its height follows the
  // interface scale and text size, so measure it.
  if (narrowBar.value && typeof ResizeObserver !== "undefined") {
    narrowBarObserver = new ResizeObserver(() => settingsRoot.value?.style.setProperty("--settings-sticky-top", `${narrowBarHeight()}px`));
    narrowBarObserver.observe(narrowBar.value);
  }
  scheduleSearch();
  void followSettingsHash();
});
onUnmounted(() => {
  searchObserver?.disconnect();
  narrowBarObserver?.disconnect();
  scroller?.removeEventListener("scroll", onMainScroll);
  if (scrollFrame) cancelAnimationFrame(scrollFrame);
  unpinSection?.();
});

const browseUrm = async () => {
  const path = await api.browseUrmFile(urmPath.value);
  if (path) { urmPath.value = path; void urmSave.commit(); }
};
</script>

<template>
  <div ref="settingsRoot" class="settings-page allow-text-selection mx-auto w-full p-8 max-w-7xl pb-12">
    <header class="mb-8">
      <h2
        class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
        style="color: var(--text-primary)"
      >
        <IconSettings class="ui-page-heading-icon" />
        <span>Settings</span>
      </h2>
      <p class="text-sm" style="color: var(--text-secondary)">
        How wLib finds, downloads and runs your games, and how it looks. Changes save automatically.
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
        class="ui-card-btn shrink-0"
        :disabled="settingsLoading"
        @click="loadSettings"
      >
        {{ settingsLoading ? "Retrying…" : "Retry" }}
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
        <div ref="narrowBar" class="settings-narrow-bar lg:hidden flex flex-col gap-2 sm:flex-row" role="search" aria-label="Settings search and sections">
          <label class="sr-only" for="settings-search-mobile">Search settings</label>
          <input id="settings-search-mobile" ref="mobileSearch" v-model="searchQuery" type="search" class="settings-input w-full min-w-0 sm:flex-1" placeholder="Search settings" />
          <label class="sr-only" for="settings-section-select">Section</label>
          <select id="settings-section-select" class="settings-input w-full sm:w-52 sm:shrink-0" :value="activeSection" @change="scrollToSection(($event.target as HTMLSelectElement).value)">
            <option v-for="section in shownSections" :key="section.id" :value="section.id">{{ section.title }}</option>
          </select>
        </div>
        <div v-if="!shownSections.length && searchQuery.trim()" class="settings-card rounded-xl p-6 mb-6" role="status">No settings match. <button type="button" class="ui-card-btn ui-card-btn--link" @click="searchQuery = ''">Clear search</button></div>
        <fieldset :disabled="!!settingsLoadError || settingsLoading" class="settings-sections space-y-6 w-full min-w-0 border-0 p-0">
      <section v-show="sectionVisible('library')" id="library" class="settings-card rounded-xl p-6 space-y-5">
        <div class="settings-section-head">
          <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
            <IconLayout2 class="ui-section-icon" />
            Library
          </h3>
          <SettingsHelpLink page="Getting-Started" anchor="1-set-your-folders" />
        </div>

        <SettingRow data-settings-row="library-downloads-static-1" data-settings-bound v-show="settingsRowVisible('library-downloads-static-1')"
          layout="stacked" label="Library folders"
          help="Choose the folders available for archive installs. New installs preselect the default."
          :save-state="foldersSave.state.value" :save-error="foldersSave.error.value">
          <div class="space-y-3">
            <div v-for="path in libraryFolders" :key="path" class="space-y-2 rounded-lg p-3" style="border: 1px solid var(--border)">
              <div class="flex items-center gap-3">
                <span class="font-mono text-sm break-all flex-1" :aria-label="path">{{ path }}</span>
                <span v-if="path === libraryFolder" class="text-xs" style="color: var(--text-secondary)">Default</span>
                <button v-else @click="libraryFolder = path; foldersSave.commit()" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn">Make default</button>
                <button @click="removeLibraryFolder(path)" :disabled="!!settingsLoadError || settingsLoading || (path === libraryFolder && libraryFolders.length > 1)" :title="path === libraryFolder && libraryFolders.length > 1 ? 'Choose another default first' : 'Remove from list'" class="ui-card-btn ui-card-btn--danger">Remove</button>
              </div>
              <div class="flex gap-3 text-xs" style="color: var(--text-muted)">
                <PathStatus :path="path" kind="dir" />
                <span>{{ folderFreeSpace(path) }}</span>
              </div>
            </div>
            <p v-if="libraryFolders.length > 1" class="text-xs" style="color: var(--text-muted)">Choose another default before removing the default folder.</p>
            <button @click="browseLibraryFolder" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn">
              <IconFolderOpen class="w-3.5 h-3.5" aria-hidden="true" />Add library folder
            </button>
          </div>
        </SettingRow>

        <SettingRow data-settings-row="library-downloads-static-2" data-settings-bound v-show="settingsRowVisible('library-downloads-static-2')"
          layout="stacked" label="Folder pattern" control-id="settings-folder-pattern"
          :save-state="patternSave.state.value" :save-error="patternSave.error.value">
          <template #help>
            Tokens: <code>{creator}</code> <code>{title}</code> <code>{version}</code> <code>{engine}</code>.
            Empty parts are skipped.
          </template>
          <input
            id="settings-folder-pattern"
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
          <p class="text-xs font-mono break-all mt-2" style="color: var(--text-muted)">
            Example: {{ patternExample }}
          </p>
        </SettingRow>

        <SettingRow data-settings-row="general-static-2" data-settings-bound v-show="settingsRowVisible('general-static-2')"
          layout="stacked" label="Play statuses" control-id="settings-new-status"
          help="Add custom statuses alongside the built-in choices."
          :save-state="statusesSave.state.value" :save-error="statusesSave.error.value">
          <div class="space-y-3">
            <div class="flex gap-3">
              <input
                id="settings-new-status"
                v-model="newStatus"
                @input="statusError = ''"
                :disabled="!!settingsLoadError || settingsLoading"
                @keydown.enter.prevent="addStatus"
                type="text"
                class="settings-input flex-1"
                placeholder="Custom status name"
                aria-label="Custom status name"
              />
              <button @click="addStatus" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn">Add status</button>
            </div>
            <p v-if="statusError" role="alert" class="save-status-error">{{ statusError }}</p>
            <div
              v-for="name in customStatuses"
              :key="name"
              class="ui-card-row flex items-center justify-between gap-3"
            >
              <span class="text-sm" style="color: var(--text-primary)">{{ name }}</span>
              <button @click="removeStatus(name)" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn ui-card-btn--danger" :aria-label="`Remove ${name}`">
                Remove
              </button>
            </div>
          </div>
        </SettingRow>

        <SettingRow data-settings-row="general-static-3" data-settings-bound v-show="settingsRowVisible('general-static-3')"
          layout="stacked" label="Collections" control-id="settings-new-collection"
          help="Your own groups of games, shown as tabs above the library. Changes apply right away.">
          <div class="space-y-3">
            <div class="flex gap-3">
              <input
                id="settings-new-collection"
                v-model="newCollectionName"
                @keydown.enter.prevent="addCollection"
                type="text"
                class="settings-input flex-1"
                placeholder="Collection name"
                aria-label="New collection name"
              />
              <button @click="addCollection" class="ui-card-btn">Add collection</button>
            </div>
            <p v-if="collectionError" role="alert" class="save-status-error">{{ collectionError }}</p>
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
              class="ui-card-row flex items-center justify-between gap-3"
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
                <button type="submit" class="ui-card-btn ui-card-btn--primary" title="Save the new name">Save</button>
                <button type="button" class="ui-card-btn" @click="cancelCollectionRename">Cancel</button>
              </form>
              <template v-else>
                <span class="text-sm truncate" style="color: var(--text-primary)">{{ name }}</span>
                <div class="flex items-center gap-1.5 shrink-0">
                  <button
                    class="ui-card-btn"
                    :disabled="index === 0"
                    :aria-label="`Move ${name} up`"
                    title="Move up"
                    @click="moveCollection(name, -1)"
                  >
                    <IconArrowUp class="w-3.5 h-3.5" aria-hidden="true" />
                  </button>
                  <button
                    class="ui-card-btn"
                    :disabled="index === collectionList.length - 1"
                    :aria-label="`Move ${name} down`"
                    title="Move down"
                    @click="moveCollection(name, 1)"
                  >
                    <IconArrowDown class="w-3.5 h-3.5" aria-hidden="true" />
                  </button>
                  <button class="ui-card-btn" @click="startCollectionRename(name)">Rename</button>
                  <button class="ui-card-btn ui-card-btn--danger" :aria-label="`Remove ${name}`" @click="removeCollection(name)">
                    Remove
                  </button>
                </div>
              </template>
            </div>
          </div>
        </SettingRow>

        <SettingRow data-settings-row="general-static-1" data-settings-bound v-show="settingsRowVisible('general-static-1')"
          label="Save backups to keep per game" control-id="save-backup-keep"
          help="Older backups are deleted after a new one is made. Changes apply right away."
          :save-state="keepStatus.state.value" :save-error="keepStatus.error.value">
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
        </SettingRow>

        <SettingRow
          data-settings-row="library-downloads-storage-recount"
          data-settings-bound
          data-keywords="storage recount disk size install update move battery"
          v-show="settingsRowVisible('library-downloads-storage-recount')"
          label="Count a game's size again after install, update or move" control-id="settings-storage-recount"
          help="Recounts only that game in the background at low priority when a storage report exists. Skipped on battery power and stored on this device only."
          :save-state="storageRecountSave.state.value" :save-error="storageRecountSave.error.value"
        >
          <label class="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              id="settings-storage-recount"
              type="checkbox"
              role="switch"
              v-model="storageRecountAfterChanges"
              @change="storageRecountSave.commit()"
              :disabled="!!settingsLoadError || settingsLoading"
              class="sr-only peer"
              aria-label="Count a game's size again after install, update or move"
            />
            <div class="ui-toggle"></div>
          </label>
        </SettingRow>
      </section>

      <section v-show="sectionVisible('downloads')" id="downloads" class="settings-card rounded-xl p-6 space-y-5">
        <div class="settings-section-head">
          <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
            <IconDownload class="ui-section-icon" />
            Downloads
          </h3>
          <SettingsHelpLink page="Updates-and-Downloads" />
        </div>

        <SettingRow data-settings-row="library-downloads-static-3" data-settings-bound v-show="settingsRowVisible('library-downloads-static-3')"
          layout="stacked" label="Downloads folder" control-id="settings-downloads-folder"
          help="wLib looks here for archives matching games with pending updates."
          :save-state="downloadsSave.state.value" :save-error="downloadsSave.error.value">
          <div class="flex gap-3">
            <input
              id="settings-downloads-folder"
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
            <button @click="browseDownloadsFolder" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn">
              <IconFolderOpen class="w-3.5 h-3.5" aria-hidden="true" />Browse
            </button>
          </div>
          <PathStatus :path="downloadsFolder" kind="dir" />
        </SettingRow>

        <SettingRow data-settings-row="library-downloads-static-4" data-settings-bound v-show="settingsRowVisible('library-downloads-static-4')"
          label="Detect downloaded updates" control-id="settings-detect-downloads"
          help="Offer to install matching archives when the app starts, after update checks, and when the window regains focus."
          :save-state="detectionSave.state.value" :save-error="detectionSave.error.value">
          <label class="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              id="settings-detect-downloads"
              type="checkbox"
              role="switch"
              v-model="downloadDetectionEnabled"
              @change="detectionSave.commit()"
              :disabled="!!settingsLoadError || settingsLoading"
              class="sr-only peer"
              aria-label="Detect downloaded updates"
            />
            <div class="ui-toggle"></div>
          </label>
        </SettingRow>

        <SettingRow data-settings-row="library-downloads-queue" data-keywords="download queue speed limit bandwidth slots same time pause playing game battery" data-settings-bound v-show="settingsRowVisible('library-downloads-queue')" data-testid="settings-download-queue"
          layout="stacked" label="In-app downloads"
          help="For file hosts and torrents together. The Downloads page shows the same settings. This device only.">
          <div class="space-y-3">
            <div class="grid gap-3 sm:grid-cols-2">
              <div class="space-y-1">
                <div class="flex items-center justify-between gap-2">
                  <label for="settings-download-limit" class="settings-field-label">Speed limit (KiB/s, 0 for no limit)</label>
                  <FieldSaveFeedback v-if="limitSave.state.value !== 'error'" :state="limitSave.state.value" :error="limitSave.error.value" />
                </div>
                <input id="settings-download-limit" v-model.number="downloadLimitKib" type="number" min="0" step="64" class="settings-input w-full" aria-describedby="settings-download-limit-mb" :disabled="!!settingsLoadError || settingsLoading" @change="downloadLimitKib = clampLimitKib(downloadLimitKib); limitSave.commit()" />
                <p id="settings-download-limit-mb" class="text-xs" style="color: var(--text-muted)" data-testid="settings-download-limit-mb">{{ limitInMegabytes(downloadLimitKib) }}. 1024 KiB/s is 1 MB/s.</p>
                <FieldSaveFeedback v-if="limitSave.state.value === 'error'" :state="limitSave.state.value" :error="limitSave.error.value" />
              </div>
              <div class="space-y-1">
                <div class="flex items-center justify-between gap-2">
                  <label for="settings-download-slots" class="settings-field-label">Downloads at the same time</label>
                  <FieldSaveFeedback v-if="slotsSave.state.value !== 'error'" :state="slotsSave.state.value" :error="slotsSave.error.value" />
                </div>
                <select id="settings-download-slots" v-model.number="downloadMaxActive" class="settings-input w-full" :disabled="!!settingsLoadError || settingsLoading" @change="slotsSave.commit()">
                  <option v-for="count in MAX_ACTIVE_DOWNLOADS" :key="count" :value="count">{{ count }}</option>
                </select>
                <FieldSaveFeedback v-if="slotsSave.state.value === 'error'" :state="slotsSave.state.value" :error="slotsSave.error.value" />
              </div>
            </div>
            <div class="space-y-1">
              <label class="flex items-center justify-between gap-3 cursor-pointer">
                <span class="text-sm" style="color: var(--text-primary)">Pause downloads while a game runs</span>
                <span class="flex items-center gap-3">
                  <FieldSaveFeedback v-if="playingSave.state.value !== 'error'" class="save-status-slot" :state="playingSave.state.value" :error="playingSave.error.value" />
                  <span class="relative inline-flex">
                    <input v-model="downloadPauseWhilePlaying" type="checkbox" role="switch" class="sr-only peer" aria-label="Pause downloads while a game runs" :disabled="!!settingsLoadError || settingsLoading" @change="playingSave.commit()" />
                    <span class="ui-toggle"></span>
                  </span>
                </span>
              </label>
              <FieldSaveFeedback v-if="playingSave.state.value === 'error'" :state="playingSave.state.value" :error="playingSave.error.value" />
            </div>
            <div class="space-y-1">
              <label class="flex items-center justify-between gap-3 cursor-pointer">
                <span class="text-sm" style="color: var(--text-primary)">Pause downloads on battery power</span>
                <span class="flex items-center gap-3">
                  <FieldSaveFeedback v-if="batterySave.state.value !== 'error'" class="save-status-slot" :state="batterySave.state.value" :error="batterySave.error.value" />
                  <span class="relative inline-flex">
                    <input v-model="downloadPauseOnBattery" type="checkbox" role="switch" class="sr-only peer" aria-label="Pause downloads on battery power" :disabled="!!settingsLoadError || settingsLoading" @change="batterySave.commit()" />
                    <span class="ui-toggle"></span>
                  </span>
                </span>
              </label>
              <FieldSaveFeedback v-if="batterySave.state.value === 'error'" :state="batterySave.state.value" :error="batterySave.error.value" />
            </div>
          </div>
        </SettingRow>

        <FileHostAccounts />

        <TorrentSettings />
      </section>

      <section v-show="sectionVisible('running-games')" id="running-games" class="settings-card rounded-xl p-6 space-y-5">
        <div class="settings-section-head">
          <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
            <IconPlayerPlay class="ui-section-icon" />
            Running games
          </h3>
          <SettingsHelpLink page="Launching-Games" />
        </div>

        <RunningGamesReadiness v-if="supportsLinuxRuntimes" data-settings-row="running-games-readiness" data-settings-bound
          data-keywords="setup ready required optional status what do i need umu wine proton"
          v-show="settingsRowVisible('running-games-readiness')"
          :windows="windowsSetup" :optional="optionalTools" @go="goToSetup" />

        <SettingsGroup id="wine-proton" title="Wine and Proton" v-if="supportsLinuxRuntimes" data-keywords="wine proton prefix runner">
          <template #description>Windows games run in Wine or Proton. Wine works on its own. Proton also needs the umu setup below.</template>
          <template #actions><SettingsHelpLink page="Launching-Games" anchor="wine-and-proton-linux" /></template>
          <SettingRow data-settings-row="wine-proton-static-2" data-settings-bound v-show="settingsRowVisible('wine-proton-static-2')" v-if="supportsLinuxRuntimes"
            layout="stacked" label="Proton / Wine executable path" control-id="settings-proton-path" help-id="settings-proton-help"
            help="Leave empty for system Wine, or UMU-Proton if Wine is unavailable. GE-Proton (latest) may check for updates when launching."
            :save-state="protonSave.state.value" :save-error="protonSave.error.value">
            <template #actions>
              <button
                type="button"
                @click="downloadProton"
                :disabled="downloadingProton"
                class="ui-card-btn ui-card-btn--link"
              >
                <IconLoader2 v-if="downloadingProton" class="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                <IconDownloadFilled v-else class="w-3.5 h-3.5" aria-hidden="true" />
                {{ downloadingProton ? "Downloading GE-Proton…" : "Auto download latest GE-Proton" }}
              </button>
            </template>
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
              <datalist id="runner-paths">
                <option v-for="runner in availableRunners" :key="runner.path" :value="runner.path">{{ runner.name }}</option>
              </datalist>
              <button @click="browseProton" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn">
                <IconFolderOpen class="w-3.5 h-3.5" aria-hidden="true" />
                Browse
              </button>
            </div>
            <PathStatus :path="protonPath" kind="runner" />
            <div v-if="protonDownload" class="mt-3 space-y-1.5" role="status">
              <div class="flex justify-between gap-3 text-xs" style="color: var(--text-secondary)">
                <span>{{ protonDownload.stage }}… It keeps downloading if you leave this page.</span>
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
            <p v-if="protonError" class="copyable-feedback save-status-error mt-2" role="alert">
              {{ protonError }}
            </p>
          </SettingRow>

          <SettingRow data-settings-row="wine-proton-static-3" data-settings-bound v-show="settingsRowVisible('wine-proton-static-3')" v-if="supportsLinuxRuntimes"
            layout="stacked" label="Default Wine prefix path (WINEPREFIX)" control-id="settings-prefix-path" help-id="settings-prefix-help"
            help="A Wine prefix is a folder that acts like a small Windows PC: its own C: drive, fonts and game saves. Games share this one unless a game sets its own. Leave empty and wLib manages it."
            :save-state="prefixSave.state.value" :save-error="prefixSave.error.value">
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
              <button @click="browsePrefix" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn">
                <IconFolderOpen class="w-3.5 h-3.5" aria-hidden="true" />
                Browse
              </button>
            </div>
            <PathStatus :path="prefixPath" kind="dir" />
          </SettingRow>

          <SettingRow data-settings-row="wine-proton-static-4" data-settings-bound v-show="settingsRowVisible('wine-proton-static-4')" v-if="supportsLinuxRuntimes"
            label="Prefix tools" help="Open Winecfg or Winetricks in the default Wine prefix.">
            <div class="flex flex-wrap gap-2">
              <button class="ui-card-btn" @click="openPrefixTool('winecfg')">Winecfg</button>
              <button class="ui-card-btn" @click="openPrefixTool('winetricks')">Winetricks</button>
            </div>
          </SettingRow>
        </SettingsGroup>

        <UmuSettings v-if="supportsLinuxRuntimes" ref="umuSettings" data-keywords="runtime steam linux runtime steamrt"
          :required="windowsSetup.usesProton" @status="umuReadiness = $event" />

        <LocaleEmulatorSettings v-if="platformCapabilities.platform === 'windows'" />

        <SettingsGroup id="tools" title="Game engine tools" data-keywords="rpg maker rpgm renpy ren'py mod winetricks">
          <template v-if="supportsLinuxRuntimes" #description>All optional. Winetricks, RTP and Cheat Engine install into the default Wine prefix set under Wine and Proton.</template>
          <template #actions><SettingsHelpLink page="Launching-Games" anchor="runtime-installers" /></template>
          <SettingRow data-settings-row="wine-proton-static-5" data-settings-bound v-show="settingsRowVisible('wine-proton-static-5')" v-if="platformCapabilities.rpgmaker_linux"
            layout="stacked" label="RPGMaker Linux Runner" label-id="settings-rpgm-label" help-id="settings-rpgm-help"
            help="Optional. Runs RPG Maker XP, VX, VX Ace, MV and MZ games natively on Linux, often better than Wine. When installed, Auto Detect uses it unless a game sets its own Wine or Proton options."
            :save-state="runnerSave.state.value" :save-error="runnerSave.error.value">
            <template #actions>
              <button type="button" @click="openRpgmakerLinuxRepo" class="ui-card-btn shrink-0">
                <IconExternalLink class="w-3.5 h-3.5" aria-hidden="true" />Open GitHub
              </button>
            </template>
            <div class="flex flex-col gap-3">
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
                <button @click="browseRpgmakerLinuxRunner" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn">
                  <IconFolderOpen class="w-3.5 h-3.5" aria-hidden="true" />
                  Browse
                </button>
              </div>
              <PathStatus :path="rpgmakerLinuxRunnerPath" kind="runner" />

              <p
                class="copyable-feedback text-xs"
                :style="{ color: rpgmakerLinuxRunnerAvailable ? 'var(--success-text)' : rpgmakerRunnerOptional ? 'var(--text-muted)' : 'var(--warning-text)' }"
              >
                {{ rpgmakerRunnerOptional ? "Optional · not installed. RPG Maker games run in Wine or Proton until you install it." : rpgmakerLinuxRunnerStatusText }}
              </p>

              <div
                v-if="rpgmakerRunnerUi.showRunnerInstall"
                data-rpgm-runner-install
                class="rounded-lg p-3 space-y-2 border"
                style="background: var(--bg-surface); border-color: var(--border)"
              >
                <div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <p class="text-xs" style="color: var(--text-secondary)">
                    Downloads the runner, checks it, and installs it in your home folder.
                  </p>
                  <button
                    type="button"
                    class="ui-card-btn shrink-0"
                    :disabled="rpgmakerRunnerUi.installDisabled || !!settingsLoadError || settingsLoading"
                    @click="startRpgmakerRunnerInstall('runner')"
                  >
                    <IconLoader2
                      v-if="rpgmakerRunnerUi.installing && rpgmakerRunnerUi.installTarget === 'runner'"
                      class="w-3.5 h-3.5 animate-spin"
                    />
                    {{ rpgmakerRunnerUi.installing && rpgmakerRunnerUi.installTarget === 'runner' ? 'Installing…' : 'Install runner' }}
                  </button>
                </div>
                <details class="settings-disclosure" data-settings-disclosure>
                  <summary>What the installer changes</summary>
                  <div class="settings-disclosure-body space-y-1">
                    <p>
                      It downloads the pinned v1.1.9 archive, checks its SHA-256 hash and runs the
                      runner's own <code>install.sh</code>. Files go to <code>~/desktopapps/nwjs</code>
                      (or your custom install folder). It adds the <code>rpgmaker-linux</code> command
                      and menu entries, and can add a Steam compatibility tool.
                    </p>
                    <p>
                      It can also add one line to your shell settings, so the <code>rpgmaker-linux</code>
                      command works in a terminal. wLib does not need that line.
                    </p>
                    <p>
                      Installs run only when you click Install. wLib never runs the runner's
                      update, export, bug-report, patching or plugin-install commands.
                    </p>
                  </div>
                </details>
              </div>

              <p
                v-else-if="rpgmakerRunnerUi.showRunnerMissingToolsNote"
                class="text-xs"
                style="color: var(--warning-text)"
              >
                {{ rpgmakerRunnerUi.runnerMissingToolsReason }} Install manually from GitHub or install the missing command first.
              </p>

              <div
                data-rpgm-mkxpz-row
                class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between pt-2 border-t"
                style="border-color: var(--border)"
              >
                <div class="min-w-0">
                  <p class="text-xs font-medium" style="color: var(--text-primary)">
                    mkxp-z module (RPG Maker XP, VX, VX Ace)
                  </p>
                  <p class="text-xs mt-0.5" style="color: var(--text-muted)">
                    Plays these older RPG Maker games on Linux without Wine. It needs the runner above.
                  </p>
                  <p
                    class="copyable-feedback text-xs mt-0.5 break-all"
                    :style="{ color: rpgmakerRunnerUi.mkxpzInstalled ? 'var(--success-text)' : 'var(--text-muted)' }"
                  >
                    {{ rpgmakerRunnerUi.mkxpzStatusText }}
                  </p>
                </div>
                <button
                  v-if="rpgmakerRunnerUi.showMkxpzInstall"
                  type="button"
                  class="ui-card-btn shrink-0"
                  :disabled="rpgmakerRunnerUi.installDisabled || !!settingsLoadError || settingsLoading"
                  @click="startRpgmakerRunnerInstall('mkxpz')"
                >
                  <IconLoader2
                    v-if="rpgmakerRunnerUi.installing && rpgmakerRunnerUi.installTarget === 'mkxpz'"
                    class="w-3.5 h-3.5 animate-spin"
                  />
                  {{ rpgmakerRunnerUi.installing && rpgmakerRunnerUi.installTarget === 'mkxpz' ? 'Installing…' : 'Install mkxp-z' }}
                </button>
              </div>

              <p
                v-if="rpgmakerRunnerUi.installing"
                data-rpgm-install-stage
                class="ui-callout"
                role="status"
              >
                <IconLoader2 class="w-4 h-4 shrink-0 animate-spin" style="color: var(--brand)" />
                {{ rpgmakerRunnerUi.stageText }}
              </p>

              <div
                v-if="rpgmakerRunnerUi.errorText"
                data-rpgm-install-error
                class="ui-callout ui-callout--danger flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between"
                role="alert"
              >
                <span class="break-words">{{ rpgmakerRunnerUi.errorText }}</span>
                <button
                  type="button"
                  @click="openRpgmakerLinuxRepo"
                  class="ui-card-btn shrink-0"
                >
                  Open GitHub
                </button>
              </div>

            </div>
          </SettingRow>

          <!-- Wine installers (Linux only) -->
          <div data-settings-row="tools-static-0" data-settings-bound v-show="settingsRowVisible('tools-static-0')" v-if="supportsLinuxRuntimes" class="flex flex-col gap-3">
            <!-- DLL Install Card -->
            <div class="tool-card p-4 rounded-lg">
              <div class="flex flex-wrap items-center justify-between gap-4">
                <div class="flex items-start gap-3 min-w-0 grow basis-56">
                  <span class="ui-status-badge" :class="dllsInstalled ? 'ui-status-badge--ok' : ''" aria-hidden="true">
                    <IconLoader2 v-if="installingDeps" class="w-4 h-4 animate-spin" />
                    <IconCircleCheck v-else-if="dllsInstalled" class="w-4 h-4" />
                    <IconDownload v-else class="w-4 h-4" />
                  </span>
                  <div class="min-w-0">
                    <h5 class="text-sm font-medium" style="color: var(--text-primary)">
                      RPGMaker / Unity fix (Winetricks)
                    </h5>
                    <p
                      v-if="!dllsInstalled && !installingDeps"
                      class="text-xs mt-1"
                      style="color: var(--text-muted)"
                    >
                      Adds Windows fonts, video parts and runtimes that many RPG Maker and
                      Unity games need. Fixes black screens in videos. Takes 30 minutes or more.
                    </p>
                    <p v-if="installingDeps" class="text-xs mt-1" style="color: var(--text-secondary)" role="status">
                      Installing <span class="font-mono">{{ depsProgress.current }}</span> ({{ depsProgress.done }}/{{
                        depsProgress.total
                      }})… This can take 30 minutes or more. It keeps running if you leave this page.
                    </p>
                    <details v-if="!dllsInstalled" class="settings-disclosure mt-1" data-settings-disclosure>
                      <summary>What it installs</summary>
                      <p class="settings-disclosure-body">
                        Winetricks packages, among them corefonts and other fonts, d3dx9 and d3dcompiler (DirectX),
                        quartz, directshow and wmp9 (video), and the .NET and Visual C++ runtimes.
                      </p>
                    </details>
                    <p v-if="dllsInstalled && !installingDeps" class="text-xs mt-1" style="color: var(--success-text)">
                      Installed
                    </p>
                  </div>
                </div>
                <button
                  @click="installDeps"
                  :disabled="installingDeps || dllsInstalled"
                  class="ui-card-btn shrink-0"
                >
                  <IconLoader2 v-if="installingDeps" class="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                  {{ dllsInstalled ? "Installed" : installingDeps ? "Installing…" : "Install DLLs" }}
                </button>
              </div>
              <div v-if="installingDeps && depsProgress.total > 0" class="mt-3 ml-11">
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
            <div class="tool-card p-4 rounded-lg">
              <div class="flex flex-wrap items-center justify-between gap-4">
                <div class="flex items-start gap-3 min-w-0 grow basis-56">
                  <span class="ui-status-badge" :class="rtpsInstalled ? 'ui-status-badge--ok' : ''" aria-hidden="true">
                    <IconLoader2 v-if="installingRtps" class="w-4 h-4 animate-spin" />
                    <IconCircleCheck v-else-if="rtpsInstalled" class="w-4 h-4" />
                    <IconDownload v-else class="w-4 h-4" />
                  </span>
                  <div class="min-w-0">
                    <h5 class="text-sm font-medium" style="color: var(--text-primary)">
                      RPGMaker RTP (VX Ace, VX, XP, 2003)
                    </h5>
                    <p
                      v-if="!rtpsInstalled && !installingRtps"
                      class="text-xs mt-1"
                      style="color: var(--text-muted)"
                    >
                      The RTP (Run Time Package) has the shared graphics, sounds and fonts
                      that older RPG Maker games expect. Downloads and checks the official packages.
                    </p>
                    <p v-if="installingRtps" class="text-xs mt-1" style="color: var(--text-secondary)" role="status">
                      Installing <span class="font-mono">{{ rtpProgress.current }}</span> ({{ rtpProgress.done }}/{{
                        rtpProgress.total
                      }})… It keeps running if you leave this page.
                    </p>
                    <p v-if="rtpsInstalled && !installingRtps" class="text-xs mt-1" style="color: var(--success-text)">
                      Installed
                    </p>
                  </div>
                </div>
                <button
                  @click="installRtps"
                  :disabled="installingRtps || rtpsInstalled"
                  class="ui-card-btn shrink-0"
                >
                  <IconLoader2 v-if="installingRtps" class="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                  {{ rtpsInstalled ? "Installed" : installingRtps ? "Installing…" : "Install RTPs" }}
                </button>
              </div>
              <div v-if="installingRtps && rtpProgress.total > 0" class="mt-3 ml-11">
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
            <div class="tool-card p-4 rounded-lg">
              <div class="flex flex-wrap items-center justify-between gap-4">
                <div class="flex items-start gap-3 min-w-0 grow basis-56">
                  <span class="ui-status-badge" :class="ceInstalled ? 'ui-status-badge--ok' : ''" aria-hidden="true">
                    <IconLoader2 v-if="installingCe" class="w-4 h-4 animate-spin" />
                    <IconCircleCheck v-else-if="ceInstalled" class="w-4 h-4" />
                    <IconDownload v-else class="w-4 h-4" />
                  </span>
                  <div class="min-w-0">
                    <h5 class="text-sm font-medium" style="color: var(--text-primary)">
                      Cheat Engine (Lunar Engine)
                    </h5>
                    <p v-if="!ceInstalled" class="text-xs mt-1" style="color: var(--text-muted)">
                      Cheat Engine changes values in a running game, such as money or health.
                      wLib can start it with a game.
                    </p>
                    <p v-else class="text-xs mt-1" style="color: var(--success-text)">Installed</p>
                  </div>
                </div>
                <button
                  @click="downloadCe"
                  :disabled="installingCe || ceInstalled"
                  class="ui-card-btn shrink-0"
                >
                  <IconLoader2 v-if="installingCe" class="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                  {{ ceInstalled ? "Installed" : installingCe ? "Downloading…" : "Install Cheat Engine" }}
                </button>
              </div>
            </div>
          </div>

          <div data-settings-row="tools-static-1" data-settings-bound v-show="settingsRowVisible('tools-static-1')" v-if="installError || rtpError || ceError" class="space-y-1" role="alert">
            <p v-if="installError" class="copyable-feedback save-status-error">{{ installError }}</p>
            <p v-if="rtpError" class="copyable-feedback save-status-error">{{ rtpError }}</p>
            <p v-if="ceError" class="copyable-feedback save-status-error">{{ ceError }}</p>
          </div>

          <SettingRow id="renpy" data-settings-row="renpy-static-0" data-settings-bound v-show="settingsRowVisible('renpy-static-0')"
            layout="stacked" label="Universal Ren'Py Mod (URM) source file (.rpa)" control-id="urm-source" help-id="settings-urm-help"
            help="URM is a mod that adds cheat and editing tools to Ren'Py games. Download it from its official page on 0x52.dev, choose the .rpa file you got (a Ren'Py archive), then turn on URM in a Ren'Py game's details."
            :save-state="urmSave.state.value" :save-error="urmSave.error.value">
            <template #actions>
              <button type="button" @click="openUrmPage" class="ui-card-btn shrink-0">
                <IconExternalLink class="w-3.5 h-3.5" aria-hidden="true" />Open official page
              </button>
            </template>
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
              <button @click="browseUrm" :disabled="!!settingsLoadError || settingsLoading" class="ui-card-btn">
                <IconFolderOpen class="w-3.5 h-3.5" aria-hidden="true" />Browse
              </button>
            </div>
            <PathStatus :path="urmPath" kind="file" />
          </SettingRow>
        </SettingsGroup>

        <SettingsGroup id="performance" title="Performance and scaling" v-if="supportsLinuxRuntimes" description="All optional. Install a tool with your system's package manager to use it.">
          <template #actions><SettingsHelpLink page="Launching-Games" anchor="gamemode-mangohud-and-gamescope-linux" /></template>
          <SettingRow data-settings-row="performance-gamemode-mangohud" data-settings-bound data-keywords="gamemode mangohud fps overlay" v-show="settingsRowVisible('performance-gamemode-mangohud')" v-if="supportsLinuxRuntimes"
            layout="stacked" label="GameMode and MangoHud"
            help="Apply to every game unless a game sets its own choice.">
            <div class="space-y-3">
              <div v-for="tool in (['gamemode', 'mangohud'] as const)" :key="tool" class="space-y-1">
                <div class="flex items-center justify-between gap-3 text-sm">
                  <span class="min-w-0">
                    <span class="block" :style="{ color: performanceTools[tool] ? 'var(--text-primary)' : 'var(--text-secondary)' }">{{ tool === 'gamemode' ? 'GameMode' : 'MangoHud' }} <span v-if="!performanceTools[tool]" class="text-xs" style="color: var(--text-muted)">Optional · not installed</span></span>
                    <span class="block text-xs mt-0.5" style="color: var(--text-muted)">{{ tool === 'gamemode'
                      ? 'Asks your computer to run at full speed while a game plays.'
                      : 'Shows FPS, CPU and GPU use over the game.' }}</span>
                  </span>
                  <span class="flex items-center gap-3">
                    <FieldSaveFeedback v-if="performanceToolStatus[tool].state.value !== 'error'" class="save-status-slot" :state="performanceToolStatus[tool].state.value" :error="performanceToolStatus[tool].error.value" />
                    <label class="relative inline-flex items-center shrink-0" :class="performanceTools[tool] ? 'cursor-pointer' : 'cursor-not-allowed'">
                      <input type="checkbox" role="switch" :aria-label="tool === 'gamemode' ? 'GameMode' : 'MangoHud'" class="sr-only peer" :checked="tool === 'gamemode' ? gamemodeEnabled : mangohudEnabled" :disabled="!performanceTools[tool]" @change="savePerformanceTool(tool, $event)" />
                      <div class="ui-toggle"></div>
                    </label>
                  </span>
                </div>
                <FieldSaveFeedback v-if="performanceToolStatus[tool].state.value === 'error'" :state="performanceToolStatus[tool].state.value" :error="performanceToolStatus[tool].error.value" />
              </div>
            </div>
          </SettingRow>

          <SettingRow data-settings-row="wine-proton-static-6" data-settings-bound data-keywords="gamescope scaler scaling upscale resolution fsr nis integer pixel art fullscreen" v-show="settingsRowVisible('wine-proton-static-6')" v-if="supportsLinuxRuntimes"
            label-id="settings-gamescope-label" help-id="settings-gamescope-help"
            :help="gamescope.status.value && !gamescopeInstalled
              ? 'Scales small games to your screen. Install it to see its options.'
              : 'Run games in gamescope to scale small games to your screen. Each game can override this on its Launch tab. Scaling needs Fullscreen or an output resolution.'"
            :save-state="gamescope.saveStatus.state.value" :save-error="gamescope.saveStatus.error.value">
            <template #label>Gamescope <span v-if="gamescope.status.value && !gamescopeInstalled" class="text-xs font-normal" style="color: var(--text-muted)">Optional · not installed</span></template>
            <label class="relative inline-flex items-center shrink-0" :class="gamescopeInstalled ? 'cursor-pointer' : 'cursor-not-allowed'">
              <input type="checkbox" role="switch" aria-labelledby="settings-gamescope-label" aria-describedby="settings-gamescope-help" class="sr-only peer"
                :checked="gamescope.values.gamescope_enabled" :disabled="!gamescopeInstalled"
                @change="gamescope.save('gamescope_enabled', ($event.target as HTMLInputElement).checked)" />
              <div class="ui-toggle"></div>
            </label>
            <template v-if="gamescope.status.value" #after>
            <div class="space-y-3">
            <div v-if="gamescope.status.value && !gamescopeInstalled" class="rounded-lg p-3 space-y-2 text-xs" style="background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)">
              <template v-if="gamescope.status.value.command">
                <p>{{ gamescope.status.value.nixos ? "Add this to your NixOS system configuration:" : "Install gamescope with this command:" }}</p>
                <code class="block break-all rounded px-2 py-1" style="background: var(--bg-inset); color: var(--text-primary)">{{ gamescope.status.value.command }}</code>
              </template>
              <p v-else>Install gamescope from your distribution's packages.</p>
              <div class="flex flex-wrap gap-2">
                <button v-if="gamescope.status.value.command" type="button" class="ui-card-btn" @click="gamescope.copyInstallCommand()">Copy command</button>
                <button type="button" class="ui-card-btn" :disabled="gamescope.checking.value" @click="gamescope.loadStatus(true)">
                  {{ gamescope.checking.value ? "Checking…" : "Check again" }}
                </button>
              </div>
            </div>
            <fieldset v-if="gamescopeInstalled" class="settings-fieldset grid gap-3 sm:grid-cols-2">
              <legend class="sr-only">Gamescope options</legend>
              <label class="settings-field-label text-xs space-y-1">
                <span class="block">Window mode</span>
                <select class="settings-input w-full" :value="gamescope.values.gamescope_window_mode"
                  @change="gamescope.save('gamescope_window_mode', ($event.target as HTMLSelectElement).value as GamescopeWindowMode)">
                  <option v-for="option in WINDOW_MODE_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <label class="settings-field-label text-xs space-y-1">
                <span class="block">FPS limit</span>
                <input v-model="gamescope.drafts.gamescope_fps_limit" type="text" inputmode="numeric" class="settings-input w-full" placeholder="Off"
                  :aria-invalid="!!gamescope.errors.gamescope_fps_limit"
                  @input="gamescope.checkText('gamescope_fps_limit')" @blur="gamescope.commitText('gamescope_fps_limit')" @keydown.enter.prevent="gamescope.commitText('gamescope_fps_limit')" />
                <span v-if="gamescope.errors.gamescope_fps_limit" class="block" role="alert" style="color: var(--danger-text)">{{ gamescope.errors.gamescope_fps_limit }}</span>
              </label>
              <label class="settings-field-label text-xs space-y-1">
                <span class="block">Output resolution</span>
                <input v-model="gamescope.drafts.gamescope_output_resolution" type="text" class="settings-input w-full" placeholder="Auto (for example 2560x1440)"
                  :aria-invalid="!!gamescope.errors.gamescope_output_resolution"
                  @input="gamescope.checkText('gamescope_output_resolution')" @blur="gamescope.commitText('gamescope_output_resolution')" @keydown.enter.prevent="gamescope.commitText('gamescope_output_resolution')" />
                <span v-if="gamescope.errors.gamescope_output_resolution" class="block" role="alert" style="color: var(--danger-text)">{{ gamescope.errors.gamescope_output_resolution }}</span>
              </label>
              <label class="settings-field-label text-xs space-y-1">
                <span class="block">Game resolution</span>
                <input v-model="gamescope.drafts.gamescope_game_resolution" type="text" class="settings-input w-full" placeholder="Auto (the game's own size when wLib finds it, else the output)"
                  :aria-invalid="!!gamescope.errors.gamescope_game_resolution"
                  @input="gamescope.checkText('gamescope_game_resolution')" @blur="gamescope.commitText('gamescope_game_resolution')" @keydown.enter.prevent="gamescope.commitText('gamescope_game_resolution')" />
                <span v-if="gamescope.errors.gamescope_game_resolution" class="block" role="alert" style="color: var(--danger-text)">{{ gamescope.errors.gamescope_game_resolution }}</span>
              </label>
              <label class="settings-field-label text-xs space-y-1">
                <span class="block">Scaler</span>
                <select class="settings-input w-full" :value="gamescope.values.gamescope_scaler"
                  @change="gamescope.save('gamescope_scaler', ($event.target as HTMLSelectElement).value as GamescopeScaler)">
                  <option v-for="option in SCALER_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <label class="settings-field-label text-xs space-y-1">
                <span class="block">Filter</span>
                <select class="settings-input w-full" :value="gamescope.values.gamescope_filter"
                  @change="gamescope.save('gamescope_filter', ($event.target as HTMLSelectElement).value as GamescopeFilter)">
                  <option v-for="option in FILTER_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
                </select>
              </label>
              <label class="settings-field-label text-xs space-y-1 sm:col-span-2">
                <span class="block">Extra arguments</span>
                <input v-model="gamescope.drafts.gamescope_args" type="text" class="settings-input w-full font-mono" placeholder="For example --hdr-enabled"
                  :aria-invalid="!!gamescope.errors.gamescope_args"
                  @input="gamescope.checkText('gamescope_args')" @blur="gamescope.commitText('gamescope_args')" @keydown.enter.prevent="gamescope.commitText('gamescope_args')" />
                <span v-if="gamescope.errors.gamescope_args" class="block" role="alert" style="color: var(--danger-text)">{{ gamescope.errors.gamescope_args }}</span>
              </label>
            </fieldset>
            </div>
            </template>
          </SettingRow>
        </SettingsGroup>

        <SettingsGroup id="html-games" title="HTML and Flash games">
          <template #actions><SettingsHelpLink page="Launching-Games" anchor="html-and-flash-games" /></template>
          <SettingRow data-settings-row="general-static-5" data-settings-bound v-show="settingsRowVisible('general-static-5')"
            label="Play HTML games in the built-in player" control-id="settings-html-player"
            help="Runs HTML and Flash (.swf) games in a wLib window, so playtime, Stop, sandboxing and No internet work. Turn off to open HTML games in your system browser. Each game can override this."
            :save-state="htmlPlayerStatus.state.value" :save-error="htmlPlayerStatus.error.value">
            <label class="relative inline-flex items-center cursor-pointer shrink-0">
              <input
                id="settings-html-player"
                type="checkbox"
                role="switch"
                :checked="htmlBuiltinPlayer"
                @change="saveHtmlBuiltinPlayer"
                class="sr-only peer"
                aria-label="Play HTML games in the built-in player"
              />
              <div class="ui-toggle"></div>
            </label>
          </SettingRow>
        </SettingsGroup>

        <SandboxSettings data-keywords="isolation bubblewrap sandboxie" @status="onSandboxStatus" />

        <CompanionToolsSettings v-if="platformCapabilities.companion_tools" data-keywords="companion" />
      </section>

      <section v-show="sectionVisible('sources')" id="sources" class="settings-card rounded-xl p-6 space-y-5">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconDatabase class="ui-section-icon" />
          Sources
        </h3>

        <CatalogSettings />

        <SettingsGroup id="lewdcorner" title="LewdCorner">
          <template #actions><SettingsHelpLink page="Getting-Started" anchor="lewdcorner" /></template>
          <SettingRow v-for="{ key, label, description } in lcSettings" :key="key"
            :data-settings-row="key" data-settings-bound data-keywords="lewdcorner thread lc plus premium"
            v-show="settingsRowVisible(key)"
            :label="label" :label-id="`settings-${key}-label`" :control-id="`settings-${key}`" :help="description"
            :save-state="lcStatus[key].state.value" :save-error="lcStatus[key].error.value">
            <label class="relative inline-flex items-center cursor-pointer shrink-0">
              <input
                :id="`settings-${key}`"
                type="checkbox"
                role="switch"
                :aria-labelledby="`settings-${key}-label`"
                :checked="lcValues[key]"
                :disabled="settingsLoading || !!settingsLoadError || lcSaving[key]"
                class="sr-only peer"
                @change="saveLcSetting(key, $event)"
              />
              <div class="ui-toggle"></div>
            </label>
          </SettingRow>

          <SettingRow v-if="lcValues.lc_enabled" data-settings-row="lc_plus" data-settings-bound data-keywords="lewdcorner thread lc plus premium tier detect check"
            v-show="settingsRowVisible('lc_plus')" data-testid="settings-lc-plus"
            label="LC Plus" label-id="settings-lc_plus-label" control-id="settings-lc-plus-mode"
            help="Also fill in LewdCorner Plus (Premium) threads. Auto checks your LewdCorner account through the browser extension."
            :save-state="lcPlusStatus.state.value" :save-error="lcPlusStatus.error.value">
            <select
              id="settings-lc-plus-mode"
              class="settings-input shrink-0"
              aria-labelledby="settings-lc_plus-label"
              :value="lcPlus.lc_plus_mode"
              :disabled="settingsLoading || !!settingsLoadError || lcPlusSaving"
              @change="saveLcPlusMode"
            >
              <option value="auto">Auto</option>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
            <template v-if="lcPlus.lc_plus_mode === 'auto'" #after>
              <div class="space-y-2">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <p class="text-xs" style="color: var(--text-secondary)" data-testid="lc-plus-detected">
                    {{ lcPlusDetectedText(lcPlus.lc_plus_detected) }}<template v-if="lcPlusCheckedAt(lcPlus.lc_plus_detected_at)"> · checked {{ lcPlusCheckedAt(lcPlus.lc_plus_detected_at) }}</template>
                  </p>
                  <button
                    type="button"
                    class="ui-card-btn shrink-0"
                    :disabled="!!lcPlusCheckDisabled || lcPlusChecking || settingsLoading || !!settingsLoadError"
                    :title="lcPlusCheckDisabled || undefined"
                    :aria-describedby="lcPlusCheckDisabled ? 'settings-lc_plus-reason' : undefined"
                    @click="checkLcPlus"
                  >{{ lcPlusChecking ? "Checking…" : "Check now" }}</button>
                </div>
                <p v-if="lcPlusCheckDisabled" id="settings-lc_plus-reason" class="text-xs" style="color: var(--text-muted)">{{ lcPlusCheckDisabled }}</p>
                <p v-if="lcPlusMessage" role="status" class="text-xs" style="color: var(--warning-text)">{{ lcPlusMessage }}</p>
              </div>
            </template>
          </SettingRow>
        </SettingsGroup>

      </section>

      <!-- Appearance -->
      <section v-show="sectionVisible('appearance')" id="appearance" class="settings-card rounded-xl p-6 space-y-4">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconPalette class="ui-section-icon" />
          Appearance
        </h3>

        <SettingRow data-settings-row="appearance-static-0" data-keywords="dark light system" data-settings-bound v-show="settingsRowVisible('appearance-static-0')"
          label="Theme" help="System follows your desktop's light or dark setting.">
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
        </SettingRow>

        <SettingRow data-settings-row="appearance-static-1" data-settings-bound v-show="settingsRowVisible('appearance-static-1')"
          label="Accent" label-id="accent-label" help="Highlight color for buttons, tabs, and active items. Play buttons stay green.">
          <div class="accent-swatches flex flex-wrap justify-end gap-2 shrink-0 max-w-full" role="radiogroup" aria-labelledby="accent-label">
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
        </SettingRow>

        <SettingRow data-settings-row="appearance-static-2" data-settings-bound v-show="settingsRowVisible('appearance-static-2')"
          label="Text size" help="Makes text and the space around it larger or smaller. Works together with Interface scale. Kept on this device.">
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
        </SettingRow>

        <SettingRow data-settings-row="appearance-static-3" data-settings-bound v-show="settingsRowVisible('appearance-static-3')"
          label="TV mode" control-id="tv-mode">
          <template #help>Larger controls for a TV or Steam Deck. While on, it uses 150% in place of Interface scale. Kept on this device.<span v-if="tvPreference === 'auto'" class="block mt-0.5" data-testid="tv-mode-state">{{ gamingMode ? 'On now: Steam Gaming Mode is running.' : 'Off now. Auto turns it on in Steam Gaming Mode.' }}</span></template>
          <select id="tv-mode" class="settings-input" :value="tvPreference" @change="setTvPreference(($event.target as HTMLSelectElement).value)">
            <option value="auto">Auto</option><option value="on">On</option><option value="off">Off</option>
          </select>
        </SettingRow>

        <SettingRow data-settings-row="appearance-static-4" data-settings-bound v-show="settingsRowVisible('appearance-static-4')"
          label="Interface scale" control-id="interface-scale">
          <template #help>Zooms the whole window, images included. Kept on this device.<span v-if="tvModeActive" class="block mt-0.5">TV mode is on, so 150% is used now.</span></template>
          <select id="interface-scale" class="settings-input"
            :value="interfaceScale" @change="setInterfaceScale(($event.target as HTMLSelectElement).value)">
            <option v-for="option in INTERFACE_SCALE_OPTIONS" :key="option" :value="option">{{ option }}%</option>
          </select>
          <button v-if="interfaceScale !== 100" type="button" class="ui-card-btn" @click="setInterfaceScale(100)">Reset</button>
        </SettingRow>

        <SettingRow data-settings-row="appearance-static-5" data-settings-bound v-show="settingsRowVisible('appearance-static-5')"
          label="Animations" help="Turn non-essential UI motion on or off. Loading indicators stay visible either way.">
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
        </SettingRow>

        <SettingRow data-settings-row="appearance-static-6" data-keywords="gif cover covers animate animated hover" data-settings-bound v-show="settingsRowVisible('appearance-static-6')"
          label="Animate covers"
          :help="motionEnabled
            ? 'When animated covers play. They always stay still while a game is running.'
            : 'Covers stay still while Animations are off.'">
          <div class="motion-toggle inline-flex rounded-lg p-1 shrink-0" role="group" aria-label="Animate covers">
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
        </SettingRow>
      </section>

      <SteamSettings v-show="sectionVisible('steam')" v-if="platformCapabilities.platform === 'linux'" />

      <section v-show="sectionVisible('advanced')" id="advanced" class="settings-card rounded-xl p-6 space-y-5">
        <div class="settings-section-head">
          <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
            <IconTool class="ui-section-icon" />
            Advanced
          </h3>
          <SettingsHelpLink page="Troubleshooting" />
        </div>

        <SettingRow data-settings-row="general-static-0" data-settings-bound v-show="settingsRowVisible('general-static-0')"
          label="Enable debug logging" control-id="settings-enable-logging"
          :help="supportsLinuxRuntimes
            ? 'Saves Wine/Proton launch output to a .log file next to the game executable.'
            : 'Saves native launch output to a .log file next to the game executable.'"
          :save-state="loggingStatus.state.value" :save-error="loggingStatus.error.value">
          <label class="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              id="settings-enable-logging"
              type="checkbox"
              role="switch"
              :checked="enableLogging"
              :disabled="savingLogging"
              @change="toggleLogging"
              class="sr-only peer"
              aria-label="Enable debug logging"
            />
            <div class="ui-toggle"></div>
          </label>
        </SettingRow>

        <SettingRow data-settings-row="general-static-6" data-settings-bound v-show="settingsRowVisible('general-static-6')"
          label="Web player DevTools (F12)" control-id="settings-web-devtools"
          help="F12 opens DevTools in the built-in player. Turn off if a game uses F12. Applies to the next launch."
          :save-state="devtoolsStatus.state.value" :save-error="devtoolsStatus.error.value">
          <label class="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              id="settings-web-devtools"
              type="checkbox"
              role="switch"
              :checked="webPlayerDevtools"
              @change="saveWebPlayerDevtools"
              class="sr-only peer"
              aria-label="Web player DevTools (F12)"
            />
            <div class="ui-toggle"></div>
          </label>
        </SettingRow>

        <SettingRow data-settings-row="library-downloads-static-0" data-settings-bound v-show="settingsRowVisible('library-downloads-static-0')"
          :label="`Cover cache: ${coverCache.count ? `${coverCache.count} covers, ${formatCoverCacheSize(coverCache.bytes)}` : 'Empty'}`"
          help="Cached covers stay on this device. After purging, downloads resume next launch.">
          <button class="ui-card-btn ui-card-btn--danger" :disabled="coverCacheBusy || !coverCache.count" @click="purgeCoverCache">Purge</button>
          <template v-if="coverCacheError" #after>
            <p class="save-status-error" role="alert">{{ coverCacheError }}</p>
          </template>
        </SettingRow>

        <SettingRow data-settings-row="advanced-diagnostics" data-settings-bound data-keywords="diagnostics bug report logs log folder system details" v-show="settingsRowVisible('advanced-diagnostics')"
          label="Diagnostics" help="App details for a bug report, and the log folder.">
          <RouterLink to="/diagnostics" class="ui-card-btn">Open diagnostics</RouterLink>
        </SettingRow>

        <SettingRow data-settings-row="onboarding-replay" data-settings-bound data-keywords="onboarding setup welcome tour guide" v-show="settingsRowVisible('onboarding-replay')"
          label="Setup guide" help="Walk through the catalog, LewdCorner, import, and browser extension choices again.">
          <button type="button" class="ui-card-btn" @click="onboarding.replay()">Show setup again</button>
        </SettingRow>

        <SettingsGroup id="keyboard-shortcuts" title="Keyboard shortcuts" data-keywords="keys hotkeys shortcut">
          <div data-settings-row="keyboard-shortcuts-static-0" data-settings-bound v-show="settingsRowVisible('keyboard-shortcuts-static-0')">
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
        </SettingsGroup>

      </section>
        </fieldset>
      </div>
    </div>

  </div>
</template>

<style scoped>
/* No scroll anchoring here: groups that appear after their status loads would move the page on their own.
   A deep link keeps its target at the top itself (pinSection), and needs the scroll position left alone. */
.settings-page { overflow-anchor: none; }
.settings-nav-item { color: var(--text-secondary); }
.settings-nav-item:hover { color: var(--text-primary); background: var(--bg-raised); }
/* A filled state: the accent and its paired text color (as on primary buttons) clear 4.5:1 for the text and 3:1 against the page. */
.settings-nav-active, .settings-nav-active:hover { color: var(--on-brand); background: var(--brand); font-weight: 600; }
/* Narrow windows have no section list: search and the section menu stay at the top of the page. */
.settings-narrow-bar {
  position: sticky;
  top: 0;
  z-index: 10;
  margin-bottom: 1.5rem;
  padding-block: 0.75rem;
  background: var(--bg-base);
  border-bottom: 1px solid var(--border);
}
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

.motion-toggle {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  /* In a phone-width window the options wrap inside the card instead of running past its edge. */
  flex-wrap: wrap;
  max-width: 100%;
}
/* Wrapped options share each line's width, so the second line does not hang off one side. */
.motion-toggle > .motion-option { flex: 1 1 auto; }

kbd {
  font-family: ui-monospace, monospace;
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 0.3rem;
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
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

.motion-option:disabled {
  color: var(--text-muted);
  cursor: not-allowed;
}
.motion-option-active:disabled {
  box-shadow: inset 0 0 0 1px var(--border);
}


.tool-card {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}
</style>

<style>
/* Settings text: help and body text wrap without a single word on the last line, and titles balance their lines.
   Not scoped, so it reaches the rows that live in child components. */
.settings-page :is(p, li, dd, .settings-disclosure-body) { text-wrap: pretty; }
.settings-page :is(h2, h3, h4, h5, h6, .setting-row-label) { text-wrap: balance; }
/* A section heading with its quiet Help link on the right. */
.settings-section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
}
/* "What the installer changes" and similar: long details stay one click away. Native <details>, so
   Enter and Space open it. Settings search opens one whose text matches. */
.settings-disclosure > summary {
  width: fit-content;
  cursor: pointer;
  font-size: 0.75rem;
  line-height: 1rem;
  font-weight: 500;
  color: var(--text-secondary);
  border-radius: 0.25rem;
}
.settings-disclosure > summary:hover { color: var(--text-primary); }
.settings-disclosure > summary:focus-visible { outline: 2px solid var(--text-primary); outline-offset: 2px; }
.settings-disclosure-body {
  margin-top: 0.375rem;
  font-size: 0.75rem;
  line-height: 1.125rem;
  color: var(--text-secondary);
  max-width: 70ch;
}
/* Each section's heading block stays at the top of the content area while
   its section scrolls; the next card pushes it away. Not scoped, so it also
   reaches the sections that live in child components. */
.settings-sections > section[id] > :first-child {
  position: sticky;
  /* Under the narrow windows' search bar (0 when the section list shows). */
  top: var(--settings-sticky-top, 0px);
  z-index: 5;
  /* -1px: the first block's top line lies under this line, so there is never a double line. */
  margin: -1.5rem -1.5rem -1px;
  padding: 1rem 1.5rem;
  border-bottom: 1px solid var(--border);
  border-radius: calc(0.75rem - 1px) calc(0.75rem - 1px) 0 0;
  background: var(--bg-surface);
}
.settings-sections > section[id] h3.ui-section-heading {
  font-size: 1.25rem;
  line-height: 1.75rem;
  font-weight: 700;
}
/* Every block in a section draws its own top line (the same as .ui-card-row), so a block that settings search
   hides with v-show also hides its line. No sibling selectors: hidden blocks stay in the DOM. A table continues
   the block above it and gets no line. An empty block (nothing to show yet) draws no line either. */
.settings-sections > section[id] > :not(:first-child):not(table):not(:empty) {
  border-top: 1px solid var(--border);
  padding-top: var(--row-gap, 1.25rem);
}
</style>
