// SPDX-License-Identifier: GPL-3.0-or-later
import type { RgssRawNodeData, RpgmNames, RpgmSaveFormat, RpgmSaveSlot, RpgmSaveView } from "../api";
import { mockGameSaves } from "./gameSaves";
import { readMockGames } from "./games";

let mockRpgmSaveMtime = 1;
// Stands in for the SHA-256 of the slot bytes; it changes with every mock write.
const mockRpgmSaveRevision = (): string => mockRpgmSaveMtime.toString(16).padStart(64, "0");
const mockRpgmWriteRefusal = (args: unknown[], size: number): Record<string, unknown> | null => {
  if (typeof args[5] !== "string" || !/^[0-9a-f]{64}$/.test(args[5])) {
    return { success: false, mock: true, error: "The edit request has no save revision. Reopen the slot.", error_code: "invalid_patch" };
  }
  if (args[3] !== size || args[4] !== String(mockRpgmSaveMtime) || args[5] !== mockRpgmSaveRevision()) {
    return { success: false, mock: true, error: "The save changed since it was opened. Reopen the slot.", error_code: "changed" };
  }
  return null;
};

// The write's backup goes into the mock Saves list, so Undo save and the
// Saves section can find it.
const mockRpgmWriteBackup = (gameId: number): string => {
  const result = mockGameSaves("backup_game_saves", [gameId]);
  const name = typeof result === "object" && result !== null && "backup" in result ? result.backup : "";
  return typeof name === "string" && name ? name : "2026-01-06_12-00-00";
};

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

interface MockRgssFixture {
  format: RpgmSaveFormat;
  slot: RpgmSaveSlot;
  names: RpgmNames;
  view: RpgmSaveView;
  raw: RgssRawNodeData[];
}

const mockRgssFixtures = (): Record<string, MockRgssFixture> => ({
  "Save1.rxdata": {
    format: "xp",
    slot: {
      file: "Save1.rxdata",
      slot: 1,
      format: "xp",
      modified: "2026-01-03T11:00:00",
      size: 2048,
      playtime: "01:00:00",
      title: "",
    },
    names: {
      items: [
        { id: 1, name: "Herb", kind: "item", description: "Restores HP." },
        { id: 2, name: "Elixir", kind: "item", description: "Restores HP and SP." },
      ],
      weapons: [
        { id: 1, name: "Bronze Sword", kind: "item", description: "" },
        { id: 2, name: "Iron Sword", kind: "item", description: "" },
      ],
      armors: [{ id: 1, name: "Cloth Tunic", kind: "item", description: "" }],
      actors: { "1": "Aluxes" },
      classes: {},
      switches: ["", "Intro Done", "Bridge Open"],
      variables: ["", "Kills", "Hero Title"],
      currency: "Gold",
      params: ["HP", "SP", "STR", "DEX", "AGI", "INT"],
      mp_label: "SP",
    },
    view: {
      gold: 750,
      gold_ref: 10,
      inventory: {
        item: [{ id: 1, count: 4, name: "Herb", known: true, ref: 13 }],
        weapon: [{ id: 1, count: 2, name: "Bronze Sword", known: true, ref: 16 }],
        armor: [{ id: 1, count: 1, name: "Cloth Tunic", known: true, ref: 19 }],
      },
      actors: [
        {
          id: 1,
          name: "Aluxes",
          database_name: "Aluxes",
          level: 5,
          exp: 1000,
          class_id: 1,
          class_name: "",
          has_curve: true,
          hp: 420,
          mp: 110,
          param_plus: [20, 10, 5, 3, -2, 4],
          name_ref: 30,
          level_ref: 31,
          exp_ref: 32,
          hp_ref: 33,
          mp_ref: 34,
          param_plus_refs: [35, 36, 37, 38, 39, 40],
        },
      ],
      switches: [
        { id: 1, name: "Intro Done", value: true, type: "bool", ref: 50 },
        { id: 2, name: "Bridge Open", value: false, type: "bool", ref: 51 },
      ],
      variables: [
        { id: 1, name: "Kills", value: 7, type: "number", ref: 60 },
        { id: 2, name: "Hero Title", value: "Scout", type: "string", ref: 61 },
      ],
      missing: [],
    },
    raw: [
      { id: 0, t: "int", v: 144000, editable: true },
      {
        id: 1,
        t: "object",
        class: "Game_System",
        ivars: [
          ["@tone_a", { id: 2, t: "userdef", class: "Tone", size: 32 }],
          ["@tone_b", { id: 3, t: "link", to: 2 }],
          ["@raw_bytes", { id: 4, t: "str", v: "\ufffd\ufffd" }],
        ],
      },
      {
        id: 9,
        t: "object",
        class: "Game_Party",
        ivars: [
          ["@gold", { id: 10, t: "int", v: 750, editable: true }],
          [
            "@items",
            {
              id: 11,
              t: "hash",
              pairs: [[{ id: 12, t: "int", v: 1 }, { id: 13, t: "int", v: 4, editable: true }]],
            },
          ],
          [
            "@weapons",
            {
              id: 14,
              t: "hash",
              pairs: [[{ id: 15, t: "int", v: 1 }, { id: 16, t: "int", v: 2, editable: true }]],
            },
          ],
          [
            "@armors",
            {
              id: 17,
              t: "hash",
              pairs: [[{ id: 18, t: "int", v: 1 }, { id: 19, t: "int", v: 1, editable: true }]],
            },
          ],
        ],
      },
    ],
  },
  "Save1.rvdata": {
    format: "vx",
    slot: {
      file: "Save1.rvdata",
      slot: 1,
      format: "vx",
      modified: "2026-01-04T14:20:00",
      size: 2560,
      playtime: "00:01:01",
      title: "",
    },
    names: {
      items: [
        { id: 1, name: "Potion", kind: "item", description: "Heals 500 HP." },
        { id: 2, name: "Ether", kind: "item", description: "Heals 100 MP." },
      ],
      weapons: [{ id: 1, name: "Short Sword", kind: "item", description: "" }],
      armors: [{ id: 1, name: "Leather Vest", kind: "item", description: "" }],
      actors: { "1": "Sona" },
      classes: {},
      switches: ["", "Prologue Clear", "Gate Unlocked"],
      variables: ["", "Score", "Ratio"],
      currency: "G",
      params: ["HP", "MP", "ATK", "DEF", "SPI", "AGI"],
      mp_label: "MP",
    },
    view: {
      gold: 120,
      gold_ref: 100,
      inventory: {
        item: [{ id: 1, count: 5, name: "Potion", known: true, ref: 103 }],
        weapon: [{ id: 1, count: 1, name: "Short Sword", known: true, ref: 106 }],
        armor: [],
      },
      actors: [
        {
          id: 1,
          name: "Sona",
          database_name: "Sona",
          level: 4,
          exp: 420,
          class_id: 1,
          class_name: "",
          has_curve: true,
          hp: 250,
          mp: 60,
          param_plus: [15, 5, 3, 2, 1, -1],
          name_ref: 120,
          level_ref: 121,
          exp_ref: 122,
          hp_ref: 123,
          mp_ref: 124,
          param_plus_refs: [125, 126, 127, 128, 129, 130],
        },
      ],
      switches: [
        { id: 1, name: "Prologue Clear", value: false, type: "bool", ref: 140 },
        { id: 2, name: "Gate Unlocked", value: true, type: "bool", ref: 141 },
      ],
      variables: [
        { id: 1, name: "Score", value: 99, type: "number", ref: 150 },
        { id: 2, name: "Ratio", value: 2.25, type: "number", ref: 151 },
      ],
      missing: [],
    },
    raw: [
      {
        id: 99,
        t: "object",
        class: "Game_Party",
        ivars: [
          ["@gold", { id: 100, t: "int", v: 120, editable: true }],
          [
            "@items",
            {
              id: 101,
              t: "hash",
              pairs: [[{ id: 102, t: "int", v: 1 }, { id: 103, t: "int", v: 5, editable: true }]],
            },
          ],
          ["@factor", { id: 104, t: "float", v: 0.75, editable: true }],
          ["@color", { id: 105, t: "userdef", class: "Color", size: 32 }],
          ["@color_ref", { id: 107, t: "link", to: 105 }],
        ],
      },
    ],
  },
  "Save01.rvdata2": {
    format: "vxace",
    slot: {
      file: "Save01.rvdata2",
      slot: 1,
      format: "vxace",
      modified: "2026-01-05T18:30:00",
      size: 3072,
      playtime: "00:00:23",
      title: "",
    },
    names: {
      items: [
        { id: 1, name: "Magic Water", kind: "item", description: "Restores MP." },
        { id: 2, name: "Old Key", kind: "key", description: "Opens the tower gate." },
      ],
      weapons: [{ id: 1, name: "Steel Blade", kind: "item", description: "" }],
      armors: [{ id: 1, name: "Iron Helm", kind: "item", description: "" }],
      actors: { "1": "Eric" },
      classes: {},
      switches: ["", "Tutorial Seen", "Tower Opened"],
      variables: ["", "Gold Found", "Companion Name", "Raw Object"],
      currency: "GP",
      params: ["Max HP", "Max MP", "ATK", "DEF", "MAT", "MDF", "AGI", "LUK"],
      mp_label: "MP",
    },
    view: {
      gold: 2500,
      gold_ref: 210,
      inventory: {
        item: [
          { id: 1, count: 3, name: "Magic Water", known: true, ref: 213 },
          { id: 2, count: 1, name: "Old Key", known: true, ref: 215 },
        ],
        weapon: [{ id: 42, count: 1, name: "Unknown weapon #42", known: false, ref: 218 }],
        armor: [{ id: 1, count: 2, name: "Iron Helm", known: true, ref: 221 }],
      },
      actors: [
        {
          id: 1,
          name: "Eric",
          database_name: "Eric",
          level: 6,
          exp: 1250,
          class_id: 1,
          class_name: "",
          has_curve: false,
          hp: 500,
          mp: 120,
          param_plus: [25, 10, 4, 2, 0, 0, -3, 5],
          name_ref: 230,
          level_ref: 231,
          exp_ref: 232,
          hp_ref: 233,
          mp_ref: 234,
          param_plus_refs: [235, 236, 237, 238, 239, 240, 241, 242],
        },
      ],
      switches: [
        { id: 1, name: "Tutorial Seen", value: true, type: "bool", ref: 250 },
        { id: 2, name: "Tower Opened", value: false, type: "bool", ref: 251 },
      ],
      variables: [
        { id: 1, name: "Gold Found", value: 50, type: "number", ref: 260 },
        { id: 2, name: "Companion Name", value: "Natalie", type: "string", ref: 261 },
        { id: 3, name: "Raw Object", value: null, type: "other", ref: null },
      ],
      missing: [],
    },
    raw: [
      {
        id: 200,
        t: "hash",
        pairs: [
          [{ id: 201, t: "sym", v: "playtime_s" }, { id: 202, t: "str", v: "00:00:23", editable: true }],
          [{ id: 203, t: "sym", v: "frame_count" }, { id: 204, t: "int", v: 1380, editable: true }],
        ],
      },
      {
        id: 205,
        t: "hash",
        pairs: [
          [
            { id: 206, t: "sym", v: "party" },
            {
              id: 209,
              t: "object",
              class: "Game_Party",
              ivars: [
                ["@gold", { id: 210, t: "int", v: 2500, editable: true }],
                [
                  "@text_history",
                  { id: 222, t: "array", items: [{ id: 223, t: "str", v: "Welcome to the tower.", editable: true }] },
                ],
                ["@window_tone", { id: 224, t: "userdef", class: "Tone", size: 32 }],
                ["@backup_tone", { id: 225, t: "link", to: 224 }],
                ["@tiles", { id: 226, t: "userdef", class: "Table", size: 96 }],
              ],
            },
          ],
        ],
      },
    ],
  },
});

export const mockRpgmSaves = (method: string, args: unknown[]): unknown => {
  const gameId = Number(args[0]);
  const game = readMockGames().find((item) => item.id === gameId);
  if (!Number.isInteger(gameId) || gameId < 1 || !game) {
    return { success: false, mock: true, error: "Game was not found", error_code: "game_not_found" };
  }
  const isRgss = /rgss|xp|vx/i.test(game.engine ?? "") || /rgss|vx.?ace/i.test(game.exe_path);
  if (isRgss) {
    const fixtures = mockRgssFixtures();
    const slots = Object.values(fixtures).map((entry) => entry.slot);
    if (method === "list_rpgm_saves") return { success: true, mock: true, slots };
    const selected = typeof args[1] === "string" ? fixtures[args[1]] : undefined;
    if (!selected) {
      return { success: false, mock: true, error: "That save file is not available", error_code: "not_found" };
    }
    if (method === "load_rpgm_save") {
      return {
        success: true,
        mock: true,
        format: selected.format,
        size: selected.slot.size,
        mtime_ns: String(mockRpgmSaveMtime),
        revision: mockRpgmSaveRevision(),
        view: selected.view,
        names: selected.names,
        raw: selected.raw,
      };
    }
    const refusal = mockRpgmWriteRefusal(args, selected.slot.size);
    if (refusal) return refusal;
    const target = globalThis as { __wlibMockRpgmWrites?: unknown[] };
    (target.__wlibMockRpgmWrites ??= []).push({ file: args[1], patches: args[2] });
    mockRpgmSaveMtime += 1;
    const name = mockRpgmWriteBackup(gameId);
    return { success: true, mock: true, backup: name, message: `Saved. Previous saves backed up as ${name}`, backups: [{ name }] };
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
      revision: mockRpgmSaveRevision(),
      view: mockRpgmSaveView(),
      names: mockRpgmNames(),
      raw: {
        party: { "@": "Game_Party", _gold: 1200, _items: { "1": 5, "42": 1 }, _questLog: { points: 3, title: "Intro", done: false } },
        actors: { "@": "Game_Actors", _data: [null, { "@": "Game_Actor", _name: "Harold", _level: 5, _hp: 320 }] },
      },
    };
  }
  const refusal = mockRpgmWriteRefusal(args, 4096);
  if (refusal) return refusal;
  const target = globalThis as { __wlibMockRpgmWrites?: unknown[] };
  (target.__wlibMockRpgmWrites ??= []).push({ file: args[1], patches: args[2] });
  mockRpgmSaveMtime += 1;
  const name = mockRpgmWriteBackup(gameId);
  return { success: true, mock: true, backup: name, message: `Saved. Previous saves backed up as ${name}`, backups: [{ name }] };
};
