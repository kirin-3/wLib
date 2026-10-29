// SPDX-License-Identifier: GPL-3.0-or-later

// Shared rules for reading the bulk update-check status in the Updates view and
// the global poll in App.vue. The backend reports `outcome` as a free string;
// only these values are meaningful to the UI.
export type UpdateCheckOutcome = "idle" | "running" | "completed" | "cancelled" | "failed";

export interface UpdateCheckStatusLike {
  running: boolean;
  checked: number;
  outcome?: string | null;
}

// The progress row used to say "✓ Complete" for every finished check, including
// cancelled and failed ones. Keep the success mark for a completed check only.
export const updateCheckProgressLabel = (
  running: boolean,
  outcome?: string | null,
): string => {
  if (running) return "";
  if (outcome === "cancelled") return "Stopped";
  if (outcome === "failed") return "Failed";
  return "✓ Complete";
};

// A check can be started by an automatic check or an external import while the
// Updates view is already open. Its local poll only starts when the view sees a
// running status on mount, so adopt one found by a later refresh.
export const shouldAdoptBackgroundCheck = (
  currentRunning: boolean,
  fetched: UpdateCheckStatusLike,
): boolean =>
  !currentRunning &&
  (fetched.running ||
    (fetched.checked > 0 && !!fetched.outcome && fetched.outcome !== "idle"));

// App.vue polls every five seconds. A short check can start and finish between
// two polls, so waiting to observe `running` before scanning the Downloads
// folder skipped the post-check offer. A finished status with results is enough;
// scanDownloadOffers() still enforces the once-per-minute limit.
export const shouldScanDownloadsAfterCheck = (
  status: UpdateCheckStatusLike,
  wasRunning: boolean,
  progressChanged: boolean,
): boolean =>
  !status.running &&
  status.checked > 0 &&
  !!status.outcome &&
  status.outcome !== "idle" &&
  (wasRunning || progressChanged);
