// SPDX-License-Identifier: GPL-3.0-or-later
import { reactive } from "vue";

export interface ConfirmOptions {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
  /** Text of the secondary button; defaults to "Cancel". */
  cancelLabel?: string;
  /** Adds a checkbox (unticked) whose state comes back with the answer. */
  checkboxLabel?: string;
}

export interface ConfirmChoice {
  confirmed: boolean;
  checked: boolean;
}

interface ConfirmRequest extends ConfirmOptions {
  resolve: (choice: ConfirmChoice) => void;
}

export const confirmQueue = reactive<ConfirmRequest[]>([]);

/** Ask a question; resolves with the answer and the checkbox state. */
export const confirmChoice = (options: ConfirmOptions): Promise<ConfirmChoice> =>
  new Promise((resolve) => {
    confirmQueue.push({ ...options, resolve });
  });

export const confirmAction = async (options: ConfirmOptions): Promise<boolean> =>
  (await confirmChoice(options)).confirmed;

export const resolveConfirmation = (confirmed: boolean, checked = false): void => {
  const request = confirmQueue.shift();
  request?.resolve({ confirmed, checked: Boolean(request.checkboxLabel) && checked });
};
