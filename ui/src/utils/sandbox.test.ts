// SPDX-License-Identifier: GPL-3.0-or-later
import test from 'node:test';
import assert from 'node:assert/strict';
import { sandboxStatusMessage, sandboxSwitchState, sandboxLaunchError } from './sandbox.ts';

const ready = { status: 'ready' as const, tool: 'bwrap', path: '/usr/bin/bwrap', version: '0.13', reason: '' };
const missing = { ...ready, status: 'missing' as const, path: '', reason: 'Install bubblewrap.' };
test('sandbox status messages distinguish ready, missing and unusable', () => {
  assert.match(sandboxStatusMessage(ready), /Ready.*\/usr\/bin\/bwrap.*0.13/);
  assert.match(sandboxStatusMessage(missing), /Not installed.*Install bubblewrap/);
  assert.match(sandboxStatusMessage({...missing, status:'unusable', reason:'User namespaces blocked.'}), /Unusable.*namespaces/);
});
test('HTML is unavailable while missing tools preserve an editable switch', () => {
  assert.equal(sandboxSwitchState('/game/index.HTML', ready).disabled, true);
  assert.equal(sandboxSwitchState('/game/Game.exe', missing).disabled, false);
  assert.equal(sandboxSwitchState('/game/Game.exe', missing).needsSettings, true);
  assert.equal(sandboxSwitchState('/game/Game.exe', ready).needsSettings, false);
});
test('sandbox launch errors link to settings and prefix conflicts keep their explanation', () => {
  for (const code of ['sandbox_missing','sandbox_unusable']) assert.equal(sandboxLaunchError(code,'Unavailable')?.openSettings, true);
  assert.deepEqual(sandboxLaunchError('prefix_in_use','Story is already running.'), {message:'Story is already running.',openSettings:false});
  assert.equal(sandboxLaunchError('other','Failure'), null);
});
