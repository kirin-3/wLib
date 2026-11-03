// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

export const rangeIds = (visible: readonly number[], anchor: number | null, target: number): number[] => {
  const end = visible.indexOf(target);
  const start = anchor === null ? -1 : visible.indexOf(anchor);
  if (end < 0) return [];
  return visible.slice(start < 0 ? end : Math.min(start, end), start < 0 ? end + 1 : Math.max(start, end) + 1);
};
export const pruneIds = (selected: ReadonlySet<number>, visible: readonly number[]): Set<number> => {
  const allowed = new Set(visible);
  return new Set([...selected].filter((id) => allowed.has(id)));
};
export function useLibrarySelection() {
  const selectedIds = ref(new Set<number>());
  const anchorId = ref<number | null>(null);
  const toggle = (id: number) => {
    if (selectedIds.value.has(id)) selectedIds.value.delete(id); else selectedIds.value.add(id);
    anchorId.value = id;
  };
  const selectRange = (visible: readonly number[], id: number) => {
    for (const entry of rangeIds(visible, anchorId.value, id)) selectedIds.value.add(entry);
    if (anchorId.value === null) anchorId.value = id;
  };
  const selectAll = (visible: readonly number[]) => { selectedIds.value = new Set(visible); };
  const clear = () => { selectedIds.value.clear(); anchorId.value = null; };
  const prune = (visible: readonly number[]) => {
    selectedIds.value = pruneIds(selectedIds.value, visible);
    if (anchorId.value !== null && !visible.includes(anchorId.value)) anchorId.value = null;
  };
  return { selectedIds, anchorId, toggle, selectRange, selectAll, clear, prune };
}
