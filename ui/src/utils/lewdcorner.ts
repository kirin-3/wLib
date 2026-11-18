// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameRecord } from "../services/api";

export type LewdCornerMode = "" | "on" | "off";

export function normalizeLcThreadUrl(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "";
  const text = value.trim();
  const input = /^(www\.)?lewdcorner\.com\//i.test(text) ? `https://${text}` : text;
  try {
    const url = new URL(input);
    if (
      !/^https?:\/\//i.test(input) || !["https:", "http:"].includes(url.protocol)
      || !["lewdcorner.com", "www.lewdcorner.com"].includes(url.hostname)
      || url.username || url.password || url.port
    ) return "";
    const id = /^\/threads\/(?:[^/]+\.)?([0-9]+)(?:\/.*)?$/i.exec(url.pathname)?.[1]?.replace(/^0+/, "");
    return id ? `https://lewdcorner.com/threads/${id}/` : "";
  } catch {
    return "";
  }
}

export function lcActive(
  game: Pick<GameRecord, "lc_url" | "lc_mode" | "f95_url">,
  enabled: boolean,
): boolean {
  if (!game.lc_url || game.lc_mode === "off") return false;
  return game.lc_mode === "on" || enabled || !game.f95_url;
}

type UpdateThread = Pick<GameRecord, "f95_url" | "lc_url" | "lc_mode">;

export function updateCheckUrl(game: UpdateThread): string {
  return game.f95_url || (lcActive(game, false) ? game.lc_url || "" : "");
}

export function latestSiteLabel(
  game: UpdateThread & Pick<GameRecord, "latest_site">,
  lcEnabled: boolean,
): string {
  if (!game.f95_url || !lcActive(game, lcEnabled)) return "";
  if (game.latest_site === "lc") return "on LewdCorner";
  if (game.latest_site === "f95") return "on F95Zone";
  return "";
}
