// SPDX-License-Identifier: GPL-3.0-or-later
import type { RenpyArchive, RenpyConsoleState, RenpyExtractMode, RpgmToolsState } from "../services/api";

export interface ToolProgressInput {
  running: boolean;
  done: number;
  total: number;
  current: string;
}

export interface ToolActionState {
  enabled: boolean;
  // Why the action is disabled, or "" when nothing needs explaining.
  message: string;
}

export const formatToolBytes = (size: number): string => {
  if (!Number.isFinite(size) || size < 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[unit]}`;
};

export const toolProgressPercent = ({ done, total }: ToolProgressInput): number => {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
};

// "12 of 100 files - bg/room.png", or a plain "Working..." before the total is known.
export const formatToolProgress = ({ done, total, current }: ToolProgressInput): string => {
  if (total <= 0) return "Working...";
  const label = `${done} of ${total} files`;
  return current ? `${label} - ${current}` : label;
};

export const getExtractState = (
  archives: RenpyArchive[],
  mode: RenpyExtractMode,
  output: string,
  busy: boolean,
): ToolActionState => {
  if (busy) return { enabled: false, message: "A tool is running. Please wait." };
  if (archives.length === 0) return { enabled: false, message: "There are no archives to extract." };
  if (!archives.some((archive) => archive.format)) {
    return { enabled: false, message: "None of the archives are in a format wLib can read." };
  }
  if (mode === "folder" && !output.trim()) {
    return { enabled: false, message: "Choose an output folder first." };
  }
  return { enabled: true, message: "" };
};

export const getDecryptState = (state: RpgmToolsState, busy: boolean): ToolActionState => {
  if (busy) return { enabled: false, message: "A tool is running. Please wait." };
  if (state.encrypted === 0) return { enabled: false, message: "This game's assets are not encrypted." };
  if (state.missing === 0) return { enabled: false, message: "Every encrypted asset already has a decrypted copy." };
  if (!state.key_source) return { enabled: false, message: "The encryption key could not be found." };
  return { enabled: true, message: "" };
};

// Turning the switch off is always allowed; turning it on needs a decrypted copy of every asset.
export const getUseDecryptedState = (state: RpgmToolsState, busy: boolean): ToolActionState => {
  if (busy) return { enabled: false, message: "A tool is running. Please wait." };
  if (state.use_decrypted) return { enabled: true, message: "" };
  if (state.encrypted === 0) return { enabled: false, message: "This game's assets are not encrypted." };
  if (state.missing > 0) {
    return { enabled: false, message: `Decrypt ${state.missing} more asset${state.missing === 1 ? "" : "s"} first.` };
  }
  return { enabled: true, message: "" };
};

export const consoleHint = (state: RenpyConsoleState): string => {
  if (state === "developer") {
    return "The console opens with Shift+O and the developer menu with Shift+D on the next launch.";
  }
  if (state === "on") return "The console opens with Shift+O on the next launch.";
  return "";
};

export const keySourceLabel = (source: RpgmToolsState["key_source"]): string => {
  if (source === "system") return "Key read from System.json";
  if (source === "recovered") return "Key recovered from an encrypted image";
  return "No key found";
};
