// SPDX-License-Identifier: GPL-3.0-or-later
import { nextTick, onBeforeUnmount, watch, type Ref } from "vue";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Esc calls onEscape, Tab cycles inside the dialog, and focus returns to the opener on close.
export const useModalKeyboard = (
  container: Ref<HTMLElement | null>,
  isOpen: () => boolean,
  onEscape: () => void,
): void => {
  let opener: HTMLElement | null = null;

  const onKeydown = (event: KeyboardEvent) => {
    const root = container.value;
    if (!root) return;
    if (event.key === "Escape") {
      event.preventDefault();
      onEscape();
      return;
    }
    if (event.key !== "Tab") return;

    const items = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (element) => element.offsetParent !== null,
    );
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) return;
    const active = document.activeElement;
    const outside = !root.contains(active);
    if (event.shiftKey && (active === first || active === root || outside)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || outside)) {
      event.preventDefault();
      first.focus();
    }
  };

  watch(
    isOpen,
    async (open) => {
      if (open) {
        opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        document.addEventListener("keydown", onKeydown);
        await nextTick();
        container.value?.focus();
      } else {
        document.removeEventListener("keydown", onKeydown);
        opener?.focus();
        opener = null;
      }
    },
    { immediate: true },
  );

  onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown));
};
