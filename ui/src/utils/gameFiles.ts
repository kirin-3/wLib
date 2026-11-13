// SPDX-License-Identifier: GPL-3.0-or-later
// Rules shared by the Uninstall and Move dialogs, the move progress and the summary toast.
import type { GameFilePlanEntry, MoveStatus, MoveSummary } from "../services/api";

/** Why a planned game cannot be uninstalled or moved, or "". */
export const planProblem = (entry: GameFilePlanEntry): string =>
  entry.blocked_reason || (entry.removable ? "" : entry.reason || "This folder cannot be removed.") || entry.move_reason || "";

export const actionableEntries = (entries: GameFilePlanEntry[]): GameFilePlanEntry[] =>
  entries.filter((entry) => !planProblem(entry));

/** "Back up saves first" shows when a listed game that can go has a saves folder. */
export const showBackupOption = (entries: GameFilePlanEntry[]): boolean =>
  actionableEntries(entries).some((entry) => !!entry.saves_folder);

/** Ticked by default when a listed game that can go has its saves inside its folder. */
export const backupDefault = (entries: GameFilePlanEntry[]): boolean =>
  actionableEntries(entries).some((entry) => entry.saves_inside_root);

/** Why the confirm button is disabled, or "". */
export const confirmReason = (entries: GameFilePlanEntry[], loading: boolean, verb: "uninstall" | "move"): string => {
  if (loading) return "Checking the game folders...";
  if (!entries.length) return `No game to ${verb}.`;
  if (!actionableEntries(entries).length) return entries.length === 1 ? planProblem(entries[0]!) : `No selected game can ${verb === "move" ? "move" : "be uninstalled"}.`;
  return "";
};

export const formatBytes = (size: number | null | undefined): string => {
  if (size === null || size === undefined || !Number.isFinite(size)) return "";
  let value = size;
  for (const unit of ["B", "KB", "MB", "GB"]) {
    if (value < 1024 || unit === "GB") return unit === "B" ? `${Math.round(value)} B` : `${value.toFixed(1)} ${unit}`;
    value /= 1024;
  }
  return `${size} B`;
};

export interface MoveState {
  state: "" | "moving" | "waiting";
  /** 0-100 while moving; a rename on one drive (no byte total) shows 0. */
  percent: number;
}

export const NO_MOVE: MoveState = { state: "", percent: 0 };

export const moveStateFor = (status: MoveStatus | null, gameId: number): MoveState => {
  if (!status) return NO_MOVE;
  const current = status.current;
  if (current && current.game_id === gameId) {
    const percent = current.bytes_total > 0 ? Math.min(100, Math.floor((current.bytes_done / current.bytes_total) * 100)) : 0;
    return { state: "moving", percent };
  }
  return status.waiting.includes(gameId) ? { state: "waiting", percent: 0 } : NO_MOVE;
};

export const MOVE_FAST_POLL_MS = 500;
export const MOVE_IDLE_POLL_MS = 5000;

/** 500 ms while moves run, 5 s otherwise. */
export const movePollMs = (status: MoveStatus | null): number => (status?.running ? MOVE_FAST_POLL_MS : MOVE_IDLE_POLL_MS);

const plural = (count: number, word: string): string => `${count} ${word}${count === 1 ? "" : "s"}`;

/** One summary line for the end of the move queue. */
export const moveSummaryText = (summary: MoveSummary): { text: string; type: "success" | "error" | "info" } => {
  const parts: string[] = [];
  if (summary.moved) parts.push(`${plural(summary.moved, "game")} moved`);
  if (summary.skipped) parts.push(`${summary.skipped} skipped`);
  if (summary.cancelled) parts.push(`${summary.cancelled} cancelled`);
  if (summary.failed) parts.push(`${summary.failed} failed`);
  let text = parts.length ? `${parts.join(", ")}.` : "No game moved.";
  text = text.charAt(0).toUpperCase() + text.slice(1);
  if (summary.first_reason && (summary.skipped || summary.failed)) text += ` ${summary.first_reason}`;
  const type = summary.failed ? "error" : summary.moved && !summary.skipped ? "success" : "info";
  return { text, type };
};

/** "1.2 GB of 4.0 GB · 85.0 MB/s" for the queue panel. */
export const moveProgressText = (status: MoveStatus | null): string => {
  const current = status?.current;
  if (!current) return "";
  if (!current.bytes_total) return "Moving on the same drive...";
  const speed = current.speed_bps > 0 ? ` · ${formatBytes(current.speed_bps)}/s` : "";
  return `${formatBytes(current.bytes_done)} of ${formatBytes(current.bytes_total)}${speed}`;
};
