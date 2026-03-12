export const PLAY_STATUSES = [
  "Not Started",
  "Plan to Play",
  "Playing",
  "Waiting For Update",
  "On Hold",
  "Completed",
  "Abandoned",
] as const;

export type PlayStatus = (typeof PLAY_STATUSES)[number];

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

export const isPlayStatus = (value: unknown): value is PlayStatus => {
  return typeof value === "string" && normalizeStatusKey(value) in CANONICAL_PLAY_STATUS_MAP;
};

export const normalizePlayStatus = (value: unknown, legacyStatus?: unknown): PlayStatus => {
  const normalizedValue = normalizeStatusKey(value);
  const normalizedLegacyStatus = normalizeStatusKey(legacyStatus);

  const recoveredStatus = LEGACY_PLAY_STATUS_MAP[normalizedLegacyStatus];
  if (recoveredStatus && LEGACY_RECOVERABLE_PLAY_STATUSES.has(normalizedValue)) {
    return recoveredStatus;
  }

  const canonicalStatus = CANONICAL_PLAY_STATUS_MAP[normalizedValue];
  if (canonicalStatus) {
    return canonicalStatus;
  }

  const legacyStatusFromValue = LEGACY_PLAY_STATUS_MAP[normalizedValue];
  if (legacyStatusFromValue) {
    return legacyStatusFromValue;
  }

  if (recoveredStatus) {
    return recoveredStatus;
  }

  return DEFAULT_PLAY_STATUS;
};

export const PLAY_STATUS_OPTIONS: Array<{ value: PlayStatus; label: string }> = PLAY_STATUSES.map(
  (status) => ({ value: status, label: status }),
);
