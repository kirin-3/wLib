// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { performanceToolState } from './performanceTools.ts';

test('performance controls respect installation and sandbox', () => {
  assert.equal(performanceToolState('gamemode', false, false).disabled, true);
  assert.match(performanceToolState('gamemode', true, true).note, /sandbox/);
  assert.equal(performanceToolState('mangohud', true, true).disabled, false);
});
