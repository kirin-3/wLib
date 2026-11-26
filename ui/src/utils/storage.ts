// SPDX-License-Identifier: GPL-3.0-or-later
import type {
  PlatformCapabilities,
  StorageCleanupAction,
  StorageCleanupActionId,
  StorageCleanupPreviewResponse,
  StorageCategoryReport,
  StorageCleanupRunResponse,
  StorageCleanupTier,
  StorageGameRow,
} from "../services/api";
import type { ConfirmOptions } from "./confirm";

export interface StorageFlag {
  kind: "unsafe" | "uncertain" | "unavailable" | "changed" | "shared";
  label: string;
  tone: "danger" | "warn" | "info";
}

export interface StoragePartItem {
  key: keyof StorageGameRow["parts"];
  label: string;
  bytes: number;
}

const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

export function formatStorageBytes(bytes: number): string {
  const safe = Number.isFinite(bytes) ? Math.max(0, bytes) : 0;
  if (safe < 1024) return `${Math.round(safe)} B`;
  let value = safe;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < UNITS.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const decimals = value >= 100 ? 0 : 1;
  return `${value.toFixed(decimals)} ${UNITS[unitIndex]}`;
}

export function formatRelativeAge(epochSeconds: number, nowMs = Date.now()): string {
  if (!Number.isFinite(epochSeconds) || epochSeconds <= 0) return "recently";
  const diffSec = Math.max(0, Math.floor(nowMs / 1000 - epochSeconds));
  if (diffSec < 45) return "just now";
  const mins = Math.round(diffSec / 60);
  if (mins < 60) return mins === 1 ? "1 minute ago" : `${mins} minutes ago`;
  const hours = Math.round(diffSec / 3600);
  if (hours < 24) return hours === 1 ? "1 hour ago" : `${hours} hours ago`;
  const days = Math.round(diffSec / 86400);
  if (days < 30) return days === 1 ? "1 day ago" : `${days} days ago`;
  const months = Math.round(days / 30);
  return months === 1 ? "1 month ago" : `${months} months ago`;
}

/** Report parts that only exist where Wine and Proton run (Linux). */
const WINE_PROTON_CATEGORIES = new Set(["proton_builds", "wine_prefixes", "shared_wine_prefix"]);
const WINE_PROTON_ACTIONS = new Set(["proton_builds"]);

/**
 * Drop the empty Wine and Proton categories on a platform without Wine and
 * Proton. A category that holds data always stays visible.
 */
export function visibleStorageCategories(
  categories: readonly StorageCategoryReport[],
  wineProton: boolean,
): StorageCategoryReport[] {
  if (wineProton) return [...categories];
  return categories.filter((cat) => cat.bytes > 0 || !WINE_PROTON_CATEGORIES.has(cat.id));
}

/** Drop the empty Proton cleanup on a platform without Wine and Proton. */
export function visibleStorageActions(
  actions: readonly StorageCleanupAction[],
  wineProton: boolean,
): StorageCleanupAction[] {
  if (wineProton) return [...actions];
  return actions.filter(
    (action) => action.bytes > 0 || action.count > 0 || !WINE_PROTON_ACTIONS.has(action.id),
  );
}

/** Categories largest first; equal sizes keep the report's order. */
export function sortedStorageCategories(
  categories: readonly StorageCategoryReport[],
): StorageCategoryReport[] {
  return categories
    .map((cat, index) => ({ cat, index }))
    .sort((a, b) => b.cat.bytes - a.cat.bytes || a.index - b.index)
    .map(({ cat }) => cat);
}

export function sortedStorageGames(games: Record<string, StorageGameRow>): StorageGameRow[] {
  return Object.values(games).sort((a, b) => {
    if (b.total_bytes !== a.total_bytes) return b.total_bytes - a.total_bytes;
    return a.title.localeCompare(b.title);
  });
}

export interface GroupedStorageCategories {
  /** Categories that get their own row and bar segment, largest first. */
  main: StorageCategoryReport[];
  /** Small and empty categories folded into one "Other" row, largest first. */
  other: StorageCategoryReport[];
  otherBytes: number;
  otherPercent: number;
}

/**
 * Keep the largest categories (at most `maxMain`, each at least `minPercent`
 * of the total) as their own rows and fold the rest into one "Other" group.
 * A group of one is not worth a fold, so that category stays a row of its own.
 */
export function groupStorageCategories(
  categories: readonly StorageCategoryReport[],
  maxMain = 6,
  minPercent = 1,
): GroupedStorageCategories {
  const sorted = sortedStorageCategories(categories);
  let mainCount = 0;
  while (
    mainCount < sorted.length
    && mainCount < maxMain
    && (sorted[mainCount]?.bytes ?? 0) > 0
    && (sorted[mainCount]?.percent ?? 0) >= minPercent
  ) {
    mainCount += 1;
  }
  if (sorted.length - mainCount <= 1) {
    return { main: sorted, other: [], otherBytes: 0, otherPercent: 0 };
  }
  const other = sorted.slice(mainCount);
  return {
    main: sorted.slice(0, mainCount),
    other,
    otherBytes: other.reduce((sum, cat) => sum + Math.max(0, cat.bytes), 0),
    otherPercent: other.reduce((sum, cat) => sum + Math.max(0, cat.percent), 0),
  };
}

export interface LargestGamesView {
  /** Rows shown before "Show all": the largest measured games. */
  top: StorageGameRow[];
  /** Rows behind "Show all": smaller games, then games with no measured size. */
  rest: StorageGameRow[];
  /** How many of the rest have no measured size (0 B). */
  restNotCounted: number;
}

/**
 * Split the games (already sorted largest first) into the top `limit`
 * measured games and the rest. Games with no measured size never take a top
 * place; they wait behind "Show all".
 */
export function splitLargestGames(
  ordered: readonly StorageGameRow[],
  limit = 10,
): LargestGamesView {
  const top: StorageGameRow[] = [];
  const rest: StorageGameRow[] = [];
  for (const row of ordered) {
    if (top.length < limit && row.total_bytes > 0) top.push(row);
    else rest.push(row);
  }
  return { top, rest, restNotCounted: rest.filter((row) => row.total_bytes <= 0).length };
}

export function gameRowFlags(row: StorageGameRow): StorageFlag[] {
  const flags: StorageFlag[] = [];
  if (row.unsafe) {
    flags.push({ kind: "unsafe", label: "folder not counted", tone: "danger" });
  }
  if (row.uncertain) {
    flags.push({ kind: "uncertain", label: "may be incomplete", tone: "warn" });
  }
  if (row.unavailable) {
    flags.push({ kind: "unavailable", label: "folder not found", tone: "warn" });
  }
  if (row.changed) {
    flags.push({ kind: "changed", label: "changed since the scan", tone: "info" });
  }
  if (row.shared_with.length > 0) {
    flags.push({
      kind: "shared",
      label: `shared with ${row.shared_with.join(", ")}`,
      tone: "info",
    });
  }
  return flags;
}

/**
 * One line that says why a game row is flagged and what to do, or "" when
 * nothing needs the user. The worst problem wins.
 */
export function gameRowHint(row: StorageGameRow): string {
  if (row.unsafe) {
    const reason = row.unsafe_reason || "wLib could not find a safe game folder.";
    return `${reason} Use Pick folder to choose the game's own folder.`;
  }
  if (row.unavailable) {
    return "The game folder is missing or on a drive that is not connected.";
  }
  if (row.uncertain) {
    return "Only the folder with the game's executable was counted. If the game folder is higher up, use Pick folder.";
  }
  return "";
}

const PART_LABELS: readonly [keyof StorageGameRow["parts"], string][] = [
  ["game", "Game"],
  ["saves", "Save backups"],
  ["player", "Built-in player"],
  ["sandbox", "Sandbox"],
  ["prefix", "Wine prefix"],
];

export function gameRowParts(row: StorageGameRow): StoragePartItem[] {
  const items: StoragePartItem[] = [];
  for (const [key, label] of PART_LABELS) {
    const bytes = row.parts[key] ?? 0;
    if (bytes > 0) items.push({ key, label, bytes });
  }
  return items;
}

export function stoppedScanNote(reason: "" | "user" | "game", hasPreviousReport: boolean): string {
  if (!reason) return "";
  if (reason === "game") {
    return hasPreviousReport
      ? "The scan stopped because a game started. Showing the previous report."
      : "The scan stopped because a game started.";
  }
  return hasPreviousReport
    ? "The scan stopped. Showing the previous report."
    : "The scan stopped.";
}

export function skippedEntriesNote(skipped: number): string {
  if (skipped <= 0) return "";
  return skipped === 1
    ? "1 file or folder could not be read and was not counted."
    : `${skipped.toLocaleString()} files or folders could not be read and were not counted.`;
}

export function isCleanupActionAvailable(action: StorageCleanupAction): boolean {
  if (action.blocked_reason) return false;
  return action.count > 0 || action.bytes > 0;
}

/** Words that keep their capital letter inside a sentence. */
const PROPER_WORDS = new Set(["Proton", "Wine", "Steam", "BepInEx", "RPG"]);

/**
 * A label as it reads inside a sentence: "Data of removed games" becomes
 * "data of removed games"; names such as "BepInEx" keep their case.
 */
export function labelInSentence(label: string): string {
  const first = label.split(" ", 1)[0] ?? "";
  if (!first || PROPER_WORDS.has(first) || first.slice(1) !== first.slice(1).toLowerCase()) {
    return label;
  }
  return first.charAt(0).toLowerCase() + label.slice(1);
}

/** The report category that holds the same files as each cleanup action. */
const ACTION_CATEGORIES: Partial<Record<StorageCleanupActionId, string>> = {
  download_archives: "download_archives",
  removed_game_data: "removed_game_data",
  save_backups: "save_backups",
  proton_builds: "proton_builds",
  cover_cache: "cover_cache",
};

/**
 * When an action frees only part of its category, say how much of it, so
 * the two sizes on the page match up. "" when they are the same.
 */
export function cleanupActionScopeNote(
  action: Pick<StorageCleanupAction, "id" | "bytes">,
  categories: readonly StorageCategoryReport[],
): string {
  const categoryId = ACTION_CATEGORIES[action.id];
  if (!categoryId || !(action.bytes > 0)) return "";
  const category = categories.find((cat) => cat.id === categoryId);
  if (!category) return "";
  const actionText = formatStorageBytes(action.bytes);
  const categoryText = formatStorageBytes(category.bytes);
  if (category.bytes <= action.bytes || actionText === categoryText) return "";
  return `Frees ${actionText} of the ${categoryText} in ${labelInSentence(category.label)}.`;
}

/**
 * The items a preview names, one per line, largest first, with "and N more"
 * when the preview left some out. "" when the preview names none.
 */
export function previewItemLines(
  preview: Pick<StorageCleanupPreviewResponse, "items" | "more_items">,
): string {
  const items = preview.items ?? [];
  if (items.length === 0) return "";
  const lines = items.map((item) => `• ${item.label} (${formatStorageBytes(item.bytes)})`);
  const more = Math.max(0, preview.more_items ?? 0);
  if (more > 0) lines.push(more === 1 ? "• and 1 more item" : `• and ${more.toLocaleString()} more items`);
  return lines.join("\n");
}

export function buildCleanupConfirmOptions(preview: StorageCleanupPreviewResponse): ConfirmOptions {
  const sizeText = formatStorageBytes(preview.bytes ?? 0);
  const count = preview.count ?? 0;
  const itemText = count === 1 ? "1 item" : `${count.toLocaleString()} items`;
  const itemLines = previewItemLines(preview);
  const lines = [
    itemLines
      ? `This will permanently delete ${itemText} (${sizeText}):\n${itemLines}`
      : `This will permanently delete ${itemText} (${sizeText}).`,
  ];
  if (preview.warning) lines.push(preview.warning);
  if (preview.what_stays) lines.push(preview.what_stays);
  lines.push("Deleted files cannot be restored.");
  return {
    title: `Clean ${preview.label ? labelInSentence(preview.label) : "storage"}?`,
    message: lines.join("\n\n"),
    confirmLabel: `Delete ${sizeText}`,
    // Safe items come back by themselves; anything else is gone for good.
    danger: cleanupActionTier({ tier: preview.tier ?? "risky" }) !== "safe",
  };
}

/** How long the backend keeps a cleanup preview. */
export const CLEANUP_PREVIEW_TTL_MS = 15 * 60 * 1000;
/** Treat a preview as expired a little early, so a slow run does not race the backend. */
const PREVIEW_EXPIRY_MARGIN_MS = 30 * 1000;

export const PREVIEW_EXPIRED_MESSAGE =
  "Nothing was deleted. The check is more than 15 minutes old, so the files may have changed. Choose Clean again to check them again.";

/** True when a preview made at `previewedAtMs` may have expired by `nowMs`. */
export function cleanupPreviewExpired(previewedAtMs: number, nowMs = Date.now()): boolean {
  return nowMs - previewedAtMs >= CLEANUP_PREVIEW_TTL_MS - PREVIEW_EXPIRY_MARGIN_MS;
}

/** The error to show for a failed cleanup run. */
export function cleanupRunError(result: Pick<StorageCleanupRunResponse, "code" | "error">): string {
  if (result.code === "preview_expired") return PREVIEW_EXPIRED_MESSAGE;
  return result.error || "Cleanup failed.";
}

/**
 * Why a Clean button cannot run now, or "" when it can. The button keeps
 * keyboard focus (aria-disabled) and this text describes it.
 */
export function cleanupActionBlockedReason(
  action: StorageCleanupAction,
  busy: StorageCleanupBusy,
): string {
  if (busy) {
    return busy === action.id ? "This cleanup is running." : "Wait for the running cleanup to finish.";
  }
  if (action.blocked_reason) return action.blocked_reason;
  if (!isCleanupActionAvailable(action)) return NOTHING_TO_FREE;
  return "";
}

/**
 * Whether the Wine and Proton parts of the report apply. Until the platform
 * is known (or when it cannot be read) the answer is no: only Wine and Proton
 * rows that hold data show, so no empty row flashes up on Windows or macOS.
 */
export function storageWineProton(
  capabilities: Pick<PlatformCapabilities, "wine_proton"> | null | undefined,
): boolean {
  return capabilities?.wine_proton === true;
}

export function formatCleanupResult(result: StorageCleanupRunResponse): string {
  const freed = formatStorageBytes(result.freed_bytes ?? 0);
  const failed = result.failed ?? 0;
  const skipped = result.skipped ?? 0;
  const parts = [`Freed ${freed}.`];
  if (failed > 0) {
    parts.push(
      failed === 1
        ? "1 item could not be removed."
        : `${failed.toLocaleString()} items could not be removed.`,
    );
  }
  if (skipped > 0) {
    parts.push(
      skipped === 1
        ? "1 item was skipped because it is now in use."
        : `${skipped.toLocaleString()} items were skipped because they are now in use.`,
    );
  }
  return parts.join(" ");
}

// ── Risk tiers ──────────────────────────────────────────────────────────

/** The cleanup that runs now: one action, "safe" for all Safe actions, or "" for none. */
export type StorageCleanupBusy = StorageCleanupActionId | "safe" | "";

export const NOTHING_TO_FREE = "Nothing to free.";

export interface StorageCleanupTierInfo {
  tier: StorageCleanupTier;
  label: string;
  description: string;
}

/** Tiers in display order: the least risk first. */
export const CLEANUP_TIERS: readonly StorageCleanupTierInfo[] = [
  {
    tier: "safe",
    label: "Safe",
    description: "Caches and temporary files. wLib makes or downloads them again when it needs them.",
  },
  {
    tier: "review",
    label: "Review",
    description: "No saves. To get these back, you must download them again.",
  },
  {
    tier: "risky",
    label: "Risky",
    description: "Save backups, and files that can hold game saves. Check them before you delete.",
  },
];

const TIER_IDS = new Set<string>(CLEANUP_TIERS.map((info) => info.tier));

/** The action's tier; an unknown value counts as risky. */
export function cleanupActionTier(action: Pick<StorageCleanupAction, "tier">): StorageCleanupTier {
  return TIER_IDS.has(action.tier) ? action.tier : "risky";
}

export interface StorageCleanupTierGroup extends StorageCleanupTierInfo {
  actions: StorageCleanupAction[];
  bytes: number;
}

/** Actions largest first; equal sizes keep the backend order. */
function sortedByBytes(actions: readonly StorageCleanupAction[]): StorageCleanupAction[] {
  return actions
    .map((action, index) => ({ action, index }))
    .sort((a, b) => b.action.bytes - a.action.bytes || a.index - b.index)
    .map(({ action }) => action);
}

function actionBytes(actions: readonly StorageCleanupAction[]): number {
  return actions.reduce((sum, action) => sum + Math.max(0, action.bytes || 0), 0);
}

/**
 * Group the actions by tier (Safe, Review, Risky), each tier largest first,
 * with the tier's total. Tiers without actions are left out.
 */
export function groupCleanupActionsByTier(
  actions: readonly StorageCleanupAction[],
): StorageCleanupTierGroup[] {
  const groups: StorageCleanupTierGroup[] = [];
  for (const info of CLEANUP_TIERS) {
    const inTier = actions.filter((action) => cleanupActionTier(action) === info.tier);
    if (inTier.length === 0) continue;
    groups.push({ ...info, actions: sortedByBytes(inTier), bytes: actionBytes(inTier) });
  }
  return groups;
}

/** Bytes each tier can free, from the saved report. */
export function cleanupTierTotals(
  actions: readonly StorageCleanupAction[],
): Record<StorageCleanupTier, number> {
  const totals: Record<StorageCleanupTier, number> = { safe: 0, review: 0, risky: 0 };
  for (const action of actions) {
    totals[cleanupActionTier(action)] += Math.max(0, action.bytes || 0);
  }
  return totals;
}

export interface SkippedCleanup {
  label: string;
  reason: string;
}

export interface SafeCleanupPlan {
  /** Safe actions to preview and run, largest first. */
  ready: StorageCleanupAction[];
  /** Safe actions left out, with the reason. */
  skipped: SkippedCleanup[];
  bytes: number;
}

/** Pick the Safe actions that "Clean all safe items" runs; blocked or empty ones are skipped. */
export function planSafeCleanup(actions: readonly StorageCleanupAction[]): SafeCleanupPlan {
  const ready: StorageCleanupAction[] = [];
  const skipped: SkippedCleanup[] = [];
  const safe = sortedByBytes(actions.filter((action) => cleanupActionTier(action) === "safe"));
  for (const action of safe) {
    if (action.blocked_reason) {
      skipped.push({ label: action.label, reason: action.blocked_reason });
    } else if (!(action.bytes > 0)) {
      skipped.push({ label: action.label, reason: NOTHING_TO_FREE });
    } else {
      ready.push(action);
    }
  }
  return { ready, skipped, bytes: actionBytes(ready) };
}

function itemCountText(count: number): string {
  return count === 1 ? "1 item" : `${count.toLocaleString()} items`;
}

/** One confirmation for every previewed Safe action, with each size and the skipped actions. */
export function buildSafeCleanupConfirmOptions(
  previews: readonly StorageCleanupPreviewResponse[],
  skipped: readonly SkippedCleanup[],
): ConfirmOptions {
  const totalBytes = previews.reduce((sum, preview) => sum + Math.max(0, preview.bytes ?? 0), 0);
  const totalCount = previews.reduce((sum, preview) => sum + Math.max(0, preview.count ?? 0), 0);
  const sizeText = formatStorageBytes(totalBytes);
  const blocks = [`This will permanently delete ${itemCountText(totalCount)} (${sizeText}):`];
  const rows = previews.map((preview) => {
    const line = `${preview.label || "Storage"}: ${formatStorageBytes(preview.bytes ?? 0)}, ${itemCountText(preview.count ?? 0)}.`;
    const notes = [preview.warning, preview.what_stays].filter(Boolean).join(" ");
    return notes ? `${line} ${notes}` : line;
  });
  blocks.push(...rows);
  // An action with nothing to free is not news here (its row already says so);
  // only actions that could not run are named, as in the result line.
  const notRun = skipped.filter((item) => item.reason !== NOTHING_TO_FREE);
  if (notRun.length > 0) {
    blocks.push(
      `Skipped:\n${notRun.map((item) => `${item.label}: ${item.reason}`).join("\n")}`,
    );
  }
  blocks.push("Deleted files cannot be restored.");
  return {
    title: "Clean all safe items?",
    message: blocks.join("\n\n"),
    confirmLabel: `Delete ${sizeText}`,
    // Safe items come back by themselves: no danger style.
    danger: false,
  };
}

export interface SafeCleanupRun {
  label: string;
  result: StorageCleanupRunResponse;
}

/**
 * One summary for all the runs of "Clean all safe items". Skipped actions
 * that had nothing to free are not named again.
 */
export function formatSafeCleanupResult(
  runs: readonly SafeCleanupRun[],
  skipped: readonly SkippedCleanup[] = [],
): { message: string; error: string } {
  let freed = 0;
  let failed = 0;
  let inUse = 0;
  const errors: string[] = [];
  for (const { label, result } of runs) {
    if (!result.success) {
      errors.push(`${label}: ${result.error || "Cleanup failed."}`);
      continue;
    }
    freed += Math.max(0, result.freed_bytes ?? 0);
    failed += Math.max(0, result.failed ?? 0);
    inUse += Math.max(0, result.skipped ?? 0);
  }
  const ok = runs.length - errors.length;
  let message = "";
  if (ok > 0) {
    message = formatCleanupResult({ success: true, freed_bytes: freed, failed, skipped: inUse });
    const notRun = skipped.filter((item) => item.reason !== NOTHING_TO_FREE);
    if (notRun.length > 0) {
      message += ` Skipped: ${notRun.map((item) => item.label).join(", ")}.`;
    }
  }
  return { message, error: errors.join(" ") };
}

