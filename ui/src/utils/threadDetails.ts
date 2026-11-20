// SPDX-License-Identifier: GPL-3.0-or-later
// F95 first-post details on a game: language names, link labels and the
// automatic "Developer:" / "Language:" filter tags.

import type { GameRecord } from "../services/api.ts";

export const DEVELOPER_TAG_PREFIX = "Developer: ";
export const LANGUAGE_TAG_PREFIX = "Language: ";

// Same rules as parse_languages() in catalog_server/sources/f95_threads.py.
const LANGUAGE_SPLIT = /,|;|\/|&|\+|\band\b|\|/i;
const LANGUAGE_NAME = /^[A-Za-z][A-Za-z -]{1,24}$/;
const NOT_LANGUAGES = new Set(["MTL", "AI", "LLM", "TL", "OTHER", "OTHERS", "NONE", "UNKNOWN"]);

// "English (Sonnet 5.5 & Opus 5.5), Japanese" -> ["English", "Japanese"].
// Notes in brackets and note words such as MTL are not languages.
export const languageNames = (text: unknown): string[] => {
  if (typeof text !== "string" || !text.trim()) return [];
  const plain = text.replace(/\([^)]*\)|\[[^\]]*\]/g, ",");
  const names: string[] = [];
  for (const part of plain.split(LANGUAGE_SPLIT)) {
    const words = part.split(/\s+/).filter((word) => word && !NOT_LANGUAGES.has(word.toUpperCase()));
    const name = words.join(" ").replace(/^[\s.:-]+|[\s.:-]+$/g, "");
    if (!LANGUAGE_NAME.test(name)) continue;
    const titled = name
      .split(" ")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join(" ");
    if (!names.includes(titled)) names.push(titled);
  }
  return names;
};

const LINK_LABELS: [RegExp, string][] = [
  [/(^|\.)patreon\.com$/i, "Patreon"],
  [/(^|\.)subscribestar\.(com|adult)$/i, "SubscribeStar"],
  [/(^|\.)itch\.io$/i, "itch.io"],
  [/(^|\.)ko-fi\.com$/i, "Ko-fi"],
  [/(^|\.)(steampowered|steamcommunity)\.com$/i, "Steam"],
  [/(^|\.)(discord\.gg|discord\.com|discordapp\.com)$/i, "Discord"],
];

// A short label for a developer link button; the host for other sites.
export const linkLabel = (url: string): string => {
  let host = "";
  try {
    host = new URL(url).hostname;
  } catch {
    return url;
  }
  for (const [pattern, label] of LINK_LABELS) if (pattern.test(host)) return label;
  return host.replace(/^www\./i, "");
};

// The Info tab edits aliases as one title per line; a title may hold commas.
export const parseAliasLines = (text: string, savedAliases: readonly string[] = []): string[] => {
  // A saved alias can itself contain a newline. Preserve the list if the field is unchanged.
  if (text === savedAliases.join("\n")) return [...savedAliases];

  let items: readonly unknown[] = text.split(/\r?\n/);
  if (text.trimStart().startsWith("[")) {
    try {
      // The Info tab previously passed JSON arrays straight to the backend.
      const decoded: unknown = JSON.parse(text);
      if (Array.isArray(decoded)) items = decoded;
    } catch {
      // A title can also start with a bracket.
    }
  }
  return [
    ...new Set(
      items
        .filter((item): item is string => typeof item === "string")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  ];
};

// The Info tab edits links as one URL per line.
export const parseLinkLines = (text: string): string[] => [
  ...new Set(
    text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => /^https?:\/\//i.test(line)),
  ),
];

// Tags made from the game's fields; never saved in the game's tag list.
export const automaticTags = (game: Pick<GameRecord, "developer" | "language">): string[] => {
  const developer = String(game.developer ?? "").trim();
  return [
    ...(developer ? [DEVELOPER_TAG_PREFIX + developer] : []),
    ...languageNames(game.language).map((name) => LANGUAGE_TAG_PREFIX + name),
  ];
};
