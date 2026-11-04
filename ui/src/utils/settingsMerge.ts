// SPDX-License-Identifier: GPL-3.0-or-later
/** Preserve focused or subsequently edited values when applying a persisted snapshot. */
export function mergeSettingsFields<T extends object>(persisted: T, current: T, submitted: T, focused?: keyof T): T {
  const result = { ...persisted };
  for (const key of Object.keys(current) as (keyof T)[]) {
    if (key === focused || JSON.stringify(current[key]) !== JSON.stringify(submitted[key])) {
      result[key] = current[key];
    }
  }
  return result;
}
