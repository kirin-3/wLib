// SPDX-License-Identifier: GPL-3.0-or-later
export type PerformanceTool = "gamemode" | "mangohud" | "gamescope";

const TOOL_NAMES: Record<PerformanceTool, string> = { gamemode: "GameMode", mangohud: "MangoHud", gamescope: "Gamescope" };

export function performanceToolName(tool: PerformanceTool): string {
  return TOOL_NAMES[tool];
}

export function performanceToolState(tool: PerformanceTool, installed: boolean, sandboxEnabled: boolean): { disabled: boolean; note: string } {
  if (!installed) return { disabled: true, note: `${TOOL_NAMES[tool]} is not installed.` };
  if (tool === "gamemode" && sandboxEnabled) return { disabled: true, note: "GameMode does not work in the sandbox." };
  return { disabled: false, note: "" };
}
