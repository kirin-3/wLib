<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { gameCoverSrc } from "../utils/gameCover";
import CoverImage from "../components/CoverImage.vue";
import CoverPlaceholder from "../components/CoverPlaceholder.vue";
import { refreshRunningGames } from "../composables/useRunningGames";
import { ref, onMounted, onUnmounted, computed, watch, nextTick } from "vue";
import MenuButton, { type MenuButtonItem } from "../components/MenuButton.vue";
import UpdateSettingsDialog from "../components/UpdateSettingsDialog.vue";
import { useRouter } from "vue-router";
import {
  IconArrowUp,
  IconCheck,
  IconDownload,
  IconExternalLink,
  IconFileZip,
  IconInfoCircle,
  IconLoader2,
  IconPlayerSkipForward,
  IconReload,
  IconSettings,
  IconX,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import { notify, notifyError, notifyUndo } from "../utils/toast";
import { runBulk, versionSnapshot } from "../utils/bulkActions";
import { isPendingRemoval } from "../utils/pendingRemovals";
import { gamesStore } from "../composables/useGamesStore";
import { openInstallArchive } from "../utils/installArchive";
import {useUpdateDownloads} from "../composables/useUpdateDownloads";
import { gameDownloadStatus, isUnfinished } from "../utils/downloadQueue";
const updateDownloads = useUpdateDownloads();
import { hasAvailableUpdate } from "../utils/libraryGames";
import { getSafeNotesLinkUrl, renderedLinkHref } from "../utils/notesLinks";
import { libraryOpenQuery } from "../utils/storeLinks";
import { isNewerVersion } from "../utils/appVersion";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import {
  shouldAdoptBackgroundCheck,
  updateCheckProgressLabel,
} from "../utils/updateCheck";
import type { BulkUpdateStartResponse, DownloadOffer, GameRecord, UpdateStatusResponse } from "../services/api";
import { marked } from "marked";
import DOMPurify from "dompurify";

interface AppUpdateState {
  version: string;
  changelogHtml: string;
  url: string;
}

const router = useRouter();
const games = gamesStore.games;
const markingUpdatedIds = ref(new Set<number>());
const recheckingIds = ref(new Set<number>());
const downloadOffers = ref(new Map<number, DownloadOffer>());

let offersRequest = 0;
const loadDownloadOffers = async () => {
  const request = ++offersRequest;
  try {
    const result = await api.findDownloadOffers();
    if (request !== offersRequest) return;
    const offers = result?.success ? result.offers || [] : [];
    downloadOffers.value = new Map(offers.map((offer) => [offer.game_id, offer]));
  } catch (e) {
    console.error("Failed to load download offers", e);
  }
};

// The game's download, as a short status that opens the Downloads page.
const downloadStatus = (gameId: number): string => gameDownloadStatus(updateDownloads.jobFor(gameId));
const hasUnfinishedDownload = (gameId: number): boolean => {
  const job = updateDownloads.jobFor(gameId);
  return !!job && isUnfinished(job);
};
const openDownloads = () => { void router.push("/downloads"); };

// "Install found archive" preselects the archive wLib found; "Install from file"
// opens the dialog without it, so the user can choose another file.
const installUpdate = (game: GameRecord, useOffer = true) => {
  if (status.value.running) return;
  openInstallArchive({
    gameId: game.id,
    archivePath: useOffer ? downloadOffers.value.get(game.id)?.archive_path : undefined,
  });
};

const openGame = (game: GameRecord) => {
  void router.push({ path: "/", query: libraryOpenQuery(game) });
};

const recheck = async (game: GameRecord) => {
  if (!game.f95_url || recheckingIds.value.has(game.id) || status.value.running) return;
  recheckingIds.value.add(game.id);
  try {
    const result = await api.checkForUpdates(game.f95_url);
    if (!result?.success) throw new Error(result?.error || "Update check failed");
    if (result.newly_finished) {
      notify(`${game.title} is finished`, "success");
      return;
    }
    notify(
      result.has_update
        ? `${game.title}: latest version is ${result.version}.`
        : `${game.title}: no newer version (${result.version}).`,
      "success",
    );
  } catch (error) {
    notifyError(`${game.title}: ${error instanceof Error ? error.message : String(error)}`);
  } finally {
    recheckingIds.value.delete(game.id);
  }
};

const markAsUpdated = async (game: GameRecord) => {
  if (markingUpdatedIds.value.has(game.id) || status.value.running) return;
  markingUpdatedIds.value.add(game.id);
  try {
    const result = await runBulk([game], (item) => api.markGameUpdated(item),
      (item) => versionSnapshot(item, (id, fields) => api.updateGame(id, fields)));
    if (result.failed || !result.changed) throw new Error("Could not mark game as updated");
    notifyUndo(`${game.title}: installed version set to ${game.latest_version?.trim()}.`, result.undo);
  } catch (error) {
    notifyError(String(error));
  } finally {
    markingUpdatedIds.value.delete(game.id);
  }
};
const skipVersion = async (game: GameRecord) => {
  if (status.value.running) return;
  const result = await runBulk([game], (item) => api.updateGame(item.id, { skipped_version: item.latest_version || "" }), (item) => {
    const previous = item.skipped_version || "";
    return () => api.updateGame(item.id, { skipped_version: previous });
  });
  if (result.failed) notifyError("Could not skip version");
  else notifyUndo(`${game.title}: skipping version ${game.latest_version}.`, result.undo);
};
const status = ref<UpdateStatusResponse>({
  running: false,
  total: 0,
  checked: 0,
  current: "",
  results: [],
  delay_seconds: 2,
});
const lastCheckTime = ref("");
const autoCheckFreq = ref("weekly");
const desktopNotifications = ref(true);
const notificationsAvailable = ref(false);
const savingNotifications = ref(false);
const notificationSettingLoaded = ref(false);
const notificationSettingError = ref("");
const loadNotificationSetting = async () => {
  notificationSettingError.value = "";
  try {
    const settings = await api.getSettings();
    desktopNotifications.value = settings.update_notifications !== false;
    notificationSettingLoaded.value = true;
  } catch (error) {
    notificationSettingError.value = `Could not load desktop notifications: ${String(error)}`;
  }
};
const setDesktopNotifications = async (event: Event) => {
  const enabled = (event.target as HTMLInputElement).checked;
  savingNotifications.value = true;
  try {
    const result = await api.saveSettings({ update_notifications: enabled });
    if (!result.success) throw new Error(result.error || "Could not save desktop notifications");
    desktopNotifications.value = enabled;
  } catch (error) {
    (event.target as HTMLInputElement).checked = desktopNotifications.value;
    notifyError(String(error));
  } finally {
    savingNotifications.value = false;
  }
};
const lastAutoCheck = ref("");
let pollInterval: ReturnType<typeof setInterval> | null = null;
let statusRequest = 0;
// Set when the view unmounts: onWebviewReady may answer after that, and it must
// not leave a status poll running for a page that is gone.
let disposed = false;

// App Update State
const appUpdate = ref<AppUpdateState | null>(null);
const appUpdateLoading = ref(true);
const currentVersion = ref("");

const loadGames = async () => {
  await gamesStore.refresh();
};

const loadAutoCheckSetting = async () => {
  try {
    const s = await api.getAutoCheckSetting();
    if (s && s.success === false) {
      console.error("Failed to load auto-check setting:", s.error);
    } else if (s) {
      autoCheckFreq.value = s.frequency || "weekly";
      lastAutoCheck.value = s.last_check || "";
    }
  } catch (e) {
    console.error("Failed to load auto-check setting", e);
  }
};

const setFrequency = async (freq: string) => {
  // Restore the previous choice on failure so the selector matches what is saved.
  const previous = autoCheckFreq.value;
  autoCheckFreq.value = freq;
  try {
    const res = await api.setAutoCheckSetting(freq);
    if (res && res.success === false) {
      if (autoCheckFreq.value === freq) autoCheckFreq.value = previous;
      notifyError("Failed to set frequency: " + (res.error || "Unknown error"));
    }
  } catch (e) {
    console.error("Failed to set frequency", e);
    if (autoCheckFreq.value === freq) autoCheckFreq.value = previous;
    notifyError("Error setting frequency: " + String(e));
  }
};

const startingCheck = ref(false);
const statusError = ref("");

// The optimistic snapshot keeps the "Cancel" state honest while the first
// status read is in flight, so a failed read can't make the view look idle.
const applyRunningCheck = (started: BulkUpdateStartResponse) => {
  status.value = {
    ...status.value,
    running: true,
    outcome: "running",
    cancelling: false,
    total: Number(started.total) || status.value.total,
    checked: 0,
    current: "",
    results: [],
    delay_seconds: Number(started.delay_seconds) || status.value.delay_seconds,
  };
};

const startCheck = async (ids?: number[]) => {
  if (startingCheck.value || status.value.running) return;
  startingCheck.value = true;
  try {
    const result = await api.checkAllUpdates(ids);
    if (result && result.success === false) {
      notifyError(
        "Failed to start check: " + (result.error || "Unknown error"),
      );
    } else if (result && result.success) {
      statusError.value = "";
      lastCheckTime.value = new Date().toLocaleTimeString();
      applyRunningCheck(result);
      startPolling();
    }
  } catch (e) {
    console.error("Failed to start update check", e);
    notifyError("Error starting check: " + String(e));
  } finally {
    startingCheck.value = false;
  }
};

const cancelCheck = async () => {
  try {
    const result = await api.cancelUpdateCheck();
    if (result && result.success === false) {
      notifyError(
        "Failed to cancel check: " + (result.error || "Unknown error"),
      );
    }
  } catch (e) {
    console.error("Failed to cancel", e);
    notifyError("Error cancelling check: " + String(e));
  }
};

// Without a readable status the loop cannot see the check finish, so keeping it
// alive would poll every 2 s forever and never run the completion handlers.
const stopPolling = () => {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
};

const failPolling = (message: string) => {
  stopPolling();
  statusError.value = message;
};

const pollStatus = async () => {
  const request = ++statusRequest;
  try {
    const s = await api.getUpdateStatus();
    if (request !== statusRequest) return;
    if (s && s.success === false) {
      console.error("Poll status failed:", s.error);
      failPolling(
        "Could not read the update-check status: " + (s.error || "Unknown error"),
      );
    } else if (s) {
      statusError.value = "";
      status.value = s;
      if (!s.running) {
        stopPolling();
        await loadGames();
        await loadAutoCheckSetting();
        await loadDownloadOffers();
      }
    }
  } catch (e) {
    if (request !== statusRequest) return;
    console.error("Poll failed", e);
    failPolling("Could not read the update-check status: " + String(e));
  }
};

const startPolling = () => {
  if (disposed) return;
  stopPolling();
  pollInterval = setInterval(pollStatus, 2000);
  pollStatus();
};

const retryStatus = () => {
  statusError.value = "";
  startPolling();
};

const openInBrowser = async (url?: string) => {
  if (!url) return;
  try {
    const result = await api.openInBrowser(url);
    if (result && result.success === false) notifyError(result.error || "Could not open the link");
  } catch (error) {
    notifyError(String(error));
  }
};

// Release notes are rendered as HTML. A plain link would navigate the whole
// WebView away from wLib, so http(s) links open in the system browser instead
// and anything else is swallowed.
const openChangelogLink = (event: MouseEvent) => {
  const href = renderedLinkHref(event.target);
  if (href === null) return;
  event.preventDefault();
  const url = getSafeNotesLinkUrl(href);
  if (url) void openInBrowser(url);
};

const progress = computed(() => {
  if (!status.value.total) return 0;
  return Math.round((status.value.checked / status.value.total) * 100);
});

const progressLabel = computed(() =>
  updateCheckProgressLabel(status.value.running, status.value.outcome),
);

const progressToneClass = computed(() => {
  if (status.value.outcome === "completed") return "text-green-400";
  if (status.value.outcome === "failed") return "text-red-400";
  return "";
});

const statusDelaySeconds = computed(() => {
  const raw = Number(status.value.delay_seconds);
  if (!Number.isFinite(raw) || raw <= 0) return 5;
  return raw;
});

const gamesWithUpdates = computed(() => {
  return status.value.results.filter((r) => r.has_update);
});

const gamesChecked = computed(() => {
  return status.value.results.filter((r) => !r.error);
});

const gamesWithErrors = computed(() => {
  return status.value.results.filter((r) => r.error);
});

const persistedUpdates = computed(() => {
  return games.value.filter((game) => !isPendingRemoval(game.id) && hasAvailableUpdate(game));
});

const selectedUpdateIds = ref(new Set<number>());
const bulkBusy = ref(false);
watch(() => persistedUpdates.value.map((game) => game.id), (ids) => {
  const visible = new Set(ids);
  selectedUpdateIds.value = new Set([...selectedUpdateIds.value].filter((id) => visible.has(id)));
});
const toggleAllUpdates = (event: Event) => {
  selectedUpdateIds.value = (event.target as HTMLInputElement).checked ? new Set(persistedUpdates.value.map((game) => game.id)) : new Set();
};
const bulkUpdate = async (action: "updated" | "skip" | "check") => {
  if (status.value.running || startingCheck.value || bulkBusy.value) return;
  const selected = persistedUpdates.value.filter((game) => selectedUpdateIds.value.has(game.id));
  if (action === "check") { await startCheck(selected.filter((game) => game.f95_url).map((game) => game.id)); return; }
  bulkBusy.value = true;
  const result = await runBulk(selected, (game) => action === "updated" ? api.markGameUpdated(game) : api.updateGame(game.id, { skipped_version: game.latest_version || "" }), (game) => {
    if (action === "updated") return versionSnapshot(game, (id, fields) => api.updateGame(id, fields));
    const previous = game.skipped_version || "";
    return () => api.updateGame(game.id, { skipped_version: previous });
  });
  bulkBusy.value = false;
  if (result.changed) notifyUndo(`${result.changed} changed, ${result.skipped} skipped${result.failed ? `, ${result.failed} failed` : ""}.`, result.undo);
  else notifyError(`No games changed; ${result.failed} failed.`);
};

// Each row has one split button. The main part is the most direct way to get
// the new version; the menu lists every other action that applies.
type UpdateMainAction = "install-offer" | "download" | "install-file";
const CHECK_RUNNING_REASON = "Wait for the update check to finish";

const updateMainAction = (game: GameRecord): UpdateMainAction => {
  if (downloadOffers.value.get(game.id)) return "install-offer";
  if (game.f95_url) return "download";
  return "install-file";
};

const downloadDisabledReason = (game: GameRecord): string => {
  if (updateDownloads.runningGames.value.includes(game.id)) return "Close the game before you download an update";
  if (hasUnfinishedDownload(game.id)) return "A download for this game is in progress";
  return "";
};

const updateMainDisabled = (game: GameRecord): boolean =>
  updateMainAction(game) === "download" ? !!downloadDisabledReason(game) : status.value.running;

const updateMainTitle = (game: GameRecord): string => {
  const action = updateMainAction(game);
  if (action === "download") return downloadDisabledReason(game) || "Choose a build from the F95Zone thread and download it";
  if (status.value.running) return CHECK_RUNNING_REASON;
  if (action === "install-offer") return `Install from ${downloadOffers.value.get(game.id)?.archive_path ?? ""}`;
  return "Install the new version from a downloaded archive";
};

const updateMenuItems = (game: GameRecord): MenuButtonItem[] => {
  const main = updateMainAction(game);
  const running = status.value.running;
  const items: MenuButtonItem[] = [];
  if (game.f95_url && main !== "download") {
    const reason = downloadDisabledReason(game);
    items.push({ id: "download", label: "Download update", icon: IconDownload, disabled: !!reason, disabledReason: reason });
  }
  if (main !== "install-file") {
    items.push({ id: "install-file", label: "Install from file", icon: IconFileZip, disabled: running, disabledReason: CHECK_RUNNING_REASON });
  }
  const marking = markingUpdatedIds.value.has(game.id);
  items.push({
    id: "mark", label: "Mark updated", icon: IconCheck, disabled: marking || running,
    disabledReason: running ? CHECK_RUNNING_REASON : "Marking this game updated",
  });
  items.push({ id: "skip", label: `Skip version ${game.latest_version ?? ""}`.trim(), icon: IconPlayerSkipForward, disabled: running, disabledReason: CHECK_RUNNING_REASON });
  let recheckReason = "";
  if (!game.f95_url) recheckReason = "Only games with an F95Zone thread can be re-checked";
  else if (running) recheckReason = CHECK_RUNNING_REASON;
  else if (recheckingIds.value.has(game.id)) recheckReason = "Checking this game now";
  items.push({ id: "recheck", label: "Re-check", icon: IconReload, disabled: !!recheckReason, disabledReason: recheckReason });
  if (game.f95_url) items.push({ id: "thread", label: "Open F95Zone thread", icon: IconExternalLink });
  return items;
};

const runUpdateMain = (game: GameRecord) => {
  const action = updateMainAction(game);
  if (action === "download") updateDownloads.open(game);
  else installUpdate(game, action === "install-offer");
};

const chooseUpdateMenuItem = (game: GameRecord, id: string) => {
  if (id === "download") updateDownloads.open(game);
  else if (id === "install-file") installUpdate(game, false);
  else if (id === "mark") void markAsUpdated(game);
  else if (id === "skip") void skipVersion(game);
  else if (id === "recheck") void recheck(game);
  else if (id === "thread") void openInBrowser(game.f95_url);
};

// When a row leaves while focus is in it (or focus was lost), move focus to
// the main part of the row now at the same place, else the previous row, else
// "Check All Games". Rows that animate out still hold the leave class.
const checkActions = ref<HTMLElement | null>(null);
const focusUpdateRow = (index: number) => {
  const rows = [...document.querySelectorAll<HTMLElement>("[data-update-row]")]
    .filter((row) => !row.classList.contains("list-leave-active") && !row.classList.contains("list-leave-from"));
  const target = rows[index] ?? rows[rows.length - 1];
  const main = target?.querySelector<HTMLElement>("[data-update-row-action] .menu-button-main");
  if (main && !main.matches(":disabled")) { main.focus(); return; }
  if (target) { target.querySelector<HTMLElement>("[data-update-row-action] .menu-button-toggle")?.focus(); return; }
  checkActions.value?.querySelector<HTMLElement>("[data-check-action]")?.focus();
};
watch(() => persistedUpdates.value.map((game) => game.id), async (ids, previous) => {
  if (!previous) return;
  const remaining = new Set(ids);
  const removed = previous.filter((id) => !remaining.has(id));
  if (!removed.length) return;
  const active = document.activeElement;
  const removedIndex = removed
    .filter((id) => {
      const row = document.querySelector(`[data-update-row="${id}"]`);
      return !active || active === document.body || !!row?.contains(active);
    })
    .map((id) => previous.indexOf(id))[0];
  if (removedIndex === undefined) return;
  await nextTick();
  focusUpdateRow(Math.max(0, removedIndex - previous.slice(0, removedIndex).filter((id) => !remaining.has(id)).length));
});

const settingsOpen = ref(false);
const frequencyLabel = computed(() => {
  if (autoCheckFreq.value === "monthly") return "Monthly";
  if (autoCheckFreq.value === "off") return "Automatic checks off";
  return "Weekly";
});
const hasStatusContent = computed(() =>
  !!statusError.value
  || status.value.running
  || status.value.checked > 0
  || (!!status.value.outcome && status.value.outcome !== "idle")
  || status.value.total === 0,
);

const formatLastCheck = computed(() => {
  if (!lastAutoCheck.value) return "Never";
  try {
    const d = new Date(lastAutoCheck.value);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString();
  } catch {
    return lastAutoCheck.value;
  }
});

// Not awaited by onMounted: the GitHub call can take up to 10 s and must not
// delay showing a running update check.
const loadAppUpdate = async () => {
  try {
    appUpdateLoading.value = true;
    const versionInfo = await api.get_app_version();
    currentVersion.value = versionInfo?.version || "";

    const release = await api.check_app_updates();
    if (release && release.success && release.version) {
      if (isNewerVersion(release.version, currentVersion.value)) {
        const rendered = marked.parse(
          release.changelog || "No changelog provided.",
        );
        const changelogHtml =
          typeof rendered === "string" ? rendered : await rendered;
        appUpdate.value = {
          version: release.version,
          changelogHtml: DOMPurify.sanitize(changelogHtml),
          url: release.url || "",
        };
      }
    }
  } catch (e) {
    console.error("Failed to check app updates", e);
  } finally {
    appUpdateLoading.value = false;
  }
};

// Automatic checks and external imports can start a check after this page has
// mounted, in which case the mount-time status read saw nothing to poll. The
// global poll refreshes the library on progress; use that to pick the check up.
// It shares the poll's request counter so an older snapshot from an earlier
// refresh cannot replace a newer one that resolved first.
const adoptBackgroundCheck = async () => {
  if (status.value.running || pollInterval) return;
  const request = ++statusRequest;
  try {
    const s = await api.getUpdateStatus();
    if (request !== statusRequest) return;
    if (!s || s.success === false) return;
    if (!shouldAdoptBackgroundCheck(status.value.running, s)) return;
    statusError.value = "";
    status.value = s;
    if (s.running) startPolling();
  } catch (e) {
    console.error("Failed to read background update status", e);
  }
};

const handleLibraryRefresh = async () => {
  await Promise.all([loadGames(), loadDownloadOffers()]);
  await adoptBackgroundCheck();
};

onMounted(() => {
  window.addEventListener("wlib-refresh-library", handleLibraryRefresh);
  onWebviewReady(() => void refreshRunningGames(() => api.getRunningGames()));
  onWebviewReady(async () => {
    if (disposed) return;
    const capabilities = await loadPlatformCapabilities();
    if (disposed) return;
    notificationsAvailable.value = capabilities.platform === "linux";
    if (notificationsAvailable.value) void loadNotificationSetting();
    void loadAppUpdate();
    await loadGames();
    await loadAutoCheckSetting();
    await loadDownloadOffers();
    let s: UpdateStatusResponse | null;
    const request = ++statusRequest;
    try {
      s = await api.getUpdateStatus();
      if (request !== statusRequest) return;
    } catch (e) {
      if (request !== statusRequest) return;
      console.error("Poll status failed:", e);
      failPolling("Could not read the update-check status: " + String(e));
      return;
    }
    if (s && s.success === false) {
      console.error("Poll status failed:", s.error);
      failPolling(
        "Could not read the update-check status: " + (s.error || "Unknown error"),
      );
    } else if (s) {
      statusError.value = "";
      status.value = s;
      if (s.running) startPolling();
    }
  });
});

onUnmounted(() => {
  disposed = true;
  offersRequest++;
  statusRequest++;
  window.removeEventListener("wlib-refresh-library", handleLibraryRefresh);
  stopPolling();
});
</script>

<template>
  <div class="mx-auto w-full p-8 max-w-4xl pb-12">
    <header class="mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
      <div class="min-w-0">
        <h2
          class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
          style="color: var(--text-primary)"
        >
          <IconReload class="ui-page-heading-icon" />
          <span>Updates</span>
        </h2>
        <p
          class="text-sm pl-3"
          style="
            color: var(--text-secondary);
            border-left: 2px solid var(--brand);
          "
        >
          Check F95Zone for new game versions.
        </p>
        <p class="mt-2 text-xs" style="color: var(--text-secondary)" data-testid="updates-last-checked">
          Last checked: {{ formatLastCheck }} · {{ frequencyLabel }}
        </p>
      </div>
      <div ref="checkActions" class="flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="ui-action-btn update-btn"
          @click="settingsOpen = true"
        >
          <IconSettings class="ui-action-icon" />
          Settings
        </button>
        <button
          v-if="!status.running"
          type="button"
          data-check-action
          @click="startCheck()"
          :disabled="startingCheck"
          class="ui-action-btn px-5 py-2 rounded-lg text-sm font-bold transition-all hover:brightness-110 disabled:opacity-50"
          style="background: var(--brand); color: var(--on-brand); box-shadow: var(--shadow-brand)"
        >
          <IconLoader2 v-if="startingCheck" class="ui-action-icon animate-spin" />
          <IconReload v-else class="ui-action-icon" />
          Check All Games
        </button>
        <button
          v-else
          type="button"
          data-check-action
          @click="cancelCheck"
          :disabled="status.cancelling"
          class="ui-action-btn bg-red-600/20 hover:bg-red-600/30 text-red-400 px-5 py-2 rounded-lg text-sm font-bold transition-all border border-red-500/30 disabled:opacity-60 disabled:cursor-wait"
        >
          <IconX class="ui-action-icon" />
          {{ status.cancelling ? "Cancelling…" : "Cancel" }}
        </button>
      </div>
    </header>

    <UpdateSettingsDialog
      v-model="settingsOpen"
      :frequency="autoCheckFreq"
      :notifications-available="notificationsAvailable"
      :desktop-notifications="desktopNotifications"
      :notifications-saving="savingNotifications"
      :notifications-loaded="notificationSettingLoaded"
      :notifications-error="notificationSettingError"
      @set-frequency="setFrequency"
      @set-notifications="setDesktopNotifications"
      @retry-notifications="loadNotificationSetting"
    />

    <!-- Check status: progress, outcome, errors and the first-run hint -->
    <div
      role="status"
      class="mb-6 empty:hidden"
      data-testid="update-check-status"
    >
      <div v-if="hasStatusContent" class="section-card rounded-xl px-5 py-4 space-y-2">
        <div v-if="status.running || status.checked > 0" class="space-y-2">
          <div
            class="flex items-center justify-between text-xs"
            style="color: var(--text-secondary)"
          >
            <span v-if="status.running" class="flex items-center gap-2">
              <IconLoader2 class="ui-action-icon animate-spin text-green-400" />
              Checking: {{ status.current }}
            </span>
            <span v-else :class="progressToneClass">{{ progressLabel }}</span>
            <span>{{ status.checked }}/{{ status.total }}</span>
          </div>
          <div
            class="h-2 rounded-full overflow-hidden"
            style="background: var(--bg-raised)"
          >
            <div
              class="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full transition-all duration-500"
              :style="{ width: progress + '%' }"
            ></div>
          </div>
        </div>

        <p v-if="statusError" class="text-sm text-red-400">
          {{ statusError }}
          <button
            type="button"
            class="ml-2 underline font-semibold"
            @click="retryStatus"
          >
            Try again
          </button>
        </p>
        <p
          v-else-if="!status.running && status.outcome && status.outcome !== 'idle'"
          class="text-sm"
          style="color: var(--text-secondary)"
        >
          Check {{ status.outcome }}: {{ status.checked }} / {{ status.total }} games checked.
          {{ status.error }}
        </p>
        <p
          v-else-if="!status.running && status.total === 0"
          class="text-sm"
          style="color: var(--text-secondary)"
        >
          Choose "Check All Games" to scan F95Zone for new versions. Games are
          checked one-by-one with a {{ statusDelaySeconds }}-second delay to
          avoid rate limiting.
        </p>
      </div>
    </div>

    <div class="space-y-6">
      <!-- App Update Available (GitHub) -->
      <section
        v-if="appUpdate"
        class="rounded-xl p-6 shadow-lg relative overflow-hidden"
        style="
          background: linear-gradient(
            135deg,
            var(--bg-surface),
            var(--bg-inset)
          );
          border: 1px solid var(--brand-deep);
        "
      >
        <div
          class="absolute top-0 right-0 w-64 h-64 rounded-full blur-3xl -mr-20 -mt-20 pointer-events-none"
          style="background: var(--brand-glow)"
        ></div>

        <div class="flex flex-wrap items-start justify-between gap-3 mb-4 relative z-10">
          <div>
            <h3
              class="ui-section-heading text-xl font-bold mb-1"
              style="color: var(--text-primary)"
            >
              <IconArrowUp class="ui-section-icon" />
              wLib Update Available
            </h3>
            <div class="flex items-center gap-3">
              <span
                class="text-xs font-mono px-2 py-0.5 rounded-md"
                style="
                  background: var(--bg-raised);
                  color: var(--text-muted);
                  border: 1px solid var(--border);
                "
                >Current: {{ currentVersion }}</span
              >
              <span class="text-xs" style="color: var(--text-muted)">→</span>
              <span
                class="text-xs font-mono px-2 py-0.5 rounded-md font-bold animate-pulse"
                style="
                  background: var(--brand-glow);
                  color: var(--brand);
                  border: 1px solid var(--brand-deep);
                "
                >New: {{ appUpdate.version }}</span
              >
            </div>
          </div>

          <div class="flex gap-2 shrink-0">
            <button
              @click="openInBrowser(appUpdate.url)"
              class="ui-action-btn px-4 py-2 rounded-lg text-xs font-bold transition-all shrink-0"
              style="background: var(--brand); color: var(--on-brand); box-shadow: var(--shadow-brand)"
            >
              <IconExternalLink class="ui-action-icon" />
              Latest release
            </button>
          </div>
        </div>

        <div
          class="changelog-wrapper rounded-lg p-5 text-sm relative z-10 max-h-64 overflow-y-auto wlib-changelog"
          style="color: var(--text-secondary)"
          @click="openChangelogLink"
          v-html="appUpdate.changelogHtml"
        ></div>
      </section>

      <!-- Updates Available -->
      <section
        v-if="persistedUpdates.length > 0"
        class="section-card rounded-xl p-6"
      >
        <h3 class="ui-section-heading text-lg font-semibold mb-4">
          <IconArrowUp class="ui-section-icon" />
          Updates Available ({{ persistedUpdates.length }})
        </h3>

        <div class="flex flex-wrap items-center gap-2 mb-4">
          <label class="flex items-center gap-2"><input type="checkbox" :checked="selectedUpdateIds.size === persistedUpdates.length" @change="toggleAllUpdates" /> Select all ({{ selectedUpdateIds.size }} selected)</label>
          <template v-if="selectedUpdateIds.size">
            <button class="ui-action-btn px-2 py-1" :disabled="status.running || startingCheck || bulkBusy" @click="bulkUpdate('updated')">Mark updated</button>
            <button class="ui-action-btn px-2 py-1" :disabled="status.running || startingCheck || bulkBusy" @click="bulkUpdate('skip')">Skip version</button>
            <button class="ui-action-btn px-2 py-1" :disabled="status.running || startingCheck || bulkBusy || !persistedUpdates.some(game => selectedUpdateIds.has(game.id) && game.f95_url)" @click="bulkUpdate('check')">Re-check</button>
          </template>
        </div>
        <TransitionGroup name="list" tag="div" class="space-y-2">
          <div
            v-for="game in persistedUpdates"
            :key="game.id"
            :data-update-row="game.id"
            class="update-row flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg px-4 py-3"
          >
            <label class="flex items-center"><input type="checkbox" :aria-label="`Select ${game.title}`" :checked="selectedUpdateIds.has(game.id)" @change="($event.target as HTMLInputElement).checked ? selectedUpdateIds.add(game.id) : selectedUpdateIds.delete(game.id)" /></label>
            <span v-if="gameCoverSrc(game)" class="cover-thumb w-10 h-10 shrink-0">
              <CoverImage :game="game" img-class="w-10 h-10 rounded-lg object-cover" />
            </span>
            <CoverPlaceholder v-else :title="game.title" class="w-10 h-10 rounded-lg shrink-0" />
            <div class="min-w-[10rem] flex-1">
              <p class="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-base">
                <button
                  type="button"
                  @click="openGame(game)"
                  class="update-entry min-w-0 max-w-full truncate text-left font-semibold rounded-md"
                  :title="`Open ${game.title}`"
                >
                  {{ game.title }}
                </button>
                <span class="update-version font-mono whitespace-nowrap" data-testid="update-version-change">
                  <span>{{ game.version }}</span>
                  <span class="mx-1" aria-hidden="true">→</span>
                  <span class="sr-only">to</span>
                  <span class="font-bold" style="color: var(--success-text)">{{ game.latest_version }}</span>
                </span>
              </p>
              <p class="flex flex-wrap items-center gap-x-2 text-xs" style="color: var(--text-secondary)">
                <span v-if="game.developer" class="truncate">{{ game.developer }}</span>
                <button
                  v-if="downloadStatus(game.id)"
                  type="button"
                  class="download-status text-xs font-medium tabular-nums rounded-md px-1.5 py-0.5"
                  :title="`${downloadStatus(game.id)}. Open Downloads`"
                  data-testid="game-download-status"
                  @click="openDownloads"
                >
                  {{ downloadStatus(game.id) }}
                </button>
              </p>
            </div>
            <MenuButton
              split
              align="end"
              class="ml-auto"
              :data-update-row-action="game.id"
              :items="updateMenuItems(game)"
              menu-label="More update actions"
              button-label="More update actions"
              :main-disabled="updateMainDisabled(game)"
              :main-title="updateMainTitle(game)"
              @main="runUpdateMain(game)"
              @select="(id: string) => chooseUpdateMenuItem(game, id)"
            >
              <template #main>
                <IconDownload v-if="updateMainAction(game) === 'download'" class="ui-action-icon" aria-hidden="true" />
                <IconFileZip v-else class="ui-action-icon" aria-hidden="true" />
                {{ updateMainAction(game) === "install-offer" ? "Install found archive" : updateMainAction(game) === "download" ? "Download update" : "Install from file" }}
              </template>
            </MenuButton>
          </div>
        </TransitionGroup>
      </section>
      <section v-else class="section-card rounded-xl p-6">
        <p class="flex items-center gap-2 text-sm" style="color: var(--text-secondary)">
          <IconCheck class="ui-action-icon" style="color: var(--success-text)" aria-hidden="true" />
          No updates available.
        </p>
      </section>
      <!-- Check Results -->
      <section
        v-if="!status.running && status.results.length > 0"
        class="section-card rounded-xl p-6"
      >
        <h3
          class="ui-section-heading text-lg font-semibold mb-4"
          style="color: var(--text-primary)"
        >
          <IconInfoCircle class="ui-section-icon" />
          Last Check Results
        </h3>

        <div class="grid grid-cols-3 gap-4 mb-4">
          <div
            class="rounded-lg p-3 text-center"
            style="
              background: var(--bg-raised);
              border: 1px solid var(--border);
            "
          >
            <p class="text-2xl font-bold" style="color: var(--text-primary)">
              {{ gamesChecked.length }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Checked</p>
          </div>
          <div
            class="rounded-lg p-3 text-center"
            style="
              background: var(--bg-raised);
              border: 1px solid rgba(34, 197, 94, 0.2);
            "
          >
            <p class="text-2xl font-bold text-green-400">
              {{ gamesWithUpdates.length }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Updates</p>
          </div>
          <div
            class="rounded-lg p-3 text-center"
            style="
              background: var(--bg-raised);
              border: 1px solid rgba(239, 68, 68, 0.2);
            "
          >
            <p
              class="text-2xl font-bold"
              :class="gamesWithErrors.length ? 'text-red-400' : ''"
              :style="!gamesWithErrors.length ? 'color: var(--text-muted)' : ''"
            >
              {{ gamesWithErrors.length }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Errors</p>
          </div>
        </div>

        <div v-if="gamesWithErrors.length" class="space-y-1">
          <p class="text-xs mb-1" style="color: var(--text-muted)">
            Failed checks:
          </p>
          <div
            v-for="err in gamesWithErrors"
            :key="err.id"
            class="text-xs text-red-400/70 flex items-start gap-2"
          >
            <span class="shrink-0" style="color: var(--text-muted)">{{ err.title }}:</span>
            <span class="min-w-0 break-words">{{ err.error }}</span>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.download-status { color: var(--brand); text-decoration: underline; text-underline-offset: 2px; }
.download-status:hover { background: var(--brand-glow); }
.section-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}

.update-btn {
  background: var(--bg-overlay);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.75rem;
  font-weight: 500;
  border: 1px solid var(--border);
  transition: all 0.15s ease;
}
.update-btn:hover:not(:disabled) {
  background: var(--border-hover);
}

.update-row {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}
.update-entry { color: var(--text-primary); }
.update-entry:hover { color: var(--brand); }
.update-entry:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
.update-version { color: var(--text-secondary); }

.changelog-wrapper {
  background: var(--bg-inset);
  border: 1px solid var(--border);
}

.list-move,
.list-enter-active,
.list-leave-active {
  transition: all 0.4s ease;
}
.list-enter-from,
.list-leave-to {
  opacity: 0;
  transform: translateX(-20px);
}
.list-leave-active {
  position: absolute;
}

/* Markdown styling for the changelog */
:deep(.wlib-changelog h1),
:deep(.wlib-changelog h2),
:deep(.wlib-changelog h3) {
  color: var(--text-primary);
  font-weight: 600;
  margin-top: 1rem;
  margin-bottom: 0.5rem;
}
:deep(.wlib-changelog h2) {
  font-size: 1.1rem;
  border-bottom: 1px solid var(--border);
  padding-bottom: 0.25rem;
}
:deep(.wlib-changelog p) {
  margin-bottom: 0.75rem;
  line-height: 1.5;
}
:deep(.wlib-changelog ul) {
  list-style-type: disc;
  margin-left: 1.5rem;
  margin-bottom: 1rem;
}
:deep(.wlib-changelog li) {
  margin-bottom: 0.25rem;
}
:deep(.wlib-changelog a) {
  color: var(--brand);
  text-decoration: underline;
  text-underline-offset: 2px;
}
:deep(.wlib-changelog a:hover) {
  filter: brightness(1.2);
}
:deep(.wlib-changelog code) {
  background: var(--bg-raised);
  padding: 0.1rem 0.3rem;
  border-radius: 4px;
  font-family: monospace;
  font-size: 0.85em;
}
:deep(.wlib-changelog pre) {
  background: var(--bg-inset);
  padding: 1rem;
  border-radius: 8px;
  overflow-x: auto;
  margin-bottom: 1rem;
  border: 1px solid var(--border);
}
:deep(.wlib-changelog pre code) {
  background: transparent;
  padding: 0;
}
</style>
