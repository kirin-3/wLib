// SPDX-License-Identifier: GPL-3.0-or-later
import type {
  StorageCategoryReport,
  StorageCleanupAction,
  StorageCleanupActionId,
  StorageCleanupPreviewResponse,
  StorageCleanupRunResponse,
  StorageCleanupTier,
  StorageGameRow,
  StorageReport,
  StorageStateResponse,
} from "../api";

const GB = 1024 * 1024 * 1024;
const MB = 1024 * 1024;

const MOCK_ACTION_META: Record<
  StorageCleanupActionId,
  { label: string; tier: StorageCleanupTier; what_stays: string; warning: string }
> = {
  download_archives: {
    label: "Finished download archives",
    tier: "review",
    what_stays: "Partial downloads, downloads waiting to be installed, open archives, and torrent data stay.",
    warning: "",
  },
  unpack_leftovers: {
    label: "Temporary install files",
    tier: "safe",
    what_stays: "Installed games and downloads stay.",
    warning: "",
  },
  removed_game_data: {
    label: "Data of removed games",
    tier: "risky",
    what_stays: "Active games, their sandboxes, and save backups stay.",
    warning: "Removed game sandboxes and built-in player storage can hold game saves.",
  },
  save_backups: {
    label: "Old save backups",
    tier: "risky",
    what_stays: "The newest 3 backups for each game stay.",
    warning: "",
  },
  proton_builds: {
    label: "Unused Proton builds",
    tier: "review",
    what_stays: "Proton builds used by any game, the default runner, and Steam, Lutris or umu builds stay.",
    warning: "",
  },
  cover_cache: {
    label: "Cover cache",
    tier: "safe",
    what_stays: "User-chosen local covers stay.",
    warning: "",
  },
  bepinex_cache: {
    label: "BepInEx download cache",
    tier: "safe",
    what_stays: "Installed BepInEx files inside game folders stay.",
    warning: "",
  },
};

const ACTION_ORDER: StorageCleanupActionId[] = [
  "download_archives",
  "unpack_leftovers",
  "removed_game_data",
  "save_backups",
  "proton_builds",
  "cover_cache",
  "bepinex_cache",
];

function recalculatePercents(report: StorageReport): void {
  const total = report.categories.reduce((sum, cat) => sum + Math.max(0, cat.bytes), 0);
  report.total_bytes = total;
  for (const cat of report.categories) {
    cat.percent = total > 0 ? Math.round((cat.bytes / total) * 1000) / 10 : 0;
  }
}

export function buildMockStorageReport(): StorageReport {
  const nowEpoch = Math.floor(Date.now() / 1000) - 3600 * 3;
  const games: Record<string, StorageGameRow> = {
    "1": {
      game_id: 1,
      title: "Midnight Harbor",
      root: "/games/Library/Studio North/Midnight Harbor/v0.9",
      root_source: "pattern",
      exe_path: "/games/Library/Studio North/Midnight Harbor/v0.9/MidnightHarbor.sh",
      picked: false,
      uncertain: false,
      unsafe: false,
      unsafe_reason: "",
      unavailable: false,
      changed: false,
      shared_with: [],
      shared_with_ids: [],
      parts: {
        game: Math.round(12.9 * GB),
        saves: Math.round(0.8 * GB),
        player: 0,
        sandbox: Math.round(0.2 * GB),
        prefix: Math.round(0.5 * GB),
      },
      torrent_bytes: Math.round(4.1 * GB),
      total_bytes: Math.round(14.4 * GB),
    },
    "2": {
      game_id: 2,
      title: "Lantern District",
      root: "/data/LanternDistrict",
      root_source: "exe_dir",
      exe_path: "/data/LanternDistrict/Game.exe",
      picked: false,
      uncertain: true,
      unsafe: false,
      unsafe_reason: "",
      unavailable: false,
      changed: false,
      shared_with: [],
      shared_with_ids: [],
      parts: {
        game: Math.round(6.4 * GB),
        saves: Math.round(120 * MB),
        player: Math.round(45 * MB),
        sandbox: 0,
        prefix: 0,
      },
      torrent_bytes: 0,
      total_bytes: Math.round(6.4 * GB + 165 * MB),
    },
    "3": {
      game_id: 3,
      title: "Starlight Archive",
      root: "",
      root_source: "exe_dir",
      exe_path: "/games/Library/Starlight.exe",
      picked: false,
      uncertain: false,
      unsafe: true,
      unsafe_reason: "A library folder cannot be used as a game folder.",
      unavailable: false,
      changed: false,
      shared_with: [],
      shared_with_ids: [],
      parts: { game: 0, saves: 0, player: 0, sandbox: 0, prefix: 0 },
      torrent_bytes: 0,
      total_bytes: 0,
    },
  };

  const categories: StorageCategoryReport[] = [
    { id: "games", label: "Games", bytes: Math.round(19.3 * GB), files: 28410, percent: 0 },
    { id: "download_archives", label: "Download archives", bytes: Math.round(3.1 * GB), files: 4, percent: 0 },
    { id: "torrent_data", label: "Torrent data", bytes: Math.round(4.1 * GB), files: 12, percent: 0 },
    { id: "proton_builds", label: "Proton builds", bytes: Math.round(2.4 * GB), files: 9120, percent: 0 },
    { id: "wine_prefixes", label: "Wine prefixes", bytes: Math.round(0.5 * GB), files: 2340, percent: 0 },
    { id: "shared_wine_prefix", label: "Shared Wine prefix", bytes: Math.round(1.2 * GB), files: 4820, percent: 0 },
    { id: "save_backups", label: "Save backups", bytes: Math.round(0.92 * GB), files: 620, percent: 0 },
    { id: "web_player", label: "Built-in player data", bytes: Math.round(45 * MB), files: 185, percent: 0 },
    { id: "sandboxes", label: "Sandboxes", bytes: Math.round(0.2 * GB), files: 910, percent: 0 },
    { id: "removed_game_data", label: "Data of removed games", bytes: Math.round(380 * MB), files: 1140, percent: 0 },
    { id: "cover_cache", label: "Cover cache", bytes: Math.round(145 * MB), files: 520, percent: 0 },
    { id: "other_caches", label: "Other caches", bytes: Math.round(210 * MB), files: 430, percent: 0 },
    { id: "catalog", label: "Catalog", bytes: Math.round(88 * MB), files: 2, percent: 0 },
    { id: "logs_and_backups", label: "Logs and database backups", bytes: Math.round(34 * MB), files: 9, percent: 0 },
  ];

  const report: StorageReport = {
    version: 1,
    scanned_at: new Date(nowEpoch * 1000).toISOString(),
    scanned_at_epoch: nowEpoch,
    total_bytes: 0,
    total_files: categories.reduce((acc, c) => acc + c.files, 0),
    skipped_entries: 0,
    categories,
    games,
    actions: {
      download_archives: { bytes: Math.round(3.1 * GB), count: 3 },
      unpack_leftovers: { bytes: 0, count: 0 },
      removed_game_data: { bytes: Math.round(380 * MB), count: 2 },
      save_backups: { bytes: Math.round(260 * MB), count: 4 },
      proton_builds: { bytes: Math.round(1.1 * GB), count: 1 },
      cover_cache: { bytes: Math.round(145 * MB), count: 520 },
      bepinex_cache: { bytes: Math.round(64 * MB), count: 2 },
    },
  };
  recalculatePercents(report);
  return report;
}

/** Item names a preview lists, like the backend's 8 largest targets. */
const MOCK_ITEM_NAMES: Partial<Record<StorageCleanupActionId, string[]>> = {
  download_archives: ["MidnightHarbor-0.9-pc.zip", "LanternDistrict-v6.1.zip", "rpgm-runner.tar.gz"],
  removed_game_data: [
    "Sandbox of a game removed 2026-11-02 21:14",
    "Built-in player data of a game removed 2026-10-18 09:40",
  ],
  save_backups: [
    "Midnight Harbor: backup from 2026-11-01 20:12",
    "Midnight Harbor: backup from 2026-10-28 19:03",
    "Lantern District: backup from 2026-10-30 22:47",
    "Lantern District: backup from 2026-10-21 18:30",
  ],
  proton_builds: ["GE-Proton9-20"],
  bepinex_cache: ["BepInEx_win_x64_5.4.23.2", "BepInEx-Unity.IL2CPP-6.0.0-be.733"],
};

function mockPreviewItems(
  action: StorageCleanupActionId,
  count: number,
  bytes: number,
): Pick<StorageCleanupPreviewResponse, "items" | "more_items"> {
  const names = (MOCK_ITEM_NAMES[action] ?? []).slice(0, Math.min(count, 8));
  if (names.length === 0) return { items: [], more_items: 0 };
  // Largest first, splitting the total so the sizes add up.
  const weights = names.map((_, index) => names.length - index);
  const sum = weights.reduce((a, b) => a + b, 0);
  return {
    items: names.map((label, index) => ({
      label,
      bytes: Math.round((bytes * (weights[index] ?? 0)) / sum),
    })),
    more_items: Math.max(0, count - names.length),
  };
}

/**
 * Previews expire after 15 minutes, like the backend. A browser preview can
 * set `wlib-mock-storage-preview-ttl-ms` to try the expired path quickly.
 */
function mockPreviewTtlMs(): number {
  try {
    const raw = Number(localStorage.getItem("wlib-mock-storage-preview-ttl-ms"));
    if (Number.isFinite(raw) && raw > 0) return raw;
  } catch {
    // Ignore storage access errors in headless contexts.
  }
  return 15 * 60 * 1000;
}

interface MockPreview {
  id: string;
  action: StorageCleanupActionId;
  count: number;
  bytes: number;
  createdAt: number;
}

export class MockStorage {
  private report: StorageReport | null = null;
  private task: "idle" | "scan" | "recount" | "cleanup" = "idle";
  private scanStep = 0;
  private readonly scanTotalSteps = 3;
  private stoppedReason: "" | "user" | "game" = "";
  private previews = new Map<string, MockPreview>();
  private gameRunning: () => boolean;

  constructor(gameRunning: () => boolean) {
    this.gameRunning = gameRunning;
    // Allow browser preview URLs or tests to pre-seed a finished report via localStorage.
    try {
      const mode = localStorage.getItem("wlib-mock-storage-mode");
      if (mode === "report") {
        this.report = buildMockStorageReport();
      }
    } catch {
      // Ignore storage access errors in headless contexts.
    }
  }

  private buildActions(): StorageCleanupAction[] {
    const busy =
      this.task === "scan" || this.task === "recount"
        ? "A storage scan is running."
        : this.task === "cleanup"
          ? "A cleanup is running."
          : "";
    return ACTION_ORDER.map((id) => {
      const meta = MOCK_ACTION_META[id];
      const summary = this.report?.actions[id] ?? { bytes: 0, count: 0 };
      let blocked = busy;
      if (!blocked && id === "proton_builds" && this.gameRunning()) {
        blocked = "A game started by wLib is running.";
      }
      return {
        id,
        label: meta.label,
        tier: meta.tier,
        bytes: summary.bytes,
        count: summary.count,
        blocked_reason: blocked,
        what_stays: meta.what_stays,
        warning: meta.warning,
      };
    });
  }

  getState(): StorageStateResponse {
    if (this.task === "scan") {
      this.scanStep += 1;
      if (this.scanStep >= this.scanTotalSteps) {
        this.task = "idle";
        this.report = buildMockStorageReport();
        this.stoppedReason = "";
      }
    }
    const labels = ["Midnight Harbor", "Proton builds", "Cover cache"];
    const scanning = this.task === "scan" || this.task === "recount";
    return {
      success: true,
      task: this.task,
      scanning,
      cleanup_running: this.task === "cleanup",
      game_running: this.gameRunning(),
      completed_units: scanning ? this.scanStep * 6 : 0,
      total_units: scanning ? this.scanTotalSteps * 6 : 0,
      files_counted: scanning ? this.scanStep * 14200 : 0,
      bytes_counted: scanning ? Math.round(this.scanStep * 10.2 * GB) : 0,
      current_label: scanning ? (labels[this.scanStep % labels.length] ?? "Games") : "",
      stopped_reason: this.stoppedReason,
      last_error: "",
      report: this.report ? JSON.parse(JSON.stringify(this.report)) : null,
      actions: this.buildActions(),
    };
  }

  startScan(): { success: boolean; code?: string; error?: string } {
    if (this.gameRunning()) {
      return {
        success: false,
        code: "game_running",
        error: "Close the running game before scanning storage.",
      };
    }
    if (this.task !== "idle") {
      return {
        success: false,
        code: "task_running",
        error: "A storage task is already running.",
      };
    }
    this.task = "scan";
    this.scanStep = 0;
    this.stoppedReason = "";
    return { success: true };
  }

  stopScan(reason: "user" | "game" = "user"): { success: boolean; stopped: boolean } {
    if (this.task !== "scan" && this.task !== "recount") {
      return { success: true, stopped: false };
    }
    this.task = "idle";
    this.stoppedReason = reason;
    return { success: true, stopped: true };
  }

  previewCleanup(action: StorageCleanupActionId): StorageCleanupPreviewResponse {
    const meta = MOCK_ACTION_META[action];
    if (!meta) return { success: false, error: `Unknown cleanup action: ${action}` };
    if (this.task !== "idle") {
      return {
        success: false,
        code: "task_running",
        error: "A storage task is already running.",
      };
    }
    const summary = this.report?.actions[action] ?? { bytes: 0, count: 0 };
    const id = `mock-preview-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    this.previews.set(id, {
      id,
      action,
      count: summary.count,
      bytes: summary.bytes,
      createdAt: Date.now(),
    });
    return {
      success: true,
      preview_id: id,
      action,
      label: meta.label,
      tier: meta.tier,
      count: summary.count,
      bytes: summary.bytes,
      what_stays: meta.what_stays,
      warning: meta.warning,
      ...mockPreviewItems(action, summary.count, summary.bytes),
    };
  }

  runCleanup(previewId: string): StorageCleanupRunResponse {
    const entry = this.previews.get(previewId);
    this.previews.delete(previewId);
    if (!entry || Date.now() - entry.createdAt > mockPreviewTtlMs()) {
      return {
        success: false,
        code: "preview_expired",
        error: "This cleanup preview has expired. Choose the action again.",
      };
    }
    if (this.report) {
      const catByAction: Record<StorageCleanupActionId, string> = {
        download_archives: "download_archives",
        unpack_leftovers: "other_caches",
        removed_game_data: "removed_game_data",
        save_backups: "save_backups",
        proton_builds: "proton_builds",
        cover_cache: "cover_cache",
        bepinex_cache: "other_caches",
      };
      const catId = catByAction[entry.action];
      const cat = this.report.categories.find((c) => c.id === catId);
      if (cat) {
        cat.bytes = Math.max(0, cat.bytes - entry.bytes);
      }
      this.report.actions[entry.action] = { bytes: 0, count: 0 };
      recalculatePercents(this.report);
    }
    return {
      success: true,
      action: entry.action,
      freed_bytes: entry.bytes,
      removed: entry.count,
      failed: 0,
      skipped: 0,
    };
  }

  setGameFolder(gameId: number, path: string): { success: boolean; root?: string; error?: string } {
    if (!path.trim()) return { success: false, error: "Choose a folder." };
    const row = this.report?.games[String(gameId)];
    if (row) {
      row.root = path.trim();
      row.root_source = "picked";
      row.picked = true;
      row.uncertain = false;
      row.unsafe = false;
      row.unsafe_reason = "";
      row.changed = true;
    }
    return { success: true, root: path.trim() };
  }

  clearGameFolder(gameId: number): { success: boolean; cleared: boolean } {
    const row = this.report?.games[String(gameId)];
    if (row) {
      row.picked = false;
      row.changed = true;
    }
    return { success: true, cleared: true };
  }
}
