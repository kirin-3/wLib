// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { chooseScope, dispatchController, defaultHints, resolveHints, handleScopedBack } from "./controllerActions.ts";
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
