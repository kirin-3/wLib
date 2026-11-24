// SPDX-License-Identifier: GPL-3.0-or-later
import type {
  StorageCleanupAction,
  StorageCleanupPreviewResponse,
  StorageCleanupRunResponse,
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

export function sortedStorageGames(games: Record<string, StorageGameRow>): StorageGameRow[] {
  return Object.values(games).sort((a, b) => {
    if (b.total_bytes !== a.total_bytes) return b.total_bytes - a.total_bytes;
    return a.title.localeCompare(b.title);
  });
}

export function gameRowFlags(row: StorageGameRow): StorageFlag[] {
  const flags: StorageFlag[] = [];
  if (row.unsafe) {
    flags.push({ kind: "unsafe", label: "not clear", tone: "danger" });
  }
  if (row.uncertain) {
    flags.push({ kind: "uncertain", label: "may be incomplete", tone: "warn" });
  }
  if (row.unavailable) {
    flags.push({ kind: "unavailable", label: "not available", tone: "warn" });
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
    ? "1 entry could not be read and was not counted."
    : `${skipped.toLocaleString()} entries could not be read and were not counted.`;
}

export function isCleanupActionAvailable(action: StorageCleanupAction): boolean {
  if (action.blocked_reason) return false;
  return action.count > 0 || action.bytes > 0;
}

export function buildCleanupConfirmOptions(preview: StorageCleanupPreviewResponse): ConfirmOptions {
  const sizeText = formatStorageBytes(preview.bytes ?? 0);
  const count = preview.count ?? 0;
  const itemText = count === 1 ? "1 item" : `${count.toLocaleString()} items`;
  const lines = [
    `This will permanently delete ${itemText} (${sizeText}).`,
  ];
  if (preview.warning) lines.push(preview.warning);
  if (preview.what_stays) lines.push(preview.what_stays);
  lines.push("Deleted files cannot be restored.");
  return {
    title: `Clean ${preview.label || "storage"}?`,
    message: lines.join("\n\n"),
    confirmLabel: `Delete ${sizeText}`,
    danger: true,
  };
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
