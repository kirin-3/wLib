// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";
import { applyInterfaceScale } from "./interfaceScalePreference.ts";
export type TvPreference = "auto" | "on" | "off";
export const TV_MODE_STORAGE_KEY = "wlib-tv-mode";
export const tvPreference = ref<TvPreference>("auto");
export const tvModeActive = ref(false);
export const gamingMode = ref(false);
export const parseTvPreference = (value: unknown): TvPreference => value === "on" || value === "off" ? value : "auto";
export const activeForPreference = (preference: TvPreference, gaming: boolean): boolean => preference === "on" || (preference === "auto" && gaming);
export function readGamingMode(search: string): boolean { return new URLSearchParams(search).get("gamingMode") === "1"; }
function apply(): void {
  tvModeActive.value = activeForPreference(tvPreference.value, gamingMode.value);
  if (typeof document !== "undefined") document.documentElement.classList.toggle("tv-mode", tvModeActive.value);
  applyInterfaceScale();
}
export function loadTvMode(search = typeof location === "undefined" ? "" : location.search): void {
  gamingMode.value = readGamingMode(search);
  try { tvPreference.value = parseTvPreference(localStorage.getItem(TV_MODE_STORAGE_KEY)); }
  catch { tvPreference.value = "auto"; }
  apply();
}
export function setTvPreference(value: unknown): void {
  tvPreference.value = parseTvPreference(value);
  try { localStorage.setItem(TV_MODE_STORAGE_KEY, tvPreference.value); } catch { /* local preference unavailable */ }
  apply();
}
