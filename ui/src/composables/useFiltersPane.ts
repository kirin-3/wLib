// SPDX-License-Identifier: GPL-3.0-or-later
import { computed, ref, watch, type Ref } from "vue";
import { filtersPaneMode } from "../utils/navLayout";
import { useModalKeyboard } from "../utils/modalKeyboard";
import { useWindowCssWidth } from "../utils/windowWidth";

/**
 * A filters pane that sits beside the content in wide windows and is a drawer
 * over it in narrow ones (the breakpoint is filtersPaneMode in
 * utils/navLayout.ts). `inlineOpen` is the page's own saved open state for the
 * inline pane. The drawer always starts closed and is not saved.
 *
 * While the drawer is open, focus moves into `pane`, Tab stays in it, Escape
 * closes it, and focus goes back to the control that opened it.
 *
 * Discover uses this. The Library has the same logic inline in LibraryView.vue.
 */
export const useFiltersPane = (pane: Readonly<Ref<HTMLElement | null>>, inlineOpen: Ref<boolean>) => {
  const windowCssWidth = useWindowCssWidth();
  const asDrawer = computed(() => filtersPaneMode(windowCssWidth.value) === "drawer");
  const drawerOpen = ref(false);
  /** The pane is on screen: the open drawer, or the open inline pane. */
  const shown = computed(() => (asDrawer.value ? drawerOpen.value : inlineOpen.value));
  const closeDrawer = () => { drawerOpen.value = false; };
  useModalKeyboard(pane, () => asDrawer.value && drawerOpen.value, closeDrawer);
  const toggle = () => {
    if (asDrawer.value) drawerOpen.value = !drawerOpen.value;
    else inlineOpen.value = !inlineOpen.value;
  };
  watch(asDrawer, (drawer) => {
    // A drawer that is open when the window widens stays open as the pane.
    if (!drawer && drawerOpen.value) inlineOpen.value = true;
    drawerOpen.value = false;
  });
  return { asDrawer, drawerOpen, shown, closeDrawer, toggle };
};
