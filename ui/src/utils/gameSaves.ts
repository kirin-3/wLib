// SPDX-License-Identifier: GPL-3.0-or-later
export interface SaveActionInput {
  effective: string;
  effectiveExists: boolean;
  // detected folders that may be used automatically (the game folder is not)
  eligibleDetected: number;
  // every detected folder, including ones listed only as leads
  detected: number;
  busy: boolean;
}

export interface SaveActionState {
  canOpen: boolean;
  canBackup: boolean;
  canImport: boolean;
  canRestore: boolean;
  // Why actions are disabled, or "" when nothing needs explaining.
  message: string;
}

export const getSaveActionState = (input: SaveActionInput): SaveActionState => {
  const { effective, effectiveExists, eligibleDetected, detected, busy } = input;
  const none = { canOpen: false, canBackup: false, canImport: false, canRestore: false };
  if (busy) {
    return { ...none, message: "A save operation is running. Please wait." };
  }
  if (!effective) {
    return {
      ...none,
      message:
        eligibleDetected > 1
          ? "Several folders were found. Choose one to back up or import saves."
          : detected > 0
            ? 'Choose "Use this" on a folder above, or choose the folder where this game keeps its saves.'
            : "No saves folder was found. Choose the folder where this game keeps its saves.",
    };
  }
  if (!effectiveExists) {
    // Importing creates the folder; there is nothing to open or back up yet.
    return {
      ...none,
      canImport: true,
      canRestore: true,
      message: "This folder does not exist yet. Import or restore will create it.",
    };
  }
  return { canOpen: true, canBackup: true, canImport: true, canRestore: true, message: "" };
};

const BACKUP_NAME = /^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})(?:-(\d+))?$/;

// "2026-10-23_12-00-00" -> "2026-10-23 12:00:00"; a collision suffix shows as "(2)".
export const formatBackupLabel = (name: string): string => {
  const match = BACKUP_NAME.exec(name);
  if (!match) return name;
  const [, year, month, day, hour, minute, second, suffix] = match;
  const label = `${year}-${month}-${day} ${hour}:${minute}:${second}`;
  return suffix ? `${label} (${Number(suffix) + 1})` : label;
};
