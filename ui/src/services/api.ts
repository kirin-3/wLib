// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * A wrapper service to communicate with the Python backend via PyWebView.
 * It gracefully falls back to mock data if the webview object isn't injected
 * (e.g., when running in normal browser).
 */

import type { LaunchMode } from "../utils/launchMode";
import { LAUNCH_MODE_OPTIONS } from "../utils/launchMode";
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
  metadata_updated?: number;
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
  error: string;
  error_code: string;
}

export interface UpdateStatusResponse extends ApiBasicResponse {
  running: boolean;
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

export interface InstallStatusResponse {
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

export interface ScraperSessionResponse extends ApiBasicResponse {
  success: boolean;
  message?: string;
  code?: string;
}

export interface CheatEngineStatusResponse {
  installed: boolean;
  path: string;
}

export interface DownloadCheatEngineResponse extends ApiBasicResponse {
  success: boolean;
  path?: string;
}

export interface ExecutableModifiedTimeResponse extends ApiBasicResponse {
  success: boolean;
  modified_at: string | null;
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
  skipped?: number;
  ambiguous?: number;
  warnings?: number;
  warning_records?: LibraryBackupWarning[];
  settings_updated?: number;
  selected_sections?: LibraryBackupSection[];
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
  playtime_seconds?: number;
  last_played?: string;
  date_added?: string;
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
  launch_targets?: LaunchTarget[];
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
  playwright_browsers_path: string;
  extension_dir: string;
}

export interface SettingsPayload {
  custom_play_statuses?: string[];
  urm_rpa_path?: string;
  proton_path?: string;
  wine_prefix_path?: string;
  enable_logging?: boolean;
  playwright_browsers_path?: string;
  rpgmaker_linux_runner_path?: string;
}

export interface UrmStatusResponse extends ApiBasicResponse {
  renpy?: boolean;
  installed?: boolean;
  source_configured?: boolean;
}

export interface SettingsResponse {
  custom_play_statuses: string[];
  urm_rpa_path: string;
  proton_path: string;
  wine_prefix_path: string;
  enable_logging: boolean;
  playwright_browsers_path: string;
  rpgmaker_linux_runner_path: string;
  rpgmaker_linux_runner_status: RpgmakerLinuxRunnerStatus;
}

const MOCK_SETTINGS_STORAGE_KEY = "wlib-mock-settings";
const MOCK_LAUNCH_TARGETS_STORAGE_KEY = "wlib-mock-launch-targets";
const DEFAULT_MOCK_SETTINGS: SettingsResponse = {
  custom_play_statuses: [],
  urm_rpa_path: "",
  proton_path: "",
  wine_prefix_path: "",
  enable_logging: false,
  playwright_browsers_path: "",
  rpgmaker_linux_runner_path: "",
  rpgmaker_linux_runner_status: {
    available: false,
    path: "",
    source: "",
    configured_path: "",
    error: "RPGMaker Linux runner is not installed or configured.",
  },
};

const isRecord = (value: unknown): value is Record<string, unknown> => {
  return typeof value === "object" && value !== null;
};

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
    playwright_browsers_path:
      typeof source.playwright_browsers_path === "string"
        ? source.playwright_browsers_path
        : DEFAULT_MOCK_SETTINGS.playwright_browsers_path,
    rpgmaker_linux_runner_path: rpgmakerRunnerPath,
    rpgmaker_linux_runner_status: rpgmakerRunnerStatus,
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

  async updateGame(id: number, fields: Record<string, unknown>): Promise<ApiBasicResponse> {
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
    return this.updateGame(game.id, { version: game.latest_version!.trim() });
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

  async openScraperLoginSession(): Promise<ScraperSessionResponse> {
    return this.invoke<ScraperSessionResponse>("open_scraper_login_session");
  }

  async resetScraperSession(): Promise<ScraperSessionResponse> {
    return this.invoke<ScraperSessionResponse>("reset_scraper_session");
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
    destinationPath: string
  ): Promise<LibraryBackupExportResponse> {
    return this.invoke<LibraryBackupExportResponse>(
      "export_library_backup",
      options,
      destinationPath,
    );
  }

  async inspectLibraryBackup(path: string): Promise<LibraryBackupInspectResponse> {
    return this.invoke<LibraryBackupInspectResponse>("inspect_library_backup", path);
  }

  async importLibraryBackup(
    path: string,
    options: LibraryBackupOptions
  ): Promise<LibraryBackupImportResponse> {
    return this.invoke<LibraryBackupImportResponse>(
      "import_library_backup",
      path,
      options,
    );
  }

  async browseFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_file", startPath);
  }

  async browseRunnerFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_runner_file", startPath);
  }

  async browseBackupFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_backup_file", startPath);
  }

  async browseDirectory(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_directory", startPath);
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
          command_line_args: String(args[15] || ""), play_status: "Not Started" };
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
        return { success: true, id, mock: true, metadata_pending: metadataPending };
      }
      case "update_game": {
        const games = readMockGames();
        const fields = isRecord(args[1]) ? args[1] : {};
        localStorage.setItem("wlib-mock-games", JSON.stringify(games.map((game) => game.id === Number(args[0]) ? { ...game, ...fields } : game)));
        return { success: true, mock: true };
      }
      case "delete_game": {
        const gameId = Number(args[0]);
        localStorage.setItem("wlib-mock-games", JSON.stringify(readMockGames().filter((game) => game.id !== gameId)));
        writeMockLaunchTargets(readMockLaunchTargets().filter((target) => target.game_id !== gameId));
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
          selected_sections: sections,
          game_count: 0,
        };
      }
      case "inspect_library_backup":
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
          updated: 0,
          skipped: 0,
          ambiguous: 0,
          warnings: 0,
          warning_records: [],
          settings_updated: 0,
          selected_sections: sections,
        };
      }
      case "browse_file":
      case "browse_urm_file":
      case "browse_runner_file":
      case "browse_backup_file":
      case "browse_directory":
        return "";
      case "inspect_game_path": {
        const folder = String(args[0]).split(/[\/]/).slice(-2, -1)[0] || "";
        return { success: true, mock: true, title: folder, version: "", engine: "", exe_path: String(args[0]) };
      }
      case "scan_games_folder": {
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
      case "get_update_status":
        return {
          running: false,
          total: 0,
          checked: 0,
          current: "",
          results: [],
          delay_seconds: 5,
        };
      case "get_auto_check_setting":
        return { frequency: "weekly", last_check: "" };
      case "maybe_auto_check":
        return { triggered: false, reason: "mock" };
      case "get_proton_download_status":
        return { running: false, stage: "", done: 0, total: 0 };
      case "get_install_status":
        return {
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
        };
      case "open_scraper_login_session":
      case "reset_scraper_session":
        return {
          success: false,
          mock: true,
          error: "Scraper session controls require the desktop app runtime.",
        };
      case "get_extension_service_status":
        return {
          success: false,
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
        if (!Number.isFinite(gameId) || !label || !exePath) {
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
