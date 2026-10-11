// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

export const ACCENT_PRESETS = ["magenta", "violet", "blue", "cyan", "rose", "orange"] as const;
export type AccentPreset = (typeof ACCENT_PRESETS)[number];
export type AccentPreference = AccentPreset | `#${string}`;

export const ACCENT_STORAGE_KEY = "wlib-accent";

export const accentPreference = ref<AccentPreference>("magenta");

const HEX_RE = /^#[0-9a-f]{6}$/i;
const DARK_LABEL = "#0f0f14";
const LIGHT_LABEL = "#ffffff";

// Unknown or malformed values fall back to the default Magenta.
export const normalizeAccentPreference = (raw: string | null | undefined): AccentPreference => {
  const value = (raw ?? "").trim();
  if ((ACCENT_PRESETS as readonly string[]).includes(value)) return value as AccentPreset;
  return HEX_RE.test(value) ? (value.toLowerCase() as AccentPreference) : "magenta";
};

const luminance = (hex: string): number => {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

// Label color (dark or white) with the higher WCAG contrast against the accent fill.
export const onAccentColor = (hex: string): string => {
  const fill = luminance(hex);
  const contrastWhite = 1.05 / (fill + 0.05);
  const contrastDark = (fill + 0.05) / (luminance(DARK_LABEL) + 0.05);
  return contrastDark >= contrastWhite ? DARK_LABEL : LIGHT_LABEL;
};

const applyAccent = (): void => {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  const value = accentPreference.value;
  root.style.removeProperty("--brand");
  root.style.removeProperty("--on-brand");
  if (value === "magenta") {
    root.removeAttribute("data-accent");
  } else if (value.startsWith("#")) {
    root.setAttribute("data-accent", "custom");
    root.style.setProperty("--brand", value);
    root.style.setProperty("--on-brand", onAccentColor(value));
  } else {
    root.setAttribute("data-accent", value);
  }
};

// Applies without saving (live preview while a color picker is being dragged).
export const previewAccentPreference = (value: AccentPreference): void => {
  accentPreference.value = normalizeAccentPreference(value);
  applyAccent();
};

export const setAccentPreference = (value: AccentPreference): void => {
  previewAccentPreference(value);
  try {
    localStorage.setItem(ACCENT_STORAGE_KEY, accentPreference.value);
  } catch (error) {
    console.warn("Failed to write accent preference", error);
  }
};

// Reads the saved choice (none = Magenta) and applies it before the app mounts.
export const loadAccentPreference = (): void => {
  try {
    accentPreference.value = normalizeAccentPreference(localStorage.getItem(ACCENT_STORAGE_KEY));
  } catch (error) {
    console.warn("Failed to read accent preference", error);
  }
  applyAccent();
};
