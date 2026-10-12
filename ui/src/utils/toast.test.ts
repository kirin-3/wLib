// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";

import { dismissToast, notify, toasts } from "./toast.ts";

test("action toasts keep their action until dismissed", () => {
  const before = toasts.length;
  let ran = false;
  notify("Eternum 0.8 is waiting in Downloads.", "info", "Update ready", {
    label: "Install update",
    run: () => {
      ran = true;
    },
  });
  const toast = toasts[toasts.length - 1];
  assert.equal(toasts.length, before + 1);
  assert.equal(toast?.action?.label, "Install update");
  toast?.action?.run();
  assert.equal(ran, true);
  // Plain toasts carry no action.
  notify("Plain message.", "info");
  assert.equal(toasts[toasts.length - 1]?.action, undefined);
  if (toast) dismissToast(toast.id);
});

test("dismissToast removes only the matching toast", () => {
  notify("First", "info");
  notify("Second", "info");
  const first = toasts[0];
  const second = toasts[toasts.length - 1];
  if (first) dismissToast(first.id);
  assert.ok(!toasts.some((toast) => toast.id === first?.id));
  assert.ok(toasts.some((toast) => toast.id === second?.id));
  if (second) dismissToast(second.id);
});
