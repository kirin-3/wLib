// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";
import type { FieldSaveState } from "./useFieldAutosave.ts";
import { errorReason } from "../utils/toast.ts";

/**
 * The save state of a control that saves at once (a switch or a select), in the
 * same shape as useFieldAutosave, so SettingRow shows "Saving…", "Saved" or the
 * error for it. Only the newest save may change the state.
 */
export function useSaveStatus(savedFor = 2000) {
  const state = ref<FieldSaveState>("idle");
  const error = ref("");
  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const stopTimer = () => {
    if (timer) clearTimeout(timer);
    timer = undefined;
  };

  /** Run one save. Returns false (and shows the reason) when it throws. */
  const run = async (save: () => Promise<unknown>): Promise<boolean> => {
    const current = ++generation;
    stopTimer();
    state.value = "saving";
    error.value = "";
    try {
      await save();
      if (current !== generation) return true;
      state.value = "saved";
      timer = setTimeout(() => {
        if (current === generation && state.value === "saved") state.value = "idle";
      }, savedFor);
      return true;
    } catch (cause) {
      if (current === generation) {
        error.value = errorReason(cause);
        state.value = "error";
      }
      return false;
    }
  };

  /** Forget the last result, for example when the row's value is set again from outside. */
  const clear = () => {
    generation++;
    stopTimer();
    state.value = "idle";
    error.value = "";
  };

  return { state, error, run, clear };
}
