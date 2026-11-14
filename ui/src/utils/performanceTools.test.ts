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

test('mangohud notes the RPGMaker Linux runner without greying out', () => {
  assert.deepEqual(performanceToolState('mangohud', true, false, 'rpgmaker_linux'), {
    disabled: false,
    note: 'MangoHud is not used with the RPGMaker Linux runner.',
  });
  assert.deepEqual(performanceToolState('mangohud', true, true, 'rpgmaker_linux').disabled, false);
  assert.equal(performanceToolState('mangohud', true, false, 'auto').note, '');
  assert.match(performanceToolState('mangohud', false, false, 'rpgmaker_linux').note, /not installed/);
  assert.equal(performanceToolState('gamemode', true, false, 'rpgmaker_linux').note, '');
});
