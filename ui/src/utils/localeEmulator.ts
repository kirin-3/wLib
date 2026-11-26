// SPDX-License-Identifier: GPL-3.0-or-later
import type { ApiBasicResponse, LocaleEmulatorStatus } from '../services/api';

export const LOCALE_EMULATOR_RELEASES_URL = 'https://github.com/xupefei/Locale-Emulator/releases';
export const WINDOWS_TOOLS_ROUTE = '/settings#windows-tools';

export const localeEmulatorStatusMessage = (status: LocaleEmulatorStatus | null): string => {
  if (!status) return 'Checking Locale Emulator…';
  if (status.status === 'ready') return `Detected — ${status.path}`;
  return `Not detected. ${status.reason}`;
};

/**
 * The per-game Japanese locale toggle. On Windows it needs Locale Emulator:
 * while it is missing the toggle cannot be turned on, but a game that already
 * has it on (for example from a Linux device) can still turn it off.
 */
export const japaneseLocaleSwitchState = (
  platform: string,
  status: LocaleEmulatorStatus | null,
  enabled: boolean,
) => {
  if (platform !== 'windows') {
    return { label: 'Run with Japanese locale', disabled: false, needsSettings: false, note: '' };
  }
  const ready = status?.status === 'ready';
  return {
    label: 'Run with Locale Emulator',
    disabled: !ready && !enabled,
    needsSettings: status !== null && !ready,
    note: status === null ? 'Checking Locale Emulator…'
      : ready ? 'Starts the game with a Japanese locale. Turn it off if the game does not start.'
      : 'Locale Emulator is not detected.',
  };
};

export const localeEmulatorLaunchError = (code: string | undefined, message: string | undefined) => {
  if (code !== 'locale_emulator_missing') return null;
  return {
    message: message || 'Locale Emulator is not set up.',
    action: 'Open Locale Emulator settings',
    route: WINDOWS_TOOLS_ROUTE,
  };
};

export interface LocaleEmulatorPathDeps {
  saveSettings: (payload: { locale_emulator_path: string }) => Promise<ApiBasicResponse>;
  recheck: () => Promise<LocaleEmulatorStatus>;
}

/** Save the LEProc.exe path, then look for Locale Emulator again. */
export const saveLocaleEmulatorPath = async (
  deps: LocaleEmulatorPathDeps,
  path: string,
): Promise<{ ok: true; status: LocaleEmulatorStatus } | { ok: false; error: string }> => {
  const result = await deps.saveSettings({ locale_emulator_path: path });
  if (!result.success) return { ok: false, error: result.error || 'Could not save the Locale Emulator path.' };
  return { ok: true, status: await deps.recheck() };
};
