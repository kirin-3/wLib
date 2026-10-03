// SPDX-License-Identifier: GPL-3.0-or-later
import { computed, ref } from "vue";

export type ThemePreference = "system" | "light" | "dark";

export const THEME_STORAGE_KEY = "wlib-theme";

export const themePreference = ref<ThemePreference>("system");
const systemPrefersLight = ref(false);

export const isDarkTheme = computed(() =>
  themePreference.value === "system"
    ? !systemPrefersLight.value
    : themePreference.value === "dark",
);

export const normalizeThemePreference = (value: string | null): ThemePreference =>
  value === "light" || value === "dark" ? value : "system";

const systemQuery = (): MediaQueryList | undefined =>
  typeof window === "undefined" ? undefined : window.matchMedia?.("(prefers-color-scheme: light)");

const applyRootTheme = (): void => {
  if (typeof document === "undefined") return;
  // Re-read in case a change event was missed (e.g. while the window was hidden).
  systemPrefersLight.value = !!systemQuery()?.matches;
  document.documentElement.classList.toggle("light", !isDarkTheme.value);
};

export const setThemePreference = (preference: ThemePreference): void => {
  themePreference.value = preference;
  applyRootTheme();
  try {
    localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch (error) {
    console.warn("Failed to write theme preference", error);
  }
};

// Reads the saved choice (no saved choice = follow the OS) and tracks OS theme changes.
export const loadThemePreference = (): void => {
  try {
    themePreference.value = normalizeThemePreference(localStorage.getItem(THEME_STORAGE_KEY));
  } catch (error) {
    console.warn("Failed to read theme preference", error);
  }
  systemQuery()?.addEventListener("change", applyRootTheme);
  window.addEventListener("focus", applyRootTheme);
  applyRootTheme();
};
