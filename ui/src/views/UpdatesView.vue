<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { gameCoverSrc } from "../utils/gameCover";
import CoverImage from "../components/CoverImage.vue";
import CoverPlaceholder from "../components/CoverPlaceholder.vue";
import { refreshRunningGames } from "../composables/useRunningGames";
import { ref, useTemplateRef, onMounted, onUnmounted, computed, watch, nextTick } from "vue";
import { useEventListener } from "../composables/useEventListener";
import MenuButton, { type MenuButtonItem } from "../components/MenuButton.vue";
import UpdateSettingsDialog from "../components/UpdateSettingsDialog.vue";
import { useRouter } from "vue-router";
import {
  IconAlertTriangle,
  IconArrowUp,
  IconCheck,
  IconClock,
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
import { errorReason, notify, notifyError, notifyUndo } from "../utils/toast";
import { runBulk, versionSnapshot } from "../utils/bulkActions";
import { isPendingRemoval } from "../utils/pendingRemovals";
import { gamesStore } from "../composables/useGamesStore";
import { openInstallArchive } from "../utils/installArchive";
import {useUpdateDownloads} from "../composables/useUpdateDownloads";
import { gameDownloadStatus, isUnfinished } from "../utils/downloadQueue";
const updateDownloads = useUpdateDownloads();
import { downloadSites, latestSiteLabel, updateCheckUrl } from "../utils/lewdcorner";
import { hasAvailableUpdate } from "../utils/libraryGames";
import { getSafeNotesLinkUrl, renderedLinkHref } from "../utils/notesLinks";
import { libraryOpenQuery } from "../utils/storeLinks";
import { isNewerVersion } from "../utils/appVersion";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { bulkDownloadDecision, type HostPlatform } from "../utils/updateDownloads";
import {
  autoCheckText,
  lastCheckText,
  shouldAdoptBackgroundCheck,
  updateCheckAnnouncement,
  updateCheckErrorText,
  updateCheckSummary,
  updatesEmptyState,
  type UpdateCheckTone,
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
  const url = updateCheckUrl(game);
  if (!url || recheckingIds.value.has(game.id) || status.value.running) return;
  recheckingIds.value.add(game.id);
  try {
    const result = await api.checkForUpdates(url);
    if (!result?.success) {
      // Same plain words as the failed checks list; technical text in brackets.
      const { message, detail } = updateCheckErrorText(result ?? {});
      notifyError(`${game.title}: ${message}${detail ? ` (${detail})` : ""}`);
      return;
    }
    if (result.note) notify(result.note, "info");
    if (result.newly_abandoned) notify(`${game.title} was abandoned by its developer`, "info");
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
    notifyError(`${game.title}: could not check for updates. ${errorReason(error)}`);
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
    if (result.failed || !result.changed) throw new Error("not saved");
    notifyUndo(`${game.title}: installed version set to ${game.latest_version?.trim()}.`, result.undo);
  } catch {
    notifyError(`Could not mark ${game.title} as updated. Try again.`);
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
  if (result.failed) notifyError(`Could not skip the new version of ${game.title}. Try again.`);
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
const lcEnabled = ref(false);
const lcPlus = ref(false);
const hasDownloadSite = (game: GameRecord) => downloadSites(game, {
  lc_enabled: lcEnabled.value, lc_plus: lcPlus.value,
}).length > 0;
const loadUpdateSettings = async () => {
  notificationSettingError.value = "";
  try {
    const settings = await api.getSettings();
    lcEnabled.value = !!settings.lc_enabled;
    lcPlus.value = !!settings.lc_plus;
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
      notifyError(`Could not save the automatic check setting. ${errorReason(res.error)}`);
    }
  } catch (e) {
    console.error("Failed to set frequency", e);
    if (autoCheckFreq.value === freq) autoCheckFreq.value = previous;
    notifyError(`Could not save the automatic check setting. ${errorReason(e)}`);
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
  if (startingCheck.value || status.value.running || bulkDownloading.value) return;
  startingCheck.value = true;
  try {
    const result = await api.checkAllUpdates(ids);
    if (result && result.success === false) {
      notifyError(`Could not start the update check. ${errorReason(result.error)}`);
    } else if (result && result.success) {
      statusError.value = "";
      lastCheckTime.value = new Date().toLocaleTimeString();
      applyRunningCheck(result);
      startPolling();
    }
  } catch (e) {
    console.error("Failed to start update check", e);
    notifyError(`Could not start the update check. ${errorReason(e)}`);
  } finally {
    startingCheck.value = false;
  }
};

const cancelCheck = async () => {
  if (status.value.cancelling) return;
  try {
    const result = await api.cancelUpdateCheck();
    if (result && result.success === false) {
      notifyError(`Could not stop the update check. ${errorReason(result.error)}`);
    }
  } catch (e) {
    console.error("Failed to cancel", e);
    notifyError(`Could not stop the update check. ${errorReason(e)}`);
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


const statusDelaySeconds = computed(() => {
  const raw = Number(status.value.delay_seconds);
  if (!Number.isFinite(raw) || raw <= 0) return 5;
  return raw;
});

const gamesWithUpdates = computed(() => {
  return status.value.results.filter((r) => r.has_update);
});

const gamesWithErrors = computed(() => {
  return status.value.results.filter((r) => r.error);
});
// Each failed check in plain words; the raw text stays for a title and,
// when it is technical, as a muted detail for bug reports.
const failedChecks = computed(() => gamesWithErrors.value.map((result) => {
  const text = updateCheckErrorText(result);
  return { id: result.id, title: result.title, raw: result.error, ...text };
}));

// The one statement of a finished check (see updateCheckSummary); the
// announcer starts with the same words.
const checkSummary = computed(() => (status.value.running ? null : updateCheckSummary({
  outcome: status.value.outcome,
  total: status.value.total,
  checked: status.value.checked,
  error: status.value.error,
  errors: gamesWithErrors.value.length,
})));
const TONE_COLOR: Record<UpdateCheckTone, string> = {
  success: "var(--success-text)",
  warning: "var(--warning-text)",
  danger: "var(--danger-text)",
  neutral: "var(--text-secondary)",
};

const gamesWithNotes = computed(() => status.value.results.filter((result) => result.note));
// Archived games are checked only when chosen; their update is not offered.
const archivedResults = computed(() => status.value.results.filter((result) => result.archived && !result.error));

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
// Download selected: one game at a time, with the build picker's own preselection.
// File hosts only (never RPDL). Games without a clear build get a row note.
const bulkDownloading = ref(false);
const bulkDownloadCancelled = ref(false);
const bulkDownloadProgress = ref({ index: 0, total: 0, title: "" });
const needsChoice = ref(new Map<number, string>());
const BULK_DOWNLOAD_REASON = "Wait for Download selected to finish";
const bulkDownload = async () => {
  if (status.value.running || startingCheck.value || bulkBusy.value || bulkDownloading.value) return;
  const selected = persistedUpdates.value.filter((game) => selectedUpdateIds.value.has(game.id));
  if (!selected.length) return;
  bulkDownloading.value = true;
  bulkDownloadCancelled.value = false;
  announce(`Preparing downloads for ${selected.length} ${selected.length === 1 ? "game" : "games"}.`);
  let queued = 0, choose = 0, skipped = 0;
  try {
    const capabilities = await loadPlatformCapabilities();
    const host: HostPlatform = capabilities.platform === "windows" ? "windows" : "linux";
    for (const [index, game] of selected.entries()) {
      if (bulkDownloadCancelled.value) break;
      bulkDownloadProgress.value = { index: index + 1, total: selected.length, title: game.title };
      if (downloadDisabledReason(game) || !hasDownloadSite(game)) { skipped++; continue; }
      let reason = "";
      try {
        const decision = bulkDownloadDecision(await api.getUpdateDownloadLinks(game.id, ""), host);
        if (decision.kind === "start") {
          const started = await api.startUpdateDownload(game.id, decision.mirror.link_id, null);
          if (!started.success) reason = `Download did not start: ${started.error || "unknown error"}`;
        } else reason = decision.reason;
      } catch (error) {
        reason = `Links did not load: ${error instanceof Error ? error.message : String(error)}`;
      } finally {
        try { await api.closeUpdateDownloadLinks(game.id); } catch (error) { console.warn("Could not close the download links", error); }
      }
      const next = new Map(needsChoice.value);
      if (reason) { next.set(game.id, reason.replace(/\.+$/, "")); choose++; }
      else { next.delete(game.id); queued++; }
      needsChoice.value = next;
    }
  } catch (error) {
    notifyError(`Download selected stopped: ${error instanceof Error ? error.message : String(error)}`);
  } finally {
    const stopped = bulkDownloadCancelled.value;
    bulkDownloading.value = false;
    bulkDownloadCancelled.value = false;
    selectedUpdateIds.value = new Set();
    updateDownloads.refresh();
    const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
    const message = `${stopped ? "Stopped. " : ""}${plural(queued, "download was", "downloads were")} queued, ${plural(choose, "game needs", "games need")} a build choice and ${skipped} ${skipped === 1 ? "was" : "were"} skipped.`;
    notify(message, queued ? "success" : "info", "", { label: "Open Downloads", run: openDownloads });
    announce(message);
  }
};
const cancelBulkDownload = () => { bulkDownloadCancelled.value = true; };
// The note goes away when a download starts for the game or its row leaves.
watch([() => persistedUpdates.value.map((game) => game.id), () => updateDownloads.jobs.value], ([ids]) => {
  if (!needsChoice.value.size) return;
  const visible = new Set(ids);
  const next = new Map([...needsChoice.value].filter(([id]) => visible.has(id) && !hasUnfinishedDownload(id)));
  if (next.size !== needsChoice.value.size) needsChoice.value = next;
});

const bulkUpdate = async (action: "updated" | "skip" | "check") => {
  if (status.value.running || startingCheck.value || bulkBusy.value || bulkDownloading.value) return;
  const selected = persistedUpdates.value.filter((game) => selectedUpdateIds.value.has(game.id));
  if (action === "check") {
    const ids = selected.filter(updateCheckUrl).map((game) => game.id);
    // An empty list would check every game, not none.
    if (ids.length) await startCheck(ids);
    return;
  }
  bulkBusy.value = true;
  const result = await runBulk(selected, (game) => action === "updated" ? api.markGameUpdated(game) : api.updateGame(game.id, { skipped_version: game.latest_version || "" }), (game) => {
    if (action === "updated") return versionSnapshot(game, (id, fields) => api.updateGame(id, fields));
    const previous = game.skipped_version || "";
    return () => api.updateGame(game.id, { skipped_version: previous });
  });
  bulkBusy.value = false;
  const gameCount = (count: number) => `${count} ${count === 1 ? "game" : "games"}`;
  const done = action === "updated" ? `${gameCount(result.changed)} marked updated` : `Skipping the new version of ${gameCount(result.changed)}`;
  const notDone = [result.skipped ? `${result.skipped} skipped` : "", result.failed ? `${result.failed} failed` : ""].filter(Boolean).join(", ");
  if (result.changed) notifyUndo(`${done}${notDone ? ` (${notDone})` : ""}.`, result.undo);
  else notifyError(`No games changed (${notDone || "0 changed"}). Try again.`);
};

// Each row has one split button. The main part is the most direct way to get
// the new version; the menu lists every other action that applies.
type UpdateMainAction = "install-offer" | "download" | "install-file";
const CHECK_RUNNING_REASON = "Wait for the update check to finish";

const updateMainAction = (game: GameRecord): UpdateMainAction => {
  if (downloadOffers.value.get(game.id)) return "install-offer";
  if (hasDownloadSite(game)) return "download";
  return "install-file";
};

const downloadDisabledReason = (game: GameRecord): string => {
  if (updateDownloads.runningGames.value.includes(game.id)) return "Close the game before you download an update";
  if (hasUnfinishedDownload(game.id)) return "A download for this game is in progress";
  return "";
};

const updateMainDisabled = (game: GameRecord): boolean =>
  bulkDownloading.value || (updateMainAction(game) === "download" ? !!downloadDisabledReason(game) : status.value.running);

const updateMainTitle = (game: GameRecord): string => {
  if (bulkDownloading.value) return BULK_DOWNLOAD_REASON;
  const action = updateMainAction(game);
  if (action === "download") return downloadDisabledReason(game) || "Choose a build from the game thread and download it";
  if (status.value.running) return CHECK_RUNNING_REASON;
  if (action === "install-offer") return `Install from ${downloadOffers.value.get(game.id)?.archive_path ?? ""}`;
  return "Install the new version from a downloaded archive";
};

const updateMenuItems = (game: GameRecord): MenuButtonItem[] => {
  const main = updateMainAction(game);
  const running = status.value.running;
  const items: MenuButtonItem[] = [];
  if (hasDownloadSite(game) && main !== "download") {
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
  if (!updateCheckUrl(game)) recheckReason = "This game has no active update thread";
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
// "Check all games" (in the header, or in the first-run card). Rows that animate out still hold the leave class.
const checkActions = useTemplateRef<HTMLElement>("checkActions");
const focusUpdateRow = (index: number) => {
  const rows = [...document.querySelectorAll<HTMLElement>("[data-update-row]")]
    .filter((row) => !row.classList.contains("list-leave-active") && !row.classList.contains("list-leave-from"));
  const target = rows[index] ?? rows[rows.length - 1];
  const main = target?.querySelector<HTMLElement>("[data-update-row-action] .menu-button-main");
  if (main && !main.matches(":disabled")) { main.focus(); return; }
  if (target) { target.querySelector<HTMLElement>("[data-update-row-action] .menu-button-toggle")?.focus(); return; }
  (checkActions.value?.querySelector<HTMLElement>("[data-check-action]")
    ?? document.querySelector<HTMLElement>("[data-first-check]"))?.focus();
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

// The build picker returns focus to the control that opened it. When that was
// a row menu item, it is gone with its menu: focus the row's split button.
watch(() => updateDownloads.selectedGame.value?.id, async (now, previous) => {
  if (now !== undefined || previous === undefined) return;
  await nextTick();
  const active = document.activeElement;
  if (active && active !== document.body && active.isConnected) return;
  const action = document.querySelector(`[data-update-row="${previous}"] [data-update-row-action]`);
  const main = action?.querySelector<HTMLElement>(".menu-button-main");
  (main && !main.matches(":disabled") ? main : action?.querySelector<HTMLElement>(".menu-button-toggle"))?.focus();
});

const settingsOpen = ref(false);
const frequencyLabel = computed(() => autoCheckText(autoCheckFreq.value));
const hasStatusContent = computed(() =>
  bulkDownloading.value
  || !!statusError.value
  || status.value.running
  || status.value.checked > 0
  || (!!status.value.outcome && status.value.outcome !== "idle"),
);

// The bulk buttons stay focusable while blocked (aria-disabled) and point at
// one visible reason, so keyboard and screen reader users learn why.
const bulkDisabledReason = computed(() => {
  if (status.value.running || startingCheck.value) return CHECK_RUNNING_REASON;
  if (bulkDownloading.value) return BULK_DOWNLOAD_REASON;
  if (bulkBusy.value) return "Wait for the selected games to change";
  return "";
});
const bulkRecheckReason = computed(() => bulkDisabledReason.value
  || (persistedUpdates.value.some((game) => selectedUpdateIds.value.has(game.id) && updateCheckUrl(game))
    ? "" : "No selected game has an active update thread"));
const checkDisabledReason = computed(() => (bulkDownloading.value ? BULK_DOWNLOAD_REASON : ""));
// "Re-check failed" checks only the failed games that have an update thread.
const recheckFailedIds = computed(() => {
  const failed = new Set(gamesWithErrors.value.map((result) => result.id));
  return games.value.filter((game) => failed.has(game.id) && updateCheckUrl(game)).map((game) => game.id);
});
const recheckFailedReason = computed(() => {
  if (status.value.running || startingCheck.value) return CHECK_RUNNING_REASON;
  return checkDisabledReason.value;
});
const checkBlocked = computed(() => startingCheck.value || bulkDownloading.value);

// One persistent live region. It speaks when a check starts or ends, when the
// status cannot be read, and when Download selected starts or ends; never the
// per-game "Checking: X" line, which changes every few seconds.
const announcement = ref("");
const announce = (message: string) => {
  if (!message) return;
  announcement.value = "";
  void nextTick(() => { announcement.value = message; });
};
watch(() => status.value.running, (running, wasRunning) => {
  announce(updateCheckAnnouncement(!!wasRunning, {
    running,
    total: status.value.total,
    checked: status.value.checked,
    outcome: status.value.outcome,
    error: status.value.error,
    updates: gamesWithUpdates.value.length,
    errors: gamesWithErrors.value.length,
  }));
});
watch(statusError, (message) => { if (message) announce(message); });

// "Check all games" and "Cancel" replace each other in the header. Keep focus
// on the header action when it was there, instead of dropping it on the page.
let refocusCheckAction = false;
watch(() => status.value.running, () => {
  const active = document.activeElement;
  refocusCheckAction = active instanceof HTMLElement && active.hasAttribute("data-check-action");
}, { flush: "pre" });
watch(() => status.value.running, async () => {
  if (!refocusCheckAction) return;
  refocusCheckAction = false;
  await nextTick();
  const active = document.activeElement;
  if (active && active !== document.body && active.isConnected) return;
  checkActions.value?.querySelector<HTMLElement>("[data-check-action]")?.focus();
}, { flush: "post" });

// Without update rows: "Not checked yet" before any check, the green
// "up to date" mark only after a real one (see updatesEmptyState).
const emptyState = computed(() => updatesEmptyState({
  running: status.value.running,
  outcome: status.value.outcome,
  lastCheck: lastAutoCheck.value,
  resultCount: status.value.results.length,
  errorCount: gamesWithErrors.value.length,
}));
const checkSources = computed(() => (lcEnabled.value ? "F95Zone and LewdCorner" : "F95Zone"));
const checkPace = computed(() =>
  `wLib checks one game every ${statusDelaySeconds.value} seconds, so ${checkSources.value} ${lcEnabled.value ? "do" : "does"} not block it for too many requests.`,
);
// The first-run card holds the only "Check all games" button, so the page has
// one primary action instead of two.
const firstRun = computed(() => persistedUpdates.value.length === 0 && emptyState.value === "not-checked");

// The first-run card's button and "Re-check failed" leave the page while a
// check runs, so move focus to the header's Cancel button instead of dropping
// it on the page.
const startCheckFrom = async (event: MouseEvent, ids?: number[]) => {
  const button = event.currentTarget as HTMLElement | null;
  await startCheck(ids);
  await nextTick();
  if (!document.activeElement || document.activeElement === document.body || !button?.isConnected) {
    checkActions.value?.querySelector<HTMLElement>("[data-check-action]")?.focus();
  }
};

const lastCheck = computed(() => lastCheckText(lastAutoCheck.value));
const recheckFailed = (event: MouseEvent) => {
  // An empty list would check every game, not only the failed ones.
  if (recheckFailedReason.value || !recheckFailedIds.value.length) return;
  void startCheckFrom(event, recheckFailedIds.value);
};

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
  onWebviewReady(() => void refreshRunningGames(() => api.getRunningGames()));
  onWebviewReady(async () => {
    if (disposed) return;
    const capabilities = await loadPlatformCapabilities();
    if (disposed) return;
    notificationsAvailable.value = capabilities.platform === "linux";
    void loadUpdateSettings();
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
useEventListener(window, "wlib-refresh-library", handleLibraryRefresh);

onUnmounted(() => {
  disposed = true;
  offersRequest++;
  statusRequest++;
  stopPolling();
});
</script>

<template>
  <div class="mx-auto w-full p-8 max-w-4xl pb-12">
    <p class="sr-only" role="status" aria-live="polite" aria-atomic="true" data-testid="update-check-announcer">{{ announcement }}</p>
    <header class="mb-6 flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
      <div class="min-w-0">
        <h2
          class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
          style="color: var(--text-primary)"
        >
          <IconReload class="ui-page-heading-icon" aria-hidden="true" />
          <span>Updates</span>
        </h2>
        <p class="text-sm" style="color: var(--text-secondary)">
          {{ lcEnabled ? "Check F95Zone and active LewdCorner threads for new game versions." : "Check F95Zone for new game versions." }}
        </p>
        <p class="mt-2 text-xs" style="color: var(--text-secondary)" data-testid="updates-last-checked">
          <!-- Each part stays on one line; a narrow window breaks between them. -->
          <span class="whitespace-nowrap">Last check:
            <time v-if="lastCheck.full" :datetime="lastAutoCheck" :title="lastCheck.full">{{ lastCheck.text }}</time>
            <template v-else>{{ lastCheck.text }}</template></span>
          · <span class="whitespace-nowrap">{{ frequencyLabel }}</span>
        </p>
      </div>
      <div ref="checkActions" class="check-actions flex flex-wrap items-center gap-2">
        <button
          type="button"
          class="ui-card-btn page-btn"
          @click="settingsOpen = true"
        >
          <IconSettings class="ui-action-icon" aria-hidden="true" />
          Update settings
        </button>
        <button
          v-if="!status.running && !firstRun"
          type="button"
          data-check-action
          @click="startCheck()"
          :aria-disabled="checkBlocked || undefined"
          :aria-describedby="checkDisabledReason ? 'check-disabled-reason' : undefined"
          :title="checkDisabledReason || undefined"
          class="ui-card-btn ui-card-btn--primary page-btn page-btn--primary"
        >
          <IconLoader2 v-if="startingCheck" class="ui-action-icon animate-spin" aria-hidden="true" />
          <IconReload v-else class="ui-action-icon" aria-hidden="true" />
          Check all games
        </button>
        <button
          v-else-if="status.running"
          type="button"
          data-check-action
          @click="cancelCheck"
          :aria-disabled="status.cancelling || undefined"
          class="ui-card-btn ui-card-btn--danger page-btn"
        >
          <IconX class="ui-action-icon" aria-hidden="true" />
          {{ status.cancelling ? "Cancelling…" : "Cancel" }}
        </button>
        <span v-if="checkDisabledReason" id="check-disabled-reason" class="sr-only">{{ checkDisabledReason }}</span>
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
      @retry-notifications="loadUpdateSettings"
    />

    <!-- Check status: progress, outcome and errors. Not a live region: the
         announcer at the top of the page speaks the changes that matter. -->
    <div
      class="mb-6 empty:hidden"
      data-testid="update-check-status"
    >
      <div v-if="hasStatusContent" class="section-card rounded-xl px-5 py-4 space-y-2">
        <div v-if="bulkDownloading" class="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm" style="color: var(--text-secondary)" data-testid="bulk-download-progress">
          <IconLoader2 class="ui-action-icon animate-spin" aria-hidden="true" />
          <span
            role="progressbar"
            aria-label="Download selected progress"
            aria-valuemin="0"
            :aria-valuemax="bulkDownloadProgress.total"
            :aria-valuenow="bulkDownloadProgress.index"
            :aria-valuetext="`Preparing downloads ${bulkDownloadProgress.index} of ${bulkDownloadProgress.total}`"
            class="font-semibold tabular-nums"
          >Preparing downloads {{ bulkDownloadProgress.index }} / {{ bulkDownloadProgress.total }}</span>
          <span class="truncate min-w-0 flex-1">{{ bulkDownloadProgress.title }}</span>
          <button
            type="button"
            class="ui-card-btn"
            :disabled="bulkDownloadCancelled"
            @click="cancelBulkDownload"
          >{{ bulkDownloadCancelled ? "Stopping after this game…" : "Cancel" }}</button>
        </div>
        <div v-if="status.running || status.checked > 0 || (checkSummary && !statusError)" class="space-y-2">
          <div
            v-if="status.running"
            class="flex items-center justify-between gap-3 text-xs"
            style="color: var(--text-secondary)"
          >
            <span class="flex min-w-0 items-center gap-2" data-testid="update-check-current">
              <IconLoader2 class="ui-action-icon shrink-0 animate-spin" style="color: var(--brand)" aria-hidden="true" />
              <span class="truncate">{{ status.current ? `Checking: ${status.current}` : "Starting the check…" }}</span>
            </span>
            <span class="shrink-0 tabular-nums">{{ status.checked }}/{{ status.total }}</span>
          </div>
          <!-- One statement of the outcome; the announcer says the same words. -->
          <div
            v-else-if="checkSummary && !statusError"
            class="flex items-start gap-2 text-sm"
            data-testid="update-check-summary"
          >
            <IconCheck v-if="checkSummary.tone === 'success'" class="ui-action-icon mt-0.5 shrink-0" :style="{ color: TONE_COLOR.success }" aria-hidden="true" />
            <IconAlertTriangle v-else-if="checkSummary.tone !== 'neutral'" class="ui-action-icon mt-0.5 shrink-0" :style="{ color: TONE_COLOR[checkSummary.tone] }" aria-hidden="true" />
            <div class="min-w-0">
              <p :style="{ color: checkSummary.tone === 'neutral' ? TONE_COLOR.neutral : TONE_COLOR[checkSummary.tone] }">{{ checkSummary.text }}</p>
              <p
                v-if="checkSummary.reason"
                class="mt-0.5 text-xs break-words"
                style="color: var(--text-secondary)"
                :title="checkSummary.rawError !== checkSummary.reason.message ? checkSummary.rawError : undefined"
                data-testid="update-check-failure-reason"
              >
                {{ checkSummary.reason.message }}
                <span v-if="checkSummary.reason.detail" class="font-mono" style="color: var(--text-muted)">({{ checkSummary.reason.detail }})</span>
              </p>
            </div>
          </div>
          <div
            v-if="status.running || status.checked > 0"
            role="progressbar"
            aria-label="Update check progress"
            aria-valuemin="0"
            :aria-valuemax="Math.max(status.total, status.checked)"
            :aria-valuenow="status.checked"
            :aria-valuetext="`${status.checked} of ${status.total} games checked`"
            data-testid="update-check-progress"
            class="h-2 rounded-full overflow-hidden"
            style="background: var(--bg-raised)"
          >
            <div
              class="check-progress-fill h-full rounded-full"
              :style="{ width: progress + '%' }"
            ></div>
          </div>
        </div>

        <p v-if="statusError" class="text-sm" style="color: var(--danger-text)">
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
          v-if="status.running && !statusError"
          class="text-xs"
          style="color: var(--text-secondary)"
          data-testid="update-check-pace"
        >
          {{ checkPace }}
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
              <IconArrowUp class="ui-section-icon" aria-hidden="true" />
              wLib update available
            </h3>
            <div class="flex flex-wrap items-center gap-x-3 gap-y-1.5">
              <span
                class="max-w-full truncate whitespace-nowrap text-xs font-mono px-2 py-0.5 rounded-md"
                :title="`Current: ${currentVersion}`"
                style="
                  background: var(--bg-raised);
                  color: var(--text-muted);
                  border: 1px solid var(--border);
                "
                >Current: {{ currentVersion }}</span
              >
              <span class="text-xs" style="color: var(--text-muted)" aria-hidden="true">→</span>
              <span
                class="max-w-full truncate whitespace-nowrap text-xs font-mono px-2 py-0.5 rounded-md font-bold animate-pulse"
                :title="`New: ${appUpdate.version}`"
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
              type="button"
              @click="openInBrowser(appUpdate.url)"
              class="ui-card-btn ui-card-btn--primary shrink-0"
            >
              <IconExternalLink class="ui-action-icon" aria-hidden="true" />
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

      <!-- Updates available -->
      <section
        v-if="persistedUpdates.length > 0"
        class="section-card rounded-xl p-6"
      >
        <h3 class="ui-section-heading text-lg font-semibold mb-4">
          <IconArrowUp class="ui-section-icon" aria-hidden="true" />
          Updates available ({{ persistedUpdates.length }})
        </h3>

        <!-- Select all, then the bulk actions for the selected rows. The actions
             wrap as one group; in a narrow window each row of them fills the width. -->
        <div class="bulk-bar mb-4" data-testid="updates-bulk-bar">
          <label class="bulk-select">
            <input
              type="checkbox"
              :checked="selectedUpdateIds.size > 0 && selectedUpdateIds.size === persistedUpdates.length"
              :indeterminate="selectedUpdateIds.size > 0 && selectedUpdateIds.size < persistedUpdates.length"
              @change="toggleAllUpdates"
            />
            <span>Select all <span class="bulk-count">({{ selectedUpdateIds.size }} selected)</span></span>
          </label>
          <div v-if="selectedUpdateIds.size" class="bulk-actions">
            <button type="button" class="ui-card-btn ui-card-btn--primary" :aria-disabled="!!bulkDisabledReason || undefined" :aria-describedby="bulkDisabledReason ? 'bulk-disabled-reason' : undefined" @click="bulkDownload">
              <IconDownload class="ui-action-icon" aria-hidden="true" />Download selected
            </button>
            <button type="button" class="ui-card-btn" :aria-disabled="!!bulkDisabledReason || undefined" :aria-describedby="bulkDisabledReason ? 'bulk-disabled-reason' : undefined" @click="bulkUpdate('updated')">Mark updated</button>
            <button type="button" class="ui-card-btn" :aria-disabled="!!bulkDisabledReason || undefined" :aria-describedby="bulkDisabledReason ? 'bulk-disabled-reason' : undefined" @click="bulkUpdate('skip')">Skip version</button>
            <button type="button" class="ui-card-btn" :aria-disabled="!!bulkRecheckReason || undefined" :aria-describedby="bulkRecheckReason ? 'bulk-recheck-reason' : undefined" @click="bulkUpdate('check')">Re-check</button>
          </div>
          <p v-if="selectedUpdateIds.size && (bulkDisabledReason || bulkRecheckReason)" class="bulk-reason text-xs" style="color: var(--text-secondary)">
            <span v-if="bulkDisabledReason" id="bulk-disabled-reason" data-testid="bulk-disabled-reason">{{ bulkDisabledReason }}</span>
            <span v-if="bulkRecheckReason" id="bulk-recheck-reason" :class="bulkDisabledReason ? 'sr-only' : undefined">{{ bulkRecheckReason }}</span>
          </p>
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
                <span class="update-version font-mono" data-testid="update-version-change">
                  <span class="whitespace-nowrap">
                    <span>{{ game.version }}</span>
                    <span class="mx-1" aria-hidden="true">→</span>
                    <span class="sr-only">to</span>
                  </span>
                  <span class="update-version-new font-bold" style="color: var(--success-text)">{{ game.latest_version }}</span>
                  <span v-if="latestSiteLabel(game, lcEnabled)" class="ml-1 whitespace-nowrap text-xs" style="color: var(--text-muted)">{{ latestSiteLabel(game, lcEnabled) }}</span>
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
                <span
                  v-if="needsChoice.get(game.id)"
                  class="font-medium"
                  style="color: var(--warning-text)"
                  data-testid="update-needs-choice"
                >{{ needsChoice.get(game.id) }}. Open Download update to choose a build.</span>
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
              :disabled="bulkDownloading"
              :main-disabled="updateMainDisabled(game)"
              :main-disabled-reason="updateMainDisabled(game) ? updateMainTitle(game) : undefined"
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
      <section
        v-else-if="firstRun"
        class="section-card rounded-xl p-6"
        aria-labelledby="updates-not-checked-heading"
        data-testid="updates-not-checked"
      >
        <h3 id="updates-not-checked-heading" class="ui-section-heading text-lg font-semibold mb-2">
          <IconClock class="ui-section-icon" aria-hidden="true" />
          Not checked yet
        </h3>
        <div class="max-w-prose space-y-2 text-sm" style="color: var(--text-secondary)">
          <p>
            A check reads each game's {{ checkSources }} thread and lists the games that have a newer version here.
          </p>
          <p>
            {{ checkPace }} With many games this takes a while; you can keep using wLib meanwhile.
          </p>
          <p v-if="lcEnabled && lcPlus">
            LewdCorner Plus games need a LewdCorner login in the browser with the wLib extension.
          </p>
        </div>
        <button
          type="button"
          class="ui-card-btn ui-card-btn--primary page-btn page-btn--primary mt-4"
          :aria-disabled="checkBlocked || undefined"
          :aria-describedby="checkDisabledReason ? 'check-disabled-reason' : undefined"
          :title="checkDisabledReason || undefined"
          data-first-check
          @click="startCheckFrom($event)"
        >
          <IconLoader2 v-if="startingCheck" class="ui-action-icon animate-spin" aria-hidden="true" />
          <IconReload v-else class="ui-action-icon" aria-hidden="true" />
          Check all games
        </button>
      </section>
      <section v-else class="section-card rounded-xl p-6" data-testid="updates-empty">
        <p v-if="emptyState === 'up-to-date'" class="flex items-center gap-2 text-sm" style="color: var(--text-secondary)">
          <IconCheck class="ui-action-icon" style="color: var(--success-text)" aria-hidden="true" />
          Your games are up to date.
        </p>
        <p v-else-if="emptyState === 'checking'" class="text-sm" style="color: var(--text-secondary)">
          No updates found yet. Games with a newer version appear here during the check.
        </p>
        <p v-else class="text-sm" style="color: var(--text-secondary)">
          No updates found.
        </p>
      </section>
      <!-- Last check results -->
      <section
        v-if="!status.running && status.results.length > 0"
        class="section-card rounded-xl p-6"
      >
        <h3
          class="ui-section-heading text-lg font-semibold mb-4"
          style="color: var(--text-primary)"
        >
          <IconInfoCircle class="ui-section-icon" aria-hidden="true" />
          Last check results
        </h3>

        <div class="grid grid-cols-3 gap-2 sm:gap-4 mb-4">
          <div
            class="min-w-0 rounded-lg px-1.5 py-3 text-center sm:px-3"
            style="
              background: var(--bg-raised);
              border: 1px solid var(--border);
            "
          >
            <p class="text-2xl font-bold tabular-nums" style="color: var(--text-primary)" data-testid="results-checked">
              {{ status.checked }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Games checked</p>
          </div>
          <div
            class="min-w-0 rounded-lg px-1.5 py-3 text-center sm:px-3"
            :style="{
              background: 'var(--bg-raised)',
              border: `1px solid ${gamesWithUpdates.length ? 'var(--success-border)' : 'var(--border)'}`,
            }"
          >
            <p class="text-2xl font-bold tabular-nums" :style="{ color: gamesWithUpdates.length ? 'var(--success-text)' : 'var(--text-muted)' }" data-testid="results-updates">
              {{ gamesWithUpdates.length }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Updates found</p>
          </div>
          <div
            class="min-w-0 rounded-lg px-1.5 py-3 text-center sm:px-3"
            :style="{
              background: 'var(--bg-raised)',
              border: `1px solid ${gamesWithErrors.length ? 'var(--danger-border)' : 'var(--border)'}`,
            }"
          >
            <p
              class="text-2xl font-bold tabular-nums"
              :style="{ color: gamesWithErrors.length ? 'var(--danger-text)' : 'var(--text-muted)' }"
              data-testid="results-failed"
            >
              {{ gamesWithErrors.length }}
            </p>
            <p class="text-xs" style="color: var(--text-muted)">Checks failed</p>
          </div>
        </div>

        <ul v-if="archivedResults.length" class="mb-3 space-y-1 text-xs" style="color: var(--text-muted)">
          <li v-for="result in archivedResults" :key="`archived-${result.id}`">
            <span class="font-medium">{{ result.title }}</span> · Archived, so the update is not shown.<template v-if="result.latest_version"> Latest version: {{ result.latest_version }}.</template>
          </li>
        </ul>
        <ul v-if="gamesWithNotes.length" class="mb-3 space-y-1 text-xs" style="color: var(--text-muted)">
          <li v-for="result in gamesWithNotes" :key="result.id">
            <span class="font-medium">{{ result.title }}</span> · {{ result.note }}
          </li>
        </ul>
        <div v-if="failedChecks.length" data-testid="failed-checks">
          <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p id="failed-checks-label" class="text-xs" style="color: var(--text-muted)">
              Failed checks:
            </p>
            <button
              v-if="recheckFailedIds.length"
              type="button"
              class="ui-card-btn"
              :aria-disabled="!!recheckFailedReason || undefined"
              :aria-describedby="recheckFailedReason ? 'recheck-failed-reason' : undefined"
              :title="recheckFailedReason || undefined"
              data-testid="recheck-failed"
              @click="recheckFailed"
            >
              <IconReload class="ui-action-icon" aria-hidden="true" />
              Re-check failed ({{ recheckFailedIds.length }})
            </button>
            <span v-if="recheckFailedReason" id="recheck-failed-reason" class="sr-only">{{ recheckFailedReason }}</span>
          </div>
          <ul class="space-y-1 text-xs" aria-labelledby="failed-checks-label">
            <li
              v-for="failed in failedChecks"
              :key="failed.id"
              class="break-words"
              data-testid="failed-check"
            >
              <span class="font-medium" style="color: var(--text-secondary)">{{ failed.title }}:</span>
              {{ " " }}<span style="color: var(--danger-text)" :title="failed.raw !== failed.message ? failed.raw : undefined">{{ failed.message }}</span>
              <template v-if="failed.detail">
                {{ " " }}<span class="font-mono" style="color: var(--text-muted)">({{ failed.detail }})</span>
              </template>
            </li>
          </ul>
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

/* The header and first-run buttons: the shared .ui-card-btn set one size up. */
.page-btn {
  min-height: 2.375rem;
  padding-inline: 1.125rem;
  font-size: 0.875rem;
}
.page-btn--primary { box-shadow: var(--shadow-brand); }
.check-progress-fill {
  background: var(--brand);
  transition: width 0.5s ease;
}

.bulk-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 1rem;
}
.bulk-select {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2.125rem;
  font-size: 0.875rem;
  color: var(--text-secondary);
  cursor: pointer;
}
.bulk-count { color: var(--text-muted); font-variant-numeric: tabular-nums; }
.bulk-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.bulk-reason { flex-basis: 100%; }
/* The main part of every row's split button has one width, so the rows line up. */
:deep([data-update-row-action] .menu-button-main) { min-width: 10.5rem; }
@media (max-width: 639px) {
  /* Header buttons and bulk actions fill each wrapped row instead of leaving a ragged edge. */
  .check-actions { width: 100%; }
  .check-actions > button { flex: 1 1 auto; }
  .bulk-actions { width: 100%; }
  .bulk-actions > .ui-card-btn { flex: 1 1 auto; }
  /* The row's split button drops under the title: it takes the full row width. */
  :deep([data-update-row-action]) { width: 100%; }
  :deep([data-update-row-action] .menu-button-main) { flex: 1 1 auto; min-width: 0; }
}

.update-row {
  background: var(--bg-raised);
  border: 1px solid var(--border);
}
.update-entry { color: var(--text-primary); }
.update-entry:hover { color: var(--brand); }
.update-entry:focus-visible { outline: 2px solid var(--brand); outline-offset: 2px; }
/* "old →" stays on one line; a long new version wraps under it inside the row. */
.update-version {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: baseline;
  min-width: 0;
  max-width: 100%;
  color: var(--text-secondary);
}
.update-version-new { min-width: 0; overflow-wrap: anywhere; }

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
