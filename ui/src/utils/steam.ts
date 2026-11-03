// SPDX-License-Identifier: GPL-3.0-or-later
export const steamStateLabel = (state?: string): string => ({
  linked: "In Steam",
  pending_add: "Waiting for Steam to close",
  pending_update: "Waiting for Steam to close",
  pending_removal: "Waiting to be removed",
  user_removed: "Removed in Steam",
  not_in_steam: "Not in Steam",
}[state || "not_in_steam"] || "Not in Steam");

export const showSteamGameControl = (platform: string, state: { success?: boolean; reason?: string }): boolean =>
  platform === "linux" && state.success === true && state.reason !== "wishlisted";

export const steamIneligibleNote = (reason?: string): string => ({
  missing_file: "The game file was not found on this device, so it cannot be added to Steam.",
  browser_game: "Browser games open in the system browser and cannot be added to Steam.",
}[reason || ""] || "");
