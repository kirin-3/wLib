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
    needsSettings: !html && status?.status !== 'ready',
    note: html ? 'Browser games open in your system browser and cannot be sandboxed.'
      : status?.status !== 'ready' ? 'The sandbox tool is unavailable. Check Settings → Sandbox before launching.'
      : 'Each game has separate sandbox storage. Its game folder stays writable.',
  };
};

export const sandboxLaunchError = (code: string | undefined, message: string | undefined) => {
  if (!['sandbox_missing', 'sandbox_unusable', 'prefix_in_use'].includes(code || '')) return null;
  return { message: message || 'The game could not be launched.', openSettings: code !== 'prefix_in_use' };
};
