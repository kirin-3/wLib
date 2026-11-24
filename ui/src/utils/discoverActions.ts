// SPDX-License-Identifier: GPL-3.0-or-later
// Which actions the Discover game preview offers, and when a game that
// Discover added only for a download is removed again.
import type { DiscoverGameDetails } from "../services/api";

export interface DiscoverActions {
  /** "Wishlist": the game is not in the library yet. */
  wishlist: boolean;
  /** "Download": shown for a game with an F95 thread or an allowed LC thread. */
  download: { shown: boolean; enabled: boolean; reason: string };
  /** "Open in library": the game matches a library game. */
  openInLibrary: boolean;
  /** "In library", "Wishlisted" or "". */
  libraryLabel: string;
}

export const discoverActions = (game: Pick<DiscoverGameDetails, "library" | "download">): DiscoverActions => {
  const state = game.library?.state;
  const shown = game.download.available || !!game.download.reason;
  return {
    wishlist: !game.library,
    download: { shown, enabled: game.download.available, reason: game.download.available ? "" : game.download.reason },
    openInLibrary: !!game.library,
    libraryLabel: state === "installed" ? "In library" : state === "wishlisted" ? "Wishlisted" : "",
  };
};

/**
 * The game to discard when the build picker closes: the game this Download
 * action added (`pending`), once the picker that was open for it is closed.
 * The backend still refuses a game with a queued download.
 */
export const discardAfterPickerClose = (
  pending: number | null,
  previous: { id: number } | null,
  next: { id: number } | null,
): number | null => (pending !== null && previous?.id === pending && next?.id !== pending ? pending : null);

/** "2024-03-09" from Unix seconds; "" when unknown. */
export const discoverDate = (seconds: number): string =>
  seconds > 0 ? new Date(seconds * 1000).toISOString().slice(0, 10) : "";

export const DISCOVER_STATUS_LABELS: Record<string, string> = {
  ongoing: "Ongoing",
  completed: "Completed",
  onhold: "On hold",
  abandoned: "Abandoned",
};
