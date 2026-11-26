// SPDX-License-Identifier: GPL-3.0-or-later
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  japaneseLocaleSwitchState,
  localeEmulatorLaunchError,
  localeEmulatorStatusMessage,
  saveLocaleEmulatorPath,
} from './localeEmulator.ts';

const ready = { status: 'ready', path: 'C:\\LE\\LEProc.exe', reason: '' } as const;
const missing = { status: 'missing', path: '', reason: 'LEProc.exe could not be found.' } as const;

test('status message names the path or the reason', () => {
  assert.equal(localeEmulatorStatusMessage(null), 'Checking Locale Emulator…');
  assert.equal(localeEmulatorStatusMessage(ready), 'Detected — C:\\LE\\LEProc.exe');
  assert.equal(localeEmulatorStatusMessage(missing), 'Not detected. LEProc.exe could not be found.');
});

test('Windows toggle: ready, missing and off, missing and on', () => {
  const on = japaneseLocaleSwitchState('windows', ready, false);
  assert.equal(on.label, 'Run with Locale Emulator');
  assert.equal(on.disabled, false);
  assert.equal(on.needsSettings, false);

  const missingOff = japaneseLocaleSwitchState('windows', missing, false);
  assert.equal(missingOff.disabled, true);
  assert.equal(missingOff.needsSettings, true);
  assert.equal(missingOff.note, 'Locale Emulator is not detected.');

  // A game that already has it on can still turn it off.
  const missingOn = japaneseLocaleSwitchState('windows', missing, true);
  assert.equal(missingOn.disabled, false);
  assert.equal(missingOn.needsSettings, true);

  // While the status loads, an off toggle cannot be turned on yet.
  assert.equal(japaneseLocaleSwitchState('windows', null, false).disabled, true);
  assert.equal(japaneseLocaleSwitchState('windows', null, false).needsSettings, false);
});

test('Linux toggle keeps its label and never mentions Locale Emulator', () => {
  const linux = japaneseLocaleSwitchState('linux', null, false);
  assert.deepEqual(linux, { label: 'Run with Japanese locale', disabled: false, needsSettings: false, note: '' });
});

test('launch error maps only locale_emulator_missing to the Japanese locale settings', () => {
  assert.deepEqual(localeEmulatorLaunchError('locale_emulator_missing', 'Set it up.'), {
    message: 'Set it up.',
    action: 'Open Locale Emulator settings',
    route: '/settings#windows-tools',
  });
  assert.equal(localeEmulatorLaunchError('sandbox_missing', 'x'), null);
  assert.equal(localeEmulatorLaunchError(undefined, undefined), null);
});

test('saving a path rechecks; a wrong file returns the save error', async () => {
  const calls: string[] = [];
  const deps = {
    saveSettings: async ({ locale_emulator_path }: { locale_emulator_path: string }) => {
      calls.push(`save:${locale_emulator_path}`);
      return locale_emulator_path.toLowerCase().endsWith('leproc.exe') || !locale_emulator_path
        ? { success: true }
        : { success: false, error: 'Choose the Locale Emulator LEProc.exe executable.' };
    },
    recheck: async () => { calls.push('recheck'); return ready; },
  };
  assert.deepEqual(await saveLocaleEmulatorPath(deps, 'C:\\LE\\LEProc.exe'), { ok: true, status: ready });
  assert.deepEqual(calls, ['save:C:\\LE\\LEProc.exe', 'recheck']);

  calls.length = 0;
  assert.deepEqual(await saveLocaleEmulatorPath(deps, 'C:\\LE\\LEGUI.exe'), {
    ok: false,
    error: 'Choose the Locale Emulator LEProc.exe executable.',
  });
  assert.deepEqual(calls, ['save:C:\\LE\\LEGUI.exe']);
});
