<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  IconAlertTriangle,
  IconChartPie,
  IconLoader2,
  IconPlayerStop,
  IconRefresh,
} from "@tabler/icons-vue";
import {
  api,
  onWebviewReady,
  type GameRecord,
  type PlatformCapabilities,
  type StorageCleanupAction,
  type StorageCleanupActionId,
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
  formatCleanupResult,
  formatRelativeAge,
  formatStorageBytes,
  skippedEntriesNote,
  stoppedScanNote,
  visibleStorageActions,
  visibleStorageCategories,
} from "../utils/storage";

const state = ref<StorageStateResponse | null>(null);
const loading = ref(true);
const loadError = ref("");
const busyAction = ref<StorageCleanupActionId | "">("");
const cleanupResult = ref("");
const cleanupError = ref("");
const uninstallOpen = ref(false);
const uninstallTargets = ref<GameRecord[]>([]);

let pollTimer: number | null = null;
let unmounted = false;

const report = computed(() => state.value?.report ?? null);
const capabilities = ref<PlatformCapabilities | null>(null);
// Unknown until the capabilities load: keep everything visible meanwhile.
const wineProton = computed(() => capabilities.value?.wine_proton ?? true);
const visibleCategories = computed(() =>
  visibleStorageCategories(report.value?.categories ?? [], wineProton.value),
);
const visibleActions = computed(() =>
  visibleStorageActions(state.value?.actions ?? [], wineProton.value),
);
const isScanning = computed(() => Boolean(state.value?.scanning));
const isCleanupRunning = computed(() => Boolean(state.value?.cleanup_running));
const isTaskRunning = computed(() => isScanning.value || isCleanupRunning.value);
const isGameRunning = computed(() => Boolean(state.value?.game_running));

const scanDisabledReason = computed(() => {
  if (isGameRunning.value) return "Close the running game before scanning storage.";
  if (isCleanupRunning.value) return "Wait for the cleanup to finish before scanning.";
  if (isScanning.value) return "A scan is already running.";
  return "";
});

const scanButtonLabel = computed(() => (report.value ? "Scan again" : "Scan Now"));

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
  }
}

async function stopScan() {
  try {
    await api.stopStorageScan();
    await fetchState();
  } catch (error) {
    notifyError(error instanceof Error ? error.message : String(error));
  }
}

async function handleClean(action: StorageCleanupAction) {
  cleanupResult.value = "";
  cleanupError.value = "";
  busyAction.value = action.id;
  try {
    const preview = await api.previewStorageCleanup(action.id);
    if (!preview.success || !preview.preview_id) {
      cleanupError.value = preview.error || "Could not prepare cleanup preview.";
      await fetchState();
      return;
    }
    if ((preview.count ?? 0) === 0 && (preview.bytes ?? 0) === 0) {
      cleanupResult.value = "Nothing to free.";
      await fetchState();
      return;
    }
    const confirmed = await confirmAction(buildCleanupConfirmOptions(preview));
    if (!confirmed) return;

    const run = await api.runStorageCleanup(preview.preview_id);
    if (!run.success) {
      cleanupError.value = run.error || "Cleanup failed.";
      await fetchState();
      return;
    }
    cleanupResult.value = formatCleanupResult(run);
    await fetchState();
  } catch (error) {
    cleanupError.value = error instanceof Error ? error.message : String(error);
  } finally {
    busyAction.value = "";
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
  }
}

async function handleClearGameFolder(row: StorageGameRow) {
  try {
    const res = await api.clearStorageGameFolder(row.game_id);
    if (!res.success) {
      notifyError(res.error || "Could not clear picked folder.");
      return;
    }
    notify(`Cleared picked folder for ${row.title}.`, "info");
    await fetchState();
  } catch (error) {
    notifyError(error instanceof Error ? error.message : String(error));
  }
}

async function handleUninstallGame(row: StorageGameRow) {
  await gamesStore.refresh();
  const match = gamesStore.games.value.find((g) => g.id === row.game_id);
  const target: GameRecord = match ?? {
    id: row.game_id,
    title: row.title,
    exe_path: row.exe_path,
  };
  uninstallTargets.value = [target];
  uninstallOpen.value = true;
}

function confirmUninstall(targets: GameRecord[], options: UninstallOptions) {
  scheduleUninstall(targets, options);
  window.setTimeout(() => {
    void fetchState();
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
  <div class="storage-page allow-text-selection mx-auto w-full p-8 max-w-5xl pb-12 space-y-6">
    <header class="flex flex-wrap items-start justify-between gap-4">
      <div class="min-w-0 flex-1">
        <h2 class="ui-page-heading text-3xl font-bold mb-2 tracking-tight" style="color: var(--text-primary)">
          <IconChartPie class="ui-page-heading-icon" />
          <span>Storage</span>
        </h2>
        <p class="text-sm pl-3" style="color: var(--text-secondary); border-left: 2px solid var(--brand)">
          See where wLib's disk space goes (size on disk) and safely clean caches and leftovers.
        </p>
      </div>

      <div class="ml-auto shrink-0 flex flex-col items-end gap-1.5">
        <div class="flex items-center gap-2">
          <button
            v-if="isScanning"
            type="button"
            class="storage-stop-btn px-4 py-2.5 rounded-lg text-sm font-semibold inline-flex items-center gap-2"
            @click="stopScan"
          >
            <IconPlayerStop class="w-4 h-4" />
            <span>Stop</span>
          </button>
          <button
            v-else
            type="button"
            class="storage-primary-btn px-5 py-2.5 rounded-lg text-sm font-bold inline-flex items-center gap-2"
            :disabled="Boolean(scanDisabledReason)"
            :title="scanDisabledReason"
            @click="startScan"
          >
            <IconRefresh class="w-4 h-4" />
            <span>{{ scanButtonLabel }}</span>
          </button>
        </div>
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
      class="storage-card rounded-xl p-6 space-y-3"
      role="status"
      aria-live="polite"
    >
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2 text-sm font-semibold" style="color: var(--text-primary)">
          <IconLoader2 class="w-4 h-4 animate-spin" style="color: var(--brand)" />
          <span>Scanning: {{ state?.current_label || "Preparing..." }}</span>
        </div>
        <span class="text-xs tabular-nums" style="color: var(--text-secondary)">
          {{ (state?.files_counted ?? 0).toLocaleString() }} files · {{ formatStorageBytes(state?.bytes_counted ?? 0) }}
        </span>
      </div>
      <div class="h-2 w-full rounded-full overflow-hidden" style="background: var(--bg-inset)">
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
      class="storage-card rounded-xl p-10 text-center space-y-4"
    >
      <div class="mx-auto w-12 h-12 rounded-2xl flex items-center justify-center" style="background: var(--brand-glow); color: var(--brand)">
        <IconChartPie class="w-6 h-6" />
      </div>
      <div class="max-w-md mx-auto space-y-2">
        <h3 class="text-lg font-bold" style="color: var(--text-primary)">No storage report yet</h3>
        <p class="text-sm" style="color: var(--text-secondary)">
          wLib does not scan your folders until you ask. Choose Scan Now to count size on disk at low priority; the scan stops automatically if you start a game.
        </p>
      </div>
      <div class="pt-1">
        <button
          type="button"
          class="storage-primary-btn px-6 py-2.5 rounded-lg text-sm font-bold inline-flex items-center gap-2"
          :disabled="Boolean(scanDisabledReason)"
          :title="scanDisabledReason"
          @click="startScan"
        >
          <IconRefresh class="w-4 h-4" />
          <span>Scan Now</span>
        </button>
      </div>
    </section>

    <!-- Saved report -->
    <template v-if="report">
      <div class="flex flex-wrap items-center justify-between gap-2 text-xs px-1" style="color: var(--text-muted)">
        <span>
          Counted {{ reportAge }} · {{ report.total_files.toLocaleString() }} files · {{ formatStorageBytes(report.total_bytes) }} on disk
        </span>
        <span v-if="skippedNote" class="font-medium" style="color: #f59e0b">
          {{ skippedNote }}
        </span>
      </div>

      <StorageCategoryBreakdown
        :categories="visibleCategories"
        :total-bytes="report.total_bytes"
        @open-location="handleOpenLocation"
      />

      <StorageCleanupSection
        :actions="visibleActions"
        :busy-action="busyAction"
        :result-message="cleanupResult"
        :error-message="cleanupError"
        @clean="handleClean"
      />

      <StorageGamesList
        :games="report.games"
        :busy="isTaskRunning"
        @open-folder="handleOpenGameFolder"
        @uninstall="handleUninstallGame"
        @pick-folder="handlePickGameFolder"
        @clear-folder="handleClearGameFolder"
      />
    </template>

    <UninstallDialog
      v-model="uninstallOpen"
      :games="uninstallTargets"
      @confirm="confirmUninstall"
    />
  </div>
</template>

<style scoped>
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
.storage-primary-btn:hover:not(:disabled) {
  filter: brightness(1.08);
}
.storage-primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
.storage-stop-btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px var(--brand-glow);
}
</style>
