// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { createRenderer, effectScope, h } from "vue";

import { useEventListener } from "./useEventListener.ts";

for (const options of [undefined, true, { capture: true }]) {
  test(`useEventListener follows its effect scope with options ${JSON.stringify(options)}`, () => {
    const target = new EventTarget();
    const scope = effectScope();
    let calls = 0;
    scope.run(() => useEventListener(target, "change", () => { calls++; }, options));

    target.dispatchEvent(new Event("change"));
    assert.equal(calls, 1);
    scope.stop();
    target.dispatchEvent(new Event("change"));
    assert.equal(calls, 1);
  });
}

test("useEventListener can stop early without removing a later registration", () => {
  const target = new EventTarget();
  const scope = effectScope();
  let calls = 0;
  const listener = () => { calls++; };
  const stop = scope.run(() => useEventListener(target, "change", listener))!;

  stop();
  target.dispatchEvent(new Event("change"));
  assert.equal(calls, 0);

  target.addEventListener("change", listener);
  stop();
  scope.stop();
  target.dispatchEvent(new Event("change"));
  assert.equal(calls, 1);
});

test("useEventListener listens only while its component is mounted", () => {
  const target = new EventTarget();
  const renderer = createRenderer({
    createElement: () => ({}),
    createText: () => ({}),
    createComment: () => ({}),
    insert() {},
    remove() {},
    patchProp() {},
    setText() {},
    setElementText() {},
    parentNode: () => null,
    nextSibling: () => null,
  });
  let calls = 0;
  const app = renderer.createApp({
    setup() {
      useEventListener(target, "change", () => { calls++; });
      const stop = useEventListener(target, "change", () => assert.fail("Stopped in setup"));
      stop();
      target.dispatchEvent(new Event("change"));
      assert.equal(calls, 0);
      return () => h("div");
    },
  });

  app.mount({});
  target.dispatchEvent(new Event("change"));
  assert.equal(calls, 1);
  app.unmount();
  target.dispatchEvent(new Event("change"));
  assert.equal(calls, 1);
});
