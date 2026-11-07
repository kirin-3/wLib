// SPDX-License-Identifier: GPL-3.0-or-later
import { normalizeLaunchMode } from "./launchMode.ts";

export type HtmlPlayerChoice = "" | "builtin" | "browser";
export type WebPlayer = "builtin" | "browser" | null;

export const HTML_PLAYER_OPTIONS = [
  { value: "", label: "Use Settings default" },
  { value: "builtin", label: "Built-in player" },
  { value: "browser", label: "System browser" },
] as const;

export const normalizeHtmlPlayer = (value: unknown): HtmlPlayerChoice =>
  value === "builtin" || value === "browser" ? value : "";

export const isHtmlTarget = (target: string): boolean => /\.html?$/i.test((target || "").trim());
const isFlashTarget = (target: string): boolean => /\.swf$/i.test((target || "").trim());

// Mirrors core/web_launch.py: which player a launch target opens in, or null
// when the launch is not a web launch (other files, Wine/Proton, Custom Command).
export const resolveWebPlayer = (
  target: string,
  launchMode: unknown,
  htmlPlayer: unknown,
  builtinSetting: boolean | undefined,
  platform: string,
): WebPlayer => {
  const html = isHtmlTarget(target);
  if (!html && !isFlashTarget(target)) return null;
  const mode = normalizeLaunchMode(launchMode);
  if (platform !== "windows" && mode !== "auto" && mode !== "native") return null;
  if (!html) return "builtin";
  return normalizeHtmlPlayer(htmlPlayer) || (builtinSetting === false ? "browser" : "builtin");
};

export const HTML_PLAYER_FIRST_USE_NOTICE = "html_player_first_use";

// Launch results carry either a message or a notice code.
export const launchNoticeMessage = (notice: string): string =>
  notice === HTML_PLAYER_FIRST_USE_NOTICE
    ? "This game now opens in wLib's built-in player. Saves from your browser stay there and do not carry over. To use your browser again, open the game, go to Launch and set Opens in to System browser."
    : notice;
