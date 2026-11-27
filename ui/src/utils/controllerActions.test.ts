// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { chooseScope, dispatchController, defaultHints, resolveHints, handleScopedBack, scrollContainer } from "./controllerActions.ts";
import { modalStack } from "./modalKeyboard.ts";
class ElementStub {
  parent: ElementStub | null = null;
  events: string[] = [];
  contains(other: ElementStub | null): boolean { for (let p = other; p; p = p.parent) if (p === this) return true; return false; }
  matches(): boolean { return false; }
  dispatchEvent(e: { key: string }) { this.events.push(e.key); return true; }
}
test("innermost registered scope wins", () => {
  const root = new ElementStub(), inner = new ElementStub(), child = new ElementStub(); inner.parent = root; child.parent = inner;
  Object.assign(globalThis, { document: { body: {} } });
  const entry = (value: ElementStub) => ({ scope: { value: value as unknown as HTMLElement }, handlers: {} });
  const a = entry(root), b = entry(inner);
  assert.equal(chooseScope([a, b], child as unknown as HTMLElement, null), b);
  assert.deepEqual(defaultHints.map(h => h.button), ["A", "B"]);
  assert.equal(resolveHints(undefined), defaultHints);
  const hints = [{ button: "X", label: "Play" }];
  assert.equal(resolveHints({ ...b, handlers: { hints } }), hints);
  const modal = new ElementStub(), control = new ElementStub(); modal.parent = root; control.parent = modal;
  assert.equal(chooseScope([a], control as unknown as HTMLElement, modal as unknown as HTMLElement), undefined);
  const modalEntry = entry(modal);
  assert.equal(chooseScope([a, modalEntry], control as unknown as HTMLElement, modal as unknown as HTMLElement), modalEntry);
});
test("back closes a modal first and otherwise only navigates with history", () => {
  const root = new ElementStub(), child = new ElementStub(); child.parent = root;
  Object.assign(globalThis, { HTMLElement: ElementStub, document: { body: root, activeElement: child, querySelectorAll: () => [], querySelector: () => root }, history: { state: { back: "/" } }, KeyboardEvent: class { key: string; constructor(_type: string, options: { key: string }) { this.key = options.key; } } });
  modalStack.push({ container: { value: root as unknown as HTMLElement }, onEscape() {}, opener: null });
  let back = 0;
  dispatchController({ type: "back" }, () => back++);
  assert.deepEqual(child.events, ["Escape"]); assert.equal(back, 0);
  modalStack.pop();
  dispatchController({ type: "back" }, () => back++); assert.equal(back, 1);
  history.state.back = null;
  dispatchController({ type: "back" }, () => back++); assert.equal(back, 1);
  dispatchController({ type: "menu" }, () => back++); assert.deepEqual(child.events, ["Escape", "ContextMenu"]);
});


test("scoped back reports whether the handler consumed it", () => {
  const scope = { scope: { value: null }, handlers: { back: () => true } };
  assert.equal(handleScopedBack(scope), true);
  assert.equal(handleScopedBack({ ...scope, handlers: { back: () => false } }), false);
  assert.equal(handleScopedBack(undefined), false);
});
test("scroll falls back to the scope's marked scroll area, not the page behind it", () => {
  type Box = { parentElement: Box | null; overflowY: string; scrollHeight: number; clientHeight: number };
  const box = (overflowY: string, scrollHeight: number, clientHeight: number, parentElement: Box | null = null): Box => ({ parentElement, overflowY, scrollHeight, clientHeight });
  const page = box("auto", 2000, 800);
  const dialog = box("hidden", 600, 600), body = box("auto", 900, 400, dialog), footerButton = box("visible", 40, 40, dialog);
  const root = { querySelector: (selector: string) => (selector === "[data-controller-scroll]" ? body : null) };
  Object.assign(globalThis, {
    getComputedStyle: (node: Box) => ({ overflowY: node.overflowY }),
    document: { querySelector: (selector: string) => (selector === "main" ? page : null), documentElement: page },
  });
  const pick = (el: Box, scope: unknown) => scrollContainer(el as unknown as HTMLElement, scope as HTMLElement | null) as unknown as Box;
  // Focus inside the scroll area uses the nearest scrolling parent.
  const item = box("visible", 30, 30, body);
  assert.equal(pick(item, root), body);
  // A footer button has no scrolling parent: the marked body scrolls.
  assert.equal(pick(footerButton, root), body);
  // Without a scope, or when the marked area fits, the page scrolls as before.
  assert.equal(pick(footerButton, null), page);
  body.scrollHeight = 400;
  assert.equal(pick(footerButton, root), page);
});
