// SPDX-License-Identifier: GPL-3.0-or-later
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
export const gamePageUrl = (game: Pick<GameRecord, "f95_url" | "links">): string => game.f95_url || game.links?.[0]?.url || "";
export interface ExtensionGameDetail {
  url?: string;
  gameId?: number;
  title?: string;
  version?: string;
  coverImage?: string;
  tags?: string[] | string;
  rating?: string;
  developer?: string;
  engine?: string;
  links?: StoreLink[];
}
export function extensionAddQuery(data: ExtensionGameDetail): Record<string, string> {
  return { action: "import", f95url: data.url || "", title: data.title || "", version: data.version || "", coverImage: data.coverImage || "", tags: JSON.stringify(data.tags || []), rating: data.rating || "", developer: data.developer || "", engine: data.engine || "", links: JSON.stringify(readStoreLinks(data.links)) };
}
export function extensionOpenQuery(data: ExtensionGameDetail): Record<string, string> {
  return { action: "open", f95url: data.url || "", ...(extensionGameId(data.gameId) ? { gameId: String(data.gameId) } : {}) };
}
