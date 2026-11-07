// SPDX-License-Identifier: GPL-3.0-or-later
import test from 'node:test';
import assert from 'node:assert/strict';
import { sandboxStatusMessage, sandboxSwitchState, sandboxLaunchError, networkSwitchState } from './sandbox.ts';

const ready = { status: 'ready' as const, tool: 'bwrap', path: '/usr/bin/bwrap', version: '0.13', reason: '' };
const missing = { ...ready, status: 'missing' as const, path: '', reason: 'Install bubblewrap.' };
test('pending status keeps the switch editable and hides unavailable settings', () => {
  assert.deepEqual(sandboxSwitchState(null, null), {
    disabled: false,
    needsSettings: false,
    note: 'Checking sandbox tool…',
  });
});
test('resolved status shows storage details or the unavailable note', () => {
  assert.match(sandboxSwitchState(null, ready).note, /separate sandbox storage/);
  for (const status of [missing, { ...missing, status: 'unusable' as const }]) {
    assert.deepEqual(sandboxSwitchState(null, status), {
      disabled: false,
      needsSettings: true,
      note: 'Sandbox tool not installed',
    });
  }
});
test('system browser launches explain why they cannot be sandboxed, whatever the detection status', () => {
  for (const status of [null, ready, missing]) {
    assert.deepEqual(sandboxSwitchState('browser', status), {
      disabled: true,
      needsSettings: false,
      note: 'Games that open in your system browser cannot be sandboxed.',
    });
  }
});
test('sandbox status messages distinguish ready, missing and unusable', () => {
  assert.match(sandboxStatusMessage(ready), /Ready.*\/usr\/bin\/bwrap.*0.13/);
  assert.match(sandboxStatusMessage(missing), /Not installed.*Install bubblewrap/);
  assert.match(sandboxStatusMessage({...missing, status:'unusable', reason:'User namespaces blocked.'}), /Unusable.*namespaces/);
});
test('the system browser is unavailable while missing tools preserve an editable switch', () => {
  assert.equal(sandboxSwitchState('browser', ready).disabled, true);
  assert.equal(sandboxSwitchState(null, missing).disabled, false);
  assert.equal(sandboxSwitchState(null, missing).needsSettings, true);
  assert.equal(sandboxSwitchState(null, ready).needsSettings, false);
});
test('sandbox launch errors link to settings and prefix conflicts keep their explanation', () => {
  for (const code of ['sandbox_missing','sandbox_unusable','network_block_unavailable','network_block_needs_sandbox','proton_missing_offline']) assert.equal(sandboxLaunchError(code,'Unavailable')?.openSettings, true);
  assert.deepEqual(sandboxLaunchError('prefix_in_use','Story is already running.'), {message:'Story is already running.',openSettings:false});
  assert.equal(sandboxLaunchError('other','Failure'), null);
});

test('network switch respects browser games and Windows sandbox', () => {
  assert.equal(networkSwitchState('browser', 'linux', true).disabled, true);
  assert.equal(networkSwitchState(null, 'windows', false).disabled, true);
  assert.equal(networkSwitchState(null, 'windows', true).disabled, false);
  assert.equal(networkSwitchState(null, 'linux', false).disabled, false);
});

test('built-in player launches can be sandboxed and blocked from the network', () => {
  assert.equal(sandboxSwitchState('builtin', ready).disabled, false);
  assert.match(sandboxSwitchState('builtin', ready).note, /separate sandbox storage/);
  assert.equal(sandboxSwitchState('builtin', missing).needsSettings, true);
  assert.equal(networkSwitchState('builtin', 'linux', false).disabled, false);
  assert.equal(networkSwitchState('builtin', 'windows', false).disabled, true);
  assert.match(networkSwitchState('browser', 'windows', true).note, /system browser/);
});
