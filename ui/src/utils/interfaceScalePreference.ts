// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

export const INTERFACE_SCALE_STORAGE_KEY = "wlib-interface-scale";
export const INTERFACE_SCALE_OPTIONS = [80, 90, 100, 110, 120, 130, 140, 150] as const;
export type InterfaceScale = (typeof INTERFACE_SCALE_OPTIONS)[number];
export const interfaceScale = ref<InterfaceScale>(100);

export const normalizeInterfaceScale = (raw: unknown): InterfaceScale => {
  const value = typeof raw === "string" && raw.trim() !== "" ? Number(raw) : raw;
  return INTERFACE_SCALE_OPTIONS.includes(value as InterfaceScale) ? value as InterfaceScale : 100;
};

export const uiScaleFactor = (): number => interfaceScale.value / 100;
// Pointer positions and bounding rectangles use viewport pixels; styles use scaled CSS pixels.
export const toCssPx = (viewportPx: number): number => viewportPx / uiScaleFactor();

const applyInterfaceScale = (): void => {
  if (typeof document !== "undefined") {
    document.documentElement.style.setProperty("--ui-scale", String(uiScaleFactor()));
  }
};

export const setInterfaceScale = (value: unknown): void => {
  interfaceScale.value = normalizeInterfaceScale(value);
  applyInterfaceScale();
  try { localStorage.setItem(INTERFACE_SCALE_STORAGE_KEY, String(interfaceScale.value)); }
  catch (error) { console.warn("Failed to write interface scale preference", error); }
};

export const loadInterfaceScale = (): void => {
  interfaceScale.value = 100;
  try { interfaceScale.value = normalizeInterfaceScale(localStorage.getItem(INTERFACE_SCALE_STORAGE_KEY)); }
  catch (error) { console.warn("Failed to read interface scale preference", error); }
  applyInterfaceScale();
};
