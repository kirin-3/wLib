// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { ref } from "vue";

import {
  handleModalKeydown,
  modalStack,
  type ModalStackEntry,
} from "./modalKeyboard.ts";

test("handleModalKeydown only invokes onEscape on the topmost modal", () => {
  modalStack.length = 0;

  let modal1EscapeCalled = false;
  let modal2EscapeCalled = false;

  const mockDiv1 = {
    querySelectorAll: () => [],
    contains: () => true,
    focus: () => {},
  } as unknown as HTMLElement;

  const mockDiv2 = {
    querySelectorAll: () => [],
    contains: () => true,
    focus: () => {},
  } as unknown as HTMLElement;

  const entry1: ModalStackEntry = {
    container: ref(mockDiv1),
    onEscape: () => {
      modal1EscapeCalled = true;
    },
    opener: null,
  };

  const entry2: ModalStackEntry = {
    container: ref(mockDiv2),
    onEscape: () => {
      modal2EscapeCalled = true;
    },
    opener: null,
  };

  modalStack.push(entry1);
  modalStack.push(entry2);

  let defaultPrevented = false;
  const escEvent = {
    key: "Escape",
    preventDefault: () => {
      defaultPrevented = true;
    },
  } as KeyboardEvent;

  handleModalKeydown(escEvent);

  assert.equal(defaultPrevented, true);
  assert.equal(modal2EscapeCalled, true, "Topmost modal onEscape should be called");
  assert.equal(modal1EscapeCalled, false, "Lower modal onEscape must NOT be called");

  // Pop modal 2 and dispatch Escape again
  modalStack.pop();
  defaultPrevented = false;
  handleModalKeydown(escEvent);

  assert.equal(defaultPrevented, true);
  assert.equal(modal1EscapeCalled, true, "Lower modal onEscape should now be called when it becomes top");

  modalStack.length = 0;
});

test("handleModalKeydown traps Tab key within the top modal", () => {
  modalStack.length = 0;

  let focusedElement: HTMLElement | null = null;
  const button1 = {
    getClientRects: () => [{ width: 10, height: 10 }],
    focus: () => {
      focusedElement = button1;
    },
  } as unknown as HTMLElement;

  const button2 = {
    getClientRects: () => [{ width: 10, height: 10 }],
    focus: () => {
      focusedElement = button2;
    },
  } as unknown as HTMLElement;

  const containerEl = {
    querySelectorAll: () => [button1, button2],
    contains: (el: unknown) => el === button1 || el === button2,
    focus: () => {},
  } as unknown as HTMLElement;

  modalStack.push({
    container: ref(containerEl),
    onEscape: () => {},
    opener: null,
  });

  // Mock global document activeElement
  const origDocument = globalThis.document;
  globalThis.document = {
    activeElement: button2,
  } as unknown as Document;

  try {
    let prevented = false;
    // Pressing Tab from last button (button2) should loop to first button (button1)
    handleModalKeydown({
      key: "Tab",
      shiftKey: false,
      preventDefault: () => {
        prevented = true;
      },
    } as KeyboardEvent);

    assert.equal(prevented, true);
    assert.equal(focusedElement, button1, "Tab from last element should wrap to first element");

    // Pressing Shift+Tab from first button (button1) should loop to last button (button2)
    (globalThis.document as unknown as { activeElement: HTMLElement }).activeElement = button1;
    prevented = false;
    handleModalKeydown({
      key: "Tab",
      shiftKey: true,
      preventDefault: () => {
        prevented = true;
      },
    } as KeyboardEvent);

    assert.equal(prevented, true);
    assert.equal(focusedElement, button2, "Shift+Tab from first element should wrap to last element");
  } finally {
    globalThis.document = origDocument;
    modalStack.length = 0;
  }
});
