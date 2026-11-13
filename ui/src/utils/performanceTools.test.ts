// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { performanceToolName, performanceToolState } from './performanceTools.ts';

test('performance controls respect installation and sandbox', () => {
  assert.equal(performanceToolState('gamemode', false, false).disabled, true);
  assert.match(performanceToolState('gamemode', true, true).note, /sandbox/);
  assert.equal(performanceToolState('mangohud', true, true).disabled, false);
});

test('gamescope works in the sandbox and names itself', () => {
  assert.equal(performanceToolState('gamescope', true, true).disabled, false);
  assert.match(performanceToolState('gamescope', false, false).note, /Gamescope is not installed/);
  assert.equal(performanceToolName('gamescope'), 'Gamescope');
});
