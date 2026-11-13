// SPDX-License-Identifier: GPL-3.0-or-later
import type { SettingsPayload, TorrentSeedMode } from "../services/api";

export const SEED_MODE_OPTIONS: ReadonlyArray<{ value: TorrentSeedMode; label: string }> = [
  { value: "ask", label: "Ask each time" },
  { value: "always", label: "Always seed" },
  { value: "never", label: "Never seed" },
];

/** The download limit moved to the download queue settings (download_limit_kib). */
export type TorrentField = "ratio" | "hours" | "upload";

export interface ParsedField {
  /** The value to send, or null when the text is not valid. */
  value: number | string | null;
  error: string;
}

const NUMBER = /^\d+(?:\.\d+)?$/;

/** Parse the text of a torrent settings field. Blank ratio/limit mean "none"; blank hours mean no limit. */
export function parseTorrentField(field: TorrentField, text: string): ParsedField {
  const trimmed = text.trim();
  if (!trimmed) return { value: field === "hours" ? "" : 0, error: "" };
  if (!NUMBER.test(trimmed)) return { value: null, error: "Enter a number, such as 1.5." };
  const number = Number(trimmed);
  if (field === "ratio" || field === "hours") return { value: number, error: "" };
  if (!Number.isInteger(number)) return { value: null, error: "Enter a whole number of KiB/s." };
  return { value: number, error: "" };
}

const KEYS: Record<TorrentField, keyof SettingsPayload> = {
  ratio: "torrent_seed_ratio",
  hours: "torrent_seed_hours",
  upload: "torrent_upload_limit_kib",
};

export const torrentFieldKey = (field: TorrentField): keyof SettingsPayload => KEYS[field];

/** Text shown in a field for a stored value; 0 and empty show as blank. */
export const torrentFieldText = (value: number | string | undefined): string =>
  value === undefined || value === "" || value === 0 ? "" : String(value);
