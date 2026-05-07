// SPDX-License-Identifier: GPL-3.0-or-later
export const DEFAULT_LAUNCH_MODE = "auto";

export const LAUNCH_MODE_OPTIONS = [
  { value: "auto", label: "Auto Detect" },
  { value: "native", label: "Linux Native" },
  { value: "wine_proton", label: "Wine / Proton" },
  { value: "rpgmaker_linux", label: "RPGMaker Linux" },
] as const;

export type LaunchMode = (typeof LAUNCH_MODE_OPTIONS)[number]["value"];

export const normalizeLaunchMode = (value: unknown): LaunchMode => {
  return value === "native" ||
    value === "wine_proton" ||
    value === "rpgmaker_linux"
    ? value
    : DEFAULT_LAUNCH_MODE;
};

export const usesWineProtonControls = (value: unknown): boolean => {
  const mode = normalizeLaunchMode(value);
  return mode === "auto" || mode === "wine_proton";
};
