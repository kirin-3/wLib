// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

export type MotionPreference = "on" | "off";

export const MOTION_PREFERENCE_STORAGE_KEY = "wlib-ui-motion";
export const MOTION_DISABLED_CLASS = "motion-off";

export const motionEnabled = ref(true);

const normalizeMotionPreference = (value: string | null): MotionPreference =>
  value === "off" ? "off" : "on";

const getRootElement = (): HTMLElement | null =>
  typeof document !== "undefined" ? document.documentElement : null;

// Storage defaults to localStorage inside the try: touching it can throw (or be undefined in Node).
const readMotionStorage = (storage?: Storage): MotionPreference => {
  try {
    return normalizeMotionPreference((storage ?? localStorage).getItem(MOTION_PREFERENCE_STORAGE_KEY));
  } catch (error) {
    console.warn("Failed to read motion preference", error);
    return "on";
  }
};

const writeMotionStorage = (storage: Storage | undefined, enabled: boolean): void => {
  try {
    (storage ?? localStorage).setItem(MOTION_PREFERENCE_STORAGE_KEY, enabled ? "on" : "off");
  } catch (error) {
    console.warn("Failed to write motion preference", error);
  }
};

const applyRootMotionState = (enabled: boolean): void => {
  const root = getRootElement();
  if (!root) return;

  root.classList.toggle(MOTION_DISABLED_CLASS, !enabled);
  root.dataset.motion = enabled ? "on" : "off";
};

export const applyMotionPreference = (enabled: boolean): void => {
  motionEnabled.value = enabled;
  applyRootMotionState(enabled);
};

export const loadMotionPreference = (storage?: Storage): boolean => {
  const enabled = readMotionStorage(storage) === "on";
  applyMotionPreference(enabled);
  return enabled;
};

export const saveMotionPreference = (enabled: boolean, storage?: Storage): void => {
  applyMotionPreference(enabled);
  writeMotionStorage(storage, enabled);
};
