/**
 * A wrapper service to communicate with the Python backend via PyWebView.
 * It gracefully falls back to mock data if the webview object isn't injected (e.g., when running in normal browser).
 */

class ApiService {
  constructor() {
    this.isWebview = window.pywebview !== undefined;
  }

  async invoke(method, ...args) {
    if (
      window.pywebview &&
      window.pywebview.api &&
      window.pywebview.api[method]
    ) {
      return await window.pywebview.api[method](...args);
    } else {
      console.warn(`[ApiService] Mocking call to ${method}`, args);
      return this._mockResponse(method, args);
    }
  }

  async getGames() {
    return this.invoke("get_games");
  }

  async addGame(
    title,
    exe_path,
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
  ) {
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
    );
  }

  async deleteGame(id) {
    return this.invoke("delete_game", id);
  }

  async updateGame(id, fields) {
    return this.invoke("update_game", id, fields);
  }

  async openExtensionFolder() {
    return this.invoke("open_extension_folder");
  }

  async openInBrowser(url) {
    return this.invoke("open_in_browser", url);
  }

  async checkForUpdates(url) {
    return this.invoke("check_for_updates", url);
  }

  async checkAllUpdates() {
    return this.invoke("check_all_updates");
  }

  async getUpdateStatus() {
    return this.invoke("get_update_status");
  }

  async cancelUpdateCheck() {
    return this.invoke("cancel_update_check");
  }

  async getAutoCheckSetting() {
    return this.invoke("get_auto_check_setting");
  }

  async setAutoCheckSetting(frequency) {
    return this.invoke("set_auto_check_setting", frequency);
  }

  async maybeAutoCheck() {
    return this.invoke("maybe_auto_check");
  }

  async launchGame(
    exe_path,
    command_line_args = "",
    run_japanese_locale = false,
    run_wayland = false,
    auto_inject_ce = false,
  ) {
    return this.invoke(
      "launch_game",
      exe_path,
      command_line_args,
      run_japanese_locale,
      run_wayland,
      auto_inject_ce,
    );
  }

  async getSettings() {
    return this.invoke("get_settings");
  }

  async saveSettings(settings) {
    return this.invoke("save_settings", settings);
  }

  async browseFile() {
    return this.invoke("browse_file");
  }

  async browseDirectory() {
    return this.invoke("browse_directory");
  }

  async installRpgmakerDependencies() {
    return this.invoke("install_rpgmaker_dependencies");
  }

  async installRpgmakerRtp() {
    return this.invoke("install_rpgmaker_rtp");
  }

  async downloadProtonGe() {
    return this.invoke("download_proton_ge");
  }

  async openDevTools() {
    return this.invoke("open_dev_tools");
  }

  async check_app_updates() {
    return this.invoke("check_app_updates");
  }

  async get_app_version() {
    return this.invoke("get_app_version");
  }

  async isCheatEngineInstalled() {
    return this.invoke("is_cheat_engine_installed");
  }

  async downloadCheatEngine() {
    return this.invoke("download_cheat_engine");
  }

  async getInstallStatus() {
    return this.invoke("get_install_status");
  }

  async findSaveFiles(exe_path, title = "", engine = "") {
    return this.invoke("find_save_files", exe_path, title, engine);
  }

  async openFolder(path) {
    return this.invoke("open_folder", path);
  }

  async getSystemDepsCommand() {
    return this.invoke("get_system_deps_command");
  }

  // Fallback mocks
  _mockResponse(method, args) {
    switch (method) {
      case "get_games":
        return [
          {
            id: 1,
            title: "Demo RPGMaker",
            version: "v1.0.0",
            progress: "Chapter 2",
            exe_path: "/path/game.exe",
          },
        ];
      case "get_settings":
        return { proton_path: "", wine_prefix_path: "" };
      default:
        return { success: true, mock: true };
    }
  }
}

export const api = new ApiService();

// Let PyWebview inject before we consider it fully ready, though in Vue we usually just call it on mount
export function onWebviewReady(callback) {
  if (window.pywebview) {
    callback();
  } else {
    window.addEventListener("pywebviewready", callback);
  }
}
