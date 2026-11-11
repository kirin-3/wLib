// SPDX-License-Identifier: GPL-3.0-or-later
import type { RpgmNames, RpgmSaveSlot, RpgmSaveView } from "../api";
import { readMockGames } from "./games";

let mockRpgmSaveMtime = 1;

const mockRpgmNames = (): RpgmNames => ({
  items: [
    { id: 1, name: "Potion", kind: "item", description: "Restores 100 HP." },
    { id: 2, name: "Magic Water", kind: "item", description: "Restores 50 MP." },
    { id: 3, name: "Royal Crest", kind: "key", description: "Proof of birth." },
  ],
  weapons: [{ id: 1, name: "Iron Sword", kind: "item", description: "" }],
  armors: [{ id: 1, name: "Leather Armor", kind: "item", description: "" }],
  actors: { "1": "Harold" },
  classes: { "1": { name: "Hero", expParams: [30, 20, 30, 30] } },
  switches: ["", "Met the king", "Door opened"],
  variables: ["", "Gold seen", "Chapter"],
  currency: "G",
  params: ["Max HP", "Max MP", "ATK", "DEF", "MAT", "MDF", "AGI", "LUK"],
});

const mockRpgmSaveView = (): RpgmSaveView => ({
  gold: 1200,
  inventory: {
    item: [
      { id: 1, count: 5, name: "Potion", known: true },
      { id: 42, count: 1, name: "Unknown item #42", known: false },
    ],
    weapon: [],
    armor: [{ id: 1, count: 1, name: "Leather Armor", known: true }],
  },
  actors: [
    {
      id: 1,
      name: "Harold",
      database_name: "Harold",
      level: 5,
      exp: 380,
      class_id: 1,
      class_name: "Hero",
      has_curve: true,
      hp: 320,
      mp: 40,
      param_plus: [0, 0, 5, 0, 0, 0, 0, 0],
    },
  ],
  switches: [
    { id: 1, name: "Met the king", value: true, type: "bool" },
    { id: 2, name: "Door opened", value: false, type: "bool" },
  ],
  variables: [
    { id: 1, name: "Gold seen", value: 10, type: "number" },
    { id: 2, name: "Chapter", value: "One", type: "string" },
    { id: 3, name: "", value: null, type: "other" },
  ],
  missing: [],
});

export const mockRpgmSaves = (method: string, args: unknown[]): unknown => {
  const gameId = Number(args[0]);
  if (!Number.isInteger(gameId) || gameId < 1 || !readMockGames().some((game) => game.id === gameId)) {
    return { success: false, mock: true, error: "Game was not found", error_code: "game_not_found" };
  }
  const slots: RpgmSaveSlot[] = [
    { file: "file1.rpgsave", slot: 1, modified: "2026-01-02T10:00:00", size: 4096, playtime: "01:23:45", title: "Chapter 1" },
    { file: "file3.rpgsave", slot: 3, modified: "2026-01-05T18:30:00", size: 4200, playtime: "", title: "" },
  ];
  if (method === "list_rpgm_saves") return { success: true, mock: true, slots };
  if (!slots.some((slot) => slot.file === args[1])) {
    return { success: false, mock: true, error: "That save file is not available", error_code: "not_found" };
  }
  if (method === "load_rpgm_save") {
    return {
      success: true,
      mock: true,
      format: "mv",
      size: 4096,
      mtime_ns: String(mockRpgmSaveMtime),
      view: mockRpgmSaveView(),
      names: mockRpgmNames(),
      raw: {
        party: { "@": "Game_Party", _gold: 1200, _items: { "1": 5, "42": 1 }, _questLog: { points: 3, title: "Intro", done: false } },
        actors: { "@": "Game_Actors", _data: [null, { "@": "Game_Actor", _name: "Harold", _level: 5, _hp: 320 }] },
      },
    };
  }
  if (args[3] !== 4096 || args[4] !== String(mockRpgmSaveMtime)) {
    return { success: false, mock: true, error: "The save changed since it was opened. Reopen the slot.", error_code: "changed" };
  }
  const target = globalThis as { __wlibMockRpgmWrites?: unknown[] };
  (target.__wlibMockRpgmWrites ??= []).push({ file: args[1], patches: args[2] });
  mockRpgmSaveMtime += 1;
  const name = "2026-01-06_12-00-00";
  return { success: true, mock: true, backup: name, message: `Saved. Previous saves backed up as ${name}`, backups: [{ name }] };
};
