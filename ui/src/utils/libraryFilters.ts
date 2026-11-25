// SPDX-License-Identifier: GPL-3.0-or-later
import { THREAD_STATE_FILTER_LABELS, type ThreadStateFilter } from './libraryViewState.ts';

export type FilterKind = 'status' | 'install' | 'rating' | 'thread' | 'source' | 'engine' | 'tag' | 'collection';
// `excluded` marks an "Excluded ..." chip, so removing it clears the excluded value.
export interface FilterChip { kind: FilterKind; value: string; label: string; key: string; excluded: boolean }
export interface FilterChipInput {
  statuses: string[]; install: string; minRating?: string; sources: string[]; engines: string[]; tags: string[]; collection: string;
  threadStates?: ThreadStateFilter[];
  excludedSources?: string[]; excludedEngines?: string[]; excludedTags?: string[];
  statusLabels: Record<string, string>; sourceLabels: Record<string, string>;
}
export function activeFilterChips(input: FilterChipInput): FilterChip[] {
  const chips: FilterChip[] = [];
  const add = (kind: FilterKind, title: string, value: string, display = value) =>
    chips.push({ kind, value, label: `${title}: ${display}`, key: `${kind}:${value}`, excluded: false });
  const addNot = (kind: FilterKind, title: string, value: string, display = value) =>
    chips.push({ kind, value, label: `Excluded ${title.toLowerCase()}: ${display}`, key: `${kind}:not:${value}`, excluded: true });
  for (const value of input.statuses) add('status', 'Status', value, input.statusLabels[value.toLowerCase()] || value);
  if (input.install) add('install', 'Install state', input.install, input.install === 'installed' ? 'Installed' : 'Wishlist');
  if (input.minRating) add('rating', 'Weighted rating', input.minRating, `${input.minRating}+`);
  for (const value of input.threadStates ?? []) add('thread', 'Thread state', value, THREAD_STATE_FILTER_LABELS[value]);
  for (const value of input.sources) add('source', 'Source', value, input.sourceLabels[value] || value);
  for (const value of input.excludedSources ?? []) addNot('source', 'Source', value, input.sourceLabels[value] || value);
  for (const value of input.engines) add('engine', 'Engine', value);
  for (const value of input.excludedEngines ?? []) addNot('engine', 'Engine', value);
  for (const value of input.tags) add('tag', 'Tag', value);
  for (const value of input.excludedTags ?? []) addNot('tag', 'Tag', value);
  if (input.collection !== 'All') add('collection', 'Quick filter', input.collection);
  return chips;
}
export function removeFilterValue(values: string[], value: string): string[] {
  return values.filter(entry => entry.toLowerCase() !== value.toLowerCase());
}
