// SPDX-License-Identifier: GPL-3.0-or-later

const versionParts = (version: string): number[] =>
  version
    .trim()
    .replace(/^v/i, "")
    .split(/[-+]/, 1)[0]!
    .split(".")
    .map((part) => Number.parseInt(part, 10) || 0);

/** True only when `latest` is strictly newer than `current` (e.g. "v0.4.0" > "v0.3.5"). */
export const isNewerVersion = (latest: string, current: string): boolean => {
  const a = versionParts(latest);
  const b = versionParts(current);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const diff = (a[i] ?? 0) - (b[i] ?? 0);
    if (diff !== 0) return diff > 0;
  }
  return false;
};
