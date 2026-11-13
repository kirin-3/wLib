// SPDX-License-Identifier: GPL-3.0-or-later
import type { GamescopeSettings, RpgmakerLinuxRunnerStatus, SaveBackupKeep, SettingsResponse } from "../api";
import { FILTER_OPTIONS, SCALER_OPTIONS, WINDOW_MODE_OPTIONS, argsError, fpsLimitError, normalizeResolution } from "../../utils/gamescope";
import type { LibraryFilterPreset } from "../../utils/libraryViewState";
import { VALID_SORT_FIELDS } from "../../utils/libraryViewState";
import { isRecord } from "./games";

const MOCK_SETTINGS_STORAGE_KEY = "wlib-mock-settings";
export const DEFAULT_MOCK_SETTINGS: SettingsResponse = {
  gamemode_enabled: false,
  mangohud_enabled: false,
  gamescope_enabled: false,
  gamescope_window_mode: "fullscreen",
  gamescope_output_resolution: "",
  gamescope_game_resolution: "",
  gamescope_scaler: "auto",
  gamescope_filter: "linear",
  gamescope_fps_limit: "",
  gamescope_args: "",
  html_builtin_player: true,
  web_player_devtools: true,
  sandbox_new_games: false,
  sandboxie_custom_path: "",
  torrent_seed_mode: "ask",
  torrent_seed_ratio: 1,
  torrent_seed_hours: "",
  torrent_upload_limit_kib: 0,
  download_limit_kib: 0,
  download_max_active: 2,
  download_pause_while_playing: true,
  download_pause_on_battery: false,
  torrent_port_mapping: false,
  torrent_watch_folder: "",
  torrent_export_warning_accepted: false,
  tag_colors: {},
  custom_play_statuses: [],
  collections: [],
  library_filter_presets: [],
  urm_rpa_path: "",
  proton_path: "",
  wine_prefix_path: "",
  enable_logging: false,
  rpgmaker_linux_runner_path: "",
  rpgmaker_linux_runner_status: {
    available: false,
    path: "",
    source: "",
    configured_path: "",
    error: "RPGMaker Linux runner is not installed or configured.",
  },
  library_folder: "",
  library_folders: [],
  library_folder_pattern: "{creator}/{title}/{version}",
  downloads_folder: "",
  downloads_folder_exists: true,
  download_detection_enabled: true,
  save_backup_keep: "all",
  catalog_enabled: true,
};

export const normalizeMockFilterPresets = (value: unknown): LibraryFilterPreset[] => {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const presets: LibraryFilterPreset[] = [];
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null) continue;
    const source = entry as Record<string, unknown>;
    const name = typeof source.name === "string" ? source.name.trim() : "";
    if (!name || name.length > 40 || seen.has(name.toLowerCase())) continue;
    seen.add(name.toLowerCase());
    presets.push({
      name,
      filterCollection:
        typeof source.filterCollection === "string" &&
        ["All", "Favorites", "Updates available", "New since finished", "Unplayed update", "Recently played"].includes(source.filterCollection)
          ? (source.filterCollection as LibraryFilterPreset["filterCollection"])
          : "All",
      filterStatuses: Array.isArray(source.filterStatuses)
        ? source.filterStatuses.filter((status): status is string => typeof status === "string")
        : [],
      filterInstallState:
        source.filterInstallState === "installed" || source.filterInstallState === "wishlist"
          ? source.filterInstallState
          : "",
      filterSources: Array.isArray(source.filterSources)
        ? source.filterSources.filter((item): item is string => typeof item === "string")
        : [],
      filterEngines: Array.isArray(source.filterEngines)
        ? source.filterEngines.filter((engine): engine is string => typeof engine === "string")
        : [],
      filterTags: Array.isArray(source.filterTags)
        ? source.filterTags.filter((tag): tag is string => typeof tag === "string")
        : [],
      sortBy:
        typeof source.sortBy === "string" && VALID_SORT_FIELDS.has(source.sortBy as LibraryFilterPreset["sortBy"])
          ? (source.sortBy as LibraryFilterPreset["sortBy"])
          : "title",
      sortDir: source.sortDir === "desc" ? "desc" : "asc",
    });
  }
  return presets;
};

export const isSaveBackupKeep = (value: unknown): value is SaveBackupKeep =>
  value === "all" || (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 99);

// Mirrors Api._is_actionable_remote_version: empty and placeholder versions are
// extraction failures, not successful checks with no update.
export const isActionableVersion = (value: unknown): boolean => {
  const normalized = String(value ?? "").trim().toLowerCase();
  return !["", "unknown", "n/a", "na", "none", "null"].includes(normalized);
};

export const normalizeMockSettings = (value: unknown): SettingsResponse => {
  const source = typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
  const rpgmakerRunnerPath =
    typeof source.rpgmaker_linux_runner_path === "string"
      ? source.rpgmaker_linux_runner_path
      : DEFAULT_MOCK_SETTINGS.rpgmaker_linux_runner_path;
  const rpgmakerRunnerStatus = normalizeMockRpgmakerLinuxStatus(
    source.rpgmaker_linux_runner_status,
    rpgmakerRunnerPath,
  );

  return {
    custom_play_statuses: Array.isArray(source.custom_play_statuses) ? source.custom_play_statuses.filter((name): name is string => typeof name === "string") : [],
    gamemode_enabled: source.gamemode_enabled === true,
    mangohud_enabled: source.mangohud_enabled === true,
    ...normalizeMockGamescope(source),
    html_builtin_player: source.html_builtin_player !== false,
    web_player_devtools: source.web_player_devtools !== false,
    sandbox_new_games: source.sandbox_new_games === true,
    sandboxie_custom_path: typeof source.sandboxie_custom_path === "string" ? source.sandboxie_custom_path : "",
    torrent_seed_mode: source.torrent_seed_mode === "always" || source.torrent_seed_mode === "never" ? source.torrent_seed_mode : "ask",
    torrent_seed_ratio: typeof source.torrent_seed_ratio === "number" && source.torrent_seed_ratio >= 0 ? source.torrent_seed_ratio : 1,
    torrent_seed_hours: typeof source.torrent_seed_hours === "number" && source.torrent_seed_hours > 0 ? source.torrent_seed_hours : "",
    download_limit_kib: typeof source.download_limit_kib === "number" && source.download_limit_kib > 0 ? Math.floor(source.download_limit_kib) : 0,
    download_max_active: typeof source.download_max_active === "number" && Number.isInteger(source.download_max_active) && source.download_max_active >= 1 && source.download_max_active <= 5 ? source.download_max_active : 2,
    download_pause_while_playing: source.download_pause_while_playing !== false,
    download_pause_on_battery: source.download_pause_on_battery === true,
    torrent_upload_limit_kib: typeof source.torrent_upload_limit_kib === "number" && source.torrent_upload_limit_kib > 0 ? Math.floor(source.torrent_upload_limit_kib) : 0,
    torrent_port_mapping: source.torrent_port_mapping === true,
    torrent_watch_folder: typeof source.torrent_watch_folder === "string" ? source.torrent_watch_folder : "",
    torrent_export_warning_accepted: source.torrent_export_warning_accepted === true,
    tag_colors: typeof source.tag_colors === "object" && source.tag_colors !== null
      ? Object.fromEntries(Object.entries(source.tag_colors).filter(([name, swatch]) => name.trim() && typeof swatch === "string" && ["red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"].includes(swatch)).map(([name, swatch]) => [name.trim().toLowerCase(), swatch])) : {},
    collections: Array.isArray(source.collections) ? source.collections.filter((name): name is string => typeof name === "string") : [],
    library_filter_presets: normalizeMockFilterPresets(source.library_filter_presets),
    urm_rpa_path: typeof source.urm_rpa_path === "string" ? source.urm_rpa_path : "",
    proton_path: typeof source.proton_path === "string" ? source.proton_path : DEFAULT_MOCK_SETTINGS.proton_path,
    wine_prefix_path:
      typeof source.wine_prefix_path === "string"
        ? source.wine_prefix_path
        : DEFAULT_MOCK_SETTINGS.wine_prefix_path,
    enable_logging:
      typeof source.enable_logging === "boolean"
        ? source.enable_logging
        : DEFAULT_MOCK_SETTINGS.enable_logging,
    update_notifications: source.update_notifications !== false,
    rpgmaker_linux_runner_path: rpgmakerRunnerPath,
    rpgmaker_linux_runner_status: rpgmakerRunnerStatus,
    library_folder:
      typeof source.library_folder === "string"
        ? source.library_folder
        : DEFAULT_MOCK_SETTINGS.library_folder,
    library_folders: Array.isArray(source.library_folders)
      ? source.library_folders.filter((p): p is string => typeof p === "string")
      : typeof source.library_folder === "string" && source.library_folder ? [source.library_folder] : [],
    library_folder_pattern:
      typeof source.library_folder_pattern === "string" && source.library_folder_pattern.trim()
        ? source.library_folder_pattern
        : DEFAULT_MOCK_SETTINGS.library_folder_pattern,
    downloads_folder:
      typeof source.downloads_folder === "string"
        ? source.downloads_folder
        : DEFAULT_MOCK_SETTINGS.downloads_folder,
    downloads_folder_exists:
      typeof source.downloads_folder_exists === "boolean"
        ? source.downloads_folder_exists
        : DEFAULT_MOCK_SETTINGS.downloads_folder_exists,
    download_detection_enabled:
      typeof source.download_detection_enabled === "boolean"
        ? source.download_detection_enabled
        : DEFAULT_MOCK_SETTINGS.download_detection_enabled,
    save_backup_keep: isSaveBackupKeep(source.save_backup_keep) ? source.save_backup_keep : "all",
    catalog_enabled: source.catalog_enabled !== false,
  };
};

const normalizeMockRpgmakerLinuxStatus = (
  value: unknown,
  configuredPath: string,
): RpgmakerLinuxRunnerStatus => {
  const source = isRecord(value) ? value : {};
  const path =
    typeof source.path === "string" ? source.path : configuredPath.trim();
  const available =
    typeof source.available === "boolean" ? source.available : !!path.trim();

  return {
    available,
    path: available ? path : "",
    source:
      typeof source.source === "string"
        ? source.source
        : available
          ? "configured"
          : "",
    configured_path:
      typeof source.configured_path === "string"
        ? source.configured_path
        : configuredPath,
    error:
      typeof source.error === "string"
        ? source.error
        : available
          ? ""
          : DEFAULT_MOCK_SETTINGS.rpgmaker_linux_runner_status.error,
  };
};

export const readMockSettings = (): SettingsResponse => {
  try {
    const raw = localStorage.getItem(MOCK_SETTINGS_STORAGE_KEY);
    return raw ? normalizeMockSettings(JSON.parse(raw) as unknown) : DEFAULT_MOCK_SETTINGS;
  } catch (_error) {
    return DEFAULT_MOCK_SETTINGS;
  }
};

export const writeMockSettings = (settings: SettingsResponse): void => {
  try {
    localStorage.setItem(MOCK_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch (_error) {
    // Ignore browser mock persistence failures and fall back to defaults.
  }
};

const normalizeMockGamescope = (source: Record<string, unknown>): GamescopeSettings => {
  const text = (key: string): string => (typeof source[key] === "string" || typeof source[key] === "number" ? String(source[key]) : "");
  const pick = <T extends string>(key: string, options: ReadonlyArray<{ value: T }>, fallback: T): T =>
    options.find((option) => option.value === text(key))?.value ?? fallback;
  return {
    gamescope_enabled: source.gamescope_enabled === true,
    gamescope_window_mode: pick("gamescope_window_mode", WINDOW_MODE_OPTIONS, "fullscreen"),
    gamescope_output_resolution: normalizeResolution(text("gamescope_output_resolution")),
    gamescope_game_resolution: normalizeResolution(text("gamescope_game_resolution")),
    gamescope_scaler: pick("gamescope_scaler", SCALER_OPTIONS, "auto"),
    gamescope_filter: pick("gamescope_filter", FILTER_OPTIONS, "linear"),
    gamescope_fps_limit: fpsLimitError(text("gamescope_fps_limit")) ? "" : text("gamescope_fps_limit").trim(),
    gamescope_args: argsError(text("gamescope_args")) ? "" : text("gamescope_args").trim(),
  };
};
