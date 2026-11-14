// SPDX-License-Identifier: GPL-3.0-or-later

/** What a key press inside an open menu does. */
export type MenuKeyAction =
  | { type: "focus"; index: number }
  | { type: "close"; returnFocus: boolean }
  | null;

/** First enabled index at or after `start`, moving by `step` with wrap-around. -1 when all are disabled. */
function findEnabled(disabled: readonly boolean[], start: number, step: 1 | -1): number {
  const count = disabled.length;
  for (let offset = 0; offset < count; offset++) {
    const index = (((start + offset * step) % count) + count) % count;
    if (!disabled[index]) return index;
  }
  return -1;
}

/** Index of the item to focus when a menu opens: the checked item if it is enabled, otherwise the first enabled item. */
export function initialMenuIndex(disabled: readonly boolean[], checked = -1): number {
  if (checked >= 0 && checked < disabled.length && !disabled[checked]) return checked;
  return findEnabled(disabled, 0, 1);
}

/**
 * Key handling for an open menu. `current` is the focused item index (-1 when no item has focus).
 * Up and Down wrap around, Home and End go to the first and last enabled item, and disabled items are skipped.
 * Escape closes the menu and returns focus to its button; Tab closes it and lets focus move on.
 */
export function menuKeyAction(key: string, current: number, disabled: readonly boolean[]): MenuKeyAction {
  if (key === "Escape") return { type: "close", returnFocus: true };
  if (key === "Tab") return { type: "close", returnFocus: false };
  if (!disabled.length) return null;
  let index = -1;
  if (key === "ArrowDown") index = findEnabled(disabled, current < 0 ? 0 : current + 1, 1);
  else if (key === "ArrowUp") index = findEnabled(disabled, current < 0 ? disabled.length - 1 : current - 1, -1);
  else if (key === "Home") index = findEnabled(disabled, 0, 1);
  else if (key === "End") index = findEnabled(disabled, disabled.length - 1, -1);
  else return null;
  return index < 0 ? null : { type: "focus", index };
}
