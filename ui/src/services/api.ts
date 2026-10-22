// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * A wrapper service to communicate with the Python backend via PyWebView.
 * It gracefully falls back to mock data if the webview object isn't injected
 * (e.g., when running in normal browser).
 */

import type { LaunchMode } from "../utils/launchMode";
import { LAUNCH_MODE_OPTIONS } from "../utils/launchMode";
import type { LibraryFilterPreset } from "../utils/libraryViewState";
import { VALID_SORT_FIELDS } from "../utils/libraryViewState";
import { validateCustomPlayStatuses } from "../utils/playStatus";
import { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../utils/platformPolicy";
import { hasAvailableUpdate } from "../utils/libraryGames";
export type { LaunchMode } from "../utils/launchMode";
export { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../utils/platformPolicy";

type ApiInvoker = (...args: unknown[]) => Promise<unknown> | unknown;

interface PyWebViewBridge {
  api?: Record<string, ApiInvoker>;
}

export interface ApiErrorResult {
  success: false;
  error: string;
  error_code?: string;
  mock?: boolean;
}

export interface ApiBasicResponse {
  success?: boolean;
  error?: string;
  error_code?: string;
  mock?: boolean;
  code?: string;
}

export interface ApiSuccessResponse {
  success: true;
}

export interface AddGameResponse extends ApiBasicResponse {
  id?: number | null;
  title?: string;
  metadata_pending?: boolean;
}

// finished_version is present only when the backend auto-filled it by
// moving the game to Completed.
export interface UpdateGameResponse extends ApiBasicResponse {
  finished_version?: string;
}

// Title, version and engine guessed from a game's install folder.
export interface GameGuess {
  title: string;
  version: string;
  engine: string;
  exe_path: string;
}

export interface InspectGamePathResponse extends ApiBasicResponse, Partial<GameGuess> {}

export interface ScannedGame extends GameGuess {
  in_library: boolean;
}

export interface ScanGamesFolderResponse extends ApiBasicResponse {
  games?: ScannedGame[];
}

export interface ExtensionSyncStatus extends ApiBasicResponse {
  success: boolean;
  updated?: boolean;
  installed_version?: string;
  bundled_version?: string;
  path?: string;
  reason?: string;
}

export interface ExtensionServiceStatus extends ApiBasicResponse {
  success: boolean;
  reachable: boolean;
}

export interface RunnerInfo {
  name: string;
  path: string;
}

export interface GetAvailableRunnersResponse extends ApiBasicResponse {
  success: boolean;
  runners: RunnerInfo[];
}

export interface LaunchGameResponse extends ApiBasicResponse {
  success: boolean;
}

export interface UpdateCheckResponse extends ApiBasicResponse {
  success: boolean;
  version?: string;
  has_update?: boolean;
  newly_finished?: boolean;
  metadata_updated?: number;
  browser_fallback?: "unavailable" | "failed";
}

export interface BulkUpdateStartResponse extends ApiBasicResponse {
  success: boolean;
  total?: number;
  delay_seconds?: number;
}

export interface BulkUpdateResultItem {
  id: number;
  title: string;
  current_version: string;
  latest_version: string;
  has_update: boolean;
  newly_finished?: boolean;
  error: string;
  error_code: string;
  browser_fallback?: "unavailable" | "failed";
}

export interface UpdateStatusResponse extends ApiBasicResponse {
  running: boolean;
  outcome?: "idle" | "running" | "completed" | "cancelled" | "failed";
  cancelling?: boolean;
  total: number;
  checked: number;
  current: string;
  results: BulkUpdateResultItem[];
  delay_seconds: number;
}

export interface AutoCheckSettingResponse extends ApiBasicResponse {
  frequency: string;
  last_check: string;
}

export interface MaybeAutoCheckResponse {
  triggered: boolean;
  result?: BulkUpdateStartResponse;
  reason?: string;
}

// done/total are bytes while downloading; total is 0 when the size is unknown.
export interface ProtonDownloadStatus {
  running: boolean;
  stage: string;
  done: number;
  total: number;
}

export interface InstallProgressStatus {
  running: boolean;
  done: number;
  total: number;
  current: string;
  error: string;
}

export interface InstallStatusResponse extends ApiBasicResponse {
  deps: InstallProgressStatus;
  rtps: InstallProgressStatus;
  dlls_installed: boolean;
  rtps_installed: boolean;
}

export interface SystemDepsCommandResponse {
  detected: boolean;
  package_manager: string;
  distro: string;
  command: string;
  // Hosts without Linux runtime installers report why, with an error code.
  error?: string;
  code?: string;
}

export interface DownloadProtonResponse extends ApiBasicResponse {
  success: boolean;
  path?: string;
}

export interface AppReleaseAsset {
  name: string;
  url: string;
}

export interface AppUpdateResponse extends ApiBasicResponse {
  success: boolean;
  version?: string;
  current_version?: string;
  changelog?: string;
  url?: string;
  assets?: AppReleaseAsset[];
}

export interface AppVersionResponse {
  version: string;
}

// `supported` is false on hosts that cannot inject into a Wine prefix.
export interface CheatEngineStatusResponse {
  installed: boolean;
  path: string;
  supported?: boolean;
}

export interface DownloadCheatEngineResponse extends ApiBasicResponse {
  success: boolean;
  path?: string;
}

export interface ExecutableModifiedTimeResponse extends ApiBasicResponse {
  success: boolean;
  modified_at: string | null;
}

export interface ArchiveInspectResponse extends ApiBasicResponse {
  success: boolean;
  title?: string;
  version?: string;
  creator?: string;
  destination?: string;
  tool_error?: string;
  old_folder?: string;
  removable?: boolean;
  remove_reason?: string;
}

export interface ArchiveInstallOptions {
  archive_path: string;
  game_id?: number | null | undefined;
  title?: string | undefined;
  version?: string | undefined;
  creator?: string | undefined;
  engine?: string | undefined;
  f95_url?: string | undefined;
  library_folder?: string | undefined;
  pattern?: string | undefined;
  remove_old?: boolean | undefined;
  delete_archive?: boolean | undefined;
}

export interface ArchiveInstallResponse extends ApiBasicResponse {
  success: boolean;
  game_id?: number;
  exe_path?: string;
  destination?: string;
  unmoved_targets?: Array<Record<string, unknown>>;
  kept_old_folder_reason?: string;
  removed_old?: boolean;
  old_folder?: string;
}

export interface ArchiveInstallStatus {
  running: boolean;
  stage: string;
  done: number;
  total: number;
}

export interface DownloadOffer {
  game_id: number;
  game_title: string;
  installed_version: string;
  latest_version: string;
  archive_path: string;
  archive_version: string;
  guessed_title: string;
  key: string;
}

export interface DownloadOffersResponse extends ApiBasicResponse {
  success: boolean;
  offers?: DownloadOffer[];
}

export interface LaunchTarget {
  id: number;
  game_id: number;
  label: string;
  exe_path: string;
  sort_order: number;
  created_at?: string;
  updated_at?: string;
}

export interface LaunchTargetResponse extends ApiBasicResponse {
  success: boolean;
  target?: LaunchTarget;
}

export interface LaunchTargetsResponse extends ApiBasicResponse {
  success: boolean;
  targets: LaunchTarget[];
}

export interface GameAttachment {
  name: string;
  size: number;
}

export interface GameAttachmentsResponse extends ApiBasicResponse {
  success: boolean;
  attachments: GameAttachment[];
}

export interface AddGameAttachmentResponse extends ApiBasicResponse {
  success: boolean;
  attachment?: GameAttachment;
  attachments?: GameAttachment[];
}

export interface RemoveGameAttachmentResponse extends ApiBasicResponse {
  success: boolean;
  attachments?: GameAttachment[];
}

export type LibraryBackupSection =
  | "metadata"
  | "user_state"
  | "launch_config"
  | "executable_paths"
  | "launch_targets"
  | "settings_general"
  | "settings_paths";

export interface LibraryBackupOptions {
  sections: LibraryBackupSection[];
}

export interface LibraryBackupWarning {
  type: string;
  scope: string;
  message: string;
  field?: string;
  title?: string;
  path?: string;
}

export interface LibraryBackupCounts {
  total_games: number;
  matched_games: number;
  new_games: number;
  ambiguous_games: number;
  warnings: number;
}

export interface LibraryBackupManifest {
  format?: string;
  format_version?: number;
  exported_at?: string;
  app?: Record<string, unknown>;
  selected_sections?: LibraryBackupSection[];
}

export interface LibraryBackupAmbiguousGame {
  title: string;
  reason: string;
  candidate_count: number;
}

export interface LibraryBackupExportResponse extends ApiBasicResponse {
  success: boolean;
  path?: string;
  selected_sections?: LibraryBackupSection[];
  game_count?: number;
  encrypted?: boolean;
}

export type LibrarySyncState = "off" | "idle" | "syncing" | "needs_choice" | "needs_passphrase" | "error";
export type LibrarySyncChoice = "use-remote" | "use-local" | "merge-once";
export interface LibrarySyncStatus extends ApiBasicResponse {
  state?: LibrarySyncState;
  location?: "" | "folder" | "dropbox" | "gdrive";
  folder_path?: string;
  account?: string;
  encrypted?: boolean;
  last_synced_at?: string;
  last_device?: string;
  last_error?: string;
  dropbox_available?: boolean;
  dropbox_unavailable_reason?: string;
  gdrive_available?: boolean;
  gdrive_unavailable_reason?: string;
  connecting?: boolean;
}

export interface LibraryBackupInspectResponse extends ApiBasicResponse {
  success: boolean;
  manifest?: LibraryBackupManifest;
  available_sections?: LibraryBackupSection[];
  counts?: LibraryBackupCounts;
  warnings?: LibraryBackupWarning[];
  ambiguous_games?: LibraryBackupAmbiguousGame[];
}

export interface LibraryBackupImportResponse extends ApiBasicResponse {
  success: boolean;
  created?: number;
  updated?: number;
  deleted?: number;
  skipped?: number;
  ambiguous?: number;
  warnings?: number;
  warning_records?: LibraryBackupWarning[];
  settings_updated?: number;
  selected_sections?: LibraryBackupSection[];
  created_ids?: number[];
  updated_ids?: number[];
}

export type ExternalLibrarySource = "f95checker" | "atlas" | "xlibrary" | "f69";
export type ExternalLibraryMergeMode = "overwrite" | "fill_missing";

export interface ExternalLibraryDetectResponse extends ApiBasicResponse {
  success: boolean;
  source?: string;
  path?: string;
  checked?: string[];
}

export interface ExternalLibraryCounts {
  total_games: number;
  matched_games: number;
  new_games: number;
  ambiguous_games: number;
  skipped_no_identity: number;
  dropped_paths: number;
  warnings: number;
}

export interface ExternalLibraryInspectResponse extends ApiBasicResponse {
  success: boolean;
  source?: string;
  schema_version?: string | number;
  available_sections?: LibraryBackupSection[];
  counts?: ExternalLibraryCounts;
  warnings?: LibraryBackupWarning[];
  ambiguous_games?: LibraryBackupAmbiguousGame[];
}

export interface ExternalLibraryImportOptions {
  sections?: LibraryBackupSection[];
  merge_mode?: ExternalLibraryMergeMode;
}

// The external reader always applies metadata to matches and reports the
// follow-up metadata check the backend started for the imported games.
export interface ExternalLibraryImportResponse extends LibraryBackupImportResponse {
  source?: string;
  schema_version?: string | number;
  skipped_no_identity?: number;
  dropped_paths?: number;
  metadata_check?: "started" | "busy" | "none";
}

export interface SaveLocation {
  path: string;
  type: string;
  description: string;
}

export interface GameRecord {
  id: number;
  title: string;
  exe_path: string;
  f95_url?: string;
  version?: string;
  latest_version?: string;
  cover_image_path?: string;
  cover_image?: string;
  tags?: string | string[];
  rating?: string;
  developer?: string;
  engine?: string;
  status?: string;
  play_status?: string;
  finished_version?: string;
  playtime_seconds?: number;
  // Timestamps are nullable columns: a game that was never played reports
  // null for last_played, and older rows carry no date_added.
  last_played?: string | null;
  date_added?: string | null;
  command_line_args?: string;
  run_japanese_locale?: boolean;
  run_wayland?: boolean;
  auto_inject_ce?: boolean;
  custom_prefix?: string;
  proton_version?: string;
  launch_mode?: LaunchMode;
  is_favorite?: boolean;
  rating_graphics?: number;
  rating_story?: number;
  rating_fappability?: number;
  rating_gameplay?: number;
  thread_main_post_last_edit_at?: string | null;
  thread_main_post_checked_at?: string | null;
  thread_completed?: boolean | number;
  launch_targets?: LaunchTarget[];
  // Decoded collection membership; names always match the collection list.
  collections?: string[];
  // Remote screenshot URLs collected from the thread starter post.
  screenshots?: string[];
  // Notes markdown stored in games.progress.
  progress?: string;
  // F95 metadata is still being fetched in the background after adding.
  metadata_pending?: boolean;
}

export interface RpgmakerLinuxRunnerStatus {
  available: boolean;
  path: string;
  source: string;
  configured_path: string;
  error: string;
}

export type HostPlatform = "linux" | "windows" | "unsupported";

export interface PlatformCapabilities {
  platform: HostPlatform;
  native_windows_launch: boolean;
  wine_proton: boolean;
  runtime_installers: boolean;
  wayland: boolean;
  rpgmaker_linux: boolean;
  cheat_engine_injection: boolean;
  launch_modes: LaunchMode[];
  data_dir: string;
  cache_dir: string;
  extension_dir: string;
}

export interface SettingsPayload {
  custom_play_statuses?: string[];
  collections?: string[];
  library_filter_presets?: LibraryFilterPreset[];
  urm_rpa_path?: string;
  proton_path?: string;
  wine_prefix_path?: string;
  enable_logging?: boolean;
  rpgmaker_linux_runner_path?: string;
  library_folder?: string;
  library_folder_pattern?: string;
  downloads_folder?: string;
  download_detection_enabled?: boolean;
}

export interface UrmStatusResponse extends ApiBasicResponse {
  renpy?: boolean;
  installed?: boolean;
  source_configured?: boolean;
}

export interface SettingsResponse {
  custom_play_statuses: string[];
  collections: string[];
  library_filter_presets: LibraryFilterPreset[];
  urm_rpa_path: string;
  proton_path: string;
  wine_prefix_path: string;
  enable_logging: boolean;
  rpgmaker_linux_runner_path: string;
  rpgmaker_linux_runner_status: RpgmakerLinuxRunnerStatus;
  library_folder: string;
  library_folder_pattern: string;
  downloads_folder: string;
  downloads_folder_exists: boolean;
  download_detection_enabled: boolean;
}

export interface CollectionsResponse extends ApiBasicResponse {
  collections?: string[];
}

export type PathCheckKind = "file" | "dir" | "parent" | "runner";

export interface PathCheckResponse extends ApiBasicResponse {
  ok?: boolean;
  reason?: string;
}

const MOCK_SETTINGS_STORAGE_KEY = "wlib-mock-settings";
const MOCK_LAUNCH_TARGETS_STORAGE_KEY = "wlib-mock-launch-targets";
const MOCK_ATTACHMENTS_STORAGE_KEY = "wlib-mock-attachments";
const DEFAULT_MOCK_SETTINGS: SettingsResponse = {
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
  library_folder_pattern: "{creator}/{title}/{version}",
  downloads_folder: "",
  downloads_folder_exists: true,
  download_detection_enabled: true,
};

const isRecord = (value: unknown): value is Record<string, unknown> => {
  return typeof value === "object" && value !== null;
};

const mockScreenshotUri = (width: number, height: number, fill: string, label: string): string => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="${width}" height="${height}" fill="${fill}"/><text x="${width / 2}" y="${height / 2}" font-size="24" text-anchor="middle" dominant-baseline="middle" fill="#ffffff">${label}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
};

const MOCK_SCREENSHOT_SET_A: string[] = [
  mockScreenshotUri(400, 200, "#1e3a5f", "Shot 1"),
  mockScreenshotUri(200, 400, "#5f1e3a", "Shot 2"),
  mockScreenshotUri(300, 300, "#1e5f3a", "Shot 3"),
  mockScreenshotUri(600, 200, "#3a5f1e", "Shot 4"),
];

const MOCK_SCREENSHOT_SET_B: string[] = [
  mockScreenshotUri(320, 180, "#2a4a6b", "A"),
  mockScreenshotUri(180, 320, "#6b2a4a", "B"),
  mockScreenshotUri(400, 400, "#2a6b4a", "C"),
];

const normalizeMockLaunchTarget = (value: unknown): LaunchTarget | null => {
  if (!isRecord(value)) return null;
  const id = Number(value.id);
  const gameId = Number(value.game_id);
  const label = typeof value.label === "string" ? value.label.trim() : "";
  const exePath = typeof value.exe_path === "string" ? value.exe_path.trim() : "";
  const sortOrder = Number(value.sort_order);
  if (!Number.isFinite(id) || !Number.isFinite(gameId) || !label || !exePath) {
    return null;
  }
  return {
    id,
    game_id: gameId,
    label,
    exe_path: exePath,
    sort_order: Number.isFinite(sortOrder) ? Math.max(0, Math.trunc(sortOrder)) : 0,
    created_at: typeof value.created_at === "string" ? value.created_at : "",
    updated_at: typeof value.updated_at === "string" ? value.updated_at : "",
  };
};

const readMockLaunchTargets = (): LaunchTarget[] => {
  try {
    const raw = localStorage.getItem(MOCK_LAUNCH_TARGETS_STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed)
      ? parsed.map(normalizeMockLaunchTarget).filter((target): target is LaunchTarget => target !== null)
      : [];
  } catch (_error) {
    return [];
  }
};

const writeMockLaunchTargets = (targets: LaunchTarget[]): void => {
  try {
    localStorage.setItem(MOCK_LAUNCH_TARGETS_STORAGE_KEY, JSON.stringify(targets));
  } catch (_error) {
    // Ignore browser mock persistence failures and keep the desktop contract unchanged.
  }
};

const nextMockLaunchTargetId = (targets: LaunchTarget[]): number => {
  return targets.reduce((max, target) => Math.max(max, target.id), 0) + 1;
};

const normalizeMockAttachment = (value: unknown): GameAttachment | null => {
  if (!isRecord(value)) return null;
  const name = typeof value.name === "string" ? value.name : "";
  const size = Number(value.size);
  if (!name || !Number.isFinite(size)) return null;
  return { name, size: Math.max(0, Math.trunc(size)) };
};

const readMockAttachments = (): Record<string, GameAttachment[]> => {
  try {
    const raw = localStorage.getItem(MOCK_ATTACHMENTS_STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : {};
    if (!isRecord(parsed)) return {};
    const result: Record<string, GameAttachment[]> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (!Array.isArray(value)) continue;
      result[key] = value
        .map(normalizeMockAttachment)
        .filter((item): item is GameAttachment => item !== null)
        .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    }
    return result;
  } catch (_error) {
    return {};
  }
};

const writeMockAttachments = (attachments: Record<string, GameAttachment[]>): void => {
  try {
    localStorage.setItem(MOCK_ATTACHMENTS_STORAGE_KEY, JSON.stringify(attachments));
  } catch (_error) {
    // Ignore browser mock persistence failures and keep the desktop contract unchanged.
  }
};

const mockAttachmentList = (gameId: number): GameAttachment[] => {
  return readMockAttachments()[String(gameId)] || [];
};

const normalizeMockFilterPresets = (value: unknown): LibraryFilterPreset[] => {
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
        ["All", "Favorites", "Updates available", "New since finished", "Recently played"].includes(source.filterCollection)
          ? (source.filterCollection as LibraryFilterPreset["filterCollection"])
          : "All",
      filterStatuses: Array.isArray(source.filterStatuses)
        ? source.filterStatuses.filter((status): status is string => typeof status === "string")
        : [],
      filterInstallState:
        source.filterInstallState === "installed" || source.filterInstallState === "wishlist"
          ? source.filterInstallState
          : "",
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

// Mirrors the backend's name rules: 1–40 characters, unique ignoring case,
// and "All" reserved for the built-in tab.
const validateMockCollectionList = (names: readonly string[]): string => {
  const seen = new Set(["all"]);
  for (const raw of names) {
    const name = raw.trim();
    if (!name || name.length > 40) return "Collection names must contain 1–40 characters.";
    if (name.toLowerCase() === "all") return "'All' is reserved for the library-wide tab.";
    if (seen.has(name.toLowerCase())) return `A collection named '${name}' already exists.`;
    seen.add(name.toLowerCase());
  }
  return "";
};

const rewriteMockGameCollections = (
  rewrite: (names: string[], game: GameRecord) => string[],
): void => {
  const games = readMockGames().map((game) => ({
    ...game,
    collections: rewrite(game.collections || [], game),
  }));
  try {
    localStorage.setItem("wlib-mock-games", JSON.stringify(games));
  } catch (_error) {
    // Ignore browser mock persistence failures and keep the desktop contract unchanged.
  }
};

const writeMockCollections = (collections: string[]): void => {
  writeMockSettings({ ...readMockSettings(), collections });
};

const normalizeMockSettings = (value: unknown): SettingsResponse => {
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
    rpgmaker_linux_runner_path: rpgmakerRunnerPath,
    rpgmaker_linux_runner_status: rpgmakerRunnerStatus,
    library_folder:
      typeof source.library_folder === "string"
        ? source.library_folder
        : DEFAULT_MOCK_SETTINGS.library_folder,
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

const readMockSettings = (): SettingsResponse => {
  try {
    const raw = localStorage.getItem(MOCK_SETTINGS_STORAGE_KEY);
    return raw ? normalizeMockSettings(JSON.parse(raw) as unknown) : DEFAULT_MOCK_SETTINGS;
  } catch (_error) {
    return DEFAULT_MOCK_SETTINGS;
  }
};

const readMockGames = (): GameRecord[] => {
  try {
    const games: unknown = JSON.parse(localStorage.getItem("wlib-mock-games") || "[]");
    return Array.isArray(games) ? games.filter((game): game is GameRecord => isRecord(game) && typeof game.id === "number" && typeof game.title === "string" && typeof game.exe_path === "string") : [];
  } catch {
    return [];
  }
};

const writeMockSettings = (settings: SettingsResponse): void => {
  try {
    localStorage.setItem(MOCK_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch (_error) {
    // Ignore browser mock persistence failures and fall back to defaults.
  }
};

declare global {
  interface Window {
    pywebview?: PyWebViewBridge;
  }
}

class ApiService {
  private gameSaves = new Map<number, Promise<void>>();
  constructor() {
    this.isWebview = window.pywebview !== undefined;
    this._mockWarnings = new Set();
  }

  isWebview: boolean;
  _mockWarnings: Set<string>;
  _mockRunningGames = new Set<number>();
  _mockPendingMetadata = new Set<number>();
  _mockUrmInstalled = new Set<number>();
  _mockArchiveStatus: ArchiveInstallStatus = { running: false, stage: "", done: 0, total: 0 };

  async invoke<T = unknown>(method: string, ...args: unknown[]): Promise<T> {
    const invoker = window.pywebview?.api?.[method];

    if (invoker) {
      return (await invoker(...args)) as T;
    }

    if (!this._mockWarnings.has(method)) {
      console.warn(`[ApiService] Mocking call to ${method}`, args);
      this._mockWarnings.add(method);
    }

    return this._mockResponse(method, args) as T;
  }

  async getGames(): Promise<GameRecord[]> {
    return this.invoke<GameRecord[]>("get_games");
  }

  async getCollections(): Promise<string[]> {
    return this.invoke<string[]>("get_collections");
  }

  async createCollection(name: string, gameId?: number): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>(
      "create_collection",
      name,
      gameId ?? null,
    );
  }

  async renameCollection(oldName: string, newName: string): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>(
      "rename_collection",
      oldName,
      newName,
    );
  }

  async deleteCollection(name: string): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>("delete_collection", name);
  }

  async reorderCollections(names: string[]): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>("reorder_collections", names);
  }

  async setGameCollections(gameId: number, names: string[]): Promise<CollectionsResponse> {
    return this.invokeLibraryChange<CollectionsResponse>(
      "set_game_collections",
      gameId,
      names,
    );
  }

  async saveLibraryFilterPresets(presets: LibraryFilterPreset[]): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("save_library_filter_presets", presets);
  }

  async checkPath(path: string, kind: PathCheckKind): Promise<PathCheckResponse> {
    return this.invoke<PathCheckResponse>("check_path", path, kind);
  }

  private async invokeLibraryChange<T extends ApiBasicResponse>(method: string, ...args: unknown[]): Promise<T> {
    const result = await this.invoke<T>(method, ...args);
    if (result?.success) window.dispatchEvent(new Event("wlib-refresh-library"));
    return result;
  }

  async addGame(
    title: string,
    exe_path: string,
    f95_url = "",
    version = "",
    cover_image = "",
    tags = "",
    rating = "",
    developer = "",
    engine = "",
    run_japanese_locale = false,
    run_wayland = false,
    auto_inject_ce = false,
    custom_prefix = "",
    proton_version = "",
    launch_mode: LaunchMode = "auto",
    command_line_args = "",
  ): Promise<AddGameResponse> {
    return this.invokeLibraryChange(
      "add_game",
      title,
      exe_path,
      f95_url,
      version,
      cover_image,
      tags,
      rating,
      developer,
      engine,
      run_japanese_locale,
      run_wayland,
      auto_inject_ce,
      custom_prefix,
      proton_version,
      launch_mode,
      command_line_args,
    );
  }

  async deleteGame(id: number): Promise<ApiBasicResponse> {
    return this.invokeLibraryChange("delete_game", id);
  }

  async updateGame(id: number, fields: Record<string, unknown>): Promise<UpdateGameResponse> {
    const previous = this.gameSaves.get(id) ?? Promise.resolve();
    const snapshot = { ...fields };
    const request = previous.then(() => this.invokeLibraryChange("update_game", id, snapshot));
    const settled = request.then(() => {}, () => {});
    this.gameSaves.set(id, settled);
    void settled.then(() => {
      if (this.gameSaves.get(id) === settled) this.gameSaves.delete(id);
    });
    return request;
  }

  async inspectGamePath(exePath: string): Promise<InspectGamePathResponse> {
    return this.invoke<InspectGamePathResponse>("inspect_game_path", exePath);
  }

  async scanGamesFolder(folder: string): Promise<ScanGamesFolderResponse> {
    return this.invoke<ScanGamesFolderResponse>("scan_games_folder", folder);
  }

  // Adds each game without a library refresh per game, then refreshes once.
  async importGames(games: GameGuess[]): Promise<{ added: number; errors: string[] }> {
    let added = 0;
    const errors: string[] = [];
    for (const game of games) {
      try {
        const result = await this.invoke<AddGameResponse>(
          "add_game", game.title, game.exe_path, "", game.version, "", "", "", "", game.engine,
        );
        if (result?.success) added++;
        else errors.push(`${game.title}: ${result?.error || "Unknown error"}`);
      } catch (error) {
        errors.push(`${game.title}: ${String(error)}`);
      }
    }
    if (added) window.dispatchEvent(new Event("wlib-refresh-library"));
    return { added, errors };
  }

  async markGameUpdated(game: GameRecord): Promise<ApiBasicResponse> {
    if (!hasAvailableUpdate(game)) return { success: false, error: "No newer version is available." };
    return this.updateGame(game.id, { version: String(game.latest_version).trim() });
  }

  async openExtensionFolder(): Promise<ExtensionSyncStatus> {
    return this.invoke<ExtensionSyncStatus>("open_extension_folder");
  }

  async getExtensionServiceStatus(): Promise<ExtensionServiceStatus> {
    return this.invoke<ExtensionServiceStatus>("get_extension_service_status");
  }

  async getStartupExtensionSyncStatus(): Promise<ExtensionSyncStatus> {
    return this.invoke<ExtensionSyncStatus>("get_startup_extension_sync_status");
  }

  async openInBrowser(url: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_in_browser", url);
  }

  async checkForUpdates(url: string): Promise<UpdateCheckResponse> {
    return this.invokeLibraryChange<UpdateCheckResponse>("check_for_updates", url);
  }

  async checkAllUpdates(): Promise<BulkUpdateStartResponse> {
    return this.invoke<BulkUpdateStartResponse>("check_all_updates");
  }

  async getUpdateStatus(): Promise<UpdateStatusResponse> {
    return this.invoke<UpdateStatusResponse>("get_update_status");
  }

  async cancelUpdateCheck(): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("cancel_update_check");
  }

  async getAutoCheckSetting(): Promise<AutoCheckSettingResponse> {
    return this.invoke<AutoCheckSettingResponse>("get_auto_check_setting");
  }

  async setAutoCheckSetting(frequency: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("set_auto_check_setting", frequency);
  }

  async maybeAutoCheck(): Promise<MaybeAutoCheckResponse> {
    return this.invoke<MaybeAutoCheckResponse>("maybe_auto_check");
  }

  async launchGame(
    game_id: number,
    exe_path: string,
    command_line_args = "",
    run_japanese_locale = false,
    run_wayland = false,
    auto_inject_ce = false,
    custom_prefix = "",
    proton_version = "",
    launch_mode: LaunchMode = "auto"
  ): Promise<LaunchGameResponse> {
    return this.invoke(
      "launch_game",
      game_id,
      exe_path,
      command_line_args,
      run_japanese_locale,
      run_wayland,
      auto_inject_ce,
      custom_prefix,
      proton_version,
      launch_mode,
    );
  }

  async getAvailableRunners(): Promise<GetAvailableRunnersResponse> {
    return this.invoke<GetAvailableRunnersResponse>("get_available_runners");
  }

  async stopGame(gameId: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("stop_game", gameId);
  }

  async getRunningGames(): Promise<number[]> {
    return this.invoke<number[]>("get_running_games");
  }

  async getUrmStatus(gameId: number): Promise<UrmStatusResponse> {
    return this.invoke<UrmStatusResponse>("get_urm_status", gameId);
  }

  async setUrmInstalled(gameId: number, installed: boolean): Promise<UrmStatusResponse> {
    return this.invoke<UrmStatusResponse>("set_urm_installed", gameId, installed);
  }

  async browseUrmFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_urm_file", startPath);
  }

  async getExecutableModifiedTime(exe_path: string): Promise<ExecutableModifiedTimeResponse> {
    return this.invoke<ExecutableModifiedTimeResponse>("get_executable_modified_time", exe_path);
  }

  async getLaunchTargets(game_id: number): Promise<LaunchTarget[]> {
    return this.invoke<LaunchTarget[]>("get_launch_targets", game_id);
  }

  async createLaunchTarget(
    game_id: number,
    label: string,
    exe_path: string,
    sort_order: number | null = null
  ): Promise<LaunchTargetResponse> {
    return this.invoke<LaunchTargetResponse>(
      "create_launch_target",
      game_id,
      label,
      exe_path,
      sort_order,
    );
  }

  async updateLaunchTarget(
    target_id: number,
    fields: Partial<Pick<LaunchTarget, "label" | "exe_path" | "sort_order">>
  ): Promise<LaunchTargetResponse> {
    return this.invoke<LaunchTargetResponse>("update_launch_target", target_id, fields);
  }

  async deleteLaunchTarget(target_id: number): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("delete_launch_target", target_id);
  }

  async reorderLaunchTargets(
    game_id: number,
    target_ids: number[]
  ): Promise<LaunchTargetsResponse> {
    return this.invoke<LaunchTargetsResponse>(
      "reorder_launch_targets",
      game_id,
      target_ids,
    );
  }

  async getSettings(): Promise<SettingsResponse> {
    return this.invoke<SettingsResponse>("get_settings");
  }

  async getPlatformCapabilities(): Promise<PlatformCapabilities> {
    return this.invoke<PlatformCapabilities>("get_platform_capabilities");
  }

  async saveSettings(settings: SettingsPayload): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("save_settings", settings);
  }

  async exportLibraryBackup(
    options: LibraryBackupOptions,
    destinationPath: string,
    passphrase: string | null = null,
  ): Promise<LibraryBackupExportResponse> {
    return this.invoke<LibraryBackupExportResponse>(
      "export_library_backup",
      options,
      destinationPath,
      passphrase,
    );
  }

  async inspectLibraryBackup(path: string, passphrase: string | null = null): Promise<LibraryBackupInspectResponse> {
    return this.invoke<LibraryBackupInspectResponse>("inspect_library_backup", path, passphrase);
  }

  async importLibraryBackup(
    path: string,
    options: LibraryBackupOptions,
    passphrase: string | null = null,
  ): Promise<LibraryBackupImportResponse> {
    return this.invoke<LibraryBackupImportResponse>(
      "import_library_backup",
      path,
      options,
      passphrase,
    );
  }

  async detectExternalLibrary(source: ExternalLibrarySource): Promise<ExternalLibraryDetectResponse> {
    return this.invoke<ExternalLibraryDetectResponse>("detect_external_library", source);
  }

  async inspectExternalLibrary(
    source: ExternalLibrarySource,
    path: string,
  ): Promise<ExternalLibraryInspectResponse> {
    return this.invoke<ExternalLibraryInspectResponse>("inspect_external_library", source, path);
  }

  async importExternalLibrary(
    source: ExternalLibrarySource,
    path: string,
    options: ExternalLibraryImportOptions,
  ): Promise<ExternalLibraryImportResponse> {
    return this.invoke<ExternalLibraryImportResponse>(
      "import_external_library",
      source,
      path,
      options,
    );
  }

  async browseExternalLibraryFile(source: ExternalLibrarySource, startPath = ""): Promise<string> {
    return this.invoke<string>("browse_external_library_file", source, startPath);
  }

  async getSyncStatus(): Promise<LibrarySyncStatus> { return this.invoke("get_sync_status"); }
  async setSyncFolder(path: string): Promise<LibrarySyncStatus> { return this.invoke("set_sync_folder", path); }
  async connectDropbox(): Promise<LibrarySyncStatus> { return this.invoke("connect_dropbox"); }
  async connectGdrive(): Promise<LibrarySyncStatus> { return this.invoke("connect_gdrive"); }
  async disconnectSync(): Promise<LibrarySyncStatus> { return this.invoke("disconnect_sync"); }
  async syncNow(): Promise<LibrarySyncStatus> { return this.invoke("sync_now"); }
  async resolveSyncChoice(choice: LibrarySyncChoice): Promise<LibrarySyncStatus> { return this.invoke("resolve_sync_choice", choice); }
  async setSyncPassphrase(passphrase: string | null): Promise<LibrarySyncStatus> { return this.invoke("set_sync_passphrase", passphrase); }
  async unlockSync(passphrase: string): Promise<LibrarySyncStatus> { return this.invoke("unlock_sync", passphrase); }

  async browseFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_file", startPath);
  }

  async browseRunnerFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_runner_file", startPath);
  }

  async browseBackupFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_backup_file", startPath);
  }

  async browsePdfFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_pdf_file", startPath);
  }

  async listGameAttachments(gameId: number): Promise<GameAttachmentsResponse> {
    return this.invoke<GameAttachmentsResponse>("list_game_attachments", gameId);
  }

  async addGameAttachment(
    gameId: number,
    sourcePath: string,
  ): Promise<AddGameAttachmentResponse> {
    return this.invoke<AddGameAttachmentResponse>(
      "add_game_attachment",
      gameId,
      sourcePath,
    );
  }

  async openGameAttachment(gameId: number, name: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_game_attachment", gameId, name);
  }

  async removeGameAttachment(
    gameId: number,
    name: string,
  ): Promise<RemoveGameAttachmentResponse> {
    return this.invoke<RemoveGameAttachmentResponse>(
      "remove_game_attachment",
      gameId,
      name,
    );
  }

  async browseDirectory(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_directory", startPath);
  }

  async browseArchiveFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_archive_file", startPath);
  }

  async inspectArchive(archivePath: string, gameId: number | null = null): Promise<ArchiveInspectResponse> {
    return this.invoke<ArchiveInspectResponse>("inspect_archive", archivePath, gameId);
  }

  async installArchive(options: ArchiveInstallOptions): Promise<ArchiveInstallResponse> {
    return this.invokeLibraryChange<ArchiveInstallResponse>("install_archive", options);
  }

  async getArchiveInstallStatus(): Promise<ArchiveInstallStatus> {
    return this.invoke<ArchiveInstallStatus>("get_archive_install_status");
  }

  async findDownloadOffers(): Promise<DownloadOffersResponse> {
    return this.invoke<DownloadOffersResponse>("find_download_offers");
  }

  async dismissDownloadOffer(key: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("dismiss_download_offer", key);
  }

  async installRpgmakerDependencies(
    prefix_path: string | null = null,
    proton_path: string | null = null
  ): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>(
      "install_rpgmaker_dependencies",
      prefix_path,
      proton_path,
    );
  }

  async installRpgmakerRtp(
    prefix_path: string | null = null,
    proton_path: string | null = null
  ): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("install_rpgmaker_rtp", prefix_path, proton_path);
  }

  async downloadProtonGe(): Promise<DownloadProtonResponse> {
    return this.invoke<DownloadProtonResponse>("download_proton_ge");
  }

  async getProtonDownloadStatus(): Promise<ProtonDownloadStatus> {
    return this.invoke<ProtonDownloadStatus>("get_proton_download_status");
  }

  async openDevTools(): Promise<void> {
    await this.invoke<void>("open_dev_tools");
  }

  async check_app_updates(): Promise<AppUpdateResponse> {
    return this.invoke<AppUpdateResponse>("check_app_updates");
  }

  async get_app_version(): Promise<AppVersionResponse> {
    return this.invoke<AppVersionResponse>("get_app_version");
  }

  async isCheatEngineInstalled(): Promise<CheatEngineStatusResponse> {
    return this.invoke<CheatEngineStatusResponse>("is_cheat_engine_installed");
  }

  async downloadCheatEngine(): Promise<DownloadCheatEngineResponse> {
    return this.invoke<DownloadCheatEngineResponse>("download_cheat_engine");
  }

  async getInstallStatus(
    prefix_path: string | null = null,
    proton_path: string | null = null
  ): Promise<InstallStatusResponse> {
    return this.invoke<InstallStatusResponse>("get_install_status", prefix_path, proton_path);
  }

  async findSaveFiles(
    exe_path: string,
    title = "",
    engine = "",
    custom_prefix = "",
    proton_version = ""
  ): Promise<SaveLocation[]> {
    return this.invoke<SaveLocation[]>(
      "find_save_files",
      exe_path,
      title,
      engine,
      custom_prefix,
      proton_version,
    );
  }

  async openFolder(path: string): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("open_folder", path);
  }

  async getSystemDepsCommand(): Promise<SystemDepsCommandResponse> {
    return this.invoke<SystemDepsCommandResponse>("get_system_deps_command");
  }

  // Fallback mocks
  _mockResponse(method: string, args: unknown[]): unknown {
    const unavailable: ApiErrorResult = {
      success: false,
      mock: true,
      error: `Backend method '${method}' is unavailable outside PyWebView`,
    };

    switch (method) {
      case "get_games":
        return readMockGames().map((game) => this._mockPendingMetadata.has(game.id) ? { ...game, metadata_pending: true } : game);
      case "add_game": {
        const games = readMockGames();
        const id = games.reduce((max, game) => Math.max(max, game.id), 0) + 1;
        const game: GameRecord = { id, title: String(args[0]), exe_path: String(args[1]),
          f95_url: String(args[2] || ""), version: String(args[3] || ""),
          engine: String(args[8] || ""), launch_mode: args[14] as LaunchMode,
          command_line_args: String(args[15] || ""), play_status: "Not Started", thread_completed: false };
        localStorage.setItem("wlib-mock-games", JSON.stringify([...games, game]));
        const metadataPending = !!game.f95_url;
        if (metadataPending) {
          // Simulates the backend's background F95 metadata fetch.
          this._mockPendingMetadata.add(id);
          setTimeout(() => {
            this._mockPendingMetadata.delete(id);
            window.dispatchEvent(new Event("wlib-refresh-library"));
          }, 3000);
        }
        // Mirrors the backend: success, id, title and metadata_pending.
        return { success: true, mock: true, id, title: game.title, metadata_pending: metadataPending };
      }
      case "update_game": {
        const games = readMockGames();
        const fields = isRecord(args[1]) ? args[1] : {};
        let filledFinishedVersion: string | undefined;
        const nextGames = games.map((game) => {
          if (game.id !== Number(args[0])) return game;
          const merged: Record<string, unknown> = { ...game, ...fields };
          if (typeof merged.finished_version === "string") {
            merged.finished_version = merged.finished_version.trim();
          }
          const finishedSupplied =
            fields.finished_version !== undefined && fields.finished_version !== null;
          if (
            !finishedSupplied &&
            merged.play_status === "Completed" &&
            game.play_status !== "Completed"
          ) {
            // Mirrors the backend: reaching Completed records the installed version.
            filledFinishedVersion =
              String(fields.version ?? "").trim() || String(game.version ?? "").trim();
            merged.finished_version = filledFinishedVersion;
          }
          return merged;
        });
        localStorage.setItem("wlib-mock-games", JSON.stringify(nextGames));
        return filledFinishedVersion !== undefined
          ? { success: true, mock: true, finished_version: filledFinishedVersion }
          : { success: true, mock: true };
      }
      case "delete_game": {
        const gameId = Number(args[0]);
        localStorage.setItem("wlib-mock-games", JSON.stringify(readMockGames().filter((game) => game.id !== gameId)));
        writeMockLaunchTargets(readMockLaunchTargets().filter((target) => target.game_id !== gameId));
        const attachments = readMockAttachments();
        delete attachments[String(gameId)];
        writeMockAttachments(attachments);
        return { success: true, mock: true };
      }
      case "launch_game": {
        const gameId = Number(args[0]);
        if (this._mockRunningGames.has(gameId)) return { success: false, code: "already_running", error: "Game is already running" };
        if (!/\.html?$/i.test(String(args[1])) || args[8] === "custom") this._mockRunningGames.add(gameId);
        return { success: true, mock: true };
      }
      case "get_running_games":
        return [...this._mockRunningGames];
      case "stop_game": {
        const gameId = Number(args[0]);
        if (!this._mockRunningGames.delete(gameId)) return { success: false, code: "not_running", error: "Game is not running" };
        window.dispatchEvent(new CustomEvent("wlib-playtime-tick", { detail: { gameId, delta: 0, isFinal: true } }));
        return { success: true, mock: true };
      }
      case "get_urm_status":
      case "set_urm_installed": {
        const gameId = Number(args[0]);
        const game = readMockGames().find((game) => game.id === gameId);
        if (!game) return { success: false, error: "Game not found" };
        const renpy = game.engine === "Ren'Py";
        const sourceConfigured = /\.rpa$/i.test(readMockSettings().urm_rpa_path);
        if (method === "set_urm_installed") {
          if (!renpy) return { success: false, error: "This game is not a Ren'Py game" };
          if (args[1] && !sourceConfigured) return { success: false, error: "Configure a URM source in Settings" };
          if (args[1]) this._mockUrmInstalled.add(gameId);
          else this._mockUrmInstalled.delete(gameId);
        }
        return { success: true, renpy, installed: this._mockUrmInstalled.has(gameId), source_configured: sourceConfigured };
      }
      case "get_settings":
        return readMockSettings();
      case "get_collections":
        return readMockSettings().collections;
      case "create_collection": {
        const name = String(args[0] || "");
        const rawGameId = args[1];
        const gameId = rawGameId == null ? null : Number(rawGameId);
        const collections = readMockSettings().collections;
        const error = validateMockCollectionList([...collections, name]);
        if (error) return { success: false, mock: true, error };
        // The backend rejects ids that are not usable row ids before it touches
        // the collection, and refuses ids that match no game.
        if (gameId !== null) {
          if (!Number.isInteger(gameId) || gameId < 1) {
            return { success: false, mock: true, error: "Game not found", error_code: "game_not_found" };
          }
          if (!readMockGames().some((game) => game.id === gameId)) {
            return { success: false, mock: true, error: "Game not found" };
          }
        }
        const trimmed = name.trim();
        collections.push(trimmed);
        writeMockCollections(collections);
        if (gameId !== null && Number.isFinite(gameId)) {
          rewriteMockGameCollections((names, game) =>
            game.id === gameId ? [...names, trimmed] : names,
          );
        }
        return { success: true, mock: true, collections };
      }
      case "rename_collection": {
        const oldName = String(args[0] || "");
        const newName = String(args[1] || "");
        const collections = readMockSettings().collections;
        const oldKey = oldName.trim().toLowerCase();
        const index = collections.findIndex((name) => name.toLowerCase() === oldKey);
        if (index === -1) return { success: false, mock: true, error: `Collection '${oldName}' does not exist` };
        const trimmed = newName.trim();
        const error = validateMockCollectionList([
          ...collections.filter((_name, i) => i !== index),
          trimmed,
        ]);
        if (error) return { success: false, mock: true, error };
        collections[index] = trimmed;
        writeMockCollections(collections);
        rewriteMockGameCollections((names) =>
          names.map((name) => (name.toLowerCase() === oldKey ? trimmed : name)),
        );
        return { success: true, mock: true, collections };
      }
      case "delete_collection": {
        const key = String(args[0] || "").trim().toLowerCase();
        const collections = readMockSettings().collections;
        if (!collections.some((name) => name.toLowerCase() === key)) {
          return { success: false, mock: true, error: `Collection '${args[0]}' does not exist` };
        }
        const remaining = collections.filter((name) => name.toLowerCase() !== key);
        writeMockCollections(remaining);
        rewriteMockGameCollections((names) => names.filter((name) => name.toLowerCase() !== key));
        return { success: true, mock: true, collections: remaining };
      }
      case "reorder_collections": {
        const requested = Array.isArray(args[0]) ? (args[0] as unknown[]).map(String) : [];
        const collections = readMockSettings().collections;
        const byKey = new Map(collections.map((name) => [name.toLowerCase(), name]));
        const reordered: string[] = [];
        for (const name of requested) {
          const canonical = byKey.get(name.trim().toLowerCase());
          if (!canonical || reordered.includes(canonical)) {
            return { success: false, mock: true, error: "Collection order must list every collection once" };
          }
          reordered.push(canonical);
        }
        if (reordered.length !== collections.length) {
          return { success: false, mock: true, error: "Collection order must list every collection once" };
        }
        writeMockCollections(reordered);
        return { success: true, mock: true, collections: reordered };
      }
      case "set_game_collections": {
        const gameId = Number(args[0]);
        const requested = Array.isArray(args[1]) ? (args[1] as unknown[]).map(String) : [];
        const games = readMockGames();
        if (!games.some((game) => game.id === gameId)) {
          return { success: false, mock: true, error: "Game not found" };
        }
        const collections = readMockSettings().collections;
        const byKey = new Map(collections.map((name) => [name.toLowerCase(), name]));
        const members: string[] = [];
        for (const name of requested) {
          const canonical = byKey.get(name.trim().toLowerCase());
          if (!canonical) return { success: false, mock: true, error: `Collection '${name}' does not exist` };
          if (members.includes(canonical)) {
            return { success: false, mock: true, error: `Collection '${canonical}' is listed twice` };
          }
          members.push(canonical);
        }
        rewriteMockGameCollections((_names, game) => (game.id === gameId ? [...members] : _names));
        return { success: true, mock: true, collections: members };
      }
      case "save_library_filter_presets": {
        const presets = Array.isArray(args[0]) ? args[0] : null;
        if (!presets) return { success: false, mock: true, error: "Filter presets must be a list" };
        const names = presets.map((preset) =>
          typeof preset === "object" && preset !== null ? String((preset as Record<string, unknown>).name ?? "") : "",
        );
        if (names.some((name) => !name.trim() || name.trim().length > 40)) {
          return { success: false, mock: true, error: "Filter preset names must contain 1–40 characters" };
        }
        const seen = new Set<string>();
        for (const name of names) {
          if (seen.has(name.trim().toLowerCase())) {
            return { success: false, mock: true, error: `Filter preset '${name.trim()}' already exists` };
          }
          seen.add(name.trim().toLowerCase());
        }
        const next = readMockSettings();
        next.library_filter_presets = normalizeMockFilterPresets(presets);
        writeMockSettings(next);
        return { success: true, mock: true };
      }
      case "check_path": {
        const path = String(args[0] || "").trim();
        const kind = String(args[1] || "");
        if (!["file", "dir", "parent", "runner"].includes(kind)) {
          return { success: false, mock: true, error: `Unknown path kind '${kind}'` };
        }
        if (!path) return { ok: false, reason: "Enter a path" };
        // The mock has no filesystem: paths naming "missing" report missing.
        if (!path.toLowerCase().includes("missing")) return { ok: true, reason: "" };
        const reason =
          kind === "file"
            ? "File not found"
            : kind === "runner"
              ? "Command not found on PATH"
              : "Folder not found";
        return { ok: false, reason };
      }
      case "save_settings": {
        const payload = isRecord(args[0]) ? args[0] : {};
        if (Array.isArray(payload.custom_play_statuses)) {
          if (!payload.custom_play_statuses.every((name) => typeof name === "string")) return { success: false, error: "Play statuses must be names" };
          const error = validateCustomPlayStatuses(payload.custom_play_statuses as string[]);
          if (error) return { success: false, error };
          const removed = readMockSettings().custom_play_statuses.filter((name) => !(payload.custom_play_statuses as string[]).includes(name));
          localStorage.setItem("wlib-mock-games", JSON.stringify(readMockGames().map((game) => removed.some((name) => name.toLowerCase() === game.play_status?.toLowerCase()) ? { ...game, play_status: "Not Started" } : game)));
        }
        const mergedSettings: Record<string, unknown> = {
          ...readMockSettings(),
          ...payload,
        };
        if (
          Object.prototype.hasOwnProperty.call(payload, "rpgmaker_linux_runner_path") &&
          !Object.prototype.hasOwnProperty.call(payload, "rpgmaker_linux_runner_status")
        ) {
          delete mergedSettings.rpgmaker_linux_runner_status;
        }
        const nextSettings = normalizeMockSettings(mergedSettings);
        writeMockSettings(nextSettings);
        return { success: true, mock: true };
      }
      case "export_library_backup": {
        const options = isRecord(args[0]) ? args[0] : {};
        const sections = Array.isArray(options.sections)
          ? (options.sections as LibraryBackupSection[])
          : [];
        const path = typeof args[1] === "string" && args[1].trim()
          ? args[1].trim().endsWith(".json")
            ? args[1].trim()
            : `${args[1].trim()}.json`
          : "~/wlib-library-export.json";
        return {
          success: true,
          mock: true,
          path,
          // write_library_backup reports the sorted written sections and the
          // number of games it serialised.
          selected_sections: [...sections].sort(),
          game_count: readMockGames().length,
          encrypted: typeof args[2] === "string" && args[2].length > 0,
        };
      }
      case "inspect_library_backup":
        if (String(args[0]).includes("encrypted") && !args[1]) return { success: false, mock: true, error: "Enter the passphrase for this file.", error_code: "passphrase_required" };
        if (String(args[0]).includes("encrypted") && args[1] !== "mock-passphrase") return { success: false, mock: true, error: "The file could not be decrypted.", error_code: "decrypt_failed" };
        return {
          success: true,
          mock: true,
          manifest: {
            format: "wlib.library_migration",
            format_version: 1,
            exported_at: new Date().toISOString(),
            app: { name: "wLib", version: "mock" },
            selected_sections: ["user_state", "launch_config", "launch_targets"],
          },
          available_sections: [
            "metadata",
            "user_state",
            "launch_config",
            "launch_targets",
          ],
          counts: {
            total_games: 0,
            matched_games: 0,
            new_games: 0,
            ambiguous_games: 0,
            warnings: 1,
          },
          warnings: [
            {
              type: "cover_references",
              scope: "library",
              message:
                "Browser mock preview only. Desktop imports validate real paths and cover references.",
            },
          ],
          ambiguous_games: [],
        };
      case "import_library_backup": {
        const options = isRecord(args[1]) ? args[1] : {};
        const sections = Array.isArray(options.sections)
          ? (options.sections as LibraryBackupSection[])
          : [];
        if (sections.length === 0) {
          return {
            success: false,
            mock: true,
            error: "Select at least one section to import.",
            error_code: "empty_selection",
          };
        }
        return {
          success: true,
          mock: true,
          created: 0,
          deleted: 0,
          updated: 0,
          skipped: 0,
          ambiguous: 0,
          warnings: 0,
          warning_records: [],
          settings_updated: 0,
          selected_sections: sections,
          created_ids: [],
          updated_ids: [],
        };
      }
      case "get_sync_status":
      case "set_sync_folder":
      case "connect_dropbox":
      case "connect_gdrive":
      case "disconnect_sync":
      case "sync_now":
      case "resolve_sync_choice":
      case "set_sync_passphrase":
      case "unlock_sync": {
        let stored: LibrarySyncStatus = {};
        try { stored = JSON.parse(localStorage.getItem("wlib-mock-sync") || "{}"); } catch { /* Start with sync off when mock data is invalid. */ }
        const status: LibrarySyncStatus = { success: true, mock: true, state: "off", location: "", folder_path: "", account: "", encrypted: false, last_synced_at: "", last_device: "", last_error: "", dropbox_available: true, dropbox_unavailable_reason: "", gdrive_available: true, gdrive_unavailable_reason: "", connecting: false, ...stored };
        // Every successful action goes through SyncManager.start_run(), which
        // puts the syncer in "syncing" before it answers; the poll then reads
        // the finished state. connect_* only starts the OAuth round-trip and
        // answers with the sign-in URL instead of a status payload.
        if (method === "set_sync_folder") Object.assign(status, { state: "idle", location: "folder", folder_path: String(args[0]), last_error: "" });
        if (method === "connect_dropbox") Object.assign(status, { state: "idle", location: "dropbox", account: "Browser mock account", last_error: "" });
        if (method === "connect_gdrive") Object.assign(status, { state: "idle", location: "gdrive", account: "mock@example.com", last_error: "" });
        if (method === "disconnect_sync") Object.assign(status, { state: "off", location: "", encrypted: false, account: "", folder_path: "", last_synced_at: "", last_device: "", last_error: "" });
        if (method === "resolve_sync_choice") Object.assign(status, { state: "idle", last_error: "" });
        if (method === "set_sync_passphrase") status.encrypted = args[0] !== null;
        if (method === "unlock_sync") {
          if (args[0] !== "mock-passphrase") return { ...status, success: false, error: "The file could not be decrypted.", error_code: "decrypt_failed" };
          Object.assign(status, { state: "idle", encrypted: true, last_error: "" });
        }
        localStorage.setItem("wlib-mock-sync", JSON.stringify(status));
        // SyncManager.start_run() only reports a run when a location is set;
        // otherwise status() keeps answering "off".
        if (status.location && ["set_sync_folder", "sync_now", "resolve_sync_choice", "set_sync_passphrase", "unlock_sync"].includes(method)) {
          // The run is asynchronous: report "syncing" now and let the stored
          // status settle once the simulated run finishes.
          const running: LibrarySyncStatus = { ...status, state: "syncing", last_error: "" };
          localStorage.setItem("wlib-mock-sync", JSON.stringify(running));
          setTimeout(() => {
            try {
              let finished: LibrarySyncStatus = { ...running };
              const stored = JSON.parse(localStorage.getItem("wlib-mock-sync") || "{}") as LibrarySyncStatus;
              if (stored.state === "syncing") finished = { ...stored, state: "idle", last_synced_at: new Date().toISOString(), last_device: "Browser mock device" };
              localStorage.setItem("wlib-mock-sync", JSON.stringify(finished));
            } catch { /* Ignore storage that vanished with the preview page. */ }
          }, 900);
          return running;
        }
        if (method === "connect_dropbox" || method === "connect_gdrive") {
          // The desktop bridge answers with the sign-in URL it opened; the
          // status the panel renders comes from the next get_sync_status poll.
          return { success: true, mock: true, url: "" };
        }
        return status;
      }
      case "browse_file":
      case "browse_urm_file":
      case "browse_runner_file":
      case "browse_backup_file":
      case "browse_archive_file":
      case "browse_external_library_file":
      case "browse_directory":
        return "";
      case "browse_pdf_file":
        return "/mock/guide.pdf";
      // Bridges with no browser-side equivalent. Actions that need no host
      // still answer with the real payload shape so the view flow works;
      // download/install actions explain that they need the desktop app.
      case "open_in_browser":
      case "open_folder":
      case "open_extension_folder":
      case "open_dev_tools":
      case "check_all_updates":
      case "cancel_update_check":
      case "set_auto_check_setting":
      case "check_app_updates":
      case "download_proton_ge":
      case "install_rpgmaker_dependencies":
      case "install_rpgmaker_rtp":
      case "download_cheat_engine": {
        // Mocks mirror the real payloads: the actions that need no backend
        // report success, and the ones that download or install runtime
        // software report why they cannot run in a browser.
        if (method === "open_in_browser") {
          const url = String(args[0] || "");
          if (!/^https?:\/\//i.test(url)) return { success: false, mock: true, error: "Invalid URL" };
          window.open(url, "_blank", "noopener");
          return { success: true, mock: true };
        }
        if (method === "open_folder") return { success: true, mock: true };
        if (method === "open_extension_folder") {
          return {
            success: true,
            mock: true,
            updated: false,
            path: "",
            bundled_version: "",
            installed_version: "",
            reason: "mock",
          };
        }
        if (method === "open_dev_tools") return undefined;
        if (method === "check_all_updates") {
          return {
            success: true,
            mock: true,
            total: readMockGames().filter((game) => (game.f95_url || "").trim()).length,
            delay_seconds: 2,
          };
        }
        if (method === "set_auto_check_setting") {
          try { localStorage.setItem("wlib-mock-auto-check", String(args[0])); } catch { /* Preview just forgets the choice. */ }
        }
        if (method === "cancel_update_check" || method === "set_auto_check_setting") {
          return { success: true, mock: true };
        }
        const reason =
          method === "check_app_updates"
            ? "Release checks require the desktop app runtime."
            : method === "download_proton_ge"
              ? "GE-Proton downloads require the desktop app runtime."
              : method === "install_rpgmaker_dependencies"
                ? "Runtime dependency installs require the desktop app runtime."
                : method === "install_rpgmaker_rtp"
                  ? "RPG Maker RTP installs require the desktop app runtime."
                  : "Cheat Engine downloads require the desktop app runtime.";
        return { success: false, mock: true, error: reason };
      }
      case "detect_external_library": {
        const source = typeof args[0] === "string" ? args[0] : "";
        return { success: true, mock: true, source, path: "", checked: [] };
      }
      case "inspect_external_library": {
        const source = typeof args[0] === "string" ? args[0] : "";
        return {
          success: true,
          mock: true,
          source,
          schema_version: "",
          available_sections: ["metadata", "user_state", "executable_paths", "launch_targets"],
          counts: {
            total_games: 0,
            matched_games: 0,
            new_games: 0,
            ambiguous_games: 0,
            skipped_no_identity: 0,
            dropped_paths: 0,
            warnings: 1,
          },
          warnings: [
            {
              type: "cover_references",
              scope: "library",
              message:
                "Browser mock preview only. Desktop imports validate real paths and cover references.",
            },
          ],
          ambiguous_games: [],
        };
      }
      case "import_external_library": {
        const options = isRecord(args[2]) ? args[2] : {};
        const sections = Array.isArray(options.sections)
          ? (options.sections as LibraryBackupSection[])
          : [];
        if (sections.length === 0) {
          return {
            success: false,
            mock: true,
            error: "Select at least one section to import.",
            error_code: "empty_selection",
          };
        }
        return {
          success: true,
          mock: true,
          created: 0,
          deleted: 0,
          updated: 0,
          skipped: 0,
          ambiguous: 0,
          warnings: 0,
          warning_records: [],
          settings_updated: 0,
          selected_sections: sections,
          created_ids: [],
          updated_ids: [],
          skipped_no_identity: 0,
          dropped_paths: 0,
          metadata_check: "none" as const,
          source: typeof args[0] === "string" ? args[0] : "",
          schema_version: "",
        };
      }
      case "inspect_archive": {
        const fileName = String(args[0]).split(/[/\\]/).pop() || "";
        const stem = fileName.replace(/\.(zip|7z|rar)$/i, "");
        const versionMatch = stem.match(/[-_ ]v?(\d[\d._]*[a-z]?)$/i);
        const guessedVersion = versionMatch?.[1] ? versionMatch[1].replace(/_/g, ".") : "";
        const guessedTitle = (versionMatch ? stem.slice(0, versionMatch.index) : stem).replace(/[_-]+/g, " ").trim() || stem;
        const settings = readMockSettings();
        const gameId = typeof args[1] === "number" ? args[1] : null;
        const game = gameId !== null ? readMockGames().find((entry) => entry.id === gameId) : undefined;
        // Mirrors inspect_archive: the archive name is the primary guess, the
        // stored game only fills blanks (and overwrites the version when it has
        // a known latest_version).
        const title = guessedTitle || (game?.title ?? "");
        const version = game?.latest_version?.trim() ? game.latest_version : guessedVersion;
        const creator = game?.developer ?? "";
        // build_destination substitutes the pattern tokens and drops segments
        // that end up empty, falling back to the title.
        const pattern = settings.library_folder_pattern || DEFAULT_MOCK_SETTINGS.library_folder_pattern;
        const segments = pattern
          .split(/[/\\]+/)
          .map((part) => part
            .replace("{creator}", creator)
            .replace("{title}", title)
            .replace("{version}", version)
            .replace("{engine}", "")
            .replace(/\{[^{}]*\}/g, "")
            .trim())
          .filter(Boolean);
        const destination = settings.library_folder
          ? `${settings.library_folder.replace(/[/\\]+$/, "")}/${(segments.length ? segments : [title || "game"]).join("/")}`
          : "";
        const archiveFormat = /\.7z$/i.test(fileName) ? "7z" : /\.rar$/i.test(fileName) ? "rar" : "zip";
        const toolError = archiveFormat === "7z"
          ? "Install 7-Zip (7z) or bsdtar to extract .7z archives."
          : archiveFormat === "rar"
            ? "Install 7-Zip (7z), bsdtar, or unrar to extract .rar archives."
            : "";
        return {
          success: true,
          mock: true,
          title,
          version,
          creator,
          destination,
          tool_error: toolError,
          old_folder: game ? game.exe_path.split(/[/\\]/).slice(0, -1).join("/") : "",
          removable: !!game,
          remove_reason: game ? "" : "No previous install.",
        };
      }
      case "install_archive": {
        this._mockArchiveStatus = { running: true, stage: "extracting", done: 1, total: 3 };
        setTimeout(() => {
          this._mockArchiveStatus = { running: false, stage: "done", done: 3, total: 3 };
          window.dispatchEvent(new Event("wlib-refresh-library"));
        }, 900);
        const options = isRecord(args[0]) ? args[0] : {};
        return {
          success: true,
          mock: true,
          game_id: typeof options.game_id === "number" ? options.game_id : 9001,
          exe_path: String(options.archive_path || ""),
          destination: "",
          unmoved_targets: [],
          kept_old_folder_reason: "",
          removed_old: false,
          old_folder: "",
        };
      }
      case "get_archive_install_status":
        return { ...this._mockArchiveStatus };
      case "find_download_offers":
        return { success: true, mock: true, offers: [] };
      case "dismiss_download_offer":
        return { success: true, mock: true };
      case "inspect_game_path": {
        // The backend rejects a missing file; the mock has no filesystem, so it
        // keeps the same error for the one case it can detect.
        if (!String(args[0] || "").trim()) {
          return { success: false, mock: true, error: "File not found" };
        }
        const folder = String(args[0]).split(/[\/]/).slice(-2, -1)[0] || "";
        return { success: true, mock: true, title: folder, version: "", engine: "", exe_path: String(args[0]) };
      }
      case "scan_games_folder": {
        if (!String(args[0] || "").trim()) {
          return { success: false, mock: true, error: "Folder not found" };
        }
        const root = String(args[0]).replace(/[\/]+$/, "");
        const known = new Set(readMockGames().map((game) => game.exe_path));
        return { success: true, mock: true, games: [
          { title: "Eternum", version: "0.6", engine: "Ren'Py", exe_path: `${root}/Eternum-0.6-pc/Eternum.exe` },
          { title: "Quest", version: "1.2", engine: "RPGM", exe_path: `${root}/Quest-1.2/Game.exe` },
        ].map((game) => ({ ...game, in_library: known.has(game.exe_path) })) };
      }
      case "get_available_runners":
        return { success: true, mock: true, runners: localStorage.getItem("wlib-mock-platform") === "linux" ? [
          { name: "System Wine", path: "wine" },
          { name: "GE-Proton (Steam)", path: "/home/user/.local/share/Steam/compatibilitytools.d/GE-Proton/proton" },
          { name: "wine-ge (Lutris)", path: "/home/user/.local/share/lutris/runners/wine/wine-ge/bin/wine" },
        ] : [] };
      case "check_for_updates": {
        const url = String(args[0] || "").trim();
        if (!url) return { success: false, mock: true, error: "A valid thread URL is required" };
        // Mock scraper fallback failures (mirrors _build_scraper_error_payload).
        if (url.includes("mock-blocked-unavailable")) {
          return {
            success: false,
            mock: true,
            error: "Blocked by anti-bot challenge. Keep a browser with the wLib extension open and try again.",
            error_code: "blocked",
            browser_fallback: "unavailable" as const,
          };
        }
        if (url.includes("mock-blocked-failed")) {
          return {
            success: false,
            mock: true,
            error: "Blocked by anti-bot challenge. Open F95Zone in that browser, complete any check it shows, and try again.",
            error_code: "blocked",
            browser_fallback: "failed" as const,
          };
        }
        if (url.includes("mock-login")) {
          return {
            success: false,
            mock: true,
            error: "Login is required. Log in to F95Zone in the browser where the wLib extension is installed.",
            error_code: "login_required",
            browser_fallback: "failed" as const,
          };
        }
        const games = readMockGames();
        const game = games.find((entry) => entry.f95_url === url);
        // The backend scrapes the thread for every URL it is given; with no
        // matching game the installed version is unknown, which counts as an
        // update. The mock cannot scrape, so it reports a placeholder version.
        if (!game) {
          return { success: true, mock: true, version: "1.0", has_update: true, newly_finished: false, metadata_updated: 0 };
        }
        const completedIds = JSON.parse(localStorage.getItem("wlib-mock-completed-threads") || "[]") as unknown;
        const completed = Array.isArray(completedIds) && completedIds.includes(game.id);
        const newlyFinished = completed && !game.thread_completed && !game.exe_path.trim();
        const needsScreenshots = !(game.screenshots && game.screenshots.length);
        if (completed !== Boolean(game.thread_completed) || needsScreenshots) {
          localStorage.setItem("wlib-mock-games", JSON.stringify(games.map((entry) =>
            entry.id === game.id ? { ...entry, thread_completed: completed, screenshots: needsScreenshots ? [...MOCK_SCREENSHOT_SET_B] : entry.screenshots } : entry,
          )));
        }
        return {
          success: true,
          mock: true,
          version: game.latest_version || game.version || "",
          has_update: !!game.exe_path.trim() && !!game.latest_version && game.latest_version !== game.version,
          newly_finished: newlyFinished,
          metadata_updated: 0,
        };
      }
      case "get_update_status":
        return {
          running: false,
          cancelling: false,
          outcome: "idle",
          error: "",
          total: 0,
          checked: 0,
          current: "",
          results: [],
          delay_seconds: 2,
        };
      case "get_auto_check_setting":
        try { return { frequency: localStorage.getItem("wlib-mock-auto-check") || "weekly", last_check: "" }; } catch { return { frequency: "weekly", last_check: "" }; }
      case "maybe_auto_check":
        return { triggered: false, reason: "mock" };
      case "get_proton_download_status":
        return { running: false, stage: "", done: 0, total: 0 };
      case "get_install_status":
        return {
          // The backend always reports success for the status read itself; only
          // the unsupported-platform payload reports success: false.
          success: true,
          deps: { running: false, done: 0, total: 0, current: "", error: "" },
          rtps: { running: false, done: 0, total: 0, current: "", error: "" },
          dlls_installed: false,
          rtps_installed: false,
        };
      case "get_platform_capabilities":
        if (localStorage.getItem("wlib-mock-platform") === "linux") return {
          ...CONSERVATIVE_PLATFORM_CAPABILITIES, platform: "linux", wine_proton: true,
          runtime_installers: true, wayland: true, rpgmaker_linux: true, cheat_engine_injection: true,
          launch_modes: LAUNCH_MODE_OPTIONS.map((option) => option.value),
        };
        return { ...CONSERVATIVE_PLATFORM_CAPABILITIES };
      case "get_system_deps_command":
        return {
          detected: false,
          package_manager: "unknown",
          distro: "Unknown",
          command:
            "# Backend unavailable in browser mode. Run this in the desktop app.",
          error: "System dependency detection requires the desktop app runtime.",
          code: "unsupported_platform",
        };
      case "get_extension_service_status":
        // get_extension_service_status only reports reachability; the read itself
        // succeeds even when the extension server is down.
        return {
          success: true,
          mock: true,
          reachable: false,
          error: "Extension service status requires the desktop app runtime.",
        };
      case "get_startup_extension_sync_status":
        return {
          success: true,
          mock: true,
          updated: false,
          path: "",
          bundled_version: "",
          installed_version: "",
          reason: "mock",
        };
      case "get_app_version":
        return { version: "" };
      case "is_cheat_engine_installed":
        return { installed: false, path: "" };
      case "get_executable_modified_time":
        return { success: false, modified_at: null, mock: true, error: "Executable timestamps require the desktop app runtime." };
      case "get_launch_targets": {
        const gameId = Number(args[0]);
        return readMockLaunchTargets()
          .filter((target) => target.game_id === gameId)
          .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
      }
      case "create_launch_target": {
        const gameId = Number(args[0]);
        const label = typeof args[1] === "string" ? args[1].trim() : "";
        const exePath = typeof args[2] === "string" ? args[2].trim() : "";
        // game_launch_targets has a FOREIGN KEY on games(id), so the backend
        // reports a missing parent game instead of creating an orphan target.
        if (!Number.isInteger(gameId) || gameId < 1 || !readMockGames().some((game) => game.id === gameId)) {
          return {
            success: false,
            mock: true,
            error: "Parent game was not found",
            error_code: "game_not_found",
          };
        }
        if (!label || !exePath) {
          return {
            success: false,
            mock: true,
            error: "Launch target label and executable path are required.",
            error_code: "invalid_target",
          };
        }
        const targets = readMockLaunchTargets();
        const requestedOrder = Number(args[3]);
        const fallbackOrder = targets
          .filter((target) => target.game_id === gameId)
          .reduce((max, target) => Math.max(max, target.sort_order + 1), 0);
        const now = new Date().toISOString();
        const target: LaunchTarget = {
          id: nextMockLaunchTargetId(targets),
          game_id: gameId,
          label,
          exe_path: exePath,
          sort_order: Number.isFinite(requestedOrder) ? Math.max(0, Math.trunc(requestedOrder)) : fallbackOrder,
          created_at: now,
          updated_at: now,
        };
        writeMockLaunchTargets([...targets, target]);
        return { success: true, mock: true, target };
      }
      case "update_launch_target": {
        const targetId = Number(args[0]);
        const fields = isRecord(args[1]) ? args[1] : {};
        const targets = readMockLaunchTargets();
        const index = targets.findIndex((target) => target.id === targetId);
        if (index === -1) {
          return {
            success: false,
            mock: true,
            error: "Launch target was not found.",
            error_code: "target_not_found",
          };
        }

        const existingTarget = targets[index];
        if (!existingTarget) {
          return {
            success: false,
            mock: true,
            error: "Launch target was not found.",
            error_code: "target_not_found",
          };
        }

        const nextTarget: LaunchTarget = { ...existingTarget };
        if (Object.prototype.hasOwnProperty.call(fields, "label")) {
          const label = typeof fields.label === "string" ? fields.label.trim() : "";
          if (!label) {
            return { success: false, mock: true, error: "Launch target label is required.", error_code: "invalid_target" };
          }
          nextTarget.label = label;
        }
        if (Object.prototype.hasOwnProperty.call(fields, "exe_path")) {
          const exePath = typeof fields.exe_path === "string" ? fields.exe_path.trim() : "";
          if (!exePath) {
            return { success: false, mock: true, error: "Launch target executable path is required.", error_code: "invalid_target" };
          }
          nextTarget.exe_path = exePath;
        }
        if (Object.prototype.hasOwnProperty.call(fields, "sort_order")) {
          const sortOrder = Number(fields.sort_order);
          nextTarget.sort_order = Number.isFinite(sortOrder) ? Math.max(0, Math.trunc(sortOrder)) : nextTarget.sort_order;
        }
        nextTarget.updated_at = new Date().toISOString();
        targets[index] = nextTarget;
        writeMockLaunchTargets(targets);
        return { success: true, mock: true, target: nextTarget };
      }
      case "delete_launch_target": {
        const targetId = Number(args[0]);
        const targets = readMockLaunchTargets();
        const nextTargets = targets.filter((target) => target.id !== targetId);
        if (nextTargets.length === targets.length) {
          return {
            success: false,
            mock: true,
            error: "Launch target was not found.",
            error_code: "target_not_found",
          };
        }
        writeMockLaunchTargets(nextTargets);
        return { success: true, mock: true };
      }
      case "reorder_launch_targets": {
        const gameId = Number(args[0]);
        const targetIds = Array.isArray(args[1]) ? args[1].map((id) => Number(id)) : [];
        const targets = readMockLaunchTargets();
        const gameTargets = targets.filter((target) => target.game_id === gameId);
        const existingIds = new Set(gameTargets.map((target) => target.id));
        const requestedIds = new Set(targetIds);
        const validOrder =
          targetIds.length === gameTargets.length &&
          requestedIds.size === targetIds.length &&
          targetIds.every((targetId) => existingIds.has(targetId));
        if (!validOrder) {
          return {
            success: false,
            mock: true,
            error: "Launch target order must include all targets for the game.",
            error_code: "invalid_target_order",
          };
        }
        const orderById = new Map(targetIds.map((targetId, index) => [targetId, index]));
        const now = new Date().toISOString();
        const nextTargets = targets.map((target) =>
          target.game_id === gameId
            ? { ...target, sort_order: orderById.get(target.id) ?? target.sort_order, updated_at: now }
            : target,
        );
        writeMockLaunchTargets(nextTargets);
        return {
          success: true,
          mock: true,
          targets: nextTargets
            .filter((target) => target.game_id === gameId)
            .sort((a, b) => a.sort_order - b.sort_order || a.id - b.id),
        };
      }
      case "find_save_files":
        return [];
      case "list_game_attachments": {
        const gameId = Number(args[0]);
        if (!Number.isInteger(gameId) || gameId < 1 || !readMockGames().some((game) => game.id === gameId)) {
          return { success: false, mock: true, error: "Game not found", error_code: "game_not_found" };
        }
        return { success: true, mock: true, attachments: mockAttachmentList(gameId) };
      }
      case "add_game_attachment": {
        const gameId = Number(args[0]);
        const sourcePath = typeof args[1] === "string" ? args[1] : "";
        if (!Number.isInteger(gameId) || gameId < 1 || !readMockGames().some((game) => game.id === gameId)) {
          return { success: false, mock: true, error: "Game not found", error_code: "game_not_found" };
        }
        if (!/\.pdf$/i.test(sourcePath.trim())) {
          return { success: false, mock: true, error: "Only PDF files can be attached", error_code: "not_a_pdf" };
        }
        const baseName = sourcePath.split(/[/\\]/).pop() || "guide.pdf";
        const store = readMockAttachments();
        const existing = store[String(gameId)] || [];
        const stem = baseName.replace(/\.pdf$/i, "");
        let candidate = baseName;
        let counter = 2;
        const names = new Set(existing.map((item) => item.name.toLowerCase()));
        while (names.has(candidate.toLowerCase())) {
          candidate = `${stem} (${counter}).pdf`;
          counter += 1;
        }
        const attachment = { name: candidate, size: 1024 };
        const next = [...existing, attachment].sort((a, b) =>
          a.name.toLowerCase().localeCompare(b.name.toLowerCase()),
        );
        store[String(gameId)] = next;
        writeMockAttachments(store);
        return { success: true, mock: true, attachment, attachments: next };
      }
      case "open_game_attachment": {
        const gameId = Number(args[0]);
        const name = typeof args[1] === "string" ? args[1] : "";
        if (!Number.isInteger(gameId) || gameId < 1 || !readMockGames().some((game) => game.id === gameId)) {
          return { success: false, mock: true, error: "Game not found", error_code: "game_not_found" };
        }
        const found = mockAttachmentList(gameId).some((item) => item.name === name);
        if (!found) {
          return { success: false, mock: true, error: "Attachment file is missing", error_code: "attachment_missing" };
        }
        return { success: true, mock: true };
      }
      case "remove_game_attachment": {
        const gameId = Number(args[0]);
        const name = typeof args[1] === "string" ? args[1] : "";
        if (!Number.isInteger(gameId) || gameId < 1 || !readMockGames().some((game) => game.id === gameId)) {
          return { success: false, mock: true, error: "Game not found", error_code: "game_not_found" };
        }
        const existing = mockAttachmentList(gameId);
        if (!existing.some((item) => item.name === name)) {
          return { success: false, mock: true, error: "Attachment file is missing", error_code: "attachment_missing" };
        }
        const store = readMockAttachments();
        store[String(gameId)] = existing.filter((item) => item.name !== name);
        writeMockAttachments(store);
        return { success: true, mock: true, attachments: store[String(gameId)] || [] };
      }
      default:
        return unavailable;
    }
  }
}

export const api = new ApiService();

// Let PyWebview inject before we consider it fully ready, though in Vue we usually just call it on mount
export function onWebviewReady(callback: () => void): void {
  if (window.pywebview) {
    callback();
  } else if (/QtWebEngine\//.test(navigator.userAgent)) {
    // Desktop shell (always gui="qt"): the API is injected after load, so wait
    // instead of running once against mocks and again for real.
    window.addEventListener("pywebviewready", callback, { once: true });
  } else {
    // Plain browser (UI-only dev): pywebviewready never fires, so use mocks now.
    callback();
  }
}
