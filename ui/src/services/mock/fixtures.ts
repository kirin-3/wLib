// SPDX-License-Identifier: GPL-3.0-or-later
import type { CatalogSourceStatus, DiagnosticsBasic, DiagnosticsTools } from "../api";

export const MOCK_DIAGNOSTICS: DiagnosticsBasic = {
  app_version: "Browser preview", extension_version: "Not available",
  packaging: "Browser preview", os: "Browser preview", platform: "browser",
  python: "Not available", qt: null, webengine: null, renderer_stages: {},
  data_path: "Not available", database_path: "Not available", log_path: "Not available",
  database_bytes: 0, game_count: 0,
};
export const MOCK_DIAGNOSTICS_TOOLS: DiagnosticsTools = {
  "7zz": null, "7z": null, "7za": null, bsdtar: null, unrar: null,
  extension: {
    success: true, reachable: false,
    bridge: {
      connected: false, connections: 0, eligible: false, extension_version: "", can_fetch: null,
      connected_seconds: null, disconnects: 0, last_connection_seconds: null,
      last_disconnect_seconds_ago: null, last_fetch: null,
    },
  },
};
export const MOCK_DIAGNOSTICS_REPORT = "wLib diagnostics\nBrowser preview: desktop environment details are not available.";

export const MOCK_CATALOG_SOURCES: Record<string, CatalogSourceStatus> = {
  f95: { count: 27990, updated_at: "2026-10-27T05:04:11Z" },
  dlsite: { count: 41203, updated_at: "2026-10-27T05:41:52Z" },
  ryuugames: { count: 18240, updated_at: "2026-10-27T05:58:03Z" },
  vndb: { count: 31902, updated_at: "2026-10-22T06:20:37Z" },
};

const mockScreenshotUri = (width: number, height: number, fill: string, label: string): string => {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="${width}" height="${height}" fill="${fill}"/><text x="${width / 2}" y="${height / 2}" font-size="24" text-anchor="middle" dominant-baseline="middle" fill="#ffffff">${label}</text></svg>`;
  return `data:image/svg+xml,${encodeURIComponent(svg)}`;
};

const MOCK_SCREENSHOT_SET_A: string[] = [
  mockScreenshotUri(400, 200, "#1e3a5f", "Shot 1"),
  mockScreenshotUri(200, 400, "#5f1e3a", "Shot 2"),
  mockScreenshotUri(300, 300, "#1e5f3a", "Shot 3"),
  mockScreenshotUri(600, 200, "#3a5f1e", "Shot 4"),
];

export const MOCK_SCREENSHOT_SET_B: string[] = [
  mockScreenshotUri(320, 180, "#2a4a6b", "A"),
  mockScreenshotUri(180, 320, "#6b2a4a", "B"),
  mockScreenshotUri(400, 400, "#2a6b4a", "C"),
];
