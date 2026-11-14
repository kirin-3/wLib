// SPDX-License-Identifier: GPL-3.0-or-later
import type { TagSwatch } from "./tagColors.ts";

/** Same order as `TAG_COLOR_SWATCHES` in tagColors.ts. Kept here so this file has no runtime imports. */
const SWATCHES = ["red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"] as const satisfies readonly TagSwatch[];

const WORD_CHAR = /[\p{L}\p{N}]/u;

/**
 * Up to two initials for a cover placeholder: the first letter or digit of the first two words,
 * or the first two letters or digits of a one-word title, in upper case. Empty when the title has none.
 */
export function titleInitials(title: string | null | undefined): string {
  const words = String(title ?? "")
    .split(/\s+/)
    .map(word => Array.from(word).filter(ch => WORD_CHAR.test(ch)).join(""))
    .filter(Boolean);
  if (!words.length) return "";
  const chars = words.length === 1
    ? Array.from(words[0]!).slice(0, 2)
    : [Array.from(words[0]!)[0]!, Array.from(words[1]!)[0]!];
  return chars.join("").toLocaleUpperCase();
}

/** A tag swatch picked from a stable hash (FNV-1a) of the trimmed, lower-case title. */
export function titleSwatch(title: string | null | undefined): TagSwatch {
  const key = String(title ?? "").trim().toLowerCase();
  let hash = 0x811c9dc5;
  for (let i = 0; i < key.length; i++) {
    hash ^= key.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return SWATCHES[(hash >>> 0) % SWATCHES.length]!;
}
