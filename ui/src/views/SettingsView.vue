<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import {
  IconAdjustments,
  IconAutomation,
  IconBook2,
  IconDeviceFloppyFilled,
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
  SettingsResponse,
  SystemDepsCommandResponse,
  RunnerInfo,
} from "../services/api";
import { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../services/api";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { validateCustomPlayStatuses } from "../utils/playStatus";
import PathStatus from "../components/PathStatus.vue";

const RPGMAKER_LINUX_REPO_URL =
  "https://github.com/bakustarver/rpgmakermlinux-cicpoffs";

const protonPath = ref("");
const availableRunners = ref<RunnerInfo[]>([]);
const customStatuses = ref<string[]>([]);
const newStatus = ref("");
const statusError = ref("");
// Collections apply immediately through the collection API, unlike the
// save-bar fields below.
const collectionList = ref<string[]>([]);
const newCollectionName = ref("");
const collectionError = ref("");
const renamingCollection = ref<string | null>(null);
const renameCollectionDraft = ref("");
const urmPath = ref("");
const prefixPath = ref("");
const rpgmakerLinuxRunnerPath = ref("");
const libraryFolder = ref("");
const libraryFolderPattern = ref("{creator}/{title}/{version}");
const downloadsFolder = ref("");
const downloadDetectionEnabled = ref(true);
const enableLogging = ref(false);
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
const saveError = ref("");
const settingsLoaded = ref(false);
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
    return `Detected: ${rpgmakerLinuxRunnerStatus.value.path}`;
  }
  return rpgmakerLinuxRunnerStatus.value.error || "Not detected";
});

const applySettings = (data: SettingsResponse) => {
  customStatuses.value = [...(data.custom_play_statuses || [])];
  collectionList.value = [...(data.collections || [])];
  urmPath.value = data.urm_rpa_path || "";
  protonPath.value = data.proton_path || "";
  prefixPath.value = data.wine_prefix_path || "";
  libraryFolder.value = data.library_folder || "";
  libraryFolderPattern.value = data.library_folder_pattern || "{creator}/{title}/{version}";
  downloadsFolder.value = data.downloads_folder || "";
  downloadDetectionEnabled.value = data.download_detection_enabled !== false;
  rpgmakerLinuxRunnerPath.value = data.rpgmaker_linux_runner_path || "";
  rpgmakerLinuxRunnerStatus.value =
    data.rpgmaker_linux_runner_status || defaultRpgmakerLinuxRunnerStatus;
  enableLogging.value = !!data.enable_logging;
  savedValues.value = formValues();
};

// Fields saved with the save bar. Toggles (logging, theme, animations) save on click.
const formValues = () => ({
  protonPath: protonPath.value,
  prefixPath: prefixPath.value,
  rpgmakerLinuxRunnerPath: rpgmakerLinuxRunnerPath.value,
  customStatuses: [...customStatuses.value],
  urmPath: urmPath.value,
  libraryFolder: libraryFolder.value,
  libraryFolderPattern: libraryFolderPattern.value,
  downloadsFolder: downloadsFolder.value,
  downloadDetectionEnabled: downloadDetectionEnabled.value,
});
const savedValues = ref(formValues());
const hasUnsavedChanges = computed(
  () => JSON.stringify(formValues()) !== JSON.stringify(savedValues.value),
);

onBeforeRouteLeave(
  () => !hasUnsavedChanges.value || confirm("Discard unsaved settings changes?"),
);

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
  try {
    platformCapabilities.value = await loadPlatformCapabilities();
    // Read the GE-Proton status before the settings snapshot: a download that
    // finishes in between then shows up in the snapshot (or in the poll's own
    // settings read), so a stale proton_path is never loaded and saved back.
    const protonStatus = supportsLinuxRuntimes.value
      ? await api.getProtonDownloadStatus()
      : null;
    const data = await api.getSettings();
    if (data) {
      applySettings(data);
    }

    if (supportsLinuxRuntimes.value) {
      const runners = await api.getAvailableRunners();
      availableRunners.value = runners.runners || [];
      const ceCheck = await api.isCheatEngineInstalled();
      ceInstalled.value = !!ceCheck?.installed;
      cePath.value = ceCheck?.path || "";

      await pollInstallStatus();
      // An install started before leaving this page is still running; keep tracking it.
      if (installingDeps.value || installingRtps.value) startPolling();

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
  } finally {
    settingsLoaded.value = true;
  }
};

const pollInstallStatus = async () => {
  if (!supportsLinuxRuntimes.value) return;
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
      libraryFolder.value = p;
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
    }
  } catch (e) {
    console.error("Browse RPGMaker Linux runner error", e);
    notifyError("Error browsing file: " + String(e));
  }
};

const openRpgmakerLinuxRepo = async () => {
  try {
    const result = await api.openInBrowser(RPGMAKER_LINUX_REPO_URL);
    if (result && result.success === false) {
      window.open(RPGMAKER_LINUX_REPO_URL, "_blank", "noopener");
    }
  } catch (_error) {
    window.open(RPGMAKER_LINUX_REPO_URL, "_blank", "noopener");
  }
};

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
  if (pollTimer) return;
  const tick = async () => {
    await pollInstallStatus();
    if (installingDeps.value || installingRtps.value) {
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

const saving = ref(false);

watch([protonPath, prefixPath, rpgmakerLinuxRunnerPath, customStatuses, urmPath, libraryFolder, libraryFolderPattern, downloadsFolder, downloadDetectionEnabled], () => {
  if (!saving.value) saveError.value = "";
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

const saveSettings = async () => {
  if (saving.value) return;
  statusError.value = validateCustomPlayStatuses(customStatuses.value);
  if (statusError.value) return;

  saving.value = true;
  saveError.value = "";

  try {
    const res = await api.saveSettings({
      proton_path: protonPath.value,
      wine_prefix_path: prefixPath.value,
      rpgmaker_linux_runner_path: rpgmakerLinuxRunnerPath.value,
      custom_play_statuses: customStatuses.value,
      urm_rpa_path: urmPath.value,
      library_folder: libraryFolder.value,
      library_folder_pattern: libraryFolderPattern.value.trim() || "{creator}/{title}/{version}",
      downloads_folder: downloadsFolder.value,
      download_detection_enabled: downloadDetectionEnabled.value,
    });

    if (!res || res.success === false) {
      saveError.value = "Failed to save settings: " + (res?.error || "Unknown error");
      return;
    }

    const persistedSettings = await api.getSettings();
    if (!persistedSettings) {
      saveError.value = "Settings were saved but could not be reloaded.";
      return;
    }

    applySettings(persistedSettings);
    if (supportsLinuxRuntimes.value) await pollInstallStatus();
    notify("Settings saved.", "success");
  } catch (e) {
    console.error("Failed to save settings", e);
    saveError.value = "Error saving settings: " + String(e);
  } finally {
    saving.value = false;
  }
};

const addStatus = () => {
  const name = newStatus.value.trim();
  statusError.value = validateCustomPlayStatuses([...customStatuses.value, name]);
  if (statusError.value) return;
  customStatuses.value = [...customStatuses.value, name];
  newStatus.value = "";
};

const discardChanges = () => {
  const saved = savedValues.value;
  protonPath.value = saved.protonPath;
  prefixPath.value = saved.prefixPath;
  rpgmakerLinuxRunnerPath.value = saved.rpgmakerLinuxRunnerPath;
  customStatuses.value = [...saved.customStatuses];
  urmPath.value = saved.urmPath;
  libraryFolder.value = saved.libraryFolder;
  libraryFolderPattern.value = saved.libraryFolderPattern;
  downloadsFolder.value = saved.downloadsFolder;
  downloadDetectionEnabled.value = saved.downloadDetectionEnabled;
  statusError.value = "";
  saveError.value = "";
};

const removeStatus = async (name: string) => {
  try {
    const games = await api.getGames();
    const count = games.filter((game) => game.play_status?.toLowerCase() === name.toLowerCase()).length;
    if (!confirm(`Remove '${name}'? Saving will reset ${count} game${count === 1 ? '' : 's'} to Not Started.`)) return;
    customStatuses.value = customStatuses.value.filter((status) => status !== name);
    statusError.value = "";
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
  if (!confirm(`Remove the collection '${name}'?${note}`)) return;
  const result = await api.deleteCollection(name);
  if (!result.success) {
    collectionError.value = result.error || "Could not remove the collection.";
    return;
  }
  applyCollectionsResult(result);
  collectionError.value = "";
};

const browseUrm = async () => {
  const path = await api.browseUrmFile(urmPath.value);
  if (path) urmPath.value = path;
};
</script>

<template>
  <div class="allow-text-selection p-8 max-w-4xl pb-12">
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
        Configure launch paths and application behavior. Toggles save right away.
      </p>
    </header>

    <div class="space-y-6">
      <!-- General -->
      <section class="settings-card rounded-xl p-6 space-y-5">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconAdjustments class="ui-section-icon" />
          General
        </h3>

        <div class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
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

        <div class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Play Statuses</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Add custom statuses alongside the built-in choices.
            </p>
          </div>
          <div class="flex gap-3">
            <input
              v-model="newStatus"
              @keydown.enter.prevent="addStatus"
              type="text"
              class="settings-input flex-1"
              placeholder="Custom status name"
              aria-label="Custom status name"
            />
            <button @click="addStatus" class="settings-btn">Add Status</button>
          </div>
          <p v-if="statusError" role="alert" class="text-xs text-red-400">{{ statusError }}</p>
          <div
            v-for="name in customStatuses"
            :key="name"
            class="setting-row flex items-center justify-between gap-3 px-4 py-2 rounded-lg"
          >
            <span class="text-sm" style="color: var(--text-primary)">{{ name }}</span>
            <button @click="removeStatus(name)" class="settings-btn" :aria-label="`Remove ${name}`">
              Remove
            </button>
          </div>
        </div>

        <div class="space-y-3">
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

        <div class="space-y-3">
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
      <section class="settings-card rounded-xl p-6 space-y-5">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconDownloadFilled class="ui-section-icon" />
          Library &amp; Downloads
        </h3>

        <div class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Library folder</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              New installs from archives are placed inside this folder.
            </p>
          </div>
          <div class="flex gap-3">
            <input
              v-model="libraryFolder"
              type="text"
              class="settings-input flex-1 font-mono"
              placeholder="/path/to/games"
              aria-label="Library folder"
            />
            <button @click="browseLibraryFolder" class="settings-btn ui-action-btn">Browse</button>
          </div>
          <PathStatus :path="libraryFolder" kind="dir" />
        </div>

        <div class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Folder pattern</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Tokens: <code>{creator}</code> <code>{title}</code> <code>{version}</code> <code>{engine}</code>.
              Empty parts are skipped.
            </p>
          </div>
          <input
            v-model="libraryFolderPattern"
            type="text"
            class="settings-input w-full font-mono"
            placeholder="{creator}/{title}/{version}"
            aria-label="Library folder pattern"
          />
          <p class="text-xs font-mono break-all" style="color: var(--text-muted)">
            Example: {{ patternExample }}
          </p>
        </div>

        <div class="space-y-3">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">Downloads folder</h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              wLib looks here for archives matching games with pending updates.
            </p>
          </div>
          <div class="flex gap-3">
            <input
              v-model="downloadsFolder"
              type="text"
              class="settings-input flex-1 font-mono"
              placeholder="~/Downloads"
              aria-label="Downloads folder"
            />
            <button @click="browseDownloadsFolder" class="settings-btn ui-action-btn">Browse</button>
          </div>
          <PathStatus :path="downloadsFolder" kind="dir" />
        </div>

        <div class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
          <div>
            <h4 class="text-sm font-medium" style="color: var(--text-primary)">
              Detect downloaded updates
            </h4>
            <p class="text-xs mt-1" style="color: var(--text-muted)">
              Offer to install matching archives when the app starts, after update checks, and when the window regains focus.
            </p>
          </div>
          <label class="relative inline-flex items-center cursor-pointer shrink-0">
            <input
              type="checkbox"
              v-model="downloadDetectionEnabled"
              class="sr-only peer"
              aria-label="Detect downloaded updates"
            />
            <div class="ui-toggle"></div>
          </label>
        </div>
      </section>

      <!-- Appearance -->
      <section class="settings-card rounded-xl p-6 space-y-4">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconPalette class="ui-section-icon" />
          Appearance
        </h3>

        <div class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
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

        <div class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
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

        <div class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
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

        <div class="setting-row flex items-center justify-between gap-4 p-4 rounded-lg">
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
      </section>

      <!-- Wine / Proton -->
      <section class="settings-card rounded-xl p-6 space-y-5">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconAutomation class="ui-section-icon" />
          {{ supportsLinuxRuntimes ? "Wine / Proton" : platformCapabilities.platform === 'windows' ? "Windows Environment" : "Platform Environment" }}
        </h3>

        <div
          v-if="platformCapabilities.platform === 'windows'"
          class="setting-row p-4 rounded-lg text-sm"
          style="color: var(--text-secondary)"
        >
          Windows games launch directly. Proton, Wine, Winetricks, Wine
          prefixes, and Linux runtime installers are not used on Windows.
        </div>
        <div
          v-else-if="!supportsLinuxRuntimes"
          class="setting-row p-4 rounded-lg text-sm"
          style="color: var(--text-secondary)"
        >
          Wine, Proton, and Linux runtime installers are not supported on this platform.
        </div>

        <div v-if="supportsLinuxRuntimes">
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
              list="runner-paths"
              aria-describedby="settings-proton-help"
              type="text"
              placeholder="/usr/bin/wine or /path/to/GE-Proton/proton"
              class="settings-input flex-1"
            />
            <datalist id="runner-paths">
              <option v-for="runner in availableRunners" :key="runner.path" :value="runner.path">{{ runner.name }}</option>
            </datalist>
            <button @click="browseProton" class="settings-btn ui-action-btn">
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
            Leave empty to use the system default `wine` command.
          </p>
          <p v-if="protonError" class="copyable-feedback text-xs text-red-400 mt-1">
            {{ protonError }}
          </p>
        </div>

        <div v-if="supportsLinuxRuntimes">
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
              aria-describedby="settings-prefix-help"
              type="text"
              placeholder="Auto-managed (~/.local/share/wLib/prefix) if left empty"
              class="settings-input flex-1"
            />
            <button @click="browsePrefix" class="settings-btn ui-action-btn">
              <IconFolderOpen class="ui-action-icon" />
              Browse
            </button>
          </div>
          <PathStatus :path="prefixPath" kind="dir" />
          <p id="settings-prefix-help" class="text-xs mt-2" style="color: var(--text-muted)">
            The location where game dependencies and save files will be isolated.
          </p>
        </div>

        <div v-if="platformCapabilities.rpgmaker_linux" class="setting-row p-4 rounded-lg">
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
                aria-labelledby="settings-rpgm-label"
                aria-describedby="settings-rpgm-help"
                type="text"
                placeholder="Auto-detect or choose /path/to/rpgmaker-linux"
                class="settings-input flex-1"
              />
              <button @click="browseRpgmakerLinuxRunner" class="settings-btn ui-action-btn">
                <IconFolderOpen class="ui-action-icon" />
                Browse
              </button>
            </div>
            <PathStatus :path="rpgmakerLinuxRunnerPath" kind="file" />

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

      <!-- Tools -->
      <section v-if="supportsLinuxRuntimes" class="settings-card rounded-xl p-6 space-y-4">
        <div>
          <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
            <IconTool class="ui-section-icon" />
            Tools
          </h3>
          <p class="text-xs mt-1" style="color: var(--text-secondary)">
            Fix common issues with certain game engines. Installs go into the default prefix above.
          </p>
        </div>

        <div class="flex flex-col gap-3">
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

        <div class="space-y-1">
          <p v-if="installError" class="copyable-feedback text-xs text-red-400">{{ installError }}</p>
          <p v-if="rtpError" class="copyable-feedback text-xs text-red-400">{{ rtpError }}</p>
          <p v-if="ceError" class="copyable-feedback text-xs text-red-400">{{ ceError }}</p>
        </div>
      </section>

      <!-- Ren'Py -->
      <section class="settings-card rounded-xl p-6 space-y-3">
        <h3 class="ui-section-heading text-lg font-semibold" style="color: var(--text-primary)">
          <IconBook2 class="ui-section-icon" />
          Ren'Py
        </h3>
        <label for="urm-source" class="block text-sm font-medium" style="color: var(--text-secondary)">
          Universal Ren'Py Mod (URM) source file (.rpa)
        </label>
        <div class="flex gap-3">
          <input
            id="urm-source"
            v-model="urmPath"
            aria-describedby="settings-urm-help"
            type="text"
            class="settings-input flex-1"
            placeholder="/path/to/0x52_URM.rpa"
          />
          <button @click="browseUrm" class="settings-btn ui-action-btn">
            <IconFolderOpen class="ui-action-icon" />Browse
          </button>
        </div>
        <p id="settings-urm-help" class="text-xs" style="color: var(--text-muted)">
          Select the .rpa file you downloaded, then enable URM in a Ren'Py game's details.
        </p>
      </section>
    </div>

    <!-- Stays at the bottom of the window while there are unsaved changes. -->
    <div v-if="hasUnsavedChanges" class="sticky bottom-4 z-20 mt-6">
      <div
        class="save-bar rounded-xl px-5 py-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"
        role="region"
        aria-label="Unsaved changes"
      >
        <p v-if="saveError" class="copyable-feedback text-xs text-red-400">{{ saveError }}</p>
        <p v-else class="text-sm font-medium" style="color: var(--text-primary)">
          You have unsaved changes.
        </p>
        <div class="flex gap-2 shrink-0">
          <button @click="discardChanges" :disabled="saving" class="settings-btn disabled:opacity-50">
            Discard
          </button>
          <button
            @click="saveSettings"
            :disabled="saving"
            class="ui-action-btn px-5 py-2 rounded-lg text-sm font-medium disabled:opacity-50 disabled:cursor-wait"
            style="background: var(--brand); color: var(--on-brand); box-shadow: var(--shadow-brand)"
          >
            <IconDeviceFloppyFilled class="ui-action-icon" />
            {{ saving ? "Saving..." : "Save Changes" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
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

.save-bar {
  background: var(--bg-surface);
  border: 1px solid var(--brand-deep);
  box-shadow: var(--shadow-modal);
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
