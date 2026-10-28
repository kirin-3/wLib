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

export interface AnchorLike {
  closest(selector: string): { getAttribute(name: string): string | null } | null;
}

/**
 * Returns the raw href when a click landed on a link inside rendered HTML, or
 * null when it did not. Used to keep rendered links from navigating the WebView.
 */
export const renderedLinkHref = (target: unknown): string | null => {
  const element = target as AnchorLike | null;
  if (!element || typeof element.closest !== "function") return null;
  const anchor = element.closest("a[href]");
  return anchor ? anchor.getAttribute("href") : null;
};
