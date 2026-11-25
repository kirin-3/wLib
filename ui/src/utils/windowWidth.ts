// SPDX-License-Identifier: GPL-3.0-or-later
import { computed, ref } from "vue";
import { toCssPx } from "./interfaceScalePreference.ts";

// The window width in CSS pixels. The interface scale zooms the root, so a
// 1280 px window at 150 % lays out like an 853 px one; layout decisions use
// this value, not window.innerWidth.
const viewportPx = ref(typeof window === "undefined" ? Number.POSITIVE_INFINITY : window.innerWidth);
let listening = false;

export const windowCssWidth = computed(() => toCssPx(viewportPx.value));

/** Starts the shared resize listener (once) and returns the reactive width. */
export const useWindowCssWidth = (): typeof windowCssWidth => {
  if (!listening && typeof window !== "undefined") {
    listening = true;
    window.addEventListener("resize", () => { viewportPx.value = window.innerWidth; }, { passive: true });
  }
  return windowCssWidth;
};
