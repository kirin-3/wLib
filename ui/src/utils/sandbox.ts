// SPDX-License-Identifier: GPL-3.0-or-later
import type { SandboxStatus } from '../services/api';

export const sandboxStatusMessage = (status: SandboxStatus | null): string => {
  if (!status) return 'Checking sandbox tool…';
  if (status.status === 'ready') return `Ready — ${status.path}${status.version ? ` (${status.version})` : ''}`;
  if (status.status === 'missing') return `Not installed. ${status.reason}`;
  return `Unusable. ${status.reason}`;
};

export const sandboxSwitchState = (target: string, status: SandboxStatus | null) => {
  const html = /\.html?$/i.test(target.trim());
  return {
    disabled: html,
    needsSettings: !html && status !== null && status.status !== 'ready',
    note: html ? 'Browser games open in your system browser and cannot be sandboxed.'
      : status === null ? 'Checking sandbox tool…'
      : status.status !== 'ready' ? 'Sandbox tool not installed'
      : 'Each game has separate sandbox storage. Its game folder stays writable.',
  };
};

export const networkSwitchState = (target: string, platform: string, sandboxEnabled: boolean) => {
  if (/\.html?$/i.test(target.trim())) return { disabled: true, note: 'Browser games cannot be blocked from the network.' };
  if (platform === 'windows' && !sandboxEnabled) return { disabled: true, note: 'No internet needs Run in sandbox on Windows.' };
  return { disabled: false, note: 'In-game online features, including online translation, stop working.' };
};

export const sandboxLaunchError = (code: string | undefined, message: string | undefined) => {
  if (!['sandbox_missing', 'sandbox_unusable', 'network_block_unavailable', 'network_block_needs_sandbox', 'proton_missing_offline', 'prefix_in_use'].includes(code || '')) return null;
  return { message: message || 'The game could not be launched.', openSettings: code !== 'prefix_in_use' };
};
