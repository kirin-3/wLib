// SPDX-License-Identifier: GPL-3.0-or-later
export function performanceToolState(tool: "gamemode" | "mangohud", installed: boolean, sandboxEnabled: boolean): { disabled: boolean; note: string } {
  if (!installed) return { disabled: true, note: `${tool === "gamemode" ? "GameMode" : "MangoHud"} is not installed.` };
  if (tool === "gamemode" && sandboxEnabled) return { disabled: true, note: "GameMode does not work in the sandbox." };
  return { disabled: false, note: "" };
}
