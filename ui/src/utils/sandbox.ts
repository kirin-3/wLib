// SPDX-License-Identifier: GPL-3.0-or-later
import type { SandboxStatus } from '../services/api';
import type { WebPlayer } from './webPlayer';

export const sandboxStatusMessage = (status: SandboxStatus | null): string => {
  if (!status) return 'Checking sandbox tool…';
  if (status.status === 'ready') return `Ready — ${status.path}${status.version ? ` (${status.version})` : ''}`;
  if (status.status === 'missing') return `Not installed. ${status.reason}`;
  return `Unusable. ${status.reason}`;
};

export const sandboxSwitchState = (player: WebPlayer, status: SandboxStatus | null) => {
  const browser = player === 'browser';
  return {
    disabled: browser,
    needsSettings: !browser && status !== null && status.status !== 'ready',
    note: browser ? 'Games that open in your system browser cannot be sandboxed.'
      : status === null ? 'Checking sandbox tool…'
      : status.status !== 'ready' ? 'Sandbox tool not installed'
      : 'Each game has separate sandbox storage. Its game folder stays writable.',
  };
};

export const networkSwitchState = (player: WebPlayer, platform: string, sandboxEnabled: boolean) => {
  if (player === 'browser') return { disabled: true, note: 'Games that open in your system browser cannot be blocked from the network.' };
  if (platform === 'windows' && !sandboxEnabled) return { disabled: true, note: 'No internet needs Run in sandbox on Windows.' };
  return { disabled: false, note: 'In-game online features, including online translation, stop working.' };
};

export const sandboxLaunchError = (code: string | undefined, message: string | undefined) => {
  if (!['sandbox_missing', 'sandbox_unusable', 'network_block_unavailable', 'network_block_needs_sandbox', 'proton_missing_offline', 'prefix_in_use'].includes(code || '')) return null;
  return { message: message || 'The game could not be launched.', openSettings: code !== 'prefix_in_use' };
};
