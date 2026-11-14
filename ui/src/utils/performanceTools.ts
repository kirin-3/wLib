// SPDX-License-Identifier: GPL-3.0-or-later
import type { LaunchMode } from "./launchMode";

export type PerformanceTool = "gamemode" | "mangohud" | "gamescope";

const TOOL_NAMES: Record<PerformanceTool, string> = { gamemode: "GameMode", mangohud: "MangoHud", gamescope: "Gamescope" };

export function performanceToolName(tool: PerformanceTool): string {
  return TOOL_NAMES[tool];
}

export function performanceToolState(tool: PerformanceTool, installed: boolean, sandboxEnabled: boolean, launchMode?: LaunchMode): { disabled: boolean; note: string } {
  if (!installed) return { disabled: true, note: `${TOOL_NAMES[tool]} is not installed.` };
  if (tool === "gamemode" && sandboxEnabled) return { disabled: true, note: "GameMode does not work in the sandbox." };
  // Not greyed out: launch targets in other modes still use the setting.
  if (tool === "mangohud" && launchMode === "rpgmaker_linux") return { disabled: false, note: "MangoHud is not used with the RPGMaker Linux runner." };
  return { disabled: false, note: "" };
}
