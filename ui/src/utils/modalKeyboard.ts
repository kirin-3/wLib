// SPDX-License-Identifier: GPL-3.0-or-later
import { nextTick, onBeforeUnmount, toRaw, watch, type Ref } from "vue";

export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export interface ModalStackEntry {
  container: Ref<HTMLElement | null>;
  onEscape: () => void;
  opener: HTMLElement | null;
}

export const modalStack: ModalStackEntry[] = [];

let keydownAttached = false;

export const handleModalKeydown = (event: KeyboardEvent): void => {
  if (modalStack.length === 0) return;
  const current = modalStack[modalStack.length - 1];
  if (!current) return;
  const root = current.container.value;
  if (!root) return;

  if (event.key === "Escape") {
    event.preventDefault();
    current.onEscape();
    return;
  }
  if (event.key !== "Tab") return;

  const items = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (element) => element.getClientRects().length > 0,
  );
  const first = items[0];
  const last = items[items.length - 1];
  if (!first || !last) return;
  const active = typeof document !== "undefined" ? document.activeElement : null;
  const outside = !root.contains(active);
  const rawActive = active ? toRaw(active) : null;
  const isRoot = active === root || (rawActive !== null && rawActive === toRaw(root));
  const isFirst = active === first || (rawActive !== null && rawActive === toRaw(first));
  const isLast = active === last || (rawActive !== null && rawActive === toRaw(last));
  if (event.shiftKey && (isFirst || isRoot || outside)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (isLast || isRoot || outside)) {
    event.preventDefault();
    first.focus();
  }
};

const ensureKeydownListener = () => {
  if (!keydownAttached && typeof document !== "undefined") {
    document.addEventListener("keydown", handleModalKeydown);
    keydownAttached = true;
  }
};

const cleanupKeydownListener = () => {
  if (modalStack.length === 0 && keydownAttached && typeof document !== "undefined") {
    document.removeEventListener("keydown", handleModalKeydown);
    keydownAttached = false;
  }
};

// Esc calls onEscape, Tab cycles inside the dialog, and focus returns to the opener on close.
export const useModalKeyboard = (
  container: Ref<HTMLElement | null>,
  isOpen: () => boolean,
  onEscape: () => void,
): void => {
  let entry: ModalStackEntry | null = null;

  const close = () => {
    if (!entry) return;
    const index = modalStack.indexOf(entry);
    if (index !== -1) {
      modalStack.splice(index, 1);
    }
    const savedOpener = entry.opener;
    entry = null;
    cleanupKeydownListener();
    savedOpener?.focus();
  };

  watch(
    isOpen,
    async (open) => {
      if (open) {
        if (!entry) {
          entry = {
            container,
            onEscape,
            opener:
              typeof document !== "undefined" &&
              document.activeElement instanceof HTMLElement
                ? document.activeElement
                : null,
          };
          modalStack.push(entry);
          ensureKeydownListener();
        }
        await nextTick();
        if (isOpen()) {
          container.value?.focus();
        }
      } else {
        close();
      }
    },
    { immediate: true },
  );

  onBeforeUnmount(() => {
    close();
  });
};
