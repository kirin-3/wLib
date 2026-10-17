// SPDX-License-Identifier: GPL-3.0-or-later
/**
 * Returns the href when it is a safe web link, otherwise null.
 * Only absolute http: and https: URLs open in the system browser;
 * file:, javascript:, relative, and malformed hrefs do nothing.
 */
export const getSafeNotesLinkUrl = (
  href: string | null | undefined,
): string | null => {
  if (typeof href !== "string") return null;
  const trimmed = href.trim();
  if (!trimmed) return null;
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  return trimmed;
};
