<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from "vue";
import { modalStack } from "./utils/modalKeyboard";
import { inputModality, listenForPointer, setInputModality } from "./utils/inputModality";
import { gamingMode, tvModeActive } from "./utils/tvMode";
import ControllerHints from "./components/ControllerHints.vue";
import ControllerSelect from "./components/ControllerSelect.vue";
import { startControllerInput } from "./utils/controllerInput";
import { startPoller, type Poller } from "./utils/polling";
import { updateCheckRunning } from "./composables/useUpdateCheckState";
import { dispatchController } from "./utils/controllerActions";
import { useRoute, useRouter } from "vue-router";
import {
  IconBrandGithub,
  IconCloud,
  IconDatabaseExport,
  IconLayout2,
  IconMoon,
  IconDeviceDesktop,
  IconLayoutSidebarLeftCollapse,
  IconLayoutSidebarLeftExpand,
  IconPuzzle,
  IconReload,
  IconDownload,
  IconSettings,
  IconSun,
  IconX,
} from "@tabler/icons-vue";
import { api, onWebviewReady, type DownloadOffer, type GameRecord } from "./services/api";
import { isNewerVersion } from "./utils/appVersion";
import { motionEnabled } from "./utils/motionPreference";
import {
  nextThemePreference,
  setThemePreference,
  themeCycleLabel,
  themePreference,
} from "./utils/themePreference";
import { isNavAutoCollapsed, libraryFiltersPaneOpen, navAutoOverride } from "./utils/navLayout";
import { confirmAction } from "./utils/confirm";
import { trackSyncProblem } from "./utils/syncProblem";
import { gamesStore } from "./composables/useGamesStore";
import { answerPairingRequests, EXTENSION_PAIRING_EVENT } from "./utils/extensionPairing";
import { dismissToast, notify, notifyUndo, runNewestUndo, runUndo, setUndoHeld, toasts } from "./utils/toast";
import { isPendingRemoval } from "./utils/pendingRemovals";
import { hasAvailableUpdate } from "./utils/libraryGames";
import { changedInfoOfFinishedRefresh, shouldScanDownloadsAfterCheck } from "./utils/updateCheck";
import {
  INSTALL_ARCHIVE_EVENT,
  type InstallArchiveRequest,
  type InstallArchiveSeed,
} from "./utils/installArchive";
import InstallArchiveModal from "./components/modals/InstallArchiveModal.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import UpdateDownloadModal from "./components/modals/UpdateDownloadModal.vue";
import {useUpdateDownloads} from "./composables/useUpdateDownloads";
const updateDownloads = useUpdateDownloads();

import { extensionAddQuery, extensionOpenQuery, type ExtensionGameDetail } from "./utils/storeLinks";
import { convertSteamCovers } from "./utils/steamCovers";

const router = useRouter();
const route = useRoute();
let stopControllerInput: (() => void) | undefined;
let stopPointer: (() => void) | undefined;
const openControllerKeyboard = () => { if (gamingMode.value) void api.openSteamKeyboard(); };
onMounted(() => { window.addEventListener("wlib-controller-text", openControllerKeyboard); stopPointer = listenForPointer(); stopControllerInput = startControllerInput(action => { setInputModality("controller"); dispatchController(action, () => router.back()); window.dispatchEvent(new Event("wlib-controller-action")); }); });
onUnmounted(() => { window.removeEventListener("wlib-controller-text", openControllerKeyboard); stopControllerInput?.(); stopPointer?.(); });
const mainRef = ref<HTMLElement | null>(null);
// Give controller users the full Undo window without requiring them to cross
// the whole page first. Focusing the button holds the timer until focus leaves.
// When that toast goes away, focus goes back to where the user was.
let undoFocusReturn: { id: number; element: HTMLElement } | null = null;
watch(() => [...toasts].reverse().find((toast) => toast.undo)?.id, (id, previous) => {
  if (id === undefined || (previous !== undefined && id <= previous) || inputModality.value !== "controller" || modalStack.length) return;
  const button = mainRef.value?.querySelector<HTMLButtonElement>(`[data-undo-id="${id}"]`);
  if (!button) return;
  const before = document.activeElement;
  if (before instanceof HTMLElement && before !== document.body) undoFocusReturn = { id, element: before };
  button.focus();
}, { flush: "post" });
watch(() => toasts.map((toast) => toast.id), (ids) => {
  if (!undoFocusReturn || ids.includes(undoFocusReturn.id)) return;
  const { element } = undoFocusReturn;
  undoFocusReturn = null;
  const active = document.activeElement;
  if ((!active || active === document.body) && element.isConnected) element.focus();
}, { flush: "post" });

const hasAppUpdate = ref(false);
const currentVersion = ref("");
const latestVersion = ref("");
const navCollapsedStorageKey = "wlib-nav-collapsed";
const getInitialNavCollapsed = (): boolean => {
  try {
    return localStorage.getItem(navCollapsedStorageKey) === "true";
  } catch (error) {
    console.warn("Could not read nav state", error);
    return false;
  }
};
const isNavCollapsed = ref(getInitialNavCollapsed());
// The Library filters pane collapses the sidebar while it is open; this state is never saved.
const navAutoCollapsed = computed(() =>
  isNavAutoCollapsed(route.name, libraryFiltersPaneOpen.value, navAutoOverride.value),
);
const navCollapsed = computed(
  () => tvModeActive.value || isNavCollapsed.value || navAutoCollapsed.value,
);
// The override lasts until the pane closes or the user leaves the Library.
watch(
  () => [libraryFiltersPaneOpen.value, route.name] as const,
  ([paneOpen, name]) => {
    if (!paneOpen || name !== "library") navAutoOverride.value = false;
  },
);
const fadeTransitionName = computed(() => (motionEnabled.value ? "fade" : ""));
const updateCountGames = gamesStore.games;
const availableUpdateCount = computed(() => updateCountGames.value.filter((game) => !isPendingRemoval(game.id) && hasAvailableUpdate(game)).length);
let updatePoller: Poller | null = null;
let lastUpdateProgress = "";
let pollingUpdates = false;
let wasUpdateRunning = false;
let lastInfoRefreshToast = "";
const notifiedFinishedIds = new Set<number>();

// Sync problems are shown on the Sync nav item and once as a toast, so they
// are not missed while another view is open.
const syncAttention = ref(false);
const syncState = ref("off");
const downloadsNavTitle = computed(() => {
  const {count, warning} = updateDownloads.badge.value;
  const parts = ['Downloads'];
  if (count) parts.push(`${count} unfinished`);
  if (warning) parts.push('a download needs you');
  return parts.join(', ');
});
const syncNavTitle = computed(() => {
  if (navCollapsed.value) return "Sync";
  if (syncAttention.value) return "Sync needs attention";
  if (syncState.value === "offline") return "Sync location is offline";
  if (syncState.value === "paused") return "Sync is paused";
  return "";
});
let syncPoller: Poller | null = null;
let lastSyncProblem = "";
let syncToastId: number | null = null;
const closeSyncToast = () => {
  if (syncToastId !== null) dismissToast(syncToastId);
  syncToastId = null;
};
const pollSyncStatus = async () => {
  try {
    const status = await api.getSyncStatus();
    const update = trackSyncProblem(lastSyncProblem, status);
    syncState.value = status.state || "off";
    syncAttention.value = update.attention;
    if (update.close) closeSyncToast();
    // On the Sync page the problem is already in view; record it so leaving
    // the page does not show it again.
    // A new problem replaces the toast of the previous one, also on that page.
    if (update.show) closeSyncToast();
    if (update.show && router.currentRoute.value.path !== "/sync") {
      syncToastId = notify(update.show, "error", "Library sync", { label: "Open Sync", run: () => void router.push("/sync") });
    }
    lastSyncProblem = update.key;
  } catch (error) {
    console.error("Failed to read sync status", error);
  }
};

// Install-from-archive dialog (global so any view or toast can open it).
const showInstallModal = ref(false);
watch(showInstallModal, value => updateDownloads.setInstallDialogOpen(value));
const installGame = ref<GameRecord | null>(null);
const installArchivePath = ref("");
const installSeed = ref<InstallArchiveSeed | undefined>();

let installRequestId = 0;

const handleInstallArchiveRequest = async (event: Event) => {
  const detail = (event as CustomEvent<InstallArchiveRequest>).detail || {};
  const requestId = ++installRequestId;
  let game: GameRecord | null = null;
  if (detail.gameId != null) {
    try {
      await gamesStore.refresh();
      game = gamesStore.games.value.find((entry) => entry.id === detail.gameId) || null;
    } catch (error) {
      console.error("Failed to resolve game for install dialog", error);
    }
  }
  // A newer request superseded this one while the game was resolving.
  if (requestId !== installRequestId) return;
  // Set game and archive together so an open dialog re-seeds once per request.
  installGame.value = game;
  installArchivePath.value = detail.archivePath || "";
  installSeed.value = detail.seed;
  showInstallModal.value = true;
};

// Downloads-folder scan (throttled, no background watcher).
let lastDownloadScan = 0;
const shownOfferKeys = new Set<string>();
const offerToastByKey = new Map<string, number>();

const dismissOfferToast = (toastId: number) => {
  for (const [key, id] of offerToastByKey) {
    if (id === toastId) {
      offerToastByKey.delete(key);
      void api.dismissDownloadOffer(key);
      break;
    }
  }
  dismissToast(toastId);
};

const scanDownloadOffers = async () => {
  const now = Date.now();
  if (now - lastDownloadScan < 60_000) return;
  lastDownloadScan = now;
  let offers: DownloadOffer[] = [];
  try {
    const result = await api.findDownloadOffers();
    if (!result?.success) return;
    offers = result.offers || [];
  } catch (error) {
    console.error("Failed to scan Downloads folder", error);
    return;
  }
  for (const offer of offers) {
    if (shownOfferKeys.has(offer.key)) continue;
    shownOfferKeys.add(offer.key);
    notify(
      `${offer.game_title}: version ${offer.archive_version || offer.latest_version} is waiting in your Downloads folder.`,
      "info",
      "Update downloaded",
      {
        label: "Install update",
        run: () => {
          offerToastByKey.delete(offer.key);
          window.dispatchEvent(
            new CustomEvent<InstallArchiveRequest>(INSTALL_ARCHIVE_EVENT, {
              detail: { gameId: offer.game_id, archivePath: offer.archive_path },
            }),
          );
        },
      },
    );
    const toast = toasts[toasts.length - 1];
    if (toast) offerToastByKey.set(offer.key, toast.id);
  }
};

const handleWindowFocus = () => {
  void reportWindowFocus();
  void scanDownloadOffers();
  // Polls slow down while the window is inactive; catch up at once.
  updatePoller?.runNow();
  syncPoller?.runNow();
  updateDownloads.refresh();
};

const reportWindowFocus = async () => {
  try {
    await api.setWindowFocused(document.visibilityState === "visible" && document.hasFocus());
  } catch (error) {
    console.error("Failed to report window focus", error);
  }
};

// The Library and Updates views refresh the same store, so one library
// change costs one load, not one per listener.
const refreshUpdateCount = () => gamesStore.refresh();

// One toast per finished "Refresh F95 info" run. It lives here, not in the
// Library, so it still appears when the user leaves the Library during the check.
const announceInfoRefresh = (status: Awaited<ReturnType<typeof api.getUpdateStatus>>) => {
  // A new run must toast even when it changes the same games as the last one.
  if (status.running && status.refresh_info) lastInfoRefreshToast = "";
  const changed = changedInfoOfFinishedRefresh(status);
  if (!changed.length) return;
  const signature = JSON.stringify(changed);
  if (signature === lastInfoRefreshToast) return;
  lastInfoRefreshToast = signature;
  notifyUndo(`F95 info changed for ${changed.length} game${changed.length === 1 ? "" : "s"}.`, () =>
    api.withLibraryBatch(async () => {
      let failures = 0;
      for (const { id, previous } of changed) {
        try { if (!(await api.updateGame(id, { ...previous })).success) failures++; }
        catch { failures++; }
      }
      window.dispatchEvent(new Event("wlib-refresh-library"));
      if (failures) throw new Error(`Could not restore ${failures} game${failures === 1 ? "" : "s"}.`);
    }));
};

const pollLibraryUpdates = async () => {
  if (pollingUpdates) return;
  pollingUpdates = true;
  try {
    const status = await api.getUpdateStatus();
    if (status.success === false) return;
    if (status.running && !wasUpdateRunning) notifiedFinishedIds.clear();
    for (const result of status.results) {
      if (result.newly_finished && !notifiedFinishedIds.has(result.id)) {
        notifiedFinishedIds.add(result.id);
        notify(`${result.title} is finished`, "success");
      }
    }
    const progress = JSON.stringify([status.running, status.checked, status.total, status.results]);
    const progressChanged = progress !== lastUpdateProgress;
    if (progressChanged) {
      lastUpdateProgress = progress;
      window.dispatchEvent(new Event("wlib-refresh-library"));
    }
    // A short check can start and finish between two polls; don't require
    // having observed `running` before offering a matching downloaded archive.
    if (shouldScanDownloadsAfterCheck(status, wasUpdateRunning, progressChanged)) {
      void scanDownloadOffers();
    }
    wasUpdateRunning = !!status.running;
    updateCheckRunning.value = wasUpdateRunning;
    announceInfoRefresh(status);
  } catch (error) {
    console.error("Failed to refresh update progress", error);
  } finally {
    pollingUpdates = false;
  }
};

const releaseToastPointer = (id: number, event: MouseEvent) => {
  const element = event.currentTarget as HTMLElement;
  setUndoHeld(id, element.contains(document.activeElement));
};

// Ctrl+N opens Add Game from any view; LibraryView handles ?action=add.
const handleGlobalShortcut = (event: KeyboardEvent) => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return;
  if (document.querySelector('[aria-modal="true"]')) return;
  const target = event.target instanceof Element ? event.target : null;
  if (event.key.toLowerCase() === "z") {
    if (target?.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])')) return;
    event.preventDefault();
    void runNewestUndo();
    return;
  }
  if (event.key.toLowerCase() !== "n") return;
  event.preventDefault();
  void router.push({ path: "/", query: { action: "add" } });
};

const toggleTheme = () => setThemePreference(nextThemePreference(themePreference.value));
const themeControlLabel = computed(() => themeCycleLabel(themePreference.value));

const toggleNavCollapse = () => {
  // While the filters pane collapses the sidebar, expanding and collapsing
  // again only change the override, so the saved choice stays as it is.
  if (navAutoCollapsed.value && !isNavCollapsed.value) {
    navAutoOverride.value = true;
    return;
  }
  if (navAutoOverride.value && !isNavCollapsed.value) {
    navAutoOverride.value = false;
    return;
  }
  // The user's own collapsed choice under the open pane: expand it for real.
  if (navAutoCollapsed.value) navAutoOverride.value = true;
  isNavCollapsed.value = !isNavCollapsed.value;
  try {
    localStorage.setItem(
      navCollapsedStorageKey,
      isNavCollapsed.value ? "true" : "false",
    );
  } catch (error) {
    console.warn("Could not save nav state", error);
  }
};

const handleExtensionAdd = (event: Event) => {
  const data = (event as CustomEvent<ExtensionGameDetail>).detail;
  if (data && (data.url || data.links?.length)) {
    router.push({ path: "/", query: extensionAddQuery(data) }).catch((err) => console.error("Router error:", err));
  }
};

const handleExtensionOpen = (event: Event) => {
  const data = (event as CustomEvent<ExtensionGameDetail>).detail;
  if (data && (data.url || data.gameId)) {
    router.push({ path: "/", query: extensionOpenQuery(data) }).catch((err) => console.error("Router error:", err));
  }
};

const handleSteamCovers = () => { void convertSteamCovers(api); };

// A browser extension that is not allowed yet asked to connect.
const handleExtensionPairing = () => {
  answerPairingRequests(api, confirmAction).catch((error) => console.error("Extension pairing failed", error));
};

const handleLaunchError = (event: Event) => {
  const message = (event as CustomEvent<{ error: string }>).detail?.error;
  if (message) notify(message, "error", "Game launch failed");
};

onMounted(() => {
  window.addEventListener("wlib-launch-error", handleLaunchError);
  window.addEventListener("wlib-steam-covers", handleSteamCovers);
  window.addEventListener("keydown", handleGlobalShortcut);
  window.addEventListener("wlib-extension-add", handleExtensionAdd);
  window.addEventListener("wlib-extension-open", handleExtensionOpen);
  window.addEventListener(EXTENSION_PAIRING_EVENT, handleExtensionPairing);
  window.addEventListener("wlib-refresh-library", refreshUpdateCount);
  window.addEventListener(INSTALL_ARCHIVE_EVENT, handleInstallArchiveRequest);

  // Check for App Updates on Startup
  onWebviewReady(async () => {
    void refreshUpdateCount();
    handleSteamCovers();
    // Requests that arrived before the window could show a question.
    handleExtensionPairing();
    void api.getStartupLaunchError().then(({ error }) => { if (error) notify(error, "error", "Game launch failed"); });
    updateDownloads.startPolling();
    void scanDownloadOffers();
    // Listen only once the backend is injected: a focus event at startup would
    // otherwise scan the mocks and the 60 s throttle would skip this real scan.
    window.addEventListener("focus", handleWindowFocus);
    window.addEventListener("blur", reportWindowFocus);
    document.addEventListener("visibilitychange", reportWindowFocus);
    void reportWindowFocus();
    updatePoller = startPoller(pollLibraryUpdates, () => 5000);
    updatePoller.runNow();
    syncPoller = startPoller(pollSyncStatus, () => 15000);
    syncPoller.runNow();
    try {
      const extensionSync = await api.getStartupExtensionSyncStatus();
      if (extensionSync?.success && extensionSync?.updated) {
        const version = extensionSync.installed_version || extensionSync.bundled_version;
        notify(
          version
            ? `Browser extension updated to v${version}. Chromium browsers apply it on your next F95Zone page; Firefox updates automatically.`
            : "Browser extension updated. Chromium browsers apply it on your next F95Zone page; Firefox updates automatically.",
          "info",
          "Extension Updated",
        );
      }

      const versionInfo = await api.get_app_version();
      currentVersion.value = versionInfo?.version || "";

      const release = await api.check_app_updates();
      if (release && release.success && release.version) {
        latestVersion.value = release.version;
        if (isNewerVersion(release.version, currentVersion.value)) {
          hasAppUpdate.value = true;
        }
      }
    } catch (e) {
      console.error("Failed to check for app updates globally", e);
    }

    // Run the scheduled (weekly/monthly) game update check regardless of the open view.
    try {
      await api.maybeAutoCheck();
    } catch (e) {
      console.error("Auto-check trigger failed", e);
    }
  });
});

onUnmounted(() => {
  updateDownloads.stopPolling();
  window.removeEventListener("wlib-launch-error", handleLaunchError);
  window.removeEventListener("wlib-steam-covers", handleSteamCovers);
  window.removeEventListener("keydown", handleGlobalShortcut);
  window.removeEventListener("wlib-extension-add", handleExtensionAdd);
  window.removeEventListener("wlib-extension-open", handleExtensionOpen);
  window.removeEventListener(EXTENSION_PAIRING_EVENT, handleExtensionPairing);
  window.removeEventListener("wlib-refresh-library", refreshUpdateCount);
  window.removeEventListener(INSTALL_ARCHIVE_EVENT, handleInstallArchiveRequest);
  window.removeEventListener("focus", handleWindowFocus);
  window.removeEventListener("blur", reportWindowFocus);
  document.removeEventListener("visibilitychange", reportWindowFocus);
  updatePoller?.stop();
  syncPoller?.stop();
});
</script>

<template>
  <ControllerSelect />
  <ControllerHints v-if="inputModality === 'controller' || tvModeActive" />
  <div class="app-shell flex h-[var(--app-vh)] w-[var(--app-vw)] overflow-hidden">
    <!-- Sidebar -->
    <aside
      :class="[
        navCollapsed ? 'w-16' : 'w-64',
        'flex flex-col justify-between shrink-0 overflow-y-auto overflow-x-hidden collapse-width-transition',
      ]"
      style="
        background: var(--bg-surface);
        border-right: 1px solid var(--border);
      "
    >
      <div>
        <!-- Same top padding in both states, so the logo does not move on collapse. -->
        <div :class="navCollapsed ? 'px-2 pt-8 pb-6' : 'pl-6 pr-4 pt-8 pb-8'">
          <div
            class="flex items-center"
            :class="navCollapsed ? 'justify-center' : 'gap-3'"
          >
            <img
              src="/icon.svg"
              alt="wLib Logo"
              class="w-8 h-8 drop-shadow-lg shrink-0"
            />
            <h1
              class="brand-gradient-text text-2xl font-extrabold bg-clip-text text-transparent whitespace-nowrap transition-opacity duration-150"
              :class="navCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
            >
              wLib
            </h1>
          </div>
          <p
            class="text-xs uppercase tracking-widest font-semibold whitespace-nowrap transition-opacity duration-150"
            :class="
              navCollapsed
                ? 'opacity-0 h-0 mt-0 overflow-hidden'
                : 'opacity-100 mt-1.5'
            "
            style="color: var(--text-muted)"
          >
            Game Manager
          </p>
        </div>

        <nav :class="navCollapsed ? 'px-2 space-y-1 mt-2' : 'px-4 space-y-1 mt-2'">
          <router-link
            to="/"
            :title="navCollapsed ? 'Library' : ''"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              navCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconLayout2 class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="navCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Library</span
            >
          </router-link>

          <router-link
            to="/updates"
            :title="`Updates${availableUpdateCount ? ` (${availableUpdateCount} available)` : ''}`"
            :aria-label="`Updates, ${availableUpdateCount} available`"
            :class="[
              'nav-link relative flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              navCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconReload class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="navCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Updates</span
            >
            <span
              v-if="availableUpdateCount"
              class="rounded-full px-1.5 min-w-5 text-center text-[0.625rem] font-bold"
              :class="navCollapsed ? 'absolute -top-1 -right-1' : 'ml-auto'"
              style="background: var(--brand); color: var(--on-brand)"
              aria-hidden="true"
              >{{ availableUpdateCount }}</span
            >
          </router-link>

          <router-link
            to="/downloads"
            :title="downloadsNavTitle"
            :aria-label="downloadsNavTitle"
            :class="[
              'nav-link relative flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              navCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconDownload class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="navCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Downloads</span
            >
            <span
              v-if="updateDownloads.badge.value.count"
              class="rounded-full px-1.5 min-w-5 text-center text-[0.625rem] font-bold"
              :class="navCollapsed ? 'absolute -top-1 -right-1' : 'ml-auto'"
              style="background: var(--brand); color: var(--on-brand)"
              aria-hidden="true"
              data-testid="downloads-count"
              >{{ updateDownloads.badge.value.count }}</span
            >
            <span
              v-if="updateDownloads.badge.value.warning"
              class="rounded-full w-2 h-2"
              :class="navCollapsed ? 'absolute top-1 left-1' : updateDownloads.badge.value.count ? '' : 'ml-auto'"
              style="background: #ef4444"
              aria-hidden="true"
              data-testid="downloads-warning"
            ></span>
          </router-link>

          <router-link
            to="/import-export"
            :title="navCollapsed ? 'Import / Export' : ''"
            :aria-label="navCollapsed ? 'Import / Export' : undefined"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium w-full text-left',
              navCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconDatabaseExport class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="navCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Import / Export</span
            >
          </router-link>

          <router-link
            to="/sync"
            :title="syncNavTitle"
            :class="[
              'nav-link relative flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              navCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconCloud class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="navCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Sync</span
            >
            <span
              v-if="syncAttention"
              class="rounded-full w-2 h-2"
              :class="navCollapsed ? 'absolute top-1 right-1' : 'ml-auto'"
              style="background: #ef4444"
              aria-label="Sync needs attention"
              role="img"
            ></span>
          </router-link>

          <router-link
            to="/extension"
            :title="navCollapsed ? 'Extension' : ''"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              navCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconPuzzle class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="navCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Extension</span
            >
          </router-link>

          <router-link
            to="/settings"
            :title="navCollapsed ? 'Settings' : ''"
            :class="[
              'nav-link flex items-center px-4 py-2.5 rounded-lg text-sm font-medium',
              navCollapsed ? 'justify-center' : 'gap-3',
            ]"
            active-class="nav-active"
            exact-active-class="nav-active"
          >
            <IconSettings class="shrink-0" :size="18" />
            <span
              class="whitespace-nowrap transition-opacity duration-150"
              :class="navCollapsed ? 'opacity-0 w-0 overflow-hidden' : 'opacity-100'"
              >Settings</span
            >
          </router-link>
        </nav>
      </div>

      <!-- Footer: one icon strip (theme, links, collapse) and the app version line under it -->
      <div
        class="footer-block flex flex-col"
        :class="navCollapsed ? 'items-center gap-1 px-2 py-3' : 'gap-1 px-6 py-3'"
        style="border-top: 1px solid var(--border)"
      >
        <div
          class="flex items-center"
          :class="navCollapsed ? 'flex-col gap-1' : 'gap-1'"
          data-testid="sidebar-footer-strip"
        >
          <button
            type="button"
            @click="toggleTheme"
            class="nav-link footer-icon flex items-center justify-center rounded-lg p-2"
            :title="themeControlLabel"
            :aria-label="themeControlLabel"
          >
            <IconDeviceDesktop v-if="themePreference === 'system'" :size="18" aria-hidden="true" />
            <IconSun v-else-if="themePreference === 'light'" :size="18" aria-hidden="true" />
            <IconMoon v-else :size="18" aria-hidden="true" />
          </button>

          <a
            v-if="!navCollapsed"
            href="https://github.com/kirin-3/wLib"
            target="_blank"
            rel="noopener noreferrer"
            class="nav-link footer-icon flex items-center justify-center rounded-lg p-2"
            title="View Source on GitHub"
            aria-label="View Source on GitHub"
          >
            <IconBrandGithub :size="18" aria-hidden="true" />
          </a>

          <a
            v-if="!navCollapsed"
            href="https://buymeacoffee.com/thekirin"
            target="_blank"
            rel="noopener noreferrer"
            class="nav-link footer-icon flex items-center justify-center rounded-lg p-2"
            title="Buy Me a Coffee"
            aria-label="Buy Me a Coffee"
          >
            <!-- Buy Me a Coffee logo from Simple Icons (CC0) -->
            <svg class="w-[18px] h-[18px]" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M20.216 6.415l-.132-.666c-.119-.598-.388-1.163-1.001-1.379-.197-.069-.42-.098-.57-.241-.152-.143-.196-.366-.231-.572-.065-.378-.125-.756-.192-1.133-.057-.325-.102-.69-.25-.987-.195-.4-.597-.634-.996-.788a5.723 5.723 0 00-.626-.194c-1-.263-2.05-.36-3.077-.416a25.834 25.834 0 00-3.7.062c-.915.083-1.88.184-2.75.5-.318.116-.646.256-.888.501-.297.302-.393.77-.177 1.146.154.267.415.456.692.58.36.162.737.284 1.123.366 1.075.238 2.189.331 3.287.37 1.218.05 2.437.01 3.65-.118.299-.033.598-.073.896-.119.352-.054.578-.513.474-.834-.124-.383-.457-.531-.834-.473-.466.074-.96.108-1.382.146-1.177.08-2.358.082-3.536.006a22.228 22.228 0 01-1.157-.107c-.086-.01-.18-.025-.258-.036-.243-.036-.484-.08-.724-.13-.111-.027-.111-.185 0-.212h.005c.277-.06.557-.108.838-.147h.002c.131-.009.263-.032.394-.048a25.076 25.076 0 013.426-.12c.674.019 1.347.067 2.017.144l.228.031c.267.04.533.088.798.145.392.085.895.113 1.07.542.055.137.08.288.111.431l.319 1.484a.237.237 0 01-.199.284h-.003c-.037.006-.075.01-.112.015a36.704 36.704 0 01-4.743.295 37.059 37.059 0 01-4.699-.304c-.14-.017-.293-.042-.417-.06-.326-.048-.649-.108-.973-.161-.393-.065-.768-.032-1.123.161-.29.16-.527.404-.675.701-.154.316-.199.66-.267 1-.069.34-.176.707-.135 1.056.087.753.613 1.365 1.37 1.502a39.69 39.69 0 0011.343.376.483.483 0 01.535.53l-.071.697-1.018 9.907c-.041.41-.047.832-.125 1.237-.122.637-.553 1.028-1.182 1.171-.577.131-1.165.2-1.756.205-.656.004-1.31-.025-1.966-.022-.699.004-1.556-.06-2.095-.58-.475-.458-.54-1.174-.605-1.793l-.731-7.013-.322-3.094c-.037-.351-.286-.695-.678-.678-.336.015-.718.3-.678.679l.228 2.185.949 9.112c.147 1.344 1.174 2.068 2.446 2.272.742.12 1.503.144 2.257.156.966.016 1.942.053 2.892-.122 1.408-.258 2.465-1.198 2.616-2.657.34-3.332.683-6.663 1.024-9.995l.215-2.087a.484.484 0 01.39-.426c.402-.078.787-.212 1.074-.518.455-.488.546-1.124.385-1.766zm-1.478.772c-.145.137-.363.201-.578.233-2.416.359-4.866.54-7.308.46-1.748-.06-3.477-.254-5.207-.498-.17-.024-.353-.055-.47-.18-.22-.236-.111-.71-.054-.995.052-.26.152-.609.463-.646.484-.057 1.046.148 1.526.22.577.088 1.156.159 1.737.212 2.48.226 5.002.19 7.472-.14.45-.06.899-.13 1.345-.21.399-.072.84-.206 1.08.206.166.281.188.657.162.974a.544.544 0 01-.169.364zm-6.159 3.9c-.862.37-1.84.788-3.109.788a5.884 5.884 0 01-1.569-.217l.877 9.004c.065.78.717 1.38 1.5 1.38 0 0 1.243.065 1.658.065.447 0 1.786-.065 1.786-.065.783 0 1.434-.6 1.499-1.38l.94-9.95a3.996 3.996 0 00-1.322-.238c-.826 0-1.491.284-2.26.613z" />
            </svg>
          </a>

          <button
            v-if="!tvModeActive"
            type="button"
            @click="toggleNavCollapse"
            class="nav-link footer-icon flex items-center justify-center rounded-lg p-2"
            :class="navCollapsed ? '' : 'ml-auto'"
            :title="navCollapsed ? 'Expand navigation' : 'Collapse navigation'"
            :aria-label="navCollapsed ? 'Expand navigation' : 'Collapse navigation'"
          >
            <IconLayoutSidebarLeftExpand v-if="navCollapsed" :size="18" aria-hidden="true" />
            <IconLayoutSidebarLeftCollapse v-else :size="18" aria-hidden="true" />
          </button>
        </div>

        <router-link
          v-if="hasAppUpdate"
          to="/updates"
          class="app-version-update font-mono font-bold rounded-md"
          :class="navCollapsed ? 'px-1 py-0.5 text-[0.625rem] text-center' : 'self-start px-2 py-0.5 text-xs'"
          :title="`${latestVersion} available. Open Updates`"
          :aria-label="`${latestVersion} available. Open Updates`"
          data-testid="sidebar-app-version"
        >{{ latestVersion }}<span v-if="!navCollapsed"> available</span></router-link>
        <span
          v-else
          class="app-version font-mono font-semibold"
          :class="navCollapsed ? 'text-[0.625rem] text-center' : 'px-2 text-xs'"
          :title="`wLib ${currentVersion}`"
          data-testid="sidebar-app-version"
        >{{ currentVersion }}</span>
      </div>
    </aside>

    <!-- Main Content Area -->
    <main ref="mainRef" class="flex-1 overflow-y-auto relative">
      <transition-group
        :name="fadeTransitionName"
        tag="div"
        class="allow-text-selection fixed bottom-5 right-5 z-[60] flex w-full max-w-sm flex-col gap-2"
      >
        <div
          v-for="toast in toasts"
          :key="toast.id"
          :role="toast.type === 'error' ? 'alert' : 'status'"
          class="toast flex items-start gap-3 rounded-xl px-4 py-3 shadow-2xl backdrop-blur-sm"
          :class="`toast--${toast.type}`"
          @mouseenter="setUndoHeld(toast.id, true)"
          @mouseleave="releaseToastPointer(toast.id, $event)"
          @focusin="setUndoHeld(toast.id, true)"
          @focusout="setUndoHeld(toast.id, ($event.currentTarget as HTMLElement).matches(':hover') || ($event.currentTarget as HTMLElement).contains(($event as FocusEvent).relatedTarget as Node | null))"
        >
          <div class="min-w-0 flex-1">
            <p v-if="toast.title" class="text-sm font-semibold">{{ toast.title }}</p>
            <p class="copyable-feedback max-h-60 overflow-y-auto pr-1 text-xs leading-5" :class="toast.title ? 'mt-1' : ''">
              {{ toast.message }}
            </p>
            <button v-if="toast.undo" :data-undo-id="toast.id" class="toast-action mt-2 px-3 py-1.5 rounded-lg text-xs font-bold" @click="runUndo(toast.id)">Undo</button>
            <button
              v-if="toast.action"
              @click="dismissToast(toast.id); toast.action.run()"
              class="toast-action mt-2 px-3 py-1.5 rounded-lg text-xs font-bold"
            >
              {{ toast.action.label }}
            </button>
          </div>
          <button
            @click="dismissOfferToast(toast.id)"
            class="theme-toggle -mr-1 rounded-md p-1 shrink-0"
            title="Dismiss"
            aria-label="Dismiss notification"
          >
            <IconX class="w-4 h-4" />
          </button>
        </div>
      </transition-group>

      <router-view v-slot="{ Component }">
        <transition
          :name="fadeTransitionName"
          mode="out-in"
          @before-enter="mainRef?.scrollTo(0, 0)"
        >
          <component :is="Component" />
        </transition>
      </router-view>

      <UpdateDownloadModal />
      <ConfirmDialog />
      <InstallArchiveModal
        v-model="showInstallModal"
        :game="installGame"
        @installed="updateDownloads.markInstalled"
        :preselected-archive="installArchivePath"
        :seed="installSeed"
      />
    </main>

  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.toast {
  background: color-mix(in srgb, var(--bg-surface) 88%, var(--brand) 12%);
  border: 1px solid var(--brand-deep);
  color: var(--text-primary);
}
.toast--success {
  background: color-mix(in srgb, var(--bg-surface) 85%, var(--success-text) 15%);
  border-color: var(--success-border);
}
.toast--error {
  background: color-mix(in srgb, var(--bg-surface) 85%, var(--danger-text) 15%);
  border-color: var(--danger-border);
  color: var(--danger-text);
}

.toast-action {
  background: var(--brand);
  color: var(--on-brand);
  box-shadow: var(--shadow-brand);
}
.toast-action:hover {
  filter: brightness(1.1);
}

.app-shell {
  background: var(--bg-base);
  color: var(--text-primary);
}

.brand-gradient-text {
  background-image: linear-gradient(to right, var(--brand), var(--brand-deep));
}

.nav-link {
  color: var(--text-secondary);
  transition: background-color 0.15s ease, color 0.15s ease;
}
.nav-link:hover {
  background: var(--bg-raised);
  color: var(--text-primary);
}
.nav-active {
  background: var(--brand-glow) !important;
  color: var(--brand) !important;
  font-weight: 600;
}

.app-version { color: var(--text-secondary); word-break: break-all; }
.app-version-update {
  background: var(--brand-glow-strong);
  color: var(--brand);
  border: 1px solid var(--brand-deep);
  word-break: break-all;
}
.app-version-update:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.theme-toggle {
  color: var(--text-muted);
  transition: background-color 0.15s ease, color 0.15s ease;
}
.theme-toggle:hover {
  color: var(--brand);
  background: var(--bg-raised);
}
</style>
