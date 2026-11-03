// SPDX-License-Identifier: GPL-3.0-or-later
import type { ApiBasicResponse, SteamCoverConversion } from "../services/api";

export interface SteamCoverApi {
  getSteamCoverConversions(): Promise<ApiBasicResponse & { covers?: SteamCoverConversion[] }>;
  saveSteamCoverPng(token: string, data: string): Promise<ApiBasicResponse>;
}

export type PngEncoder = (data: string, type: string) => Promise<string>;

// Qt cannot decode some cover formats (AVIF); the webview can, so it hands
// the backend PNG copies for Steam artwork. An empty string reports a failure.
export const encodeCoverAsPng: PngEncoder = async (data, type) => {
  const bytes = Uint8Array.from(atob(data), (char) => char.charCodeAt(0));
  const bitmap = await createImageBitmap(new Blob([bytes], { type }));
  try {
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("Canvas is unavailable");
    context.drawImage(bitmap, 0, 0);
    const png = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("PNG encoding failed"))), "image/png"));
    const buffer = new Uint8Array(await png.arrayBuffer());
    let binary = "";
    for (let offset = 0; offset < buffer.length; offset += 0x8000) {
      binary += String.fromCharCode(...buffer.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
  } finally {
    bitmap.close();
  }
};

let converting = false;

export const convertSteamCovers = async (api: SteamCoverApi, encode: PngEncoder = encodeCoverAsPng): Promise<number> => {
  if (converting) return 0;
  converting = true;
  let converted = 0;
  try {
    // Each batch is bounded; the backend drops tokens as they are answered.
    for (let batch = 0; batch < 50; batch++) {
      const result = await api.getSteamCoverConversions();
      const covers = result.success ? result.covers ?? [] : [];
      if (!covers.length) break;
      for (const cover of covers) {
        let png = "";
        try { png = await encode(cover.data, cover.type); }
        catch (error) { console.error("Could not convert cover for Steam", error); }
        const saved = await api.saveSteamCoverPng(cover.token, png);
        if (saved.success && png) converted++;
      }
    }
  } finally {
    converting = false;
  }
  return converted;
};
