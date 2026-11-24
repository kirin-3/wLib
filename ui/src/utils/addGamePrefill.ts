// SPDX-License-Identifier: GPL-3.0-or-later
import type { CatalogThreadEntry, StoreLink } from "../services/api";
import { readStoreLinks } from "./storeLinks.ts";

export interface AddGamePrefillForm {
  title: string;
  developer: string;
  coverImage: string;
  tags: string;
  rating: string;
  engine: string;
  links: StoreLink[];
  threadCompleted?: boolean | undefined;
  threadOnhold?: boolean | undefined;
  threadAbandoned?: boolean | undefined;
  threadStatesSeenAt: string;
}

export interface AddGamePrefillResult extends AddGamePrefillForm {
  catalogVersion: string;
}

function formatCatalogRating(value: number | string | undefined): string {
  if (typeof value === "number") {
    if (!Number.isFinite(value) || value <= 0 || value > 5) return "";
    return `${(Math.round(value * 10) / 10).toFixed(1)}/5`;
  }
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Fill empty Add Game dialog fields from a catalog thread entry.
 *
 * Only empty fields are filled, except `title`, which also replaces a title
 * that still equals `guessedTitle` (guessed from the executable path).
 * Never sets the form's `version` field; returns `catalogVersion` separately.
 */
export function applyCatalogEntry(
  form: AddGamePrefillForm,
  entry: CatalogThreadEntry | null | undefined,
  guessedTitle = "",
): AddGamePrefillResult {
  const result: AddGamePrefillResult = {
    ...form,
    links: [...form.links],
    catalogVersion: "",
  };
  if (!entry) return result;

  const entryTitle = String(entry.title || "").trim();
  const currentTitle = form.title.trim();
  if (entryTitle && (!currentTitle || (!!guessedTitle && form.title === guessedTitle))) {
    result.title = entryTitle;
  }

  if (!form.developer.trim() && entry.developer) {
    result.developer = entry.developer.trim();
  }
  if (!form.coverImage.trim() && entry.coverImage) {
    result.coverImage = entry.coverImage.trim();
  }
  if (!form.tags.trim() && entry.tags) {
    result.tags = entry.tags.trim();
  }
  if (!form.rating.trim()) {
    const formattedRating = formatCatalogRating(entry.rating);
    if (formattedRating) result.rating = formattedRating;
  }
  if (!form.engine.trim() && entry.engine) {
    result.engine = entry.engine.trim();
  }
  if (!form.links.length && entry.links) {
    result.links = readStoreLinks(entry.links);
  }
  if (form.threadCompleted === undefined && typeof entry.thread_completed === "boolean") {
    result.threadCompleted = entry.thread_completed;
  }
  if (
    form.threadOnhold === undefined &&
    form.threadAbandoned === undefined &&
    typeof entry.thread_onhold === "boolean" &&
    typeof entry.thread_abandoned === "boolean"
  ) {
    result.threadOnhold = entry.thread_onhold;
    result.threadAbandoned = entry.thread_abandoned;
    result.threadStatesSeenAt = entry.thread_states_seen_at || "";
  }

  result.catalogVersion = String(entry.version || "").trim();
  return result;
}

/**
 * Return the version to save from the Add Game dialog.
 *
 * Uses `version.trim()` when an executable is set or the version field is not
 * empty, and falls back to `catalogVersion` only for wishlist saves (empty
 * executable path) with an empty version field.
 */
export function versionToSave(
  version: string,
  exePath: string,
  catalogVersion: string,
): string {
  const trimmedVersion = version.trim();
  if (exePath.trim() || trimmedVersion) return trimmedVersion;
  return catalogVersion.trim();
}
