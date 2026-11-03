// SPDX-License-Identifier: GPL-3.0-or-later
import { reactive } from "vue";

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
}

interface ConfirmRequest extends ConfirmOptions {
  resolve: (confirmed: boolean) => void;
}

export const confirmQueue = reactive<ConfirmRequest[]>([]);

export const confirmAction = (options: ConfirmOptions): Promise<boolean> =>
  new Promise((resolve) => {
    confirmQueue.push({ ...options, resolve });
  });

export const resolveConfirmation = (confirmed: boolean): void => {
  const request = confirmQueue.shift();
  request?.resolve(confirmed);
};
