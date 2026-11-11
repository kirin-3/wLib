// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameSaveBackup, GameSavesResponse, SaveLocation } from "../api";
import { isRecord, readMockGames } from "./games";
import { readMockSettings } from "./settings";

const MOCK_GAME_SAVES_STORAGE_KEY = "wlib-mock-game-saves";
const MOCK_SAVE_DETECTED: SaveLocation[] = [
  { path: "/mock/game", type: "Game Folder", description: "Found 2 save file(s) in game directory", eligible: false },
  { path: "~/.renpy/MockGame-1234567890", type: "Ren'Py", description: "Ren'Py native save folder", eligible: true },
];

interface MockGameSavesState {
  override: string;
  backups: string[];
}

const readMockGameSaves = (): Record<string, MockGameSavesState> => {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(MOCK_GAME_SAVES_STORAGE_KEY) || "{}");
    return isRecord(raw) ? (raw as Record<string, MockGameSavesState>) : {};
  } catch {
    return {};
  }
};

const mockBackupName = (existing: string[]): string => {
  const now = new Date();
  const pad = (value: number): string => String(value).padStart(2, "0");
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}-${pad(now.getMinutes())}-${pad(now.getSeconds())}`;
  let name = stamp;
  for (let counter = 1; existing.includes(name); counter += 1) name = `${stamp}-${counter}`;
  return name;
};

export const mockGameSaves = (method: string, args: unknown[]): unknown => {
  const gameId = Number(args[0]);
  if (!Number.isInteger(gameId) || gameId < 1 || !readMockGames().some((game) => game.id === gameId)) {
    return { success: false, mock: true, error: "Game was not found", error_code: "game_not_found" };
  }
  const all = readMockGameSaves();
  const state: MockGameSavesState = { override: "", backups: [], ...all[String(gameId)] };
  const save = (): void => {
    try {
      localStorage.setItem(MOCK_GAME_SAVES_STORAGE_KEY, JSON.stringify({ ...all, [String(gameId)]: state }));
    } catch {
      // storage may be unavailable in a private window
    }
  };
  const backups = (): GameSaveBackup[] => [...state.backups].sort().reverse().map((name) => ({ name }));
  const eligible = MOCK_SAVE_DETECTED.filter((entry) => entry.eligible);
  const effective = state.override || (eligible.length === 1 ? (eligible[0]?.path ?? "") : "");
  const takeBackup = (): string => {
    const name = mockBackupName(state.backups);
    state.backups.push(name);
    save();
    return name;
  };

  switch (method) {
    case "get_game_saves":
      return {
        success: true,
        mock: true,
        override: state.override,
        override_exists: !!state.override,
        detected: state.override ? [] : MOCK_SAVE_DETECTED,
        effective,
        effective_exists: !!effective,
        backups: backups(),
        keep: readMockSettings().save_backup_keep,
      } satisfies GameSavesResponse;
    case "set_game_save_folder": {
      const path = String(args[1] ?? "").trim();
      if (path && !path.startsWith("/") && !path.startsWith("~")) {
        return { success: false, mock: true, error: "The saves folder must be an absolute path" };
      }
      state.override = path;
      save();
      return { success: true, mock: true, override: path, override_exists: !!path };
    }
    case "backup_game_saves": {
      if (!effective) return { success: false, mock: true, error: "Choose a saves folder first" };
      const name = takeBackup();
      return { success: true, mock: true, message: "Backed up 2 file(s) (1.0 KB)", backup: name, backups: backups() };
    }
    case "import_game_saves":
    case "restore_game_saves_backup": {
      if (!effective) return { success: false, mock: true, error: "Choose a saves folder first" };
      if (method === "restore_game_saves_backup" && !state.backups.includes(String(args[1]))) {
        return { success: false, mock: true, error: "That backup no longer exists" };
      }
      const name = takeBackup();
      return {
        success: true,
        mock: true,
        message: `Imported 2 file(s) (1.0 KB). Previous saves backed up as ${name}`,
        backup: name,
        backups: backups(),
      };
    }
    default:
      return state.backups.includes(String(args[1]))
        ? { success: true, mock: true }
        : { success: false, mock: true, error: "That backup no longer exists" };
  }
};
