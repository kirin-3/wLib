// SPDX-License-Identifier: GPL-3.0-or-later
export const DEFAULT_LAUNCH_MODE = "auto";

export const LAUNCH_MODE_OPTIONS = [
  { value: "auto", label: "Auto Detect" },
  { value: "native", label: "Linux Native" },
  { value: "wine_proton", label: "Wine / Proton" },
  { value: "rpgmaker_linux", label: "RPGMaker Linux" },
  { value: "custom", label: "Custom Command" },
] as const;

export type LaunchMode = (typeof LAUNCH_MODE_OPTIONS)[number]["value"];

export const normalizeLaunchMode = (value: unknown): LaunchMode => {
  return value === "native" ||
    value === "wine_proton" ||
    value === "rpgmaker_linux" || value === "custom"
    ? value
    : DEFAULT_LAUNCH_MODE;
};

export const usesWineProtonControls = (value: unknown): boolean => {
  const mode = normalizeLaunchMode(value);
  return mode === "auto" || mode === "wine_proton";
};

export interface LaunchModeCapabilities {
  launch_modes: readonly LaunchMode[];
  rpgmaker_linux: boolean;
}

export interface LaunchRuntimeOverrideInput {
  wineProtonSupported: boolean;
  usesWineProtonRuntime: boolean;
  useCustomPrefix: boolean;
  customPrefix: string;
  protonVersion: string;
  storedCustomPrefix: string;
  storedProtonVersion: string;
}

export interface LaunchRuntimeOverrides {
  custom_prefix: string;
  proton_version: string;
}

export const resolveLaunchRuntimeOverrides = (
  input: LaunchRuntimeOverrideInput,
): LaunchRuntimeOverrides => {
  if (!input.wineProtonSupported) {
    return {
      custom_prefix: input.storedCustomPrefix,
      proton_version: input.storedProtonVersion,
    };
  }
  if (!input.usesWineProtonRuntime) {
    return { custom_prefix: "", proton_version: "" };
  }
  return {
    custom_prefix: input.useCustomPrefix ? input.customPrefix : "",
    proton_version: input.protonVersion,
  };
};

export const getLaunchModeOptions = (
  capabilities: LaunchModeCapabilities,
  rpgmakerLinuxRunnerAvailable: boolean,
  currentMode?: unknown,
) => {
  const current = normalizeLaunchMode(currentMode);
  const supported = new Set(capabilities.launch_modes);
  return LAUNCH_MODE_OPTIONS.filter((option) => {
    if (option.value === current && !supported.has(option.value)) return true;
    if (!supported.has(option.value)) return false;
    return (
      option.value !== "rpgmaker_linux" ||
      (capabilities.rpgmaker_linux && rpgmakerLinuxRunnerAvailable)
    );
  }).map((option) => ({
    ...option,
    label:
      option.value === current && !supported.has(option.value)
        ? `${option.label} (Unavailable on this platform)`
        : option.label,
  }));
};
