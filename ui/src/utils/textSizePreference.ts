// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

export const TEXT_SIZE_STORAGE_KEY = "wlib-text-size";

export const TEXT_SIZE_OPTIONS = [
  { value: "small", label: "Small", px: 14 },
  { value: "default", label: "Default", px: 16 },
  { value: "large", label: "Large", px: 18 },
  { value: "xlarge", label: "Extra large", px: 20 },
] as const;

export type TextSizeValue = (typeof TEXT_SIZE_OPTIONS)[number]["value"];

export const textSizePreference = ref<TextSizeValue>("default");

// Unknown or unreadable values fall back to Default.
export const normalizeTextSizePreference = (
  raw: string | null | undefined,
): TextSizeValue => {
  const value = (raw ?? "").trim().toLowerCase();
  if (value === "small" || value === "default" || value === "large") return value;
  if (value === "extra large" || value === "extra-large" || value === "xlarge" || value === "xl")
    return "xlarge";
  return "default";
};

const pxForTextSize = (value: TextSizeValue): number => {
  const option = TEXT_SIZE_OPTIONS.find((entry) => entry.value === value);
  return option ? option.px : 16;
};

const applyTextSize = (): void => {
  if (typeof document === "undefined") return;
  document.documentElement.style.fontSize = `${pxForTextSize(textSizePreference.value)}px`;
};

export const setTextSizePreference = (value: TextSizeValue): void => {
  textSizePreference.value = normalizeTextSizePreference(value);
  applyTextSize();
  try {
    localStorage.setItem(TEXT_SIZE_STORAGE_KEY, textSizePreference.value);
  } catch (error) {
    console.warn("Failed to write text size preference", error);
  }
};

// Reads the saved choice (none = Default) and applies it before the app mounts.
export const loadTextSizePreference = (): void => {
  try {
    textSizePreference.value = normalizeTextSizePreference(
      localStorage.getItem(TEXT_SIZE_STORAGE_KEY),
    );
  } catch (error) {
    console.warn("Failed to read text size preference", error);
  }
  applyTextSize();
};
