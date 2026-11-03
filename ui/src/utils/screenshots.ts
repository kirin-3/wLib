// SPDX-License-Identifier: GPL-3.0-or-later
export interface ScreenshotUrls { thumb: string; full: string }

export function screenshotUrls(url: string): ScreenshotUrls {
  try {
    const parsed = new URL(url);
    if (parsed.host === "preview.f95zone.to" || parsed.host === "attachments.f95zone.to") {
      parsed.host = "preview.f95zone.to";
      const thumb = parsed.toString();
      parsed.host = "attachments.f95zone.to";
      return { thumb, full: parsed.toString() };
    }
  } catch { /* Keep malformed URLs unchanged. */ }
  return { thumb: url, full: url };
}
