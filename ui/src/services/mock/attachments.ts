// SPDX-License-Identifier: GPL-3.0-or-later
import type { GameAttachment } from "../api";
import { isRecord } from "./games";

const MOCK_ATTACHMENTS_STORAGE_KEY = "wlib-mock-attachments";

const normalizeMockAttachment = (value: unknown): GameAttachment | null => {
  if (!isRecord(value)) return null;
  const name = typeof value.name === "string" ? value.name : "";
  const size = Number(value.size);
  if (!name || !Number.isFinite(size)) return null;
  return { name, size: Math.max(0, Math.trunc(size)) };
};

export const readMockAttachments = (): Record<string, GameAttachment[]> => {
  try {
    const raw = localStorage.getItem(MOCK_ATTACHMENTS_STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as unknown) : {};
    if (!isRecord(parsed)) return {};
    const result: Record<string, GameAttachment[]> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (!Array.isArray(value)) continue;
      result[key] = value
        .map(normalizeMockAttachment)
        .filter((item): item is GameAttachment => item !== null)
        .sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
    }
    return result;
  } catch (_error) {
    return {};
  }
};

export const writeMockAttachments = (attachments: Record<string, GameAttachment[]>): void => {
  try {
    localStorage.setItem(MOCK_ATTACHMENTS_STORAGE_KEY, JSON.stringify(attachments));
  } catch (_error) {
    // Ignore browser mock persistence failures and keep the desktop contract unchanged.
  }
};

export const mockAttachmentList = (gameId: number): GameAttachment[] => {
  return readMockAttachments()[String(gameId)] || [];
};
