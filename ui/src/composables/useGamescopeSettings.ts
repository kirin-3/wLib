// SPDX-License-Identifier: GPL-3.0-or-later
import { reactive, ref } from "vue";
import { api, type GamescopeSettings, type GamescopeStatus } from "../services/api";
import { argsError, fpsLimitError, normalizeResolution, resolutionError } from "../utils/gamescope";
import { copyText } from "../utils/copyText";
import { notify, notifyError } from "../utils/toast";

export type GamescopeTextField =
  | "gamescope_output_resolution"
  | "gamescope_game_resolution"
  | "gamescope_fps_limit"
  | "gamescope_args";

export const GAMESCOPE_DEFAULTS: GamescopeSettings = {
  gamescope_enabled: false,
  gamescope_window_mode: "fullscreen",
  gamescope_output_resolution: "",
  gamescope_game_resolution: "",
  gamescope_scaler: "auto",
  gamescope_filter: "linear",
  gamescope_fps_limit: "",
  gamescope_args: "",
};

const TEXT_CHECKS: Record<GamescopeTextField, (value: string) => string> = {
  gamescope_output_resolution: resolutionError,
  gamescope_game_resolution: resolutionError,
  gamescope_fps_limit: fpsLimitError,
  gamescope_args: argsError,
};

const normalizeText = (field: GamescopeTextField, value: string): string =>
  field === "gamescope_fps_limit" || field === "gamescope_args"
    ? value.trim()
    : normalizeResolution(value);

/** The global gamescope settings row: values, field errors, saves and host status. */
export function useGamescopeSettings() {
  const values = reactive<GamescopeSettings>({ ...GAMESCOPE_DEFAULTS });
  const drafts = reactive<Record<GamescopeTextField, string>>({
    gamescope_output_resolution: "",
    gamescope_game_resolution: "",
    gamescope_fps_limit: "",
    gamescope_args: "",
  });
  const errors = reactive<Partial<Record<keyof GamescopeSettings, string>>>({});
  const status = ref<GamescopeStatus | null>(null);
  const checking = ref(false);

  const apply = (data: Partial<GamescopeSettings>) => {
    Object.assign(values, GAMESCOPE_DEFAULTS, Object.fromEntries(
      Object.entries(data).filter(([key, value]) => key in GAMESCOPE_DEFAULTS && value !== undefined && value !== null),
    ));
    values.gamescope_fps_limit = String(values.gamescope_fps_limit ?? "");
    for (const field of Object.keys(drafts) as GamescopeTextField[]) drafts[field] = String(values[field] ?? "");
    for (const key of Object.keys(errors)) delete errors[key as keyof GamescopeSettings];
  };

  const save = async <K extends keyof GamescopeSettings>(key: K, value: GamescopeSettings[K]) => {
    const previous = values[key];
    values[key] = value;
    delete errors[key];
    try {
      const result = await api.saveSettings({ [key]: value });
      if (!result.success) throw new Error(result.error || "Unknown error");
    } catch (error) {
      // Put back the old value so the row shows what is saved.
      values[key] = previous;
      if (key in drafts) drafts[key as GamescopeTextField] = String(previous ?? "");
      const message = error instanceof Error ? error.message : String(error);
      errors[key] = message;
      notifyError(`Failed to save the gamescope setting: ${message}`);
    }
  };

  /** Check a text field; save it when it is valid and changed. */
  const commitText = async (field: GamescopeTextField) => {
    const draft = drafts[field];
    const error = TEXT_CHECKS[field](draft);
    if (error) {
      errors[field] = error;
      return;
    }
    delete errors[field];
    const value = normalizeText(field, draft);
    drafts[field] = value;
    if (value === values[field]) return;
    await save(field, value);
  };

  /** Show a field error while typing, without saving. */
  const checkText = (field: GamescopeTextField) => {
    const error = TEXT_CHECKS[field](drafts[field]);
    if (error) errors[field] = error;
    else delete errors[field];
  };

  const loadStatus = async (refresh = false) => {
    checking.value = true;
    try {
      status.value = await api.getGamescopeStatus(refresh);
    } catch (error) {
      notifyError(`Could not check for gamescope: ${String(error)}`);
    } finally {
      checking.value = false;
    }
  };

  const copyInstallCommand = async () => {
    const command = status.value?.command;
    if (!command) return;
    if (await copyText(command)) notify("Install command copied", "success");
    else notifyError("Could not copy the install command.");
  };

  return { values, drafts, errors, status, checking, apply, save, commitText, checkText, loadStatus, copyInstallCommand };
}
