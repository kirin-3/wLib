// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from "node:test";
import assert from "node:assert/strict";
import { ref } from "vue";
import { useFieldAutosave } from "./useFieldAutosave.ts";

test("skips unchanged values and blocks invalid values", async () => {
  const value = ref("old");
  const calls: string[] = [];
  const field = useFieldAutosave({ value, save: async v => { calls.push(v); }, validate: v => v ? undefined : "Required" });
  await field.commit();
  value.value = "";
  await field.commit();
  assert.deepEqual(calls, []);
  assert.equal(field.error.value, "Required");
});

test("serializes commits and retries failures without changing the typed value", async () => {
  const value = ref("old");
  const calls: string[] = [];
  let release!: () => void;
  let fail = true;
  const field = useFieldAutosave({ value, save: async v => {
    calls.push(v);
    if (v === "first") await new Promise<void>(resolve => { release = resolve; });
    if (v === "second" && fail) throw new Error("failed");
  } });
  value.value = "first";
  const first = field.commit();
  value.value = "second";
  const second = field.commit();
  assert.deepEqual(calls, ["first"]);
  release();
  await Promise.all([first, second]);
  assert.deepEqual(calls, ["first", "second"]);
  assert.equal(field.state.value, "error");
  assert.equal(value.value, "second");
  fail = false;
  await field.commit();
  assert.equal(field.state.value, "saved");
  assert.deepEqual(calls, ["first", "second", "second"]);
});

test("uses the normalized persisted value as the new baseline", async () => {
  const value = ref("old");
  let calls = 0;
  const field = useFieldAutosave({ value, save: async () => { calls++; return "normalized"; } });
  value.value = "submitted";
  await field.commit();
  value.value = "normalized";
  await field.commit();
  assert.equal(calls, 1);
});
