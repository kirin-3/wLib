// SPDX-License-Identifier: GPL-3.0-or-later
import { ref, type Ref } from "vue";

export type FieldSaveState = "idle" | "saving" | "saved" | "error";

type Options<T> = {
  value: Ref<T>;
  save: (value: T) => Promise<T | void>;
  validate?: (value: T) => string | undefined;
  enabled?: () => boolean;
};

const snapshot = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const equal = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Save committed snapshots in order, leaving the live value untouched on failure. */
export function useFieldAutosave<T>(options: Options<T>) {
  const state = ref<FieldSaveState>("idle");
  const error = ref("");
  let saved = snapshot(options.value.value);
  let pending: Promise<void> | null = null;
  let queued = false;
  let clearTimer: ReturnType<typeof setTimeout> | undefined;

  const resetSaved = (value: T) => { saved = snapshot(value); };
  const commit = (): Promise<void> => {
    if (options.enabled && !options.enabled()) return Promise.resolve();
    if (pending) {
      queued = true;
      return pending;
    }
    const value = snapshot(options.value.value);
    const validation = options.validate?.(value);
    if (validation) {
      error.value = validation;
      state.value = "error";
      return Promise.resolve();
    }
    if (equal(value, saved)) {
      error.value = "";
      state.value = "idle";
      return Promise.resolve();
    }
    if (clearTimer) clearTimeout(clearTimer);
    state.value = "saving";
    error.value = "";
    pending = (async () => {
      try {
        const persisted = await options.save(value);
        saved = persisted === undefined ? value : snapshot(persisted);
        state.value = "saved";
        clearTimer = setTimeout(() => { if (state.value === "saved") state.value = "idle"; }, 2000);
      } catch (cause) {
        error.value = cause instanceof Error ? cause.message : String(cause);
        state.value = "error";
      } finally {
        pending = null;
      }
      if (queued) {
        queued = false;
        await commit();
      }
    })();
    return pending;
  };
  return { state, error, commit, resetSaved };
}
