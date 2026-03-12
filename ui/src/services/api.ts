/**
 * A wrapper service to communicate with the Python backend via PyWebView.
 * It gracefully falls back to mock data if the webview object isn't injected
 * (e.g., when running in normal browser).
 */

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
}

export interface ApiSuccessResponse {
  success: true;
}

export interface AddGameResponse extends ApiBasicResponse {
  id?: number | null;
  title?: string;
  metadata_updated?: number;
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
  is_favorite?: boolean;
  rating_graphics?: number;
  rating_story?: number;
  rating_fappability?: number;
  rating_gameplay?: number;
  thread_main_post_last_edit_at?: string | null;
  thread_main_post_checked_at?: string | null;
}

export interface SettingsPayload {
  proton_path?: string;
  wine_prefix_path?: string;
  enable_logging?: boolean;
  playwright_browsers_path?: string;
}

export interface SettingsResponse {
  proton_path: string;
  wine_prefix_path: string;
  enable_logging: boolean;
  playwright_browsers_path: string;
}

declare global {
  interface Window {
    pywebview?: PyWebViewBridge;
  }
}

class ApiService {
  constructor() {
    this.isWebview = window.pywebview !== undefined;
    this._mockWarnings = new Set();
  }

  isWebview: boolean;
  _mockWarnings: Set<string>;

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
    proton_version = ""
  ): Promise<AddGameResponse> {
    return this.invoke(
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
    );
  }

  async deleteGame(id: number): Promise<ApiBasicResponse> {
    return this.invoke("delete_game", id);
  }

  async updateGame(id: number, fields: Record<string, unknown>): Promise<ApiBasicResponse> {
    return this.invoke("update_game", id, fields);
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
    return this.invoke<UpdateCheckResponse>("check_for_updates", url);
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
    proton_version = ""
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
    );
  }

  async getAvailableRunners(): Promise<GetAvailableRunnersResponse> {
    return this.invoke<GetAvailableRunnersResponse>("get_available_runners");
  }

  async getExecutableModifiedTime(exe_path: string): Promise<ExecutableModifiedTimeResponse> {
    return this.invoke<ExecutableModifiedTimeResponse>("get_executable_modified_time", exe_path);
  }

  async getSettings(): Promise<SettingsResponse> {
    return this.invoke<SettingsResponse>("get_settings");
  }

  async saveSettings(settings: SettingsPayload): Promise<ApiBasicResponse> {
    return this.invoke<ApiBasicResponse>("save_settings", settings);
  }

  async browseFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_file", startPath);
  }

  async browseRunnerFile(startPath = ""): Promise<string> {
    return this.invoke<string>("browse_runner_file", startPath);
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
  _mockResponse(method: string, _args: unknown[]): unknown {
    const unavailable: ApiErrorResult = {
      success: false,
      mock: true,
      error: `Backend method '${method}' is unavailable outside PyWebView`,
    };

    switch (method) {
      case "get_games":
        return [];
      case "get_settings":
        return {
          proton_path: "",
          wine_prefix_path: "",
          enable_logging: false,
          playwright_browsers_path: "~/.cache/ms-playwright",
        };
      case "browse_file":
      case "browse_runner_file":
      case "browse_directory":
        return "";
      case "get_available_runners":
        return { success: true, mock: true, runners: [] };
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
      case "get_install_status":
        return {
          deps: { running: false, done: 0, total: 0, current: "", error: "" },
          rtps: { running: false, done: 0, total: 0, current: "", error: "" },
          dlls_installed: false,
          rtps_installed: false,
        };
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
          path: "~/.local/share/wLib/extension",
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
  } else {
    window.addEventListener("pywebviewready", callback);
  }
}
