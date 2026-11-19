// SPDX-License-Identifier: GPL-3.0-or-later
import { isLewdCornerMedia } from "./gameCover.ts";

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

// What the strip and lightbox may load. LC screenshots arrive as local
// 127.0.0.1:8183/lc-images/ addresses from the backend; a raw LC URL (a
// stored list shown before that answer) is left out, never loaded.
export function displayScreenshots(images: ScreenshotUrls[]): ScreenshotUrls[] {
  return images.filter(image => !isLewdCornerMedia(image.thumb) && !isLewdCornerMedia(image.full));
}
