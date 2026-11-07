// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * Checks that run before the app leaves the current view (a nav link, a
 * toast button, an extension request). An open editor with unsaved changes
 * registers one, so leaving asks before its edits are thrown away.
 */
type LeaveGuard = () => Promise<boolean>;

const guards = new Set<LeaveGuard>();

export const addLeaveGuard = (guard: LeaveGuard): (() => void) => {
  guards.add(guard);
  return () => { guards.delete(guard); };
};

/** True when every registered guard allows leaving. */
export const confirmLeave = async (): Promise<boolean> => {
  for (const guard of [...guards]) {
    if (!(await guard())) return false;
  }
  return true;
};
