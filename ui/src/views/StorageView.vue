<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  IconAlertTriangle,
  IconChartPie,
  IconLoader2,
  IconPlayerStop,
  IconRefresh,
  IconSparkles,
} from "@tabler/icons-vue";
import {
  api,
  onWebviewReady,
  type GameRecord,
  type PlatformCapabilities,
  type StorageCleanupAction,
  type StorageCleanupPreviewResponse,
  type StorageGameRow,
  type StorageStateResponse,
  type UninstallOptions,
} from "../services/api";
import StorageCategoryBreakdown from "../components/storage/StorageCategoryBreakdown.vue";
import StorageCleanupSection from "../components/storage/StorageCleanupSection.vue";
import StorageGamesList from "../components/storage/StorageGamesList.vue";
import UninstallDialog from "../components/UninstallDialog.vue";
import { gamesStore } from "../composables/useGamesStore";
import { confirmAction } from "../utils/confirm";
import { scheduleUninstall } from "../utils/pendingUninstalls";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { notify, notifyError } from "../utils/toast";
import {
  buildCleanupConfirmOptions,
  buildSafeCleanupConfirmOptions,
  cleanupPreviewExpired,
  cleanupRunError,
  cleanupTierTotals,
  formatCleanupResult,
  formatSafeCleanupResult,
  NOTHING_TO_FREE,
  planSafeCleanup,
  PREVIEW_EXPIRED_MESSAGE,
  type SafeCleanupRun,
  type SkippedCleanup,
  type StorageCleanupBusy,
  formatRelativeAge,
  formatStorageBytes,
  skippedEntriesNote,
  stoppedScanNote,
  storageWineProton,
  visibleStorageActions,
  visibleStorageCategories,
} from "../utils/storage";

const state = ref<StorageStateResponse | null>(null);
const loading = ref(true);
const loadError = ref("");
const busyAction = ref<StorageCleanupBusy>("");
// "checking" covers the preview and the open confirmation; "cleaning" the delete.
const busyStage = ref<"checking" | "cleaning">("checking");
const busyLabel = computed(() => (busyStage.value === "cleaning" ? "Cleaning..." : "Checking..."));
const cleanupResult = ref("");
const cleanupError = ref("");
const uninstallOpen = ref(false);
const uninstallTargets = ref<GameRecord[]>([]);

let pollTimer: number | null = null;
let unmounted = false;

const report = computed(() => state.value?.report ?? null);
const capabilities = ref<PlatformCapabilities | null>(null);
// Unknown (or unreadable) capabilities count as "no Wine/Proton": rows that
// hold data still show, empty Wine/Proton rows wait until the platform is known.
const wineProton = computed(() => storageWineProton(capabilities.value));
const visibleCategories = computed(() =>
  visibleStorageCategories(report.value?.categories ?? [], wineProton.value),
);
const visibleActions = computed(() =>
  visibleStorageActions(state.value?.actions ?? [], wineProton.value),
);
const tierTotals = computed(() => cleanupTierTotals(visibleActions.value));
const safePlan = computed(() => planSafeCleanup(visibleActions.value));
const isScanning = computed(() => Boolean(state.value?.scanning));
const isCleanupRunning = computed(() => Boolean(state.value?.cleanup_running));
const isTaskRunning = computed(() => isScanning.value || isCleanupRunning.value);
const isGameRunning = computed(() => Boolean(state.value?.game_running));

const scanDisabledReason = computed(() => {
  if (isGameRunning.value) return "Close the running game before scanning storage.";
  if (isCleanupRunning.value || busyAction.value) return "Wait for the cleanup to finish before scanning.";
  if (isScanning.value) return "A scan is already running.";
  return "";
});

// Why "Clean all safe items..." cannot run now, or "". The button keeps focus
// (aria-disabled) while a cleanup runs, so the keyboard user stays in place.
const safeCleanReason = computed(() => {
  if (busyAction.value === "safe") return "The safe items are being cleaned.";
  if (busyAction.value) return "Wait for the running cleanup to finish.";
  if (isScanning.value) return "Wait for the scan to finish.";
  if (isCleanupRunning.value) return "Wait for the cleanup to finish.";
  if (safePlan.value.ready.length === 0) return safePlan.value.skipped[0]?.reason ?? NOTHING_TO_FREE;
  return "";
});

const pageEl = ref<HTMLElement | null>(null);
const summaryEl = ref<HTMLElement | null>(null);

/**
 * After an action, put focus back on its control when the page dropped it
 * (a re-sorted row, a button that went away). `fallback` is used when the
 * control is gone. Focus that sits on another element is left alone.
 */
async function restoreFocus(selector: string, fallback?: () => HTMLElement | null) {
  await nextTick();
  const active = document.activeElement;
  if (active && active !== document.body && active.isConnected) return;
  const target = pageEl.value?.querySelector<HTMLElement>(selector) ?? fallback?.() ?? null;
  target?.focus();
}

// A scan that ends by itself removes the Stop button: keep keyboard focus on
// the page's scan button instead of letting it fall to the body.
// The watcher runs before the DOM update, so it can still see Stop focused.
watch(isScanning, (scanning, was) => {
  if (!was || scanning) return;
  if (document.activeElement?.matches("[data-storage-stop]")) {
    void restoreFocus("[data-storage-rescan], [data-storage-scan-now]");
  }
});

// The backend knows the number of folders only after it has made its plan.
const hasProgress = computed(() => (state.value?.total_units ?? 0) > 0);
const progressPercent = computed(() => {
  const total = state.value?.total_units ?? 0;
  const done = state.value?.completed_units ?? 0;
  if (total <= 0) return 0;
  return Math.min(100, Math.round((done / total) * 100));
});

const stoppedNote = computed(() =>
  stoppedScanNote(state.value?.stopped_reason ?? "", Boolean(report.value)),
);

const skippedNote = computed(() =>
  skippedEntriesNote(report.value?.skipped_entries ?? 0),
);

const reportAge = computed(() =>
  report.value ? formatRelativeAge(report.value.scanned_at_epoch) : "",
);

function stopPolling() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function scheduleNextPoll() {
  stopPolling();
  if (unmounted) return;
  if (state.value?.scanning || state.value?.cleanup_running) {
    pollTimer = window.setTimeout(() => {
      void fetchState();
    }, 500);
  }
}

async function fetchState() {
  try {
    const res = await api.getStorageState();
    if (unmounted) return;
    if (!res.success) {
      loadError.value = res.error || "Could not load storage state.";
    } else {
      loadError.value = "";
      state.value = res;
    }
  } catch (error) {
    if (!unmounted) {
      loadError.value = error instanceof Error ? error.message : String(error);
    }
  } finally {
    if (!unmounted) {
      loading.value = false;
      scheduleNextPoll();
    }
  }
}

async function startScan() {
  cleanupResult.value = "";
  cleanupError.value = "";
  try {
    const res = await api.startStorageScan();
    if (!res.success) {
      notifyError(res.error || "Could not start the storage scan.");
      await fetchState();
      return;
    }
    await fetchState();
  } catch (error) {
    notifyError(error instanceof Error ? error.message : String(error));
  } finally {
    // The scan button gives way to Stop while the scan runs.
    void restoreFocus("[data-storage-stop]");
  }
}

async function stopScan() {
  try {
    await api.stopStorageScan();
    await fetchState();
  } catch (error) {
    notifyError(error instanceof Error ? error.message : String(error));
  } finally {
    void restoreFocus("[data-storage-rescan], [data-storage-scan-now]");
  }
}

async function handleClean(action: StorageCleanupAction) {
  cleanupResult.value = "";
  cleanupError.value = "";
  busyAction.value = action.id;
  busyStage.value = "checking";
  try {
    const preview = await api.previewStorageCleanup(action.id);
    if (!preview.success || !preview.preview_id) {
      cleanupError.value = preview.error || "Could not check what to clean.";
      await fetchState();
      return;
    }
    if ((preview.count ?? 0) === 0 && (preview.bytes ?? 0) === 0) {
      cleanupResult.value = "Nothing to free.";
      await fetchState();
      return;
    }
    const previewedAt = Date.now();
    const confirmed = await confirmAction(buildCleanupConfirmOptions(preview));
    if (!confirmed) return;
    // The backend forgets a preview after 15 minutes; say so instead of running it.
    if (cleanupPreviewExpired(previewedAt)) {
      cleanupError.value = PREVIEW_EXPIRED_MESSAGE;
      await fetchState();
      return;
    }

    busyStage.value = "cleaning";
    const run = await api.runStorageCleanup(preview.preview_id);
    if (!run.success) {
      cleanupError.value = cleanupRunError(run);
      await fetchState();
      return;
    }
    cleanupResult.value = formatCleanupResult(run);
    await fetchState();
  } catch (error) {
    cleanupError.value = error instanceof Error ? error.message : String(error);
  } finally {
    busyAction.value = "";
    void restoreFocus(`[data-cleanup-button="${action.id}"]`);
  }
}

/** Preview every available Safe action, confirm them once, then run each preview. */
async function handleCleanSafe() {
  cleanupResult.value = "";
  cleanupError.value = "";
  if (safeCleanReason.value) return;
  const plan = planSafeCleanup(visibleActions.value);
  if (plan.ready.length === 0) return;
  busyAction.value = "safe";
  busyStage.value = "checking";
  try {
    const skipped: SkippedCleanup[] = [...plan.skipped];
    const previews: StorageCleanupPreviewResponse[] = [];
    const previewedAt = Date.now();
    for (const action of plan.ready) {
      const preview = await api.previewStorageCleanup(action.id);
      if (!preview.success || !preview.preview_id) {
        skipped.push({
          label: action.label,
          reason: preview.error || "Could not check what to clean.",
        });
      } else if ((preview.count ?? 0) === 0 && (preview.bytes ?? 0) === 0) {
        skipped.push({ label: action.label, reason: NOTHING_TO_FREE });
      } else {
        previews.push({ ...preview, label: preview.label || action.label });
      }
    }
    if (previews.length === 0) {
      cleanupResult.value = "Nothing can be freed safely.";
      await fetchState();
      return;
    }
    const confirmed = await confirmAction(buildSafeCleanupConfirmOptions(previews, skipped));
    if (!confirmed) return;
    if (cleanupPreviewExpired(previewedAt)) {
      cleanupError.value = PREVIEW_EXPIRED_MESSAGE;
      await fetchState();
      return;
    }

    busyStage.value = "cleaning";
    const runs: SafeCleanupRun[] = [];
    for (const preview of previews) {
      const result = await api.runStorageCleanup(preview.preview_id ?? "");
      runs.push({
        label: preview.label ?? "",
        result: result.success ? result : { ...result, error: cleanupRunError(result) },
      });
    }
    const summary = formatSafeCleanupResult(runs, skipped);
    cleanupResult.value = summary.message;
    cleanupError.value = summary.error;
    await fetchState();
  } catch (error) {
    cleanupError.value = error instanceof Error ? error.message : String(error);
    await fetchState();
  } finally {
    busyAction.value = "";
    // With nothing safe left the button goes away: the summary takes focus.
    void restoreFocus("[data-storage-summary-clean]", () => summaryEl.value);
  }
}

async function handleOpenLocation(target: string) {
  try {
    const res = await api.openStorageLocation(target);
    if (!res.success) notifyError(res.error || "Could not open folder.");
  } catch (error) {
    notifyError(error instanceof Error ? error.message : String(error));
  }
}

/** Focus for a game row's actions after its folder or library entry changed. */
function restoreGameFocus(row: Pick<StorageGameRow, "game_id">) {
  void restoreFocus(
    `[data-game-actions="${row.game_id}"] .menu-button-toggle`,
    () => pageEl.value?.querySelector<HTMLElement>("#storage-games-heading") ?? null,
  );
}

async function handleOpenGameFolder(row: StorageGameRow) {
  await handleOpenLocation(`game:${row.game_id}`);
}

async function handlePickGameFolder(row: StorageGameRow) {
  const start = row.root || row.exe_path || "";
  let picked = "";
  try {
    picked = await api.browseDirectory(start);
  } catch (error) {
    notifyError(error instanceof Error ? error.message : String(error));
    return;
  }
  if (!picked) return;
  try {
    const res = await api.setStorageGameFolder(row.game_id, picked);
    if (!res.success) {
      notifyError(res.error || "Could not set that folder.");
      return;
    }
    notify(`Saved folder for ${row.title}. Scan again to update its size.`, "success");
    await fetchState();
  } catch (error) {
    notifyError(error instanceof Error ? error.message : String(error));
  } finally {
    restoreGameFocus(row);
  }
}

async function handleClearGameFolder(row: StorageGameRow) {
  try {
    const res = await api.clearStorageGameFolder(row.game_id);
    if (!res.success) {
      notifyError(res.error || "Could not reset the folder.");
      return;
    }
    notify(`${row.title} now uses the folder wLib finds. Scan again to update its size.`, "info");
    await fetchState();
  } catch (error) {
    notifyError(error instanceof Error ? error.message : String(error));
  } finally {
    restoreGameFocus(row);
  }
}

async function handleUninstallGame(row: StorageGameRow) {
  const loaded = await gamesStore.refresh();
  const match = gamesStore.games.value.find((g) => g.id === row.game_id);
  // A saved report can name a game that has left the library since the scan.
  // The uninstall check would only answer "Game was not found", so say what
  // happened and how to update the list instead of opening a dead end.
  if (!match) {
    if (loaded) {
      notify(`${row.title} is no longer in your library. Scan again to update this list.`, "info");
    } else {
      notifyError(gamesStore.error.value || "Could not load the library. Try again.");
    }
    restoreGameFocus(row);
    return;
  }
  uninstallTargets.value = [match];
  uninstallOpen.value = true;
}

function confirmUninstall(targets: GameRecord[], options: UninstallOptions) {
  scheduleUninstall(targets, options);
  window.setTimeout(() => {
    void fetchState().then(() => {
      const game = targets[0];
      if (game) restoreGameFocus({ game_id: game.id });
    });
  }, 600);
}

onMounted(() => {
  onWebviewReady(() => {
    void fetchState();
    void loadPlatformCapabilities().then((caps) => {
      if (!unmounted) capabilities.value = caps;
    });
  });
});

onBeforeUnmount(() => {
  unmounted = true;
  stopPolling();
});
</script>

<template>
  <div ref="pageEl" class="storage-page allow-text-selection mx-auto w-full max-w-5xl space-y-6">
    <header class="storage-header">
      <div class="storage-header-text min-w-0">
        <h2 class="ui-page-heading text-3xl font-bold mb-2 tracking-tight" style="color: var(--text-primary)">
          <IconChartPie class="ui-page-heading-icon" />
          <span>Storage</span>
        </h2>
        <p class="text-sm pl-3" style="color: var(--text-secondary); border-left: 2px solid var(--brand)">
          See how much disk space your games and wLib's files use, and free the space you do not need.
        </p>
      </div>

      <!-- Before the first report the empty state holds the only Scan now button.
           With a report, scanning again is a secondary action: the page's primary
           action is cleaning the safe items. -->
      <div v-if="isScanning || report" class="storage-header-actions">
        <button
          v-if="isScanning"
          type="button"
          class="storage-stop-btn px-4 py-2 rounded-lg text-sm font-semibold inline-flex items-center gap-2"
          data-storage-stop
          @click="stopScan"
        >
          <IconPlayerStop class="w-4 h-4" />
          <span>Stop</span>
        </button>
        <button
          v-else
          type="button"
          class="storage-secondary-btn px-4 py-2 rounded-lg text-sm font-semibold inline-flex items-center gap-2"
          :disabled="Boolean(scanDisabledReason)"
          :title="scanDisabledReason"
          data-storage-rescan
          @click="startScan"
        >
          <IconRefresh class="w-4 h-4" />
          <span>Scan again</span>
        </button>
        <p v-if="scanDisabledReason && !isScanning" class="text-xs" style="color: var(--text-muted)">
          {{ scanDisabledReason }}
        </p>
      </div>
    </header>

    <div v-if="loadError" role="alert" class="ui-callout ui-callout--danger">
      <IconAlertTriangle class="w-4 h-4 shrink-0" />
      <span>{{ loadError }}</span>
    </div>

    <div v-if="state?.last_error" role="alert" class="ui-callout ui-callout--danger">
      <IconAlertTriangle class="w-4 h-4 shrink-0" />
      <span>{{ state.last_error }}</span>
    </div>

    <div v-if="stoppedNote && !isScanning" role="status" class="storage-note rounded-xl px-4 py-3 text-sm">
      {{ stoppedNote }}
    </div>

    <!-- Active scan progress -->
    <section
      v-if="isScanning"
      class="storage-card storage-pad-lg rounded-xl space-y-3"
      role="status"
      aria-live="polite"
    >
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2 text-sm font-semibold" style="color: var(--text-primary)">
          <IconLoader2 class="w-4 h-4 animate-spin" style="color: var(--brand)" />
          <span>Scanning: {{ state?.current_label || "Preparing..." }}</span>
        </div>
        <span class="text-xs tabular-nums" style="color: var(--text-secondary)">
          {{ (state?.files_counted ?? 0).toLocaleString() }} files · {{ formatStorageBytes(state?.bytes_counted ?? 0) }}<template
            v-if="hasProgress"
          > · <span class="font-semibold" style="color: var(--text-primary)" data-storage-progress>{{ progressPercent }}%</span></template>
        </span>
      </div>
      <!-- The share of folders done, not of bytes: a large game folder can hold the bar still for a while. -->
      <div
        class="h-2 w-full rounded-full overflow-hidden"
        style="background: var(--bg-inset)"
        role="progressbar"
        aria-label="Scan progress"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-valuenow="hasProgress ? progressPercent : undefined"
      >
        <div
          class="h-full rounded-full transition-all duration-200"
          :style="{ width: `${progressPercent}%`, backgroundColor: 'var(--brand)' }"
        ></div>
      </div>
      <p class="text-xs" style="color: var(--text-muted)">
        Running in the background at low CPU and disk priority. You can leave this page and come back while it runs.
      </p>
    </section>

    <!-- Empty state before first scan -->
    <section
      v-if="!loading && !report && !isScanning"
      class="storage-card storage-empty rounded-xl text-center"
      aria-labelledby="storage-empty-heading"
      data-storage-empty
    >
      <div class="max-w-md mx-auto">
        <h3 id="storage-empty-heading" class="text-lg font-bold" style="color: var(--text-primary)">No storage report yet</h3>
        <p class="text-sm mt-2" style="color: var(--text-secondary)">
          wLib scans your folders only when you ask. The scan runs at low priority and stops by itself if you start a game.
        </p>
        <button
          type="button"
          class="storage-primary-btn mt-6 px-6 py-2.5 rounded-lg text-sm font-bold inline-flex items-center gap-2"
          :disabled="Boolean(scanDisabledReason)"
          :title="scanDisabledReason"
          data-storage-scan-now
          @click="startScan"
        >
          <IconRefresh class="w-4 h-4" />
          <span>Scan now</span>
        </button>
        <p v-if="scanDisabledReason" class="text-xs mt-2" style="color: var(--text-muted)">
          {{ scanDisabledReason }}
        </p>
      </div>
    </section>

    <!-- Saved report: the answer ("how much can I free") and the cleanup that acts
         on it come first and sit close together; the breakdown and the games list
         below are the evidence behind those numbers. -->
    <template v-if="report">
      <div class="space-y-3">
        <section
          ref="summaryEl"
          class="storage-card storage-summary rounded-xl flex flex-wrap items-center justify-between gap-x-6 gap-y-4"
          aria-labelledby="storage-summary-headline"
          tabindex="-1"
          data-storage-summary
        >
          <div class="storage-summary-text min-w-0">
            <p
              id="storage-summary-headline"
              class="text-2xl font-semibold leading-tight tracking-tight"
              style="color: var(--text-primary)"
            >
              <template v-if="tierTotals.safe > 0">
                <span class="tabular-nums whitespace-nowrap" style="color: var(--success-text)">{{ formatStorageBytes(tierTotals.safe) }}</span>
                can be freed safely
              </template>
              <template v-else>Nothing can be freed safely</template>
            </p>
            <p
              v-if="tierTotals.review > 0 || tierTotals.risky > 0"
              class="text-sm mt-1.5"
              style="color: var(--text-secondary)"
            >
              <template v-if="tierTotals.review > 0">
                <span class="tabular-nums whitespace-nowrap font-semibold">{{ formatStorageBytes(tierTotals.review) }}</span>
                to review</template><template v-if="tierTotals.review > 0 && tierTotals.risky > 0">, </template>
              <template v-if="tierTotals.risky > 0">
                <span class="tabular-nums whitespace-nowrap font-semibold">{{ formatStorageBytes(tierTotals.risky) }}</span>
                that can hold saves</template>.
            </p>
            <p
              v-if="safePlan.ready.length === 0 && safePlan.skipped.length > 0 && tierTotals.safe > 0"
              class="text-xs mt-1.5"
              style="color: var(--text-muted)"
            >
              {{ safePlan.skipped[0]?.reason }}
            </p>
            <p class="text-xs mt-3" style="color: var(--text-muted)" data-storage-meta>
              <span class="tabular-nums whitespace-nowrap">{{ formatStorageBytes(report.total_bytes) }} on disk</span>
              · <span class="tabular-nums whitespace-nowrap">{{ report.total_files.toLocaleString() }} files</span>
              · <span class="whitespace-nowrap">counted {{ reportAge }}</span>
              <template v-if="skippedNote">
                · <span class="font-medium" style="color: var(--warning-text)">{{ skippedNote }}</span>
              </template>
            </p>
          </div>
          <button
            v-if="tierTotals.safe > 0"
            type="button"
            class="storage-primary-btn storage-summary-clean px-5 py-2.5 rounded-lg text-sm font-bold inline-flex items-center justify-center gap-2 shrink-0"
            :aria-disabled="Boolean(safeCleanReason) || undefined"
            :aria-busy="busyAction === 'safe' || undefined"
            :aria-describedby="safeCleanReason ? 'storage-safe-clean-reason' : undefined"
            :title="safeCleanReason || undefined"
            data-storage-summary-clean
            @click="handleCleanSafe"
          >
            <IconLoader2 v-if="busyAction === 'safe'" class="w-4 h-4 animate-spin" />
            <IconSparkles v-else class="w-4 h-4" />
            <span>{{ busyAction === "safe" ? busyLabel : "Clean all safe items..." }}</span>
          </button>
          <span v-if="safeCleanReason" id="storage-safe-clean-reason" class="sr-only">{{ safeCleanReason }}</span>
        </section>

        <StorageCleanupSection
          :actions="visibleActions"
          :busy-action="busyAction"
          :busy-label="busyLabel"
          :categories="visibleCategories"
          :result-message="cleanupResult"
          :error-message="cleanupError"
          @clean="handleClean"
        />
      </div>

      <div class="storage-evidence space-y-6">
        <StorageCategoryBreakdown
          :categories="visibleCategories"
          :total-bytes="report.total_bytes"
          @open-location="handleOpenLocation"
        />

        <StorageGamesList
          :games="report.games"
          :busy="isTaskRunning"
          @open-folder="handleOpenGameFolder"
          @uninstall="handleUninstallGame"
          @pick-folder="handlePickGameFolder"
          @clear-folder="handleClearGameFolder"
        />
      </div>
    </template>

    <UninstallDialog
      v-model="uninstallOpen"
      :games="uninstallTargets"
      @confirm="confirmUninstall"
    />
  </div>
</template>

<style scoped>
/* The page is the container its sections measure: the sidebar takes a varying
   width (and the interface scale zooms everything), so the window width says
   little about the room the page has. Side padding is a share of the main
   area, so it shrinks with the room too. */
.storage-page {
  container: storage-page / inline-size;
  padding: clamp(1rem, 4%, 2rem) clamp(1rem, 4%, 2rem) 3rem;
}
.storage-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
.storage-header-text {
  flex: 1 1 auto;
}
.storage-header-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.375rem;
  flex-shrink: 0;
  margin-left: auto;
}
.storage-summary {
  padding: 1.25rem 1.5rem;
}
/* The text keeps a readable width; the button wraps under it before it squeezes the text. */
.storage-summary-text {
  flex: 1 1 18rem;
}
.storage-pad-lg {
  padding: 1.5rem;
}
.storage-empty {
  padding: 2.5rem 1.5rem;
}
/* Too narrow for the subtitle beside the button: the button goes under it. */
@container storage-page (max-width: 34rem) {
  .storage-header {
    flex-direction: column;
    align-items: stretch;
    gap: 0.75rem;
  }
  .storage-header-actions {
    align-items: flex-start;
    margin-left: 0;
  }
}
/* Phone-width room: tighter cards, and the page's main action spans the card. */
@container storage-page (max-width: 30rem) {
  .storage-summary,
  .storage-pad-lg {
    padding: 1rem;
  }
  .storage-empty {
    padding: 2rem 1rem;
  }
  .storage-summary-clean {
    width: 100%;
  }
}
.storage-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
}
.storage-note {
  background: var(--bg-inset);
  border: 1px solid var(--border);
  color: var(--text-secondary);
}
.storage-primary-btn {
  background: var(--brand);
  color: var(--on-brand);
  box-shadow: var(--shadow-brand);
}
.storage-primary-btn:hover:not(:disabled):not([aria-disabled="true"]) {
  filter: brightness(1.08);
}
/* A blocked button looks like the app's other disabled buttons (.ui-card-btn in
   style.css): flat, muted label, dashed edge. The label stays readable. */
.storage-primary-btn:disabled,
.storage-primary-btn[aria-disabled="true"],
.storage-secondary-btn:disabled {
  background: var(--bg-raised);
  color: var(--text-muted);
  border-color: transparent;
  box-shadow: none;
  outline: 1px dashed var(--border-hover);
  outline-offset: -1px;
  filter: none;
  cursor: not-allowed;
}
/* The running batch keeps its colour so "Checking..." or "Cleaning..." reads as work, not as blocked. */
.storage-primary-btn[aria-busy="true"] {
  background: var(--brand);
  color: var(--on-brand);
  outline: none;
  opacity: 0.85;
  cursor: progress;
}
.storage-summary:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--brand-glow);
}
.storage-secondary-btn {
  background: var(--bg-raised);
  color: var(--text-primary);
  border: 1px solid var(--border);
}
.storage-secondary-btn:hover:not(:disabled) {
  background: var(--bg-overlay);
  border-color: var(--border-hover);
}
/* The evidence sits a step further from the answer than the answer's parts sit from each other. */
.storage-evidence {
  padding-top: 1rem;
}
/* No one-word last lines in the page's sentences (the header subtitle, card notes, row hints). */
.storage-page :deep(p) {
  text-wrap: pretty;
}
.storage-stop-btn {
  background: var(--danger-bg);
  color: var(--danger-text);
  border: 1px solid var(--danger-border);
}
.storage-stop-btn:hover {
  filter: brightness(1.08);
}
.storage-primary-btn:focus-visible,
.storage-secondary-btn:focus-visible,
.storage-stop-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--brand-glow);
}
</style>
