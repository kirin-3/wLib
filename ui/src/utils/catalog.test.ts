// SPDX-License-Identifier: GPL-3.0-or-later
import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogSourceLines, catalogSummary, formatCatalogTime } from './catalog.ts';

test('catalog summary covers off, missing, downloading and installed', () => {
  assert.match(catalogSummary(null), /Loading/);
  assert.match(catalogSummary({ success: true, enabled: false }), /Turned off/);
  assert.match(catalogSummary({ success: true, enabled: true, installed: false }), /Not downloaded/);
  assert.match(catalogSummary({ success: true, enabled: true, installed: false, checking: true }), /Downloading/);
  assert.match(catalogSummary({ success: true, enabled: true, installed: true, built_at: '2026-10-27T05:00:00Z' }), /^Built /);
  assert.equal(catalogSummary({ success: false, error: 'boom' }), 'boom');
});

test('source lines keep a fixed order and readable labels', () => {
  const lines = catalogSourceLines({
    success: true,
    sources: {
      vndb: { count: 31902, updated_at: null },
      extra: { count: null, updated_at: null },
      f95: { count: 27990, updated_at: '2026-10-27T05:00:00Z' },
    },
  });
  assert.deepEqual(lines.map((line) => line.label), ['F95Zone', 'VNDB', 'extra']);
  assert.equal(lines[1].updated, 'never');
  assert.equal(lines[2].count, '—');
  assert.equal(formatCatalogTime('not a date'), 'not a date');
});
