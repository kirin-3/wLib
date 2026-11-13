// SPDX-License-Identifier: GPL-3.0-or-later
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  argsError,
  defaultOverrideLabel,
  filterLabel,
  fpsLimitError,
  gamescopeEnabledForGame,
  normalizeResolution,
  parseResolution,
  resolutionError,
  scalerLabel,
  splitArgs,
  windowModeLabel,
} from './gamescope.ts';

test('resolution validation', () => {
  assert.deepEqual(parseResolution('816x624'), [816, 624]);
  assert.deepEqual(parseResolution(' 1280 X 720 '), [1280, 720]);
  assert.equal(parseResolution('0x600'), null);
  assert.equal(parseResolution('16385x1'), null);
  assert.equal(resolutionError(''), '');
  assert.match(resolutionError('0x600'), /1 to 16384/);
  assert.equal(normalizeResolution(' 816 x 624 '), '816x624');
});

test('fps limit validation', () => {
  assert.equal(fpsLimitError(''), '');
  assert.equal(fpsLimitError('60'), '');
  assert.equal(fpsLimitError(1000), '');
  assert.match(fpsLimitError('0'), /1 to 1000/);
  assert.match(fpsLimitError('6.5'), /1 to 1000/);
});

test('extra arguments follow the backend rules', () => {
  assert.deepEqual(splitArgs(`--hdr-enabled "a b" 'c d' e\\ f`), ['--hdr-enabled', 'a b', 'c d', 'e f']);
  assert.deepEqual(splitArgs('""'), ['']);
  assert.equal(argsError('--hdr-enabled'), '');
  assert.match(argsError('--hdr-enabled "unclosed'), /quoting is not closed/);
  assert.match(argsError("'open"), /quoting is not closed/);
  assert.match(argsError('-f -- foo'), /`--` is not allowed/);
  assert.equal(argsError('-f "--" foo') !== '', true);
});

test('labels', () => {
  assert.equal(windowModeLabel('windowed'), 'Window');
  assert.equal(scalerLabel('integer'), 'Integer');
  assert.equal(filterLabel('fsr'), 'FSR');
  assert.equal(defaultOverrideLabel(true), 'Default (On)');
  assert.equal(defaultOverrideLabel(false), 'Default (Off)');
});

test('enabled for this game', () => {
  assert.equal(gamescopeEnabledForGame('default', true), true);
  assert.equal(gamescopeEnabledForGame(undefined, false), false);
  assert.equal(gamescopeEnabledForGame('on', false), true);
  assert.equal(gamescopeEnabledForGame('off', true), false);
});
