// SPDX-License-Identifier: GPL-3.0-or-later

// The WAI-ARIA radio group pattern for groups of card buttons
// (role="radiogroup" with role="radio" children). Each group is one Tab stop:
// the checked radio, or the first enabled one when the group has no checked
// radio. Arrow keys move to the next or previous enabled radio in the group
// (wrapping) and check it; disabled radios are skipped.

/** Index of the radio that takes `tabindex="0"`, or -1 when every radio is disabled. */
export function radioTabStop(checked: number, disabled: readonly boolean[]): number {
  if (checked >= 0 && checked < disabled.length && !disabled[checked]) return checked;
  return disabled.findIndex((flag) => !flag);
}

function nextEnabled(disabled: readonly boolean[], start: number, step: 1 | -1): number {
  const count = disabled.length;
  for (let offset = 0; offset < count; offset++) {
    const index = (((start + offset * step) % count) + count) % count;
    if (!disabled[index]) return index;
  }
  return -1;
}

/**
 * The radio to move to for a key press (it can be `current` when no other
 * radio is enabled), or null when the key does not move in a radio group.
 * Down and Right go forward, Up and Left go back, Home and End go to the
 * first and last enabled radio.
 */
export function radioKeyTarget(key: string, current: number, disabled: readonly boolean[]): number | null {
  const count = disabled.length;
  if (!count) return null;
  let index: number;
  if (key === "ArrowDown" || key === "ArrowRight") index = nextEnabled(disabled, current + 1, 1);
  else if (key === "ArrowUp" || key === "ArrowLeft") index = nextEnabled(disabled, current < 0 ? count - 1 : current - 1, -1);
  else if (key === "Home") index = nextEnabled(disabled, 0, 1);
  else if (key === "End") index = nextEnabled(disabled, count - 1, -1);
  else return null;
  return index < 0 ? null : index;
}

const isDisabled = (element: HTMLElement): boolean =>
  element.matches(":disabled") || element.getAttribute("aria-disabled") === "true";

/**
 * Keydown handler for a `role="radiogroup"` element. It moves focus to the
 * target radio and clicks it, so the radio's own click handler checks it.
 */
export function onRadioGroupKeydown(event: KeyboardEvent): void {
  if (event.altKey || event.ctrlKey || event.metaKey) return;
  const group = event.currentTarget;
  if (!(group instanceof HTMLElement)) return;
  const radios = [...group.querySelectorAll<HTMLElement>('[role="radio"]')];
  const current = radios.findIndex((radio) => radio === event.target || radio.contains(event.target as Node | null));
  if (current < 0) return;
  const target = radioKeyTarget(event.key, current, radios.map(isDisabled));
  if (target === null) return;
  event.preventDefault();
  if (target === current) return;
  const radio = radios[target];
  radio?.focus();
  radio?.click();
}
