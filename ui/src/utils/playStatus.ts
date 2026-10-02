// SPDX-License-Identifier: GPL-3.0-or-later
import type { Component } from "vue";
import {
  IconCalendarTime,
  IconCircleCheck,
  IconCircleX,
  IconClockPause,
  IconDeviceGamepad2,
  IconForbid2,
  IconPlayerPause,
  IconTag,
} from "@tabler/icons-vue";

export const PLAY_STATUSES = [
  "Not Started",
  "Plan to Play",
  "Playing",
  "Waiting For Update",
  "On Hold",
  "Completed",
  "Abandoned",
] as const;

export type PlayStatus = string;

export interface PlayStatusOption {
  value: PlayStatus;
  label: string;
  icon: Component;
  toneClass: string;
}

export const DEFAULT_PLAY_STATUS: PlayStatus = PLAY_STATUSES[0];

const CANONICAL_PLAY_STATUS_MAP: Record<string, PlayStatus> = PLAY_STATUSES.reduce(
  (acc, status) => {
    acc[status.toLowerCase()] = status;
    return acc;
  },
  {} as Record<string, PlayStatus>,
);

const LEGACY_PLAY_STATUS_MAP: Record<string, PlayStatus> = {
  completed: "Completed",
  in_progress: "Playing",
  replaying: "Playing",
  waiting_update: "Waiting For Update",
  abandoned: "Abandoned",
};

const LEGACY_RECOVERABLE_PLAY_STATUSES = new Set<string>([
  "",
  DEFAULT_PLAY_STATUS.toLowerCase(),
  "on hold",
  "waiting_update",
  "abandoned",
]);

const normalizeStatusKey = (value: unknown): string => String(value || "").trim().toLowerCase();

const PLAY_STATUS_ICON_MAP: Record<PlayStatus, Component> = {
  "Not Started": IconForbid2,
  "Plan to Play": IconCalendarTime,
  Playing: IconDeviceGamepad2,
  "Waiting For Update": IconClockPause,
  "On Hold": IconPlayerPause,
  Completed: IconCircleCheck,
  Abandoned: IconCircleX,
};

const PLAY_STATUS_TONE_CLASS_MAP: Record<PlayStatus, string> = {
  "Not Started": "ui-status-tone-not-started",
  "Plan to Play": "ui-status-tone-plan-to-play",
  Playing: "ui-status-tone-playing",
  "Waiting For Update": "ui-status-tone-waiting",
  "On Hold": "ui-status-tone-on-hold",
  Completed: "ui-status-tone-completed",
  Abandoned: "ui-status-tone-abandoned",
};

export const PLAY_STATUS_META: Record<PlayStatus, PlayStatusOption> = PLAY_STATUSES.reduce(
  (acc, status) => {
    acc[status] = {
      value: status,
      label: status,
      icon: PLAY_STATUS_ICON_MAP[status]!,
      toneClass: PLAY_STATUS_TONE_CLASS_MAP[status]!,
    };
    return acc;
  },
  {} as Record<PlayStatus, PlayStatusOption>,
);

export const isPlayStatus = (value: unknown): value is PlayStatus => {
  return typeof value === "string" && !!value.trim() && value.trim().length <= 40;
};

export const normalizePlayStatus = (value: unknown, legacyStatus?: unknown): PlayStatus => {
  const normalizedValue = normalizeStatusKey(value);
  const normalizedLegacyStatus = normalizeStatusKey(legacyStatus);

  const recoveredStatus = Object.prototype.hasOwnProperty.call(LEGACY_PLAY_STATUS_MAP, normalizedLegacyStatus) ? LEGACY_PLAY_STATUS_MAP[normalizedLegacyStatus] : undefined;
  if (recoveredStatus && LEGACY_RECOVERABLE_PLAY_STATUSES.has(normalizedValue)) {
    return recoveredStatus;
  }

  const canonicalStatus = Object.prototype.hasOwnProperty.call(CANONICAL_PLAY_STATUS_MAP, normalizedValue) ? CANONICAL_PLAY_STATUS_MAP[normalizedValue] : undefined;
  if (canonicalStatus) {
    return canonicalStatus;
  }

  const legacyStatusFromValue = Object.prototype.hasOwnProperty.call(LEGACY_PLAY_STATUS_MAP, normalizedValue) ? LEGACY_PLAY_STATUS_MAP[normalizedValue] : undefined;
  if (legacyStatusFromValue) {
    return legacyStatusFromValue;
  }

  if (normalizedValue) return String(value).trim().slice(0, 40);

  if (recoveredStatus) {
    return recoveredStatus;
  }

  return DEFAULT_PLAY_STATUS;
};

export const getPlayStatusMeta = (value: unknown, legacyStatus?: unknown): PlayStatusOption => {
  const normalized = normalizePlayStatus(value, legacyStatus);
  return (Object.prototype.hasOwnProperty.call(PLAY_STATUS_META, normalized) ? PLAY_STATUS_META[normalized] : undefined) || {
    value: normalized, label: normalized, icon: IconTag, toneClass: "ui-status-tone-custom",
  };
};

export const PLAY_STATUS_OPTIONS: PlayStatusOption[] = PLAY_STATUSES.map(
  (status) => PLAY_STATUS_META[status]!,
);

export const getPlayStatusOptions = (customStatuses: readonly string[]): PlayStatusOption[] => {
  const seen = new Set(PLAY_STATUSES.map((status) => status.toLowerCase()));
  return [...PLAY_STATUS_OPTIONS, ...customStatuses.flatMap((status) => {
    const meta = getPlayStatusMeta(status);
    const key = meta.value.toLowerCase();
    if (seen.has(key)) return [];
    seen.add(key);
    return [meta];
  })];
};

export const validateCustomPlayStatuses = (statuses: readonly string[]): string => {
  const seen = new Set(PLAY_STATUSES.map((status) => status.toLowerCase()));
  for (const status of statuses) {
    const name = status.trim();
    if (!name || name.length > 40) return "Play status names must contain 1–40 characters.";
    if (seen.has(name.toLowerCase())) return `Play status '${name}' already exists.`;
    // Legacy aliases (e.g. "Replaying") would be rewritten to a built-in status on save.
    if (normalizePlayStatus(name) !== name) return `Play status '${name}' is reserved.`;
    seen.add(name.toLowerCase());
  }
  return "";
};
