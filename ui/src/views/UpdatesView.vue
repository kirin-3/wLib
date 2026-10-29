<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { gameCoverSrc, gameCoverError } from "../utils/gameCover";
import { ref, onMounted, onUnmounted, computed } from "vue";
import { useRouter } from "vue-router";
import {
  IconArrowUp,
  IconClockUp,
  IconCheck,
  IconExternalLink,
  IconInfoCircle,
  IconLoader2,
  IconReload,
  IconX,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import { notify, notifyError } from "../utils/toast";
import { openInstallArchive } from "../utils/installArchive";
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
const games = ref<GameRecord[]>([]);
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

const installUpdate = (game: GameRecord) => {
  if (status.value.running) return;
  openInstallArchive({
    gameId: game.id,
    archivePath: downloadOffers.value.get(game.id)?.archive_path,
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
    const result = await api.markGameUpdated(game);
    if (!result.success)
      throw new Error(result.error || "Could not mark game as updated");
    notify(
      `${game.title}: installed version set to ${game.latest_version?.trim()}.`,
      "success",
    );
  } catch (error) {
    notifyError(String(error));
  } finally {
    markingUpdatedIds.value.delete(game.id);
  }
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

let gamesRequest = 0;
const loadGames = async () => {
  const request = ++gamesRequest;
  try {
    const res = await api.getGames();
    if (request !== gamesRequest) return;
    games.value = res || [];
  } catch (e) {
    console.error("Failed to load games", e);
    if (request === gamesRequest) games.value = [];
  }
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

const startCheck = async () => {
  if (startingCheck.value || status.value.running) return;
  startingCheck.value = true;
  try {
    const result = await api.checkAllUpdates();
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
  return games.value.filter((game) => hasAvailableUpdate({ ...game, skipped_version: game.skipped_version }));
});

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
  gamesRequest++;
  offersRequest++;
  statusRequest++;
  window.removeEventListener("wlib-refresh-library", handleLibraryRefresh);
  stopPolling();
});
</script>

<template>
  <div class="mx-auto w-full p-8 max-w-4xl pb-12">
    <header class="mb-8">
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
    </header>

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

        <div class="flex items-start justify-between mb-4 relative z-10">
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

      <!-- F95Zone Game Auto-Check Setting -->
      <section class="section-card rounded-xl p-6">
        <div class="flex items-center justify-between mb-3">
          <h3
            class="ui-section-heading text-lg font-semibold"
            style="color: var(--text-primary)"
          >
            <IconClockUp class="ui-section-icon" />
            Automatic Checking
          </h3>
        </div>

        <div class="flex items-center gap-3 mb-3">
          <span class="text-sm" style="color: var(--text-secondary)"
            >Frequency:</span
          >
          <div
            class="flex items-center rounded-lg overflow-hidden"
            style="border: 1px solid var(--border)"
          >
            <button
              v-for="opt in [
                { key: 'weekly', label: 'Weekly' },
                { key: 'monthly', label: 'Monthly' },
                { key: 'off', label: 'Off' },
              ]"
              :key="opt.key"
              @click="setFrequency(opt.key)"
              class="px-3 py-1.5 text-xs font-medium transition-all"
              :style="
                autoCheckFreq === opt.key
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'background: var(--bg-surface); color: var(--text-muted)'
              "
            >
              {{ opt.label }}
            </button>
          </div>
        </div>
        <p class="text-xs" style="color: var(--text-muted)">
          Last checked: {{ formatLastCheck }}
        </p>
        <div v-if="notificationsAvailable" class="mt-4 text-sm" style="color: var(--text-primary)">
          <label class="flex min-h-11 items-center gap-3 w-fit cursor-pointer">
            <input type="checkbox" :checked="desktopNotifications"
              :disabled="savingNotifications || !notificationSettingLoaded"
              @change="setDesktopNotifications" />
            Desktop notifications
          </label>
          <p v-if="notificationSettingError" role="alert">
            {{ notificationSettingError }}
            <button class="underline min-h-11" @click="loadNotificationSetting">Retry</button>
          </p>
          <p v-else-if="!notificationSettingLoaded" role="status">Loading notification setting...</p>
          <p v-else class="text-xs" style="color: var(--text-secondary)">
            Notify after automatic checks, or manual checks that finish while you are away from wLib.
          </p>
        </div>
      </section>

      <!-- Check All Button / Progress -->
      <section class="section-card rounded-xl p-6">
        <div class="flex items-center justify-between mb-4">
          <h3
            class="ui-section-heading text-lg font-semibold"
            style="color: var(--text-primary)"
          >
            <IconReload class="ui-section-icon text-green-400" />
            Manual Check
          </h3>

          <div class="flex items-center gap-3">
            <button
              v-if="!status.running"
              @click="startCheck"
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
              @click="cancelCheck"
              :disabled="status.cancelling"
              class="ui-action-btn bg-red-600/20 hover:bg-red-600/30 text-red-400 px-5 py-2 rounded-lg text-sm font-bold transition-all border border-red-500/30 disabled:opacity-60 disabled:cursor-wait"
            >
              <IconX class="ui-action-icon" />
              {{ status.cancelling ? "Cancelling…" : "Cancel" }}
            </button>
          </div>
        </div>

        <!-- Progress Bar -->
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

        <p
          v-if="statusError"
          class="text-sm text-red-400"
          role="alert"
        >
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
          role="status"
        >
          Check {{ status.outcome }}: {{ status.checked }} / {{ status.total }} games checked.
          {{ status.error }}
        </p>
        <p
          v-else-if="!status.running && status.total === 0"
          class="text-sm"
          style="color: var(--text-muted)"
        >
          Click "Check All Games" to scan F95Zone for new versions. Games are
          checked one-by-one with a {{ statusDelaySeconds }}-second delay to
          avoid rate limiting.
        </p>
      </section>

      <!-- Updates Available -->
      <section
        v-if="persistedUpdates.length > 0"
        class="section-card rounded-xl p-6"
        style="border-color: rgba(34, 197, 94, 0.2)"
      >
        <h3
          class="ui-section-heading text-lg font-semibold text-green-400 mb-4"
        >
          <IconArrowUp class="ui-section-icon" />
          Updates Available ({{ persistedUpdates.length }})
        </h3>

        <TransitionGroup name="list" tag="div" class="space-y-2">
          <div
            v-for="game in persistedUpdates"
            :key="game.id"
            class="flex items-center justify-between gap-3 rounded-lg px-4 py-3"
            style="
              background: var(--bg-raised);
              border: 1px solid var(--border);
            "
          >
            <button
              @click="openGame(game)"
              class="update-entry flex items-center gap-3 min-w-0 flex-1 text-left rounded-md"
              :title="`Open ${game.title}`"
            >
              <img
                v-if="gameCoverSrc(game)"
                :src="gameCoverSrc(game)"
                @error="gameCoverError($event, game)"
                class="w-10 h-10 rounded-lg object-cover shrink-0"
              />
              <div
                class="w-6 h-6 rounded shrink-0 flex items-center justify-center"
                style="background: var(--bg-overlay)"
                v-else
              >
                <svg
                  class="w-3 h-3"
                  style="color: var(--text-muted)"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                >
                  <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
                </svg>
              </div>
              <div class="min-w-0">
                <p
                  class="text-sm font-semibold truncate"
                  style="color: var(--text-primary)"
                >
                  {{ game.title }}
                </p>
                <p class="text-xs" style="color: var(--text-muted)">
                  {{ game.developer }}
                </p>
              </div>
            </button>
            <div class="flex items-center gap-3 shrink-0">
              <button
                @click="installUpdate(game)"
                :disabled="status.running"
                class="ui-action-btn px-2 py-1.5 rounded-lg text-xs disabled:opacity-50"
                style="
                  background: var(--brand-glow);
                  color: var(--brand);
                  border: 1px solid var(--brand-deep);
                "
                :title="downloadOffers.get(game.id) ? `Install from ${downloadOffers.get(game.id)?.archive_path}` : 'Install the new version from a downloaded archive'"
              >
                <IconArrowUp class="ui-action-icon" />
                Install update
              </button>
              <span
                v-if="downloadOffers.get(game.id)"
                class="text-xs font-semibold"
                style="color: var(--brand)"
                title="A matching archive was found in your Downloads folder"
              >
                Archive found
              </span>
              <button
                @click="markAsUpdated(game)"
                :disabled="markingUpdatedIds.has(game.id) || status.running"
                class="ui-action-btn px-2 py-1.5 rounded-lg text-xs disabled:opacity-50"
                style="
                  background: var(--success-bg);
                  color: var(--success-text);
                  border: 1px solid var(--success-border);
                "
                :title="`Set installed version to ${game.latest_version}`"
              >
                <IconCheck class="ui-action-icon" />
                Mark updated
              </button>
              <button
                @click="recheck(game)"
                :disabled="!game.f95_url || recheckingIds.has(game.id) || status.running"
                class="ui-action-btn update-btn !px-2 !py-1.5 disabled:opacity-50"
                :title="game.f95_url ? `Check F95Zone again for ${game.title}` : 'Only games with an F95Zone thread can be re-checked'"
              >
                <IconLoader2
                  v-if="recheckingIds.has(game.id)"
                  class="ui-action-icon animate-spin"
                />
                <IconReload v-else class="ui-action-icon" />
                Re-check
              </button>
              <div class="text-right">
                <span
                  class="text-xs font-mono"
                  style="color: var(--text-muted)"
                  >{{ game.version }}</span
                >
                <span class="text-xs mx-1" style="color: var(--text-muted)"
                  >→</span
                >
                <span class="text-xs text-green-400 font-mono font-bold">{{
                  game.latest_version
                }}</span>
              </div>
              <button
                v-if="game.f95_url"
                @click="openInBrowser(game.f95_url)"
                class="ui-icon-btn p-1.5 rounded-lg transition-all"
                style="color: var(--brand)"
                title="Open F95Zone thread"
              >
                <IconExternalLink class="ui-action-icon" />
              </button>
            </div>
          </div>
        </TransitionGroup>
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

.update-entry:hover p:first-child {
  color: var(--brand) !important;
}

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
