// SPDX-License-Identifier: GPL-3.0-or-later
import { ref, watchEffect } from "vue";
import { runningGameIds } from "../composables/useRunningGames.ts";
import { motionEnabled } from "./motionPreference.ts";

export type CoverAnimation = "always" | "hover" | "never";
export type EffectiveCoverAnimation = CoverAnimation | "still";

export const COVER_ANIMATION_STORAGE_KEY = "wlib-cover-animation";
export const COVER_ANIMATION_DEFAULT = "hover" as CoverAnimation;

export const coverAnimation = ref<CoverAnimation>(COVER_ANIMATION_DEFAULT);

const normalize = (value: string | null): CoverAnimation =>
  value === "always" || value === "hover" || value === "never" ? value : COVER_ANIMATION_DEFAULT;

export const loadCoverAnimation = (storage?: Storage): CoverAnimation => {
  let value = COVER_ANIMATION_DEFAULT;
  try {
    value = normalize((storage ?? localStorage).getItem(COVER_ANIMATION_STORAGE_KEY));
  } catch (error) {
    console.warn("Failed to read cover animation preference", error);
  }
  coverAnimation.value = value;
  return value;
};

export const saveCoverAnimation = (value: CoverAnimation, storage?: Storage): void => {
  coverAnimation.value = normalize(value);
  try {
    (storage ?? localStorage).setItem(COVER_ANIMATION_STORAGE_KEY, coverAnimation.value);
  } catch (error) {
    console.warn("Failed to write cover animation preference", error);
  }
};

// Motion off or a running game overrides the user's choice.
export const effectiveCoverAnimation = (
  choice: CoverAnimation,
  motionOn: boolean,
  anyGameRunning: boolean,
): EffectiveCoverAnimation => (!motionOn || anyGameRunning || choice === "never" ? "still" : choice);

const applyRootState = (mode: EffectiveCoverAnimation): void => {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.toggle("covers-still", mode === "still" || mode === "never");
  root.classList.toggle("covers-hover", mode === "hover");
  root.dataset.coverAnimation = mode;
};

let started = false;

// Called once at startup. Switching modes only toggles classes on <html>, so
// no view re-renders.
export const startCoverAnimation = (storage?: Storage): void => {
  loadCoverAnimation(storage);
  if (started) return;
  started = true;
  watchEffect(() =>
    applyRootState(effectiveCoverAnimation(coverAnimation.value, motionEnabled.value, runningGameIds.value.size > 0)),
  );
};
