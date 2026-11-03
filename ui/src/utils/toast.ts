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
  undo?: (() => Promise<void>) | undefined;
  onExpire?: (() => void | Promise<void>) | undefined;
  held?: boolean | undefined;
}

export const toasts = reactive<Toast[]>([]);
let nextToastId = 1;

const undoTimers = new Map<number, ReturnType<typeof setTimeout>>();
const clearUndoTimer = (id: number) => {
  const timer = undoTimers.get(id);
  if (timer !== undefined) clearTimeout(timer);
  undoTimers.delete(id);
};

export const dismissToast = (id: number): void => {
  const index = toasts.findIndex((toast) => toast.id === id);
  if (index === -1) return;
  const [toast] = toasts.splice(index, 1);
  clearUndoTimer(id);
  if (toast?.onExpire) void toast.onExpire();
};

const startUndoTimer = (id: number): void => {
  clearUndoTimer(id);
  undoTimers.set(id, setTimeout(() => dismissToast(id), 8000));
};

export const setUndoHeld = (id: number, held: boolean): void => {
  const toast = toasts.find((item) => item.id === id && item.undo);
  if (!toast || toast.held === held) return;
  toast.held = held;
  if (held) clearUndoTimer(id);
  else startUndoTimer(id);
};

export const notifyUndo = (
  message: string,
  undo: () => Promise<void>,
  options: { onExpire?: () => void | Promise<void> } = {},
): void => {
  const id = nextToastId++;
  toasts.push({ id, type: "success", title: "", message, undo, onExpire: options.onExpire, held: false });
  startUndoTimer(id);
};

export const runUndo = async (id: number): Promise<void> => {
  const index = toasts.findIndex((toast) => toast.id === id && toast.undo);
  if (index === -1) return;
  const [toast] = toasts.splice(index, 1);
  clearUndoTimer(id);
  try { await toast?.undo?.(); }
  catch (error) { notifyError(`Could not undo: ${String(error)}`); }
};

export const runNewestUndo = async (): Promise<void> => {
  const toast = [...toasts].reverse().find((item) => item.undo);
  if (toast) await runUndo(toast.id);
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
