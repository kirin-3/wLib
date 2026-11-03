// SPDX-License-Identifier: GPL-3.0-or-later
import assert from "node:assert/strict";
import test from "node:test";
import { rangeIds, pruneIds, useLibrarySelection } from "../composables/useLibrarySelection.ts";
test("range follows visible order and pruning keeps only visible ids", () => {
  assert.deepEqual(rangeIds([4, 3, 2, 1], 3, 1), [3, 2, 1]);
  assert.deepEqual(rangeIds([4, 3], 9, 4), [4]);
  assert.deepEqual([...pruneIds(new Set([1, 2, 3]), [2, 3, 4])], [2, 3]);
  const state = useLibrarySelection();
  state.toggle(3); state.selectRange([1, 2, 3], 1);
  assert.deepEqual([...state.selectedIds.value].sort(), [1, 2, 3]);
  state.prune([2]);
  assert.deepEqual([...state.selectedIds.value], [2]);
});
