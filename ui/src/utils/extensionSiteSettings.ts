// SPDX-License-Identifier: GPL-3.0-or-later
export const SITE_SETTINGS_ID = "site-settings";
export const SITE_SETTINGS_HASH = `#${SITE_SETTINGS_ID}`;
export const SITE_SETTINGS_ROUTE = `/extension${SITE_SETTINGS_HASH}`;

export const EXTENSION_SITE_SWITCHES = [
  { name: "DLsite", description: "Add games from DLsite product pages." },
  { name: "VNDB", description: "Add visual novels from VNDB pages." },
  { name: "Ryuugames", description: "Add games from Ryuugames posts." },
  { name: "LewdCorner", description: "Add games from LewdCorner threads." },
  { name: "LewdCorner downloads", description: "Use LewdCorner download links in wLib. Turn on LewdCorner first." },
  { name: "Buzzheavier downloads", description: "Download Buzzheavier mirrors in wLib." },
] as const;
