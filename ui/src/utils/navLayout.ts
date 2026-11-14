// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

// Shared sidebar layout state between App.vue and LibraryView.vue.
// LibraryView reports whether its filters pane is open; App.vue collapses the
// sidebar while it is, unless the user expanded it again (the override).
// Neither ref is saved: only the user's own collapse choice is.
export const libraryFiltersPaneOpen = ref(false);
export const navAutoOverride = ref(false);

export const LIBRARY_ROUTE_NAME = "library";

export const isNavAutoCollapsed = (
  routeName: unknown,
  paneOpen: boolean,
  override: boolean,
): boolean => routeName === LIBRARY_ROUTE_NAME && paneOpen && !override;
