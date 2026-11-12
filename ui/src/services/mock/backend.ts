// SPDX-License-Identifier: GPL-3.0-or-later
import type { ApiErrorResult, ArchiveInstallStatus, BepInExInstalled, BulkUpdateResultItem, CompanionTool, CompanionToolInput, GameRecord, GameToolEngine, GameToolsResponse, LaunchMode, LaunchTarget, LibraryBackupSection, LibrarySyncStatus, PlaySession, RenpyConsoleState, RenpyExtractOptions, StoreLink, UpdateStatusResponse } from "../api";
import { LAUNCH_MODE_OPTIONS } from "../../utils/launchMode";
import { hasAvailableUpdate, isUpdateSkipped, normalizeVersion } from "../../utils/libraryGames";
import { CONSERVATIVE_PLATFORM_CAPABILITIES } from "../../utils/platformPolicy";
import { validateCustomPlayStatuses } from "../../utils/playStatus";
import { HTML_PLAYER_FIRST_USE_NOTICE, resolveWebPlayer } from "../../utils/webPlayer";
import { mockAttachmentList, readMockAttachments, writeMockAttachments } from "./attachments";
import { rewriteMockGameCollections, validateMockCollectionList, writeMockCollections } from "./collections";
import { readMockCompanions, writeMockCompanions } from "./companions";
import { MOCK_CATALOG_SOURCES, MOCK_DIAGNOSTICS, MOCK_DIAGNOSTICS_REPORT, MOCK_DIAGNOSTICS_TOOLS, MOCK_SCREENSHOT_SET_B } from "./fixtures";
import { mockGameSaves } from "./gameSaves";
import { isRecord, readMockGames } from "./games";
import { nextMockLaunchTargetId, readMockLaunchTargets, sortMockLaunchTargets, writeMockLaunchTargets } from "./launchTargets";
import { mockRpgmSaves } from "./rpgm";
import { DEFAULT_MOCK_SETTINGS, isActionableVersion, isSaveBackupKeep, normalizeMockFilterPresets, normalizeMockSettings, readMockSettings, writeMockSettings } from "./settings";

interface MockToolRun {
  action: string;
  done: number;
  total: number;
  current: string;
  cancel: boolean;
}

export class MockBackend {
  _mockRunningGames = new Set<number>();
  _mockCompanionRuns = new Set<string>();
  _mockCompanionWine = new Set<number>();
  _mockPendingMetadata = new Set<number>();
  _mockUpdateStatus: UpdateStatusResponse | null = null;
  _mockUrmInstalled = new Set<number>();
  _mockToolRuns = new Map<number, MockToolRun>();
  // Slow enough for the 500 ms status poll to see progress in the browser preview.
  _mockToolStepMs = 350;
  _mockRenpyConsole = new Map<number, RenpyConsoleState>();
  _mockRpgmDecrypted = new Set<number>();
  _mockRpgmUseDecrypted = new Set<number>();
  _mockBepinex = new Map<number, BepInExInstalled>();
  _mockBepinexBackup = new Map<number, BepInExInstalled | null>();
  _mockArchiveStatus: ArchiveInstallStatus = { running: false, stage: "", done: 0, total: 0 };

  _mockToolEngine(gameId: number): GameToolEngine {
    const engine = readMockGames().find((game) => game.id === gameId)?.engine ?? "";
    if (/ren.?py/i.test(engine)) return "renpy";
    if (/rpg/i.test(engine)) return "rpgm";
    if (/unity/i.test(engine)) return "unity";
    return "";
  }

  // Mirrors the desktop rules: a known game of the right engine, not running, one run at a time.
  _mockToolGuard(gameId: number, engine: GameToolEngine): string {
    if (!readMockGames().some((game) => game.id === gameId)) return "Game not found";
    if (this._mockToolEngine(gameId) !== engine) {
      const label = { renpy: "Ren'Py", rpgm: "RPG Maker MV/MZ", unity: "Unity" }[engine as "renpy" | "rpgm" | "unity"] ?? engine;
      return `This game is not a ${label} game`;
    }
    if (this._mockRunningGames.has(gameId)) return "Close the game before using its tools";
    if (this._mockToolRuns.has(gameId)) return "A tool is already running for this game";
    return "";
  }

  // Advances one file per tick so polling sees `done` grow, like the desktop run.
  async _runMockTool(gameId: number, action: string, total: number): Promise<{ done: number; cancelled: boolean }> {
    const run: MockToolRun = { action, done: 0, total, current: "", cancel: false };
    this._mockToolRuns.set(gameId, run);
    try {
      while (run.done < run.total && !run.cancel) {
        run.current = `file_${run.done + 1}.png`;
        await new Promise((resolve) => setTimeout(resolve, this._mockToolStepMs));
        if (run.cancel) break;
        run.done += 1;
      }
      return { done: run.done, cancelled: run.cancel };
    } finally {
      this._mockToolRuns.delete(gameId);
    }
  }

  _mockGameTools(gameId: number): GameToolsResponse {
    const engine = this._mockToolEngine(gameId);
    if (engine === "renpy") {
      return {
        success: true,
        mock: true,
        engine,
        renpy: {
          console: this._mockRenpyConsole.get(gameId) ?? "off",
          archives: [
            { name: "images.rpa", size: 52428800, format: "RPA-3.0" },
            { name: "scripts.rpa", size: 1048576, format: "RPA-3.0" },
          ],
          urm: {
            success: true,
            renpy: true,
            installed: this._mockUrmInstalled.has(gameId),
            source_configured: /\.rpa$/i.test(readMockSettings().urm_rpa_path),
          },
        },
      };
    }
    if (engine === "rpgm") {
      const decrypted = this._mockRpgmDecrypted.has(gameId);
      return {
        success: true,
        mock: true,
        engine,
        rpgm: {
          encrypted: 12,
          images: 8,
          audio: 4,
          missing: decrypted ? 0 : 12,
          key_source: "system",
          use_decrypted: this._mockRpgmUseDecrypted.has(gameId),
          backup: this._mockRpgmUseDecrypted.has(gameId),
        },
      };
    }
    if (engine === "unity") return { success: true, mock: true, engine, unity: {
      platform: "windows", build: { platform: "windows", data_folder: "Game_Data", backend: "il2cpp", arch: "x64", version: "2022.3.21" },
      installed: this._mockBepinex.get(gameId) ?? null, unity_libs: this._mockBepinex.has(gameId), has_backup: this._mockBepinexBackup.has(gameId),
    }};
    return { success: true, mock: true, engine: "" };
  }

  async _mockGameToolAction(method: string, args: unknown[]): Promise<unknown> {
    const gameId = Number(args[0]);
    const fail = (error: string) => ({ success: false, mock: true, error });
    switch (method) {
      case "set_renpy_console": {
        const error = this._mockToolGuard(gameId, "renpy");
        if (error) return fail(error);
        const state: RenpyConsoleState = args[1] ? (args[2] ? "developer" : "on") : "off";
        this._mockRenpyConsole.set(gameId, state);
        return { success: true, mock: true, state };
      }
      case "extract_renpy_archives": {
        const error = this._mockToolGuard(gameId, "renpy");
        if (error) return fail(error);
        const options = (args[1] ?? {}) as Partial<RenpyExtractOptions>;
        if (options.mode !== "in_place" && !(options.output && options.output.startsWith("/"))) {
          return fail("Choose an output folder");
        }
        const run = await this._runMockTool(gameId, "extract", 8);
        return {
          success: true,
          mock: true,
          files: run.done,
          bytes: run.done * 1048576,
          skipped: 0,
          skipped_scripts: 0,
          skipped_existing: 0,
          unsupported: [],
          cancelled: run.cancelled,
          message: `Extracted ${run.done} file(s)${run.cancelled ? "; cancelled" : ""}`,
        };
      }
      case "decrypt_rpgm_assets": {
        const error = this._mockToolGuard(gameId, "rpgm");
        if (error) return fail(error);
        const run = await this._runMockTool(gameId, "decrypt", 12);
        if (run.done === 12) this._mockRpgmDecrypted.add(gameId);
        return {
          success: true,
          mock: true,
          decrypted: run.done,
          already: 0,
          skipped: 0,
          key_source: "system",
          cancelled: run.cancelled,
          message: `Decrypted ${run.done} file(s)${run.cancelled ? "; cancelled" : ""}`,
        };
      }
      default: {
        const error = this._mockToolGuard(gameId, "rpgm");
        if (error) return fail(error);
        const enabled = args[1] === true;
        if (enabled && !this._mockRpgmDecrypted.has(gameId)) {
          return fail("12 encrypted asset(s) still need decrypting first");
        }
        if (enabled) this._mockRpgmUseDecrypted.add(gameId);
        else this._mockRpgmUseDecrypted.delete(gameId);
        return { success: true, mock: true, use_decrypted: enabled };
      }
    }
  }

  // Fallback mocks
  respond(method: string, args: unknown[]): unknown {
    const unavailable: ApiErrorResult = {
      success: false,
      mock: true,
      error: `Backend method '${method}' is unavailable outside PyWebView`,
    };

    switch (method) {
      case "list_companion_tools":
        return { success: true, tools: readMockCompanions().tools };
      case "save_companion_tool": {
        const store = readMockCompanions();
        const input = args[0] as CompanionToolInput;
        const name = input.name.trim();
        if (!name) return { success: false, error: "Name must not be empty" };
        if (store.tools.some(tool => tool.id !== input.id && tool.name.toLowerCase() === name.toLowerCase())) return { success: false, error: "Name is already used by another tool" };
        if (!["wine", "native", "command"].includes(input.kind)) return { success: false, error: "Kind must be Wine, Native or Command" };
        if (input.kind === "command" && !input.arguments.trim()) return { success: false, error: "Command line must not be empty" };
        if (input.kind !== "command" && !input.path.trim()) return { success: false, error: "Path must point to an existing file" };
        if (input.kind === "wine" && !/\.(exe|bat)$/i.test(input.path)) return { success: false, error: "Path: Wine tools need a .exe or .bat file" };
        const tool: CompanionTool = { ...input, id: input.id ?? Math.max(0, ...store.tools.map(tool => tool.id)) + 1, name, path: input.kind === "command" ? "" : input.path };
        if (input.id && !store.tools.some(tool => tool.id === input.id)) return { success: false, error: "Tool was not found" };
        store.tools = [...store.tools.filter(entry => entry.id !== tool.id), tool].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
        writeMockCompanions(store);
        return { success: true, tool };
      }
      case "delete_companion_tool": {
        const store = readMockCompanions();
        const toolId = Number(args[0]);
        if (!store.tools.some(tool => tool.id === toolId)) return { success: false, error: "Tool was not found" };
        store.tools = store.tools.filter(tool => tool.id !== toolId);
        Object.values(store.overrides).forEach(overrides => { delete overrides[String(toolId)]; });
        writeMockCompanions(store);
        return { success: true };
      }
      case "get_game_companion_tools":
      case "set_game_companion_tool_override":
      case "list_launchable_companion_tools":
      case "launch_companion_tool": {
        const gameId = Number(args[0]);
        const store = readMockCompanions();
        const game = readMockGames().find(game => game.id === gameId);
        if (!game) return { success: false, error: "Game was not found" };
        const overrides = store.overrides[String(gameId)] || {};
        const toolId = Number(args[1]);
        const tool = store.tools.find(tool => tool.id === toolId);
        if (method === "set_game_companion_tool_override") {
          if (!tool) return { success: false, error: "Tool was not found" };
          if (args[2] === null) delete overrides[String(toolId)];
          else overrides[String(toolId)] = !!args[2];
          store.overrides[String(gameId)] = overrides;
          writeMockCompanions(store);
          return { success: true };
        }
        const tools = store.tools.map(tool => ({ ...tool, visible: overrides[String(tool.id)] ?? tool.show_on_all_games, overridden: Object.prototype.hasOwnProperty.call(overrides, String(tool.id)) }));
        if (method === "get_game_companion_tools") return { success: true, tools };
        if (localStorage.getItem("wlib-mock-platform") !== "linux") return { success: false, error: "Custom tools are only available on Linux" };
        const supported = tools.filter(tool => tool.visible && this._mockRunningGames.has(gameId) && (tool.kind !== "wine" || this._mockCompanionWine.has(gameId)));
        if (method === "list_launchable_companion_tools") return { success: true, tools: supported };
        const name = tool?.name || `Tool ${toolId}`;
        if (!supported.some(tool => tool.id === toolId)) return { success: false, error: `${name}: tool is unavailable for this game run` };
        const key = `${gameId}:${toolId}`;
        if (this._mockCompanionRuns.has(key)) return { success: false, code: "tool_running", error: `${name} is already open for this game` };
        this._mockCompanionRuns.add(key);
        return { success: true };
      }

      case "choose_game_cover":
      case "set_game_cover_file":
      case "clear_game_cover": {
        const games = readMockGames();
        const game = games.find((entry) => entry.id === Number(args[0]));
        if (!game) return { success: false, error: "Game was not found", error_code: "game_not_found" };
        if (method === "choose_game_cover") return { success: true, mock: true, cancelled: true };
        if (method === "set_game_cover_file") {
          if (!String(args[1] || "").trim()) return { success: false, error: "Choose an image file." };
          game.cover_local = "mock-local-cover.png";
          game.cover_local_url = String(args[1]);
          delete game.cover_card_still_url;
          delete game.cover_card_anim_url;
          delete game.cover_animated;
        } else {
          game.cover_local = null;
          delete game.cover_local_url;
          delete game.cover_card_still_url;
          delete game.cover_card_anim_url;
          delete game.cover_animated;
        }
        localStorage.setItem("wlib-mock-games", JSON.stringify(games));
        return { success: true, mock: true, game };
      }
      case "get_cover_cache_status":
        return { success: true, count: Number(sessionStorage.getItem("wlib-mock-cover-count") ?? 25), bytes: Number(sessionStorage.getItem("wlib-mock-cover-bytes") ?? 12582912), running: false };
      case "purge_cover_cache":
        sessionStorage.setItem("wlib-mock-cover-count", "0");
        sessionStorage.setItem("wlib-mock-cover-bytes", "0");
        return { success: true, count: 0, bytes: 0, running: false };
      case "get_game_screenshots": {
        const game = readMockGames().find(item => item.id === args[0]);
        const screenshots = game?.screenshots ?? [];
        return { success: true, screenshots: screenshots.map(url => ({ thumb: url.replace("attachments.f95zone.to", "preview.f95zone.to"), full: url.replace("preview.f95zone.to", "attachments.f95zone.to") })), source: "catalog", in_catalog: !!game?.f95_url };
      }
      case "get_games": {
        // get_all_games() embeds every game's additional targets in the same
        // order as get_launch_targets(); the library launch menu reads them there.
        const targets = readMockLaunchTargets();
        return readMockGames().map((game) => ({
          ...game,
          launch_targets: sortMockLaunchTargets(targets.filter((target) => target.game_id === game.id)),
          ...(this._mockPendingMetadata.has(game.id) ? { metadata_pending: true } : {}),
        }));
      }
      case "add_game": {
        const games = readMockGames();
        const links = Array.isArray(args[16]) ? args[16] as StoreLink[] : [];
        const matches = games.filter((game) => (game.f95_url && game.f95_url === args[2]) ||
          (game.links || []).some((stored) => !["other", "patreon", "subscribestar", "gumroad"].includes(stored.source) &&
            links.some((link) => link.source === stored.source && link.id === stored.id)));
        if (matches.length) {
          const game = matches[0]!;
          if (matches.length === 1 && args[2] && !game.f95_url) {
            game.f95_url = String(args[2]);
            game.links = [...(game.links || []), ...links.filter((link) => !(game.links || []).some((stored) => stored.source === link.source && stored.id === link.id))];
            localStorage.setItem("wlib-mock-games", JSON.stringify(games));
            return { success: true, id: game.id, attached: true };
          }
          return { success: false, error_code: "duplicate_url", error: "A game with this URL is already in your library" };
        }
        const id = games.reduce((max, game) => Math.max(max, game.id), 0) + 1;
        const game: GameRecord = { id, title: String(args[0]), exe_path: String(args[1]),
          f95_url: String(args[2] || ""), version: String(args[3] || ""), links,
          cover_image_path: String(args[4] || ""), tags: String(args[5] || ""),
          rating: String(args[6] || ""), developer: String(args[7] || ""),
          engine: String(args[8] || ""),
          run_japanese_locale: !!args[9], run_wayland: !!args[10], auto_inject_ce: !!args[11],
          custom_prefix: String(args[12] || ""), proton_version: String(args[13] || ""),
          launch_mode: args[14] as LaunchMode,
          command_line_args: String(args[15] || ""), date_added: new Date().toISOString(),
          gamemode: "default", mangohud: "default", html_player: "", sandbox_enabled: readMockSettings().sandbox_new_games, block_network: false, play_status: "Not Started", thread_completed: false };
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
          if (fields.version != null && normalizeVersion(String(fields.version)) !== normalizeVersion(game.version)) {
            merged.skipped_version = "";
          }
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
        const companions = readMockCompanions();
        delete companions.overrides[String(gameId)];
        writeMockCompanions(companions);
        const attachments = readMockAttachments();
        delete attachments[String(gameId)];
        writeMockAttachments(attachments);
        return { success: true, mock: true };
      }
      case "launch_game": {
        const gameId = Number(args[0]);
        if (this._mockRunningGames.has(gameId)) return { success: false, code: "already_running", error: "Game is already running" };
        const game = readMockGames().find(game => game.id === gameId);
        const mode = args[8] || "auto";
        if (!game?.sandbox_enabled && (mode === "wine_proton" || (mode === "auto" && /\.(exe|bat)$/i.test(String(args[1]))))) this._mockCompanionWine.add(gameId);
        else this._mockCompanionWine.delete(gameId);
        // Only system-browser launches have no process to track.
        const player = resolveWebPlayer(String(args[1]), mode, game?.html_player, readMockSettings().html_builtin_player, "linux");
        if (player !== "browser") this._mockRunningGames.add(gameId);
        if (player === "builtin" && /\.html?$/i.test(String(args[1])) && !game?.html_player && localStorage.getItem("wlib-mock-html-player-note") !== "shown") {
          localStorage.setItem("wlib-mock-html-player-note", "shown");
          return { success: true, mock: true, notice: HTML_PLAYER_FIRST_USE_NOTICE };
        }
        return { success: true, mock: true };
      }
      case "get_running_games":
        return [...this._mockRunningGames];
      case "stop_game": {
        const gameId = Number(args[0]);
        if (!this._mockRunningGames.delete(gameId)) return { success: false, code: "not_running", error: "Game is not running" };
        this._mockCompanionWine.delete(gameId);
        for (const key of this._mockCompanionRuns) if (key.startsWith(`${gameId}:`)) this._mockCompanionRuns.delete(key);
        window.dispatchEvent(new CustomEvent("wlib-playtime-tick", { detail: { gameId, delta: 0, isFinal: true } }));
        return { success: true, mock: true };
      }
      case "get_urm_status":
      case "set_urm_installed": {
        const gameId = Number(args[0]);
        const game = readMockGames().find((game) => game.id === gameId);
        if (!game) return { success: false, error: "Game not found" };
        // Same engine detection as get_game_tools/get_game_tool_support: the
        // browser mock reads the engine label instead of the game folder.
        const renpy = this._mockToolEngine(gameId) === "renpy";
        const sourceConfigured = /\.rpa$/i.test(readMockSettings().urm_rpa_path);
        if (method === "set_urm_installed") {
          if (!renpy) return { success: false, error: "This game is not a Ren'Py game" };
          if (args[1] && !sourceConfigured) return { success: false, error: "Configure a URM source in Settings" };
          if (args[1]) this._mockUrmInstalled.add(gameId);
          else this._mockUrmInstalled.delete(gameId);
        }
        return { success: true, renpy, installed: this._mockUrmInstalled.has(gameId), source_configured: sourceConfigured };
      }
      case "get_game_tool_support": {
        const gameId = Number(args[0]);
        if (!readMockGames().some((game) => game.id === gameId)) {
          return { success: false, mock: true, error: "Game not found", engine: "" };
        }
        return { success: true, mock: true, engine: this._mockToolEngine(gameId) };
      }
      case "get_game_tools": {
        const gameId = Number(args[0]);
        if (!readMockGames().some((game) => game.id === gameId)) {
          return { success: false, mock: true, error: "Game not found" };
        }
        return this._mockGameTools(gameId);
      }
      case "list_bepinex_versions": {
        const error = this._mockToolGuard(Number(args[0]), "unity");
        if (error) return { success: false, error };
        const build = { id: "6.0.0-be.791", major: 6, backend: "il2cpp", channel: "be", date: "" };
        return { success: true, builds: [build], recommended: build, warnings: [] };
      }
      case "install_bepinex":
      case "uninstall_bepinex":
      case "restore_bepinex": return (async () => {
        const id = Number(args[0]);
        const error = this._mockToolGuard(id, "unity");
        if (error) return { success: false, error };
        if (method === "restore_bepinex") {
          const old = this._mockBepinexBackup.get(id);
          if (old) this._mockBepinex.set(id, old); else this._mockBepinex.delete(id);
          this._mockBepinexBackup.delete(id);
        } else {
          const old = this._mockBepinex.get(id);
          if (old) this._mockBepinexBackup.set(id, old);
          if (method === "install_bepinex") {
            const run = await this._runMockTool(id, "bepinex_install", 5);
            if (run.cancelled) return { success: false, error: "Installation cancelled" };
            this._mockBepinex.set(id, { version: "6.0.0-be.791", major: 6, backend: "il2cpp", complete: true, managed_by_wlib: true });
          } else this._mockBepinex.delete(id);
        }
        return { success: true };
      })();
      case "set_renpy_console":
      case "extract_renpy_archives":
      case "decrypt_rpgm_assets":
      case "set_rpgm_use_decrypted":
        return this._mockGameToolAction(method, args);
      case "get_game_tool_status": {
        const run = this._mockToolRuns.get(Number(args[0]));
        return {
          success: true,
          mock: true,
          running: !!run,
          action: run?.action ?? "",
          done: run?.done ?? 0,
          total: run?.total ?? 0,
          current: run?.current ?? "",
          cancel: run?.cancel ?? false,
        };
      }
      case "cancel_game_tool": {
        const run = this._mockToolRuns.get(Number(args[0]));
        if (!run) return { success: false, mock: true, error: "No tool is running for this game" };
        run.cancel = true;
        return { success: true, mock: true };
      }
      case "get_settings":
        return readMockSettings();
      case "set_tag_color": {
        const tag = String(args[0] || "").trim().toLowerCase();
        const swatch = args[1];
        if (!tag || (swatch != null && swatch !== "" && !["red", "orange", "yellow", "green", "teal", "blue", "purple", "pink"].includes(String(swatch)))) {
          return { success: false, error: "Invalid tag color" };
        }
        const settings = readMockSettings();
        if (swatch == null || swatch === "") delete settings.tag_colors[tag];
        else settings.tag_colors[tag] = String(swatch);
        writeMockSettings(settings);
        return { success: true, tag_colors: settings.tag_colors };
      }
      case "get_catalog_status":
      case "check_catalog_now": {
        const enabled = readMockSettings().catalog_enabled;
        if (method === "check_catalog_now" && !enabled) {
          return { success: false, error: "The game catalog is turned off" };
        }
        if (method === "check_catalog_now") {
          localStorage.setItem("wlib-mock-catalog-checked", new Date().toISOString().replace(/\.\d+Z$/, "Z"));
        }
        const installed = enabled;
        return {
          success: true,
          mock: true,
          enabled,
          installed,
          built_at: installed ? "2026-10-27T05:58:40Z" : null,
          sources: installed ? MOCK_CATALOG_SOURCES : {},
          last_check: installed ? localStorage.getItem("wlib-mock-catalog-checked") || "2026-10-27T08:12:00Z" : null,
          last_error: null,
          checking: false,
          ...(method === "check_catalog_now" ? { started: true } : {}),
        };
      }
      case "get_diagnostics":
        return { success: true, basic: MOCK_DIAGNOSTICS, report_basic: MOCK_DIAGNOSTICS_REPORT };
      case "get_diagnostics_tools":
        return { success: true, tools: MOCK_DIAGNOSTICS_TOOLS };
      case "get_diagnostics_report":
        return { success: true, report: MOCK_DIAGNOSTICS_REPORT };
      case "open_log_folder":
        return { success: false, error: "The log folder is available in the desktop app." };
      case "set_window_focused":
        return { success: true, mock: true };
      case "get_game_play_sessions": {
        const sessions: PlaySession[] = JSON.parse(localStorage.getItem("wlib-mock-play-sessions") || "[]");
        return { success: true, mock: true, sessions: sessions
          .filter((session) => session.game_id === Number(args[0]))
          .sort((a, b) => b.started_at.localeCompare(a.started_at) || b.id - a.id) };
      }
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
        if (Object.prototype.hasOwnProperty.call(payload, "save_backup_keep") && !isSaveBackupKeep(payload.save_backup_keep)) {
          return { success: false, error: "Save backups to keep must be 'all' or 1–99" };
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
      case "list_sync_snapshots":
        return {
          success: true,
          mock: true,
          snapshots: [
            { name: "2026-11-06T18-20-00-000000Z.json", created_at: "2026-11-06T18:20:00Z", games: 42, bytes: 81234 },
            { name: "2026-11-02T09-05-00-000000Z.json", created_at: "2026-11-02T09:05:00Z", games: 40, bytes: 79001 },
          ],
        };
      case "restore_sync_snapshot":
        return { success: true, mock: true, created: 0, updated: 2, deleted: 0 };
      case "get_sync_status":
      case "set_sync_folder":
      case "connect_dropbox":
      case "connect_gdrive":
      case "disconnect_sync":
      case "sync_now":
      case "pause_sync":
      case "resume_sync":
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
        // The pause stays when the same folder is picked again and goes with a
        // new location or a disconnect, like SyncManager._reset_connection.
        if (method === "set_sync_folder" && status.folder_path !== String(args[0])) status.paused = false;
        if (method === "set_sync_folder") Object.assign(status, { state: status.paused ? "paused" : "idle", location: "folder", folder_path: String(args[0]), last_error: "" });
        if (method === "pause_sync" && status.location) Object.assign(status, { paused: true, state: status.state === "syncing" ? "syncing" : "paused" });
        if (method === "resume_sync") Object.assign(status, { paused: false, state: status.location ? "idle" : "off" });
        if (method === "connect_dropbox") Object.assign(status, { state: "idle", paused: false, location: "dropbox", account: "Browser mock account", last_error: "" });
        if (method === "connect_gdrive") Object.assign(status, { state: "idle", paused: false, location: "gdrive", account: "mock@example.com", last_error: "" });
        if (method === "disconnect_sync") Object.assign(status, { state: "off", paused: false, location: "", encrypted: false, account: "", folder_path: "", last_synced_at: "", last_device: "", last_error: "" });
        if (method === "resolve_sync_choice") Object.assign(status, { state: "idle", last_error: "" });
        if (method === "set_sync_passphrase") status.encrypted = args[0] !== null;
        if (method === "unlock_sync") {
          if (args[0] !== "mock-passphrase") return { ...status, success: false, error: "The file could not be decrypted.", error_code: "decrypt_failed" };
          Object.assign(status, { state: "idle", encrypted: true, last_error: "" });
        }
        localStorage.setItem("wlib-mock-sync", JSON.stringify(status));
        // SyncManager.start_run() only reports a run when a location is set;
        // otherwise status() keeps answering "off".
        // A paused device starts no run (SyncManager.start_run returns status()).
        if (status.location && !status.paused && ["set_sync_folder", "sync_now", "resume_sync", "resolve_sync_choice", "set_sync_passphrase", "unlock_sync"].includes(method)) {
          // The run is asynchronous: report "syncing" now and let the stored
          // status settle once the simulated run finishes.
          const running: LibrarySyncStatus = { ...status, state: "syncing", last_error: "" };
          localStorage.setItem("wlib-mock-sync", JSON.stringify(running));
          setTimeout(() => {
            try {
              let finished: LibrarySyncStatus = { ...running };
              const stored = JSON.parse(localStorage.getItem("wlib-mock-sync") || "{}") as LibrarySyncStatus;
              if (stored.state === "syncing") finished = { ...stored, state: stored.paused ? "paused" : "idle", last_synced_at: new Date().toISOString(), last_device: "Browser mock device" };
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
      case "browse_attachment_file":
        return "/mock/guide.md";
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
          const total = readMockGames().filter((game) => (game.f95_url || "").trim() && (!Array.isArray(args[0]) || args[0].includes(game.id))).length;
          if (Array.isArray(args[0]) && total) {
            const refreshCovers = args[1] === true;
            this._mockUpdateStatus = { running: true, cancelling: false, outcome: "running", error: "", total, checked: 0, current: "", results: [], delay_seconds: 2, refresh_covers: refreshCovers };
            setTimeout(() => {
              if (!this._mockUpdateStatus?.running) return;
              let results: BulkUpdateResultItem[] = [];
              if (refreshCovers) {
                // Mirrors the backend: every F95 game reports cover_changed/previous_cover.
                const stored = readMockGames();
                results = stored
                  .filter((game) => (game.f95_url || "").trim() && (args[0] as number[]).includes(game.id))
                  .map((game, index) => ({
                    id: game.id, title: game.title, current_version: game.version || "", latest_version: game.version || "",
                    has_update: false, error: "", error_code: "",
                    cover_changed: index % 2 === 0, previous_cover: game.cover_image_path || "",
                  }));
                const changed = new Map(results.filter((r) => r.cover_changed).map((r) => [r.id, `https://example.com/refreshed-${r.id}.jpg`]));
                localStorage.setItem("wlib-mock-games", JSON.stringify(stored.map((game) => (changed.has(game.id) ? { ...game, cover_image_path: changed.get(game.id) } : game))));
              }
              this._mockUpdateStatus = { ...this._mockUpdateStatus, running: false, outcome: "completed", checked: total, results };
            }, refreshCovers ? 7000 : 3000);
          }
          return { success: true, mock: true, total, delay_seconds: 2 };
        }
        if (method === "set_auto_check_setting") {
          try { localStorage.setItem("wlib-mock-auto-check", String(args[0])); } catch { /* Preview just forgets the choice. */ }
        }
        if (method === "cancel_update_check" || method === "set_auto_check_setting") {
          if (method === "cancel_update_check" && this._mockUpdateStatus) this._mockUpdateStatus = { ...this._mockUpdateStatus, running: false, outcome: "cancelled" };
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
          available_sections: ["metadata", "user_state", "executable_paths", "launch_targets", ...(source === "playnite" ? ["covers"] : [])],
          counts: {
            ...(source === "playnite" ? { covers_to_copy: 2, covers_to_fill: 2 } : {}),
            total_games: source === "playnite" ? 147 : 0,
            matched_games: 0,
            new_games: source === "playnite" ? 146 : 0,
            ambiguous_games: 0,
            skipped_no_identity: source === "playnite" ? 1 : 0,
            dropped_paths: source === "playnite" ? 1 : 0,
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
      case "lookup_thread_details": {
        if (!/^https?:\/\/(?:www\.)?f95zone\.to\/threads\/(?:[^/]+\.)?\d+(?:\/|$)/i.test(String(args[0] || ""))) {
          return { success: false, error: "A valid F95Zone thread URL is required." };
        }
        return { success: true, mock: true, title: "Eternum", version: "0.8", creator: "Caribdis", engine: "Ren'Py", cover_image: "", tags: ["3d"] };
      }
      case "list_library_folders": {
        const settings = readMockSettings();
        return settings.library_folders.map((path) => ({ path, is_default: path === settings.library_folder, available: true, free_bytes: 500 * 1024 ** 3 }));
      }
      case "preview_install_destination": {
        const options = isRecord(args[0]) ? args[0] : {};
        const settings = readMockSettings();
        const pattern = settings.library_folder_pattern || DEFAULT_MOCK_SETTINGS.library_folder_pattern;
        const segments = pattern.split(/[/\\]+/).map((part) => {
          for (const key of ["creator", "title", "version", "engine"]) {
            part = part.split(`{${key}}`).join(String(options[key] || "").trim());
          }
          return part.replace(/\{[^{}]*\}/g, "").replace(/[<>:"|?*\x00-\x1f]/g, "_").trim();
        }).filter(Boolean);
        const folder = String(options.library_folder || settings.library_folder);
        return {
          destination: folder && String(options.title || "").trim()
            ? `${folder.replace(/[/\\]+$/, "")}/${(segments.length ? segments : [String(options.title || "game")]).join("/")}` : "",
          engine_pending: pattern.includes("{engine}") && !String(options.engine || "").trim(),
        };
      }
      case "get_update_download_links":
        return {success:true, version:"v1.2", builds:[{label:"Windows/Linux",section:"",platform:"windows/linux",kind:"full",mirrors:[{link_id:"mock-pixeldrain",host:"pixeldrain",label:"Pixeldrain",part:"",split:false},{link_id:"mock-other",host:"gofile",label:"Gofile",part:"",split:false}]}]};
      case "close_update_download_links":
        return {success:true};
      case "start_update_download":
        return {success:false,error:"Downloads are available in the desktop app"};
      case "get_update_download_status":
        return {success:true,jobs:[]};
      case "cancel_update_download":
        return {success:false,error:"Download is not running"};
      case "open_update_download_in_browser":
        return {success:true,mock:true};
      case "inspect_archive": {
        const fileName = String(args[0]).split(/[/\\]/).pop() || "";
        const stem = fileName.replace(/\.(zip|7z|rar)$/i, "");
        const versionMatch = stem.match(/[-_ ]v?(\d[\d._]*[a-z]?)$/i);
        const guessedVersion = versionMatch?.[1] ? versionMatch[1].replace(/_/g, ".") : "";
        const guessedTitle = (versionMatch ? stem.slice(0, versionMatch.index) : stem).replace(/[_-]+/g, " ").trim() || stem;
        const settings = readMockSettings();
        const gameId = typeof args[1] === "number" ? args[1] : null;
        const game = gameId !== null ? readMockGames().find((entry) => entry.id === gameId) : undefined;
        const targetId = typeof args[2] === "number" ? args[2] : null;
        const gameTargets = readMockLaunchTargets().filter((t) => t.game_id === gameId);
        const chosen = gameTargets.find((t) => t.id === targetId);
        if (targetId !== null && !chosen) return { success: false, error: "The launch target does not belong to this game." };
        const oldFolder = (chosen?.exe_path || game?.exe_path || "").split(/[/\\]/).slice(0, -1).join("/");
        const matches = settings.library_folders.filter((p) => oldFolder === p || oldFolder.startsWith(`${p.replace(/[/\\]+$/, "")}/`)).sort((a, b) => b.length - a.length);
        const folder = typeof args[3] === "string" && args[3] ? args[3] : matches[0] || settings.library_folder;
        const ownTargets = [...gameTargets.filter((t) => t.id !== targetId), ...(targetId !== null && game ? [{ label: game.default_target_label?.trim() || "Default", exe_path: game.exe_path }] : [])];
        // Mirrors inspect_archive: the archive name is the primary guess, the
        // stored game only fills blanks (and overwrites the version when it has
        // a known latest_version).
        const title = guessedTitle || (game?.title ?? "");
        const version = game?.latest_version?.trim() ? game.latest_version : guessedVersion;
        const creator = game?.developer ?? "";
        const engine = game?.engine ?? "";
        // build_destination substitutes the pattern tokens and drops segments
        // that end up empty, falling back to the title.
        const pattern = settings.library_folder_pattern || DEFAULT_MOCK_SETTINGS.library_folder_pattern;
        const segments = pattern
          .split(/[/\\]+/)
          .map((part) => part
            .replace("{creator}", creator)
            .replace("{title}", title)
            .replace("{version}", version)
            .replace("{engine}", engine)
            .replace(/\{[^{}]*\}/g, "")
            .trim())
          .filter(Boolean);
        const destination = folder
          ? `${folder.replace(/[/\\]+$/, "")}/${(segments.length ? segments : [title || "game"]).join("/")}`
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
          engine,
          downloaded_by_wlib: false,
          engine_pending: pattern.includes("{engine}") && !engine.trim(),
          destination,
          tool_error: toolError,
          old_folder: oldFolder,
          library_folder: folder,
          own_targets_inside: ownTargets.filter((t) => oldFolder && t.exe_path.startsWith(`${oldFolder}/`)).map((t) => t.label),
          outside_library_folders: !!oldFolder && matches.length === 0,
          suggested_library_folder: oldFolder && matches.length === 0 ? oldFolder.split("/").slice(0, -1).join("/") : "",
          removable: !!game,
          remove_reason: game ? "" : "No previous install.",
        };
      }
      case "cancel_archive_install":
        if (!this._mockArchiveStatus.running) return { success: false, mock: true, error: "No install is running.", error_code: "not_running" };
        this._mockArchiveStatus = { ...this._mockArchiveStatus, stage: "cancelling", cancellable: false };
        return { success: true, mock: true };
      case "install_archive": {
        // Like the backend, answer only when the install ends; the dialog
        // polls get_archive_install_status meanwhile.
        const options = isRecord(args[0]) ? args[0] : {};
        const total = 300 * 1024 ** 2;
        if (/ambiguous/i.test(String(options.archive_path || "")) && !options.exe_relpath) {
          // Mimics an update whose archive holds launch files but no clear pick.
          return {
            success: false,
            mock: true,
            error: "No launchable file could be picked automatically. Choose the file to launch.",
            error_code: "no_launcher_chosen",
            launch_candidates: ["PC/Game/Game.exe", "Mac/Game/Game.sh"],
          };
        }
        this._mockArchiveStatus = { running: true, stage: "extracting", done: 0, total, cancellable: true };
        return new Promise((resolve) => {
          let step = 0;
          const tick = () => {
            if (this._mockArchiveStatus.stage === "cancelling") {
              this._mockArchiveStatus = { running: false, stage: "cancelled", done: 0, total: 0, cancellable: false };
              resolve({ success: false, mock: true, error: "The install was cancelled. Nothing was changed.", error_code: "install_cancelled" });
              return;
            }
            step += 1;
            if (step < 5) {
              this._mockArchiveStatus = { ...this._mockArchiveStatus, done: Math.round((total * step) / 5) };
              setTimeout(tick, 600);
              return;
            }
            this._mockArchiveStatus = { running: false, stage: "done", done: total, total, cancellable: false };
            window.dispatchEvent(new Event("wlib-refresh-library"));
            resolve({
              success: true,
              mock: true,
              game_id: typeof options.game_id === "number" ? options.game_id : 9001,
              exe_path: String(options.archive_path || ""),
              destination: "",
              unmoved_targets: [],
              kept_old_folder_reason: "",
              removed_old: false,
              old_folder: "",
            });
          };
          setTimeout(tick, 600);
        });
      }
      case "release_download_archive":
        return {success:true};
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
          { name: "GE-Proton (latest)", path: "GE-Proton" },
          { name: "UMU-Proton", path: "UMU-Proton" },
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
            error: "Login is required to access this thread. The wLib extension loaded it without an F95Zone login. Log in to F95Zone in that browser and keep an F95Zone tab open.",
            error_code: "login_required",
            browser_fallback: "logged_out" as const,
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
        const version = String(game.latest_version || "").trim() || String(game.version || "").trim();
        if (!isActionableVersion(version)) {
          // check_for_updates treats an empty or placeholder scraped version as
          // an extraction failure; the mock has nothing better to report.
          return {
            success: false,
            mock: true,
            error: "Could not extract a version from thread",
            error_code: "extract_failed",
          };
        }
        return {
          success: true,
          mock: true,
          version,
          has_update: hasAvailableUpdate(game),
          skipped: isUpdateSkipped(game),
          newly_finished: newlyFinished,
          metadata_updated: 0,
        };
      }
      case "get_update_status":
        return this._mockUpdateStatus || {
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
      case "get_umu_status":
        return { success: true, source: "", path: "", version: "", python3: true };
      case "get_umu_download_status":
        return { success: true, running: false, stage: "", done: 0, total: 0, error: "" };
      case "get_umu_runtime_status":
        return { success: true, runtime: "steamrt4", path: "", ready: false, running: false, stage: "", done: 0, error: "", error_class: "", cancelled: false };
      case "download_umu":
      case "prepare_umu_runtime":
      case "cancel_umu_runtime_prepare":
        return { success: true, mock: true };
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
      case "get_performance_tools":
        return { gamemode: false, mangohud: false };
      case "open_wine_prefix_tool":
        return { success: false, error: "Wine prefix tools require the desktop app." };
      case "get_sandbox_status":
      case "recheck_sandbox_status":
        return { status: "missing", tool: "bwrap", path: "", version: "", reason: "Sandbox tools require the desktop app." };
      case "get_sandbox_install_info":
        return { platform: "linux", tool: "bwrap", command: "sudo pacman -S bubblewrap", can_install: false, pkexec_available: false, download_url: "https://github.com/containers/bubblewrap", package_manager: "pacman" };
      case "get_sandbox_install_status":
        return { running: false, done: 0, total: 1, current: "", error: "" };
      case "install_sandbox_tool":
        return { success: false, error: "Sandbox installation requires the desktop app." };
      case "open_game_sandbox_folder":
        return { success: false, error: "Opening sandbox folders requires the desktop app." };
      case "set_sandbox_for_all_games": {
        let changed = 0;
        const games = readMockGames().map((game) => {
          if (!!game.sandbox_enabled !== !!args[0]) changed++;
          return { ...game, sandbox_enabled: !!args[0] };
        });
        localStorage.setItem("wlib-mock-games", JSON.stringify(games));
        return { success: true, changed };
      }
      case "open_steam_keyboard":
        return { success: false, code: "unsupported" };
      case "get_platform_capabilities":
        if (localStorage.getItem("wlib-mock-platform") === "linux") return {
          ...CONSERVATIVE_PLATFORM_CAPABILITIES, platform: "linux", sandbox: "bwrap", wine_proton: true,
          runtime_installers: true, wayland: true, rpgmaker_linux: true, cheat_engine_injection: true, companion_tools: true,
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
      case "get_download_accounts":
        return {
          success: true,
          pixeldrain: { connected: !!localStorage.getItem("wlib-mock-pixeldrain"), username: localStorage.getItem("wlib-mock-pixeldrain") || "" },
          mega: { connected: !!localStorage.getItem("wlib-mock-mega"), email: localStorage.getItem("wlib-mock-mega") || "" },
          rpdl: { connected: !!localStorage.getItem("wlib-mock-rpdl"), username: localStorage.getItem("wlib-mock-rpdl") || "" },
        };
      case "connect_pixeldrain_account":
        if (!String(args[0] || "").trim()) return { success: false, error: "Enter a valid Pixeldrain API key" };
        localStorage.setItem("wlib-mock-pixeldrain", "mock-user");
        return { success: true, username: "mock-user" };
      case "disconnect_pixeldrain_account":
        localStorage.removeItem("wlib-mock-pixeldrain");
        return { success: true };
      case "connect_mega_account": {
        const email = String(args[0] || "").trim().toLowerCase();
        if (!email || !args[1]) return { success: false, error: "Enter your MEGA email and password", needs_code: false };
        localStorage.setItem("wlib-mock-mega", email);
        return { success: true, email };
      }
      case "disconnect_mega_account":
        localStorage.removeItem("wlib-mock-mega");
        return { success: true };
      case "connect_rpdl_account": {
        const name = String(args[0] || "").trim();
        if (!name || !args[1]) return { success: false, error: "Enter your RPDL username and password" };
        localStorage.setItem("wlib-mock-rpdl", name);
        return { success: true, username: name };
      }
      case "disconnect_rpdl_account":
        localStorage.removeItem("wlib-mock-rpdl");
        return { success: true };
      case "find_rpdl_torrents": {
        const wanted = String(args[1] || "");
        const results = [
          { id: 1, title: `MockGame-${wanted || "1.0"}-pc`, size: 3_221_225_472, seeders: 12, leechers: 1, date: 1_767_225_600, score: 96, version_match: wanted ? true : null, no_seeders: false },
          { id: 2, title: "MockGame-0.9-pc", size: 3_000_000_000, seeders: 0, leechers: 0, date: 1_760_000_000, score: 88, version_match: wanted ? false : null, no_seeders: true },
        ];
        return { success: true, results, preselected_id: 1, needs_choice: false, signed_in: !!localStorage.getItem("wlib-mock-rpdl"), query: "MockGame" };
      }
      case "start_rpdl_download":
        if (!localStorage.getItem("wlib-mock-rpdl")) return { success: false, error: "Sign in to RPDL in Settings", error_code: "sign_in" };
        return { success: false, error: "Downloads are available in the desktop app" };
      case "pause_torrent":
      case "resume_torrent":
      case "answer_seed_prompt":
      case "stop_seeding":
        return { success: false, error: "Torrent downloads are available in the desktop app" };
      case "get_steam_status":
        return { success: true, found: true, flatpak_steam: false, accounts: ["123456"], account_id: "123456", running: false, deck: false, gaming_mode: false, mode: localStorage.getItem("wlib-mock-steam-mode") || "off", sync: { running: false } };
      case "get_game_steam_state":
        return { success: true, state: "not_in_steam", marked: localStorage.getItem(`wlib-mock-steam-${args[0]}`) === "true", mode: localStorage.getItem("wlib-mock-steam-mode") || "off", eligible: true, reason: "" };
      case "set_steam_games_mode":
        localStorage.setItem("wlib-mock-steam-mode", String(args[0]));
        return { success: true };
      case "set_game_steam_shortcut":
        localStorage.setItem(`wlib-mock-steam-${args[0]}`, String(args[1]));
        return { success: true };
      case "add_wlib_to_steam":
      case "set_steam_account":
      case "sync_steam_shortcuts":
        return { success: true };
      case "get_steam_cover_conversions":
        return { success: true, covers: [] };
      case "save_steam_cover_png":
        return { success: true };
      case "get_startup_launch_error":
        return { error: "" };
      case "get_extension_pairing":
        return { success: true, pending: [], paired: [] };
      case "resolve_extension_pairing":
      case "revoke_extension_pairing":
        return { success: true, mock: true };
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
        return sortMockLaunchTargets(readMockLaunchTargets().filter((target) => target.game_id === gameId));
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
      case "list_rpgm_saves":
      case "load_rpgm_save":
      case "write_rpgm_save":
        return mockRpgmSaves(method, args);
      case "get_game_saves":
      case "set_game_save_folder":
      case "backup_game_saves":
      case "import_game_saves":
      case "restore_game_saves_backup":
      case "open_game_save_backup":
        return mockGameSaves(method, args);
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
        if (!/\.(pdf|txt|md)$/i.test(sourcePath.trim())) {
          return { success: false, mock: true, error: "Only PDF, TXT and MD files can be attached", error_code: "unsupported_attachment" };
        }
        const baseName = sourcePath.split(/[/\\]/).pop() || "guide.pdf";
        const store = readMockAttachments();
        const existing = store[String(gameId)] || [];
        const extension = baseName.match(/\.[^.]+$/)?.[0] || "";
        const stem = baseName.slice(0, -extension.length);
        let candidate = baseName;
        let counter = 2;
        const names = new Set(existing.map((item) => item.name.toLowerCase()));
        while (names.has(candidate.toLowerCase())) {
          candidate = `${stem} (${counter})${extension}`;
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

export const mockBackend = new MockBackend();
