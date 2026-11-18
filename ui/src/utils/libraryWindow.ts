// SPDX-License-Identifier: GPL-3.0-or-later
// The library keeps only the cards near the visible area in the page. These
// helpers turn the measured grid (columns, row pitch) and the visible part of
// the cards into the range of games to render and the empty space around it.

/** How the cards are laid out, measured from the page. */
export interface WindowMetrics {
  columns: number;
  /** Row height plus the gap to the next row, in CSS pixels. */
  pitch: number;
}

/** The visible part of the cards, relative to the top of the first row. */
export interface WindowView {
  top: number;
  height: number;
}

export interface RenderWindow {
  /** First game index in the page. Always the start of a row. */
  start: number;
  /** One past the last game index in the page. */
  end: number;
  /** Empty space for the rows before `start`. */
  padTop: number;
  /** Empty space for the rows after `end`. */
  padBottom: number;
  /** The first game on screen: the controller's starting point. */
  primary: number;
}

/** Cards in the page before the first measurement; fills any common screen. */
export const INITIAL_WINDOW = 48;
/** Rows kept above and below the visible area, at least. */
export const MIN_BUFFER_ROWS = 2;
/**
 * The focused card keeps its rows in the page while it is at most this many
 * buffers from the visible area, or at any distance while focus is moving
 * (`focusMoving`). A held D-pad moves focus faster than the smooth scroll
 * follows, so the next row must exist below the focus.
 */
export const FOCUS_REACH_BUFFERS = 3;
/** How long after a focus move the focused card is kept at any distance. */
export const FOCUS_HOLD_MS = 1000;

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/**
 * The games to render: the visible rows plus one screen height (at least
 * `MIN_BUFFER_ROWS` rows) above and below, and the rows around the focused
 * game (`focus`, an index) when it is near the visible area.
 */
export const renderWindow = (
  total: number,
  metrics: WindowMetrics | null,
  view: WindowView,
  focus: number | null = null,
  focusMoving = false,
): RenderWindow => {
  if (total <= 0) return { start: 0, end: 0, padTop: 0, padBottom: 0, primary: 0 };
  if (!metrics || !(metrics.pitch > 0) || !(metrics.columns >= 1))
    return { start: 0, end: Math.min(total, INITIAL_WINDOW), padTop: 0, padBottom: 0, primary: 0 };
  const columns = Math.floor(metrics.columns);
  const { pitch } = metrics;
  const rows = Math.ceil(total / columns);
  const top = Math.max(0, view.top);
  const height = Math.max(0, view.height);
  const buffer = Math.max(MIN_BUFFER_ROWS, Math.ceil(height / pitch));
  const firstVisible = clamp(Math.floor(top / pitch), 0, rows - 1);
  const lastVisible = clamp(Math.ceil((top + height) / pitch), firstVisible + 1, rows);
  let firstRow = firstVisible - buffer;
  let lastRow = lastVisible + buffer;
  if (focus !== null && focus >= 0 && focus < total) {
    const focusRow = Math.floor(focus / columns);
    const reach = buffer * FOCUS_REACH_BUFFERS;
    if (focusMoving || (focusRow >= firstVisible - reach && focusRow < lastVisible + reach)) {
      firstRow = Math.min(firstRow, focusRow - MIN_BUFFER_ROWS);
      lastRow = Math.max(lastRow, focusRow + 1 + MIN_BUFFER_ROWS);
    }
  }
  firstRow = Math.max(0, firstRow);
  lastRow = Math.min(rows, lastRow);
  // The first row that shows at least half of itself.
  const primaryRow = clamp(Math.round(top / pitch), firstRow, lastRow - 1);
  return {
    start: firstRow * columns,
    end: Math.min(total, lastRow * columns),
    padTop: firstRow * pitch,
    padBottom: (rows - lastRow) * pitch,
    primary: Math.min(total - 1, primaryRow * columns),
  };
};

export const sameWindow = (a: RenderWindow, b: RenderWindow): boolean =>
  a.start === b.start && a.end === b.end && a.padTop === b.padTop && a.padBottom === b.padBottom && a.primary === b.primary;

/**
 * Keep the old pitch when the new one is within 1 px, so rounding in the
 * measurement cannot make the window move back and forth.
 */
export const steadyMetrics = (previous: WindowMetrics | null, next: WindowMetrics): WindowMetrics =>
  previous && previous.columns === next.columns && Math.abs(previous.pitch - next.pitch) <= 1 ? previous : next;

/** Column count from a computed `grid-template-columns` value ("220px 220px …"). */
export const gridColumnCount = (template: string): number => {
  const tracks = template.trim() === "none" ? [] : template.trim().split(/\s+/).filter(Boolean);
  return Math.max(1, tracks.length);
};

/**
 * Row pitch from the top of the first card of the first and of the last
 * rendered row. With one row it is that row's height plus the row gap.
 */
export const measuredPitch = (firstTop: number, lastRowTop: number, rows: number, firstHeight: number, rowGap: number): number =>
  rows >= 2 ? (lastRowTop - firstTop) / (rows - 1) : rows === 1 ? firstHeight + rowGap : 0;
