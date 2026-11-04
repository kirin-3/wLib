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
