// SPDX-License-Identifier: GPL-3.0-or-later
import { reactive } from "vue";

export type ToastType = "info" | "success" | "error";

export interface ToastAction {
  label: string;
  run: () => void;
}

export interface Toast {
  id: number;
  type: ToastType;
  title: string;
  message: string;
  action?: ToastAction | undefined;
}

export const toasts = reactive<Toast[]>([]);
let nextToastId = 1;

export const dismissToast = (id: number): void => {
  const index = toasts.findIndex((toast) => toast.id === id);
  if (index !== -1) toasts.splice(index, 1);
};

// Errors and action toasts stay until dismissed so their text can be read
// and their action can be taken. Plain info/success toasts auto-dismiss.
export const notify = (
  message: string,
  type: ToastType = "info",
  title = "",
  action?: ToastAction,
): void => {
  const id = nextToastId++;
  toasts.push({ id, type, title, message, action });
  if (type !== "error" && !action) setTimeout(() => dismissToast(id), 5000);
};

export const notifyError = (message: string, title = ""): void => notify(message, "error", title);
