// SPDX-License-Identifier: GPL-3.0-or-later
// The game details window has two save models. Ratings, tags, favorite and
// play status save as the user changes them. The fields below wait for
// "Save changes"; this file says where each one lives, so the window can mark
// the tabs that hold unsaved edits and say what Save changes covers.

/** A place in the window whose edits wait for Save changes. */
export type PendingArea = "Launch" | "Info" | "Notes" | "Collections";

/** Display order: the tab order, then the header's collection chips. */
export const PENDING_AREA_ORDER: readonly PendingArea[] = ["Launch", "Info", "Notes", "Collections"];

/** Where each field that waits for Save changes is edited. */
export const PENDING_FIELD_AREA = {
  gamemode: "Launch",
  mangohud: "Launch",
  gamescope: "Launch",
  gamescope_resolution: "Launch",
  gamescope_scaler: "Launch",
  gamescope_filter: "Launch",
  gamescope_args: "Launch",
  html_player: "Launch",
  sandbox_enabled: "Launch",
  block_network: "Launch",
  exe_path: "Launch",
  command_line_args: "Launch",
  run_japanese_locale: "Launch",
  run_wayland: "Launch",
  auto_inject_ce: "Launch",
  use_custom_prefix: "Launch",
  custom_prefix: "Launch",
  proton_version: "Launch",
  launch_mode: "Launch",
  title: "Info",
  f95_url: "Info",
  lc_url: "Info",
  lc_mode: "Info",
  version: "Info",
  developer: "Info",
  overview: "Info",
  original_title: "Info",
  aliases: "Info",
  language: "Info",
  external_links: "Info",
  finished_version: "Info",
  cover_image: "Info",
  engine: "Info",
  progress: "Notes",
  note_links: "Notes",
  collections: "Collections",
} as const satisfies Record<string, PendingArea>;

export type PendingField = keyof typeof PENDING_FIELD_AREA;

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b);

/** The areas with a field that differs from its saved value, in display order. */
export function pendingAreas(
  current: Partial<Record<PendingField, unknown>>,
  saved: Partial<Record<PendingField, unknown>>,
): PendingArea[] {
  const dirty = new Set<PendingArea>();
  for (const [field, area] of Object.entries(PENDING_FIELD_AREA) as [PendingField, PendingArea][]) {
    if (!same(current[field], saved[field])) dirty.add(area);
  }
  return PENDING_AREA_ORDER.filter((area) => dirty.has(area));
}

const joinAnd = (items: readonly string[]): string =>
  items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;

/**
 * The footer text next to Save changes. With no named area (an instant save
 * that failed or is still running) it stays general.
 */
export function unsavedSummary(areas: readonly PendingArea[]): string {
  if (!areas.length) return "Unsaved changes";
  const names = areas.map((area) => (area === "Collections" ? "collections" : area));
  return `Unsaved changes in ${joinAnd(names)}`;
}
