// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

// Shared sidebar layout state between App.vue and LibraryView.vue.
// LibraryView reports whether its filters pane is open beside the games; App.vue
// collapses the sidebar while it is, and in narrow windows, unless the user
// expanded it again (the override). Neither ref is saved: only the user's own
// collapse choice is.
export const libraryFiltersPaneOpen = ref(false);
export const navAutoOverride = ref(false);

export const LIBRARY_ROUTE_NAME = "library";

/** Below this window width (CSS px) the sidebar collapses to its icon rail on every page. */
export const NAV_AUTO_COLLAPSE_BELOW = 900;

/**
 * From this window width (CSS px) the Library filters pane sits beside the games.
 * The icon rail (64 px) and the pane (256 px) leave about 760 px, which keeps the
 * toolbar on one row and three medium cards per row. Narrower windows show the
 * pane as a drawer over the games instead.
 */
export const LIBRARY_FILTERS_INLINE_MIN = 1080;

export type FiltersPaneMode = "inline" | "drawer";

export const filtersPaneMode = (windowWidth: number): FiltersPaneMode =>
  windowWidth >= LIBRARY_FILTERS_INLINE_MIN ? "inline" : "drawer";

/** Whether the app wants the sidebar collapsed, before the user's override. */
export const wantsNavAutoCollapse = (
  routeName: unknown,
  paneOpen: boolean,
  windowWidth = Number.POSITIVE_INFINITY,
): boolean => windowWidth < NAV_AUTO_COLLAPSE_BELOW || (routeName === LIBRARY_ROUTE_NAME && paneOpen);

export const isNavAutoCollapsed = (
  routeName: unknown,
  paneOpen: boolean,
  override: boolean,
  windowWidth = Number.POSITIVE_INFINITY,
): boolean => wantsNavAutoCollapse(routeName, paneOpen, windowWidth) && !override;
