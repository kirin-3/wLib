// SPDX-License-Identifier: GPL-3.0-or-later
import type { DownloadSite, DownloadSiteOption, GameRecord, LcPlusCheck, LcPlusTier } from "../services/api";

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

/**
 * Whether the game edit form shows the LC thread field and the LC mode:
 * with "Use LewdCorner" on, for a game whose LC mode is not Default, and for
 * an LC thread without an F95 thread (LC is active for it).
 */
export function showLcFields(
  game: Partial<Pick<GameRecord, "lc_url" | "lc_mode" | "f95_url">>,
  enabled: boolean,
): boolean {
  return enabled || !!game.lc_mode || (!!game.lc_url && !game.f95_url);
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


type DownloadThread = UpdateThread & Pick<GameRecord, "latest_site" | "f95_latest_version" | "lc_latest_version" | "lc_catalog_tier">;

export function downloadSites(
  game: DownloadThread,
  settings: { lc_enabled?: boolean; lc_plus?: boolean },
  catalogTier = game.lc_catalog_tier,
): DownloadSiteOption[] {
  const sites: DownloadSite[] = [];
  if (game.f95_url) sites.push("f95");
  if (lcActive(game, !!settings.lc_enabled) && (settings.lc_plus || catalogTier?.toLowerCase() !== "premium")) {
    sites.push("lc");
  }
  return sites.map(site => {
    const version = game[site === "f95" ? "f95_latest_version" : "lc_latest_version"] || "";
    // An unknown version is not "older"; only a known version can lose.
    const older = !!version && (game.latest_site === "lc" || game.latest_site === "f95") && game.latest_site !== site;
    return { site, version, older };
  });
}

export function defaultDownloadSite(game: Pick<GameRecord, "latest_site">, sites: DownloadSiteOption[]): DownloadSite | "" {
  if (game.latest_site === "lc" && sites.some(option => option.site === "lc")) return "lc";
  return sites[0]?.site || "";
}

/** "Detected: Plus / Standard / Not detected yet" for the LC Plus setting in Auto. */
export function lcPlusDetectedText(tier: LcPlusTier): string {
  return tier === "plus" ? "Detected: Plus" : tier === "standard" ? "Detected: Standard" : "Not detected yet";
}

/** Why "Check now" is disabled, or "" when it can run. */
export function lcPlusCheckReason(check: LcPlusCheck, lcEnabled: boolean): string {
  if (!lcEnabled) return "Turn on Use LewdCorner to check LC Plus.";
  if (check === "no_catalog") return "The game catalog must be on to check LC Plus.";
  if (check === "no_extension") return "Open a browser with the wLib extension and turn on its LewdCorner switch to check LC Plus.";
  return "";
}

/** When the tier was last checked, in the user's locale; "" when never. */
export function lcPlusCheckedAt(value: string, locale?: string): string {
  const time = value ? Date.parse(value) : NaN;
  return Number.isNaN(time) ? "" : new Date(time).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}
