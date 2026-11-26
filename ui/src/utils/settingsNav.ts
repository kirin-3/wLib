// SPDX-License-Identifier: GPL-3.0-or-later
// Which Settings section the section list marks as current.

export interface SectionTop {
  id: string;
  /** The section's top edge, in the same coordinates as the probe line. */
  top: number;
}

export interface ScrollBox {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** Distance from the end that still counts as "scrolled to the bottom". */
export const BOTTOM_SLACK_PX = 2;

/** True when the box can scroll and is scrolled to its end. */
export const atScrollEnd = (box: ScrollBox) =>
  box.scrollHeight - box.clientHeight > BOTTOM_SLACK_PX &&
  box.scrollHeight - box.clientHeight - box.scrollTop <= BOTTOM_SLACK_PX;

/**
 * The last section whose top is at or above the probe line. At the end of the
 * page the last section is current: short sections at the end never reach
 * the probe line. Returns "" when there are no sections.
 */
export const pickActiveSection = (sections: readonly SectionTop[], probe: number, box: ScrollBox): string => {
  const first = sections[0], last = sections[sections.length - 1];
  if (!first || !last) return "";
  if (atScrollEnd(box)) return last.id;
  let active = first.id;
  for (const section of sections) if (section.top <= probe) active = section.id;
  return active;
};
