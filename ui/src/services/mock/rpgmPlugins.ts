// SPDX-License-Identifier: GPL-3.0-or-later
import type {
  RpgmPluginBackupState,
  RpgmPluginEngine,
  RpgmPluginEntry,
  RpgmPluginIssue,
  RpgmPluginsState,
} from "../api";

// Stateful browser mock of the RPG Maker plugin list. Pick a scenario per
// game with localStorage `wlib-mock-rpgm-plugins` ({"<game id>": "<name>"}) or
// for every game with `?mock_rpgm_plugins=<name>`. Names: default, long, mv,
// empty, unreadable, changed, invalid_backup, load_error, stale.
// `?mock_rpgm_plugins_delay=<ms>` (or localStorage
// `wlib-mock-rpgm-plugins-delay`) slows each load.

type FileState = "ok" | "case" | "missing" | "unsafe";

interface MockPlugin {
  name: string;
  description: string;
  enabled: boolean;
  file: FileState;
  base: string[];
  orderAfter: string[];
}

interface MockPluginGame {
  scenario: string;
  engine: RpgmPluginEngine;
  plugins: MockPlugin[];
  backup: MockPlugin[] | null;
  backupInvalid: string;
  readable: boolean;
  reason: string;
  revision: number;
  staleOnce: boolean;
}

const plugin = (name: string, extra: Partial<MockPlugin> = {}): MockPlugin => ({
  name,
  description: "",
  enabled: true,
  file: "ok",
  base: [],
  orderAfter: [],
  ...extra,
});

const defaultPlugins = (): MockPlugin[] => [
  plugin("CoreEngine", { description: "Shared functions that other plugins need" }),
  plugin("MessagePlus", { description: "Adds message window options", base: ["CoreEngine"], file: "case" }),
  plugin("BattleHud", { description: "Shows a battle HUD", base: ["CoreEngine"] }),
  plugin("-----", { description: "Divider" }),
  plugin("Addon", { description: "Extends LateCore", orderAfter: ["LateCore"] }),
  plugin("LateCore", { description: "Loaded late" }),
  plugin("-----", { description: "Divider" }),
  plugin("GoneOn", { description: "Its file was deleted", file: "missing" }),
  plugin("GoneOff", { description: "Off and without a file", enabled: false, file: "missing" }),
  plugin("TextLog", { description: "メッセージログを表示します", enabled: false }),
  plugin("Author/Minimap", { description: "Map overlay in a subfolder" }),
  plugin("../../outside", { description: "Unsafe name", enabled: false, file: "unsafe" }),
];

const longPlugins = (): MockPlugin[] =>
  Array.from({ length: 150 }, (_, i) =>
    plugin(`Plugin${String(i + 1).padStart(3, "0")}`, {
      description: `Mock plugin number ${i + 1}`,
      enabled: i % 7 !== 3,
    }),
  );

const clone = (plugins: MockPlugin[]): MockPlugin[] =>
  plugins.map((item) => ({ ...item, base: [...item.base], orderAfter: [...item.orderAfter] }));

const readSetting = (urlKey: string, storageKey: string, gameId: number): string => {
  try {
    const map: unknown = JSON.parse(localStorage.getItem(storageKey) || "{}");
    if (typeof map === "object" && map !== null) {
      const value = (map as Record<string, unknown>)[String(gameId)];
      if (typeof value === "string") return value;
    }
  } catch {
    // ignore a broken mock setting
  }
  try {
    if (typeof window !== "undefined" && window.location?.search) {
      return new URLSearchParams(window.location.search).get(urlKey) ?? "";
    }
  } catch {
    // no URL in node tests
  }
  return "";
};

const readDelay = (): number => {
  let raw = "";
  try {
    raw = localStorage.getItem("wlib-mock-rpgm-plugins-delay") ?? "";
  } catch {
    raw = "";
  }
  try {
    if (!raw && typeof window !== "undefined" && window.location?.search) {
      raw = new URLSearchParams(window.location.search).get("mock_rpgm_plugins_delay") ?? "";
    }
  } catch {
    raw = "";
  }
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? Math.min(value, 10000) : 0;
};

const sameList = (a: MockPlugin[], b: MockPlugin[], statuses: boolean): boolean =>
  a.length === b.length &&
  a.every((item, i) => {
    const other = b[i];
    return (
      other !== undefined &&
      item.name === other.name &&
      item.description === other.description &&
      (!statuses || item.enabled === other.enabled)
    );
  });

export class MockRpgmPlugins {
  games = new Map<number, MockPluginGame>();

  _game(gameId: number): MockPluginGame {
    let game = this.games.get(gameId);
    if (game) return game;
    const scenario = readSetting("mock_rpgm_plugins", "wlib-mock-rpgm-plugins", gameId) || "default";
    const plugins =
      scenario === "long" ? longPlugins()
      : scenario === "empty" ? []
      : scenario === "mv" ? defaultPlugins().map((item) => (item.name === "CoreEngine" ? { ...item, enabled: false } : item))
      : defaultPlugins();
    game = {
      scenario,
      engine: scenario === "mv" || scenario === "long" ? "mv" : "mz",
      plugins,
      backup: null,
      backupInvalid: "",
      readable: true,
      reason: "",
      revision: 1,
      staleOnce: scenario === "stale",
    };
    if (scenario === "changed") {
      game.backup = clone(plugins);
      game.plugins = [...clone(plugins), plugin("UpdateAddon", { description: "Added by a game update" })];
    } else if (scenario === "invalid_backup") {
      game.backupInvalid = "The backup is not a readable plugin list: The plugin list has code after the $plugins array";
    } else if (scenario === "unreadable") {
      game.backup = clone(plugins);
      game.readable = false;
      game.reason = "The plugin list has code after the $plugins array";
    }
    this.games.set(gameId, game);
    return game;
  }

  _backupState(game: MockPluginGame): RpgmPluginBackupState {
    if (game.backupInvalid) return "invalid";
    if (!game.backup) return "none";
    if (!game.readable) return "changed";
    if (sameList(game.plugins, game.backup, true)) return "same";
    return sameList(game.plugins, game.backup, false) ? "toggles" : "changed";
  }

  _entries(game: MockPluginGame): RpgmPluginEntry[] {
    const loadedIndex = new Map<string, number>();
    const duplicate = new Set<number>();
    game.plugins.forEach((item, i) => {
      if (!item.enabled) return;
      if (loadedIndex.has(item.name)) duplicate.add(i);
      else loadedIndex.set(item.name, i);
    });
    const takenOver = new Set([...duplicate].map((i) => game.plugins[i]?.name ?? ""));
    const mz = game.engine === "mz";
    const neededBy: string[][] = game.plugins.map(() => []);
    if (mz) {
      game.plugins.forEach((item, i) => {
        if (loadedIndex.get(item.name) !== i) return;
        for (const base of item.base) {
          const target = loadedIndex.get(base);
          if (target !== undefined && !takenOver.has(base)) neededBy[target]?.push(item.name);
        }
      });
    }
    return game.plugins.map((item, index) => {
      const loaded = loadedIndex.get(item.name) === index;
      const issues: RpgmPluginIssue[] = [];
      if (duplicate.has(index)) {
        issues.push({ code: "duplicate", level: "note", names: [] });
      } else {
        if (item.file === "unsafe") issues.push({ code: "unsafe_name", level: "warning", names: [] });
        if (item.file === "missing") {
          issues.push({ code: "missing_file", level: loaded ? "warning" : "note", names: [] });
        }
        if (item.file === "case") issues.push({ code: "case_mismatch", level: "note", names: [] });
        if (mz && loaded) {
          const missing = item.base.filter((name) => !loadedIndex.has(name));
          if (missing.length) issues.push({ code: "missing_dependency", level: "warning", names: missing });
          const after = item.orderAfter.filter((name) => (loadedIndex.get(name) ?? -1) > index);
          if (after.length) issues.push({ code: "wrong_order", level: "warning", direction: "after", names: after });
        }
      }
      return {
        index,
        name: item.name,
        description: item.description,
        enabled: item.enabled,
        loaded,
        can_enable: item.file !== "missing" && item.file !== "unsafe",
        needed_by: neededBy[index] ?? [],
        issues,
      };
    });
  }

  _state(game: MockPluginGame): RpgmPluginsState {
    const backup = this._backupState(game);
    return {
      success: true,
      mock: true,
      engine: game.engine,
      readable: game.readable,
      reason: game.reason,
      revision: `r${game.revision}`,
      entries: game.readable ? this._entries(game) : [],
      backup: { state: backup, reason: backup === "invalid" ? game.backupInvalid : "" },
    };
  }

  async get(gameId: number): Promise<RpgmPluginsState> {
    const delay = readDelay();
    if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
    const game = this._game(gameId);
    if (game.scenario === "load_error") {
      return { success: false, mock: true, error: "Could not read the js folder: permission denied" };
    }
    return this._state(game);
  }

  _stale(game: MockPluginGame, revision: unknown): RpgmPluginsState | null {
    if (game.staleOnce) {
      game.staleOnce = false;
      game.revision += 1;
    }
    if (revision !== `r${game.revision}`) {
      return {
        success: false,
        mock: true,
        code: "stale",
        error: "The plugin list changed after it was loaded. Showing the current list.",
      };
    }
    return null;
  }

  setEnabled(gameId: number, index: unknown, enabled: unknown, revision: unknown): RpgmPluginsState {
    const fail = (error: string): RpgmPluginsState => ({ success: false, mock: true, error });
    if (typeof index !== "number" || !Number.isInteger(index)) return fail("Index must be an integer");
    if (typeof enabled !== "boolean") return fail("Enabled must be a boolean");
    if (typeof revision !== "string") return fail("Revision must be a string");
    const game = this._game(gameId);
    const stale = this._stale(game, revision);
    if (stale) return stale;
    if (!game.readable) return fail(game.reason);
    const item = game.plugins[index];
    if (!item) return fail("The plugin entry does not exist");
    if (item.enabled === enabled) return this._state(game);
    if (enabled && (item.file === "missing" || item.file === "unsafe")) {
      return fail("This plugin cannot be switched on: its file is missing or its name is unsafe");
    }
    if (game.backupInvalid && game.backupInvalid.includes("symbolic link")) {
      return fail("plugins.js.wlib-backup is not a regular file, so no backup can be made");
    }
    if (!game.backup && !game.backupInvalid) game.backup = clone(game.plugins);
    item.enabled = enabled;
    game.revision += 1;
    return this._state(game);
  }

  restore(gameId: number, revision: unknown, replaceChanged: unknown): RpgmPluginsState {
    const fail = (error: string): RpgmPluginsState => ({ success: false, mock: true, error });
    if (typeof revision !== "string") return fail("Revision must be a string");
    if (typeof replaceChanged !== "boolean") return fail("Replace changed must be a boolean");
    const game = this._game(gameId);
    const stale = this._stale(game, revision);
    if (stale) return stale;
    const state = this._backupState(game);
    if (state === "none" || !game.backup) return fail("There is no plugin list backup");
    if (state === "invalid") return fail(game.backupInvalid);
    if (state === "same") return fail("The plugin list already equals its backup");
    if (state === "changed" && !replaceChanged) {
      return fail("The plugin list has other changes than on/off values. Confirm that they will be lost.");
    }
    game.plugins = clone(game.backup);
    game.readable = true;
    game.reason = "";
    game.revision += 1;
    return this._state(game);
  }

  // Simulates an edit by another program, for stale checks in tests.
  touch(gameId: number): void {
    this._game(gameId).revision += 1;
  }
}
