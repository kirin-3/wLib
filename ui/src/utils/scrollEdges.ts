// SPDX-License-Identifier: GPL-3.0-or-later

/** The scroll numbers of a scroll container (an HTMLElement fits). */
export interface ScrollMetrics {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

/** Which edges of a scroll container have more content past them. */
export interface ScrollEdges {
  /** The content is taller than the container. */
  scrollable: boolean;
  /** There is content above the visible part. */
  top: boolean;
  /** There is content below the visible part. */
  bottom: boolean;
}

export const NO_SCROLL_EDGES: ScrollEdges = Object.freeze({ scrollable: false, top: false, bottom: false });

/**
 * Reads the scroll edges. `threshold` (px) ignores sub-pixel rounding, so a
 * container scrolled to the end does not report one stray pixel below.
 */
export function scrollEdges(metrics: ScrollMetrics, threshold = 2): ScrollEdges {
  const overflow = metrics.scrollHeight - metrics.clientHeight;
  if (!(overflow > threshold)) return NO_SCROLL_EDGES;
  const top = Math.max(0, metrics.scrollTop);
  return {
    scrollable: true,
    top: top > threshold,
    bottom: overflow - top > threshold,
  };
}

export const sameScrollEdges = (a: ScrollEdges, b: ScrollEdges): boolean =>
  a.scrollable === b.scrollable && a.top === b.top && a.bottom === b.bottom;
