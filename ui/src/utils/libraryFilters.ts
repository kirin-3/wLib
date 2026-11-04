// SPDX-License-Identifier: GPL-3.0-or-later
export type FilterKind = 'status' | 'install' | 'source' | 'engine' | 'tag' | 'collection';
export interface FilterChip { kind: FilterKind; value: string; label: string; key: string }
export interface FilterChipInput {
  statuses: string[]; install: string; sources: string[]; engines: string[]; tags: string[]; collection: string;
  statusLabels: Record<string, string>; sourceLabels: Record<string, string>;
}
export function activeFilterChips(input: FilterChipInput): FilterChip[] {
  const chips: FilterChip[] = [];
  const add = (kind: FilterKind, title: string, value: string, display = value) => chips.push({ kind, value, label: `${title}: ${display}`, key: `${kind}:${value}` });
  for (const value of input.statuses) add('status', 'Status', value, input.statusLabels[value.toLowerCase()] || value);
  if (input.install) add('install', 'Install state', input.install, input.install === 'installed' ? 'Installed' : 'Wishlist');
  for (const value of input.sources) add('source', 'Source', value, input.sourceLabels[value] || value);
  for (const value of input.engines) add('engine', 'Engine', value);
  for (const value of input.tags) add('tag', 'Tag', value);
  if (input.collection !== 'All') add('collection', 'Collection', input.collection);
  return chips;
}
export function removeFilterValue(values: string[], value: string): string[] {
  return values.filter(entry => entry.toLowerCase() !== value.toLowerCase());
}
