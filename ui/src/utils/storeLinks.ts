// SPDX-License-Identifier: GPL-3.0-or-later
import { normalizeLcThreadUrl } from "./lewdcorner.ts";
import type { GameRecord, StoreLink } from "../services/api";

const SOURCES = new Set(["dlsite", "steam", "itch", "fanza", "patreon", "subscribestar", "gumroad", "vndb", "ryuugames", "other"]);
export const storeLabel = (source: string): string => ({ dlsite: "DLsite", steam: "Steam", itch: "itch.io", fanza: "Fanza / DMM", patreon: "Patreon", subscribestar: "SubscribeStar", gumroad: "Gumroad", vndb: "VNDB", ryuugames: "Ryuugames", other: "Website" }[source] || source);
export const queryText = (value: unknown): string => typeof value === "string" ? value : Array.isArray(value) && typeof value[0] === "string" ? value[0] : "";
export function readStoreLinks(value: unknown): StoreLink[] {
  if (typeof value === "string") {
    try { value = JSON.parse(value); } catch { return []; }
  }
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  return value.filter((link): link is StoreLink => {
    if (!link || !SOURCES.has(link.source) || typeof link.id !== "string" || !link.id || typeof link.url !== "string") return false;
    if (normalizeLcThreadUrl(link.url)) return false;
    try { if (!["http:", "https:"].includes(new URL(link.url).protocol)) return false; } catch { return false; }
    const key = `${link.source}:${link.id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
export function extensionGameId(value: unknown): number | undefined {
  const text = typeof value === "number" ? String(value) : queryText(value);
  return /^[1-9]\d*$/.test(text) && Number.isSafeInteger(Number(text)) ? Number(text) : undefined;
}
export const gamePageUrl = (game: Pick<GameRecord, "f95_url" | "lc_url" | "links">): string => game.f95_url || game.lc_url || game.links?.[0]?.url || "";
export interface ExtensionGameDetail {
  url?: string;
  gameId?: number;
  title?: string;
  version?: string;
  coverImage?: string;
  tags?: string[] | string;
  rating?: string | number;
  lc_url?: string;
  lc_suggested?: string;
  thread_completed?: boolean;
  developer?: string;
  engine?: string;
  links?: StoreLink[];
  // Image URLs read from an LC page (the first post's images after the cover).
  screenshots?: string[];
}
export function extensionAddQuery(data: ExtensionGameDetail): Record<string, string> {
  return { action: "import", f95url: data.url || "", title: data.title || "", version: data.version || "", coverImage: data.coverImage || "", tags: JSON.stringify(data.tags || []), rating: String(data.rating || ""), developer: data.developer || "", engine: data.engine || "", links: JSON.stringify(readStoreLinks(data.links)),
    ...(data.lc_url ? { lcurl: data.lc_url } : {}),
    ...(data.lc_suggested ? { lcSuggested: data.lc_suggested } : {}),
    ...(typeof data.thread_completed === "boolean" ? { threadCompleted: String(data.thread_completed) } : {}),
    ...(data.lc_url && Array.isArray(data.screenshots) && data.screenshots.length
      ? { screenshots: JSON.stringify(data.screenshots.filter((url): url is string => typeof url === "string")) }
      : {}),
  };
}

// The screenshot URLs an extension add put in the query (see extensionAddQuery).
export function readQueryScreenshots(value: unknown): string[] {
  try {
    const parsed = JSON.parse(queryText(value) || "[]") as unknown;
    return Array.isArray(parsed) ? parsed.filter((url): url is string => typeof url === "string" && url.startsWith("https://")) : [];
  } catch {
    return [];
  }
}
export function extensionOpenQuery(data: ExtensionGameDetail): Record<string, string> {
  return { action: "open", f95url: data.url || "", ...(extensionGameId(data.gameId) ? { gameId: String(data.gameId) } : {}) };
}

// Opens one of the user's games in the library. The id is the exact key, so
// games without an F95 thread (store-link-only) can still be opened; the URL is
// kept for older handlers that only understand thread identity.
export function libraryOpenQuery(
  game: Pick<GameRecord, "id" | "f95_url">,
): Record<string, string> {
  return extensionOpenQuery({ url: game.f95_url || "", gameId: game.id });
}
