export const DEFAULT_LAUNCH_MODE = "auto";

export const LAUNCH_MODE_OPTIONS = [
  { value: "auto", label: "Auto Detect" },
  { value: "native", label: "Linux Native" },
  { value: "wine_proton", label: "Wine / Proton" },
] as const;

export type LaunchMode = (typeof LAUNCH_MODE_OPTIONS)[number]["value"];

export const normalizeLaunchMode = (value: unknown): LaunchMode => {
  return value === "native" || value === "wine_proton"
    ? value
    : DEFAULT_LAUNCH_MODE;
};

export const usesWineProtonControls = (value: unknown): boolean => {
  return normalizeLaunchMode(value) !== "native";
};
