// SPDX-License-Identifier: GPL-3.0-or-later
import type { LibrarySyncStatus } from "../services/api";

export interface SyncProblemUpdate {
  /** Identifies the problem now open, or "" when sync is healthy. */
  key: string;
  /** Mark the Sync nav item. */
  attention: boolean;
  /** Text of a new problem toast, when this status starts a new problem. */
  show?: string;
  /** Close the open sync toast, because sync is healthy again. */
  close: boolean;
}

// A retry puts sync in "syncing" between two failures. It keeps the previous
// problem, so the same failure after the retry is not shown again. Offline and
// paused are not faults the user must fix: no toast and no attention.
export const trackSyncProblem = (previousKey: string, status: LibrarySyncStatus): SyncProblemUpdate => {
  const state = status.state || "off";
  if (state === "syncing") return { key: previousKey, attention: previousKey !== "", close: false };
  if (state !== "error" && state !== "needs_choice" && state !== "needs_passphrase") {
    return { key: "", attention: false, close: true };
  }
  const key = `${state}:${status.last_error || ""}`;
  if (key === previousKey) return { key, attention: true, close: false };
  const show = state === "needs_choice"
    ? "Sync is waiting for you to choose which library to keep."
    : state === "needs_passphrase"
      ? "Sync needs the passphrase for the sync file."
      : status.last_error || "Sync needs attention.";
  return { key, attention: true, show, close: false };
};
