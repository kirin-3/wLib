// SPDX-License-Identifier: GPL-3.0-or-later
import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { activeFilterChips, removeFilterValue } from './libraryFilters.ts';

test('builds named chips for every filter and removes a single value', () => {
  const chips = activeFilterChips({ statuses: ['playing'], install: 'wishlist', sources: ['f95'], engines: ["Ren'Py"], tags: ['sandbox', 'romance'], collection: 'Favorites', statusLabels: { playing: 'Playing' }, sourceLabels: { f95: 'F95Zone' } });
  assert.deepEqual(chips.map(c => c.label), ['Status: Playing', 'Install state: Wishlist', 'Source: F95Zone', "Engine: Ren'Py", 'Tag: sandbox', 'Tag: romance', 'Collection: Favorites']);
  assert.deepEqual(removeFilterValue(['sandbox', 'romance'], 'sandbox'), ['romance']);
  assert.deepEqual(activeFilterChips({ statuses: [], install: '', sources: [], engines: [], tags: [], collection: 'All', statusLabels: {}, sourceLabels: {} }), []);
});

test('excluded values get "Not" chips after their included group', () => {
  const chips = activeFilterChips({
    statuses: [], install: '', sources: ['f95'], engines: ["Ren'Py"], tags: ['3dcg'], collection: 'All',
    excludedSources: ['dlsite'], excludedEngines: ['RPGM'], excludedTags: ['netorare'],
    statusLabels: {}, sourceLabels: { f95: 'F95Zone', dlsite: 'DLsite' },
  });
  assert.deepEqual(chips.map(c => c.label), ['Source: F95Zone', 'Not source: DLsite', "Engine: Ren'Py", 'Not engine: RPGM', 'Tag: 3dcg', 'Not tag: netorare']);
  assert.deepEqual(chips.map(c => c.key), ['source:f95', 'source:not:dlsite', "engine:Ren'Py", 'engine:not:RPGM', 'tag:3dcg', 'tag:not:netorare']);
  assert.deepEqual(chips.map(c => c.excluded), [false, true, false, true, false, true]);
  // Exclusions alone still make chips.
  const only = activeFilterChips({ statuses: [], install: '', sources: [], engines: [], tags: [], collection: 'All', excludedTags: ['netorare'], statusLabels: {}, sourceLabels: {} });
  assert.deepEqual(only.map(c => c.label), ['Not tag: netorare']);
});
