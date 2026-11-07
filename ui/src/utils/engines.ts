// SPDX-License-Identifier: GPL-3.0-or-later
// The engines Game Detail offers. Bulk "Set engine" uses the same list so a
// bulk edit never stores a value Game Detail would rewrite to "Others".
export const ENGINE_OPTIONS = [
  "ADRIFT",
  "Flash",
  "Godot",
  "HTML",
  "Java",
  "Others",
  "QSP",
  "RAGS",
  "RPGM",
  "Ren'Py",
  "Tads",
  "Unity",
  "Unreal Engine",
  "WebGL",
  "Wolf RPG",
] as const;

export type EngineOption = (typeof ENGINE_OPTIONS)[number];

/** True when `engine` already names `option`, ignoring case and spacing. */
export const isEngine = (engine: string | null | undefined, option: string): boolean =>
  String(engine || "").trim().toLowerCase() === option.trim().toLowerCase();
