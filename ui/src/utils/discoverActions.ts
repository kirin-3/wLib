// SPDX-License-Identifier: GPL-3.0-or-later
// Which actions the Discover game preview offers, and when a game that
// Discover added only for a download is removed again.
import type { DiscoverGameDetails } from "../services/api";

/** The words for a game's library state, the same as the Library's install filter. */
export const DISCOVER_LIBRARY_LABELS: Record<string, string> = {
  installed: "Installed",
  wishlisted: "Wishlist",
};

/** The backend's reason for a disabled Download, in plain words. */
const DOWNLOAD_REASONS: Record<string, string> = {
  "Needs LC Plus":
    "Needs LC Plus, LewdCorner's paid membership: this thread is for Plus members only. If you have it, set LC Plus to Yes in Settings.",
};

export interface DiscoverActions {
  /** "Add to wishlist": the game is not in the library yet. */
  wishlist: boolean;
  /**
   * Shown for a game with an F95 thread or an allowed LC thread. `label` uses
   * the download window's own title; `note` tells what Download also does;
   * `reason` says why it is disabled.
   */
  download: { shown: boolean; enabled: boolean; label: string; note: string; reason: string };
  /** "Open in library": the game is in the library (installed or wishlist). */
  openInLibrary: boolean;
  /** "Installed", "Wishlist" or "". */
  libraryLabel: string;
}

export const discoverActions = (game: Pick<DiscoverGameDetails, "library" | "download">): DiscoverActions => {
  const state = game.library?.state;
  const { available } = game.download;
  const shown = available || !!game.download.reason;
  return {
    wishlist: !game.library,
    download: {
      shown,
      enabled: available,
      // An installed game gets a newer build; any other game gets its first one.
      label: state === "installed" ? "Download update" : "Download game",
      // A new game is added on Download and removed again when no download starts.
      note: available && !game.library ? "Adds the game to your library once the download starts." : "",
      reason: available ? "" : (DOWNLOAD_REASONS[game.download.reason] ?? game.download.reason),
    },
    openInLibrary: !!game.library,
    libraryLabel: (state && DISCOVER_LIBRARY_LABELS[state]) ?? "",
  };
};

/**
 * Where focus goes once Add to wishlist succeeds and its button goes away:
 * the next action in the header (Download, else Open in library), else the
 * dialog itself.
 */
export const discoverFocusAfterWishlist = (
  actions: Pick<DiscoverActions, "download" | "openInLibrary"> | null,
): "download" | "openInLibrary" | "dialog" =>
  actions?.download.shown ? "download" : actions?.openInLibrary ? "openInLibrary" : "dialog";

/** The message for a game the preview could not load: what failed, and why when known. */
export const discoverLoadError = (result: { error?: string; error_code?: string }): { title: string; detail: string } => {
  const error = (result.error ?? "").trim().replace(/\.$/, "");
  switch (result.error_code) {
    case "unknown_game":
      return { title: "This game is no longer in the catalog.", detail: "The catalog was updated. Close this window and search again." };
    case "not_ready":
      return { title: "The game catalog is still loading.", detail: "Wait a moment, then try again." };
    case "catalog_off":
      return { title: "The game catalog is turned off.", detail: "Turn on the catalog in Settings to use Discover." };
  }
  if (!error || error === "Could not read the catalog")
    return { title: "Could not load this game.", detail: "wLib could not read the game catalog." };
  return { title: "Could not load this game.", detail: `wLib could not read the game catalog: ${error}.` };
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

/**
 * The words beside the F95 rating: where it is from and what changed it,
 * short enough for one line in a 390px window.
 */
export const discoverRatingNote = (game: Pick<DiscoverGameDetails, "wrating" | "rating" | "rating_count">): string => {
  if (game.wrating !== null && game.rating !== null && game.rating_count !== null) {
    const count = game.rating_count;
    const reviews = `${count.toLocaleString("en-US")} ${count === 1 ? "review" : "reviews"}`;
    return `F95, adjusted for ${reviews} (thread: ${game.rating.toFixed(1)})`;
  }
  return "F95, review count not known yet";
};

/**
 * The title a Found on row shows: the site's own title only when it is not
 * the dialog's title (a Japanese DLsite title, for example); else "".
 */
export const discoverEntryTitle = (entryTitle: string, gameTitle: string): string => {
  const simple = (text: string) => text.normalize("NFKC").trim().replace(/\s+/g, " ").toLowerCase();
  return entryTitle.trim() && simple(entryTitle) !== simple(gameTitle) ? entryTitle.trim() : "";
};

/** "2024-03-09" from Unix seconds; "" when unknown. */
export const discoverDate = (seconds: number): string =>
  seconds > 0 ? new Date(seconds * 1000).toISOString().slice(0, 10) : "";

export const DISCOVER_STATUS_LABELS: Record<string, string> = {
  ongoing: "Ongoing",
  completed: "Completed",
  onhold: "On hold",
  abandoned: "Abandoned",
};
