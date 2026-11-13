// SPDX-License-Identifier: GPL-3.0-or-later
// Field rules match core/gamescope.py; the backend checks again on save.
import type { GamescopeFilter, GamescopeScaler, GamescopeWindowMode } from "../services/api";

export const MAX_RESOLUTION_SIDE = 16384;
export const MAX_FPS_LIMIT = 1000;

export const WINDOW_MODE_OPTIONS: ReadonlyArray<{ value: GamescopeWindowMode; label: string }> = [
  { value: "fullscreen", label: "Fullscreen" },
  { value: "borderless", label: "Borderless" },
  { value: "windowed", label: "Window" },
];

export const SCALER_OPTIONS: ReadonlyArray<{ value: GamescopeScaler; label: string }> = [
  { value: "auto", label: "Auto" },
  { value: "integer", label: "Integer" },
  { value: "fit", label: "Fit" },
  { value: "fill", label: "Fill" },
  { value: "stretch", label: "Stretch" },
];

export const FILTER_OPTIONS: ReadonlyArray<{ value: GamescopeFilter; label: string }> = [
  { value: "linear", label: "Linear" },
  { value: "nearest", label: "Nearest" },
  { value: "fsr", label: "FSR" },
  { value: "nis", label: "NIS" },
  { value: "pixel", label: "Pixel" },
];

const RESOLUTION_RE = /^\s*(\d+)\s*[xX×]\s*(\d+)\s*$/;

export function parseResolution(value: string): [number, number] | null {
  const match = RESOLUTION_RE.exec(value);
  if (!match) return null;
  const width = Number(match[1]);
  const height = Number(match[2]);
  if (width < 1 || height < 1 || width > MAX_RESOLUTION_SIDE || height > MAX_RESOLUTION_SIDE) return null;
  return [width, height];
}

/** "" for an empty value (Auto) or a valid `WxH`; otherwise why it is not valid. */
export function resolutionError(value: string): string {
  if (!value.trim()) return "";
  return parseResolution(value)
    ? ""
    : `Enter the resolution as width x height, each a whole number from 1 to ${MAX_RESOLUTION_SIDE}.`;
}

export function normalizeResolution(value: string): string {
  const parsed = parseResolution(value);
  return parsed ? `${parsed[0]}x${parsed[1]}` : "";
}

export function fpsLimitError(value: string | number): string {
  const text = String(value ?? "").trim();
  if (!text) return "";
  if (!/^\d+$/.test(text) || Number(text) < 1 || Number(text) > MAX_FPS_LIMIT) {
    return `Enter the FPS limit as a whole number from 1 to ${MAX_FPS_LIMIT}.`;
  }
  return "";
}

/** Split like Python's `shlex.split(posix=True)`; throws on unclosed quoting. */
export function splitArgs(value: string): string[] {
  const parts: string[] = [];
  let current = "";
  let started = false;
  let quote: "'" | '"' | null = null;
  for (let index = 0; index < value.length; index += 1) {
    const char = value.charAt(index);
    if (quote === "'") {
      if (char === "'") quote = null;
      else current += char;
      continue;
    }
    if (quote === '"') {
      if (char === '"') {
        quote = null;
      } else if (char === "\\" && index + 1 < value.length && (value[index + 1] === '"' || value[index + 1] === "\\")) {
        index += 1;
        current += value.charAt(index);
      } else if (char === "\\" && index + 1 >= value.length) {
        throw new Error("The quoting is not closed.");
      } else {
        current += char;
      }
      continue;
    }
    if (char === "'" || char === '"') {
      quote = char;
      started = true;
    } else if (char === "\\") {
      if (index + 1 >= value.length) throw new Error("The quoting is not closed.");
      index += 1;
      current += value.charAt(index);
      started = true;
    } else if (/\s/.test(char)) {
      if (started) parts.push(current);
      current = "";
      started = false;
    } else {
      current += char;
      started = true;
    }
  }
  if (quote) throw new Error("The quoting is not closed.");
  if (started) parts.push(current);
  return parts;
}

/** "" when the extra arguments can be saved; otherwise why not. */
export function argsError(value: string): string {
  let parts: string[];
  try {
    parts = splitArgs(value);
  } catch (error) {
    return error instanceof Error ? error.message : "The quoting is not closed.";
  }
  return parts.includes("--") ? "`--` is not allowed; wLib adds it before the game command." : "";
}

export function windowModeLabel(value: string): string {
  return WINDOW_MODE_OPTIONS.find((option) => option.value === value)?.label ?? "Fullscreen";
}

export function scalerLabel(value: string): string {
  return SCALER_OPTIONS.find((option) => option.value === value)?.label ?? "Auto";
}

export function filterLabel(value: string): string {
  return FILTER_OPTIONS.find((option) => option.value === value)?.label ?? "Linear";
}

/** The per-game select label for Default, which names what Default means now. */
export function defaultOverrideLabel(globalEnabled: boolean): string {
  return `Default (${globalEnabled ? "On" : "Off"})`;
}

/** Whether a launch of this game uses gamescope (before host skip rules). */
export function gamescopeEnabledForGame(override: string | undefined | null, globalEnabled: boolean): boolean {
  const value = String(override ?? "default").trim().toLowerCase();
  if (value === "on") return true;
  if (value === "off") return false;
  return globalEnabled;
}
