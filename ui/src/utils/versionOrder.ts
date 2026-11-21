// SPDX-License-Identifier: GPL-3.0-or-later

type Version = string | null | undefined;

// Keep in sync with normalize_version() in core/versions.py. String() also
// handles non-string values from hand-edited or imported library rows.
export const normalizeVersion = (version: Version): string =>
  String(version || "")
    .replace(/\s+/g, "")
    .toLowerCase()
    .replace(/^v(?=\d)/, "");

const versionSpelling = (version: Version): string => {
  const value = normalizeVersion(version).replace(/[/()[\]\-_,:]/g, "");
  return value !== "final" ? value.replace(/final$/, "") : value;
};

export const sameVersion = (a: Version, b: Version): boolean =>
  versionSpelling(a) === versionSpelling(b);

const orderableVersion = /^([0-9]+(?:\.[0-9]+)*)[a-z]*$/;

export const compareVersions = (a: Version, b: Version): -1 | 0 | 1 | null => {
  if (sameVersion(a, b)) return 0;

  const left = orderableVersion.exec(normalizeVersion(a))?.[1];
  const right = orderableVersion.exec(normalizeVersion(b))?.[1];
  if (!left || !right) return null;

  const leftSegments = left.split(".");
  const rightSegments = right.split(".");
  const count = Math.max(leftSegments.length, rightSegments.length);
  for (let index = 0; index < count; index++) {
    const aSegment = leftSegments[index] ?? "0";
    const bSegment = rightSegments[index] ?? "0";
    if (aSegment === bSegment) continue;

    const aNumber = BigInt(aSegment);
    const bNumber = BigInt(bSegment);
    if (aNumber === bNumber) return null;
    const whole = aNumber > bNumber ? 1 : -1;
    if (index === 0) return whole;

    const width = Math.max(aSegment.length, bSegment.length);
    const aFraction = aSegment.padEnd(width, "0");
    const bFraction = bSegment.padEnd(width, "0");
    if (aFraction === bFraction) return null;
    const fraction = aFraction > bFraction ? 1 : -1;
    return whole === fraction ? whole : null;
  }

  return null;
};

export const hasNewerVersion = (installed: Version, latest: Version, skipped?: Version): boolean => {
  const normalizedLatest = normalizeVersion(latest);
  if (!normalizedLatest || normalizedLatest === normalizeVersion(skipped)) return false;
  if (!normalizeVersion(installed)) return true;
  const order = compareVersions(latest, installed);
  return order === 1 || order === null;
};
