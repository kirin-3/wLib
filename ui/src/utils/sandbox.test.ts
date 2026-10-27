// SPDX-License-Identifier: GPL-3.0-or-later
import test from 'node:test';
import assert from 'node:assert/strict';
import { sandboxStatusMessage, sandboxSwitchState, sandboxLaunchError } from './sandbox.ts';

const ready = { status: 'ready' as const, tool: 'bwrap', path: '/usr/bin/bwrap', version: '0.13', reason: '' };
const missing = { ...ready, status: 'missing' as const, path: '', reason: 'Install bubblewrap.' };
test('pending status keeps the switch editable and hides unavailable settings', () => {
  assert.deepEqual(sandboxSwitchState('/game/Game.exe', null), {
    disabled: false,
    needsSettings: false,
    note: 'Checking sandbox tool…',
  });
});
test('resolved status shows storage details or the unavailable note', () => {
  assert.match(sandboxSwitchState('/game/Game.exe', ready).note, /separate sandbox storage/);
  for (const status of [missing, { ...missing, status: 'unusable' as const }]) {
    assert.deepEqual(sandboxSwitchState('/game/Game.exe', status), {
      disabled: false,
      needsSettings: true,
      note: 'The sandbox tool is unavailable. Check Settings → Sandbox before launching.',
    });
  }
});
test('HTML targets explain browser launching regardless of detection status', () => {
  for (const status of [null, ready, missing]) {
    assert.deepEqual(sandboxSwitchState(' /game/index.HTML ', status), {
      disabled: true,
      needsSettings: false,
      note: 'Browser games open in your system browser and cannot be sandboxed.',
    });
  }
});
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
