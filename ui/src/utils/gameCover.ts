// SPDX-License-Identifier: GPL-3.0-or-later
import { reactive } from "vue";

interface CoverGame {
  cover_local_url?: string;
  cover_cache_url?: string;
  cover_image_path?: string;
  cover_card_still_url?: string;
  cover_card_anim_url?: string;
}

// Replacement files have fresh URLs. A failed URL is tried only once, across
// all cover views, and a newly chosen cover is always eligible again.
const failedSources = reactive(new Set<string>());

export function gameCoverSrc(game: CoverGame | null | undefined): string {
  return [game?.cover_local_url, game?.cover_cache_url, game?.cover_image_path]
    .find((url): url is string => !!url && !failedSources.has(url)) || "";
}

export function gameCoverError(event: Event, game: CoverGame): boolean {
  const target = event.currentTarget as HTMLImageElement | null;
  const src = target?.getAttribute("src") || gameCoverSrc(game);
  if (src) failedSources.add(src);
  const fallback = gameCoverSrc(game);
  if (target && fallback && fallback !== src) target.src = fallback;
  return !!fallback;
}

export interface CoverCardSources {
  still: string;
  anim: string;
  original: string;
}

// Dense views show the small copies when they exist and have not failed.
// The animated layer needs a still copy, which also marks the cover as
// animated: without a usable animated copy (too long to copy, or it failed to
// load) the original plays there instead, so animation is never lost.
export function coverCardSources(game: CoverGame | null | undefined): CoverCardSources {
  const usable = (url: string | undefined): string => (url && !failedSources.has(url) ? url : "");
  const still = usable(game?.cover_card_still_url);
  const original = gameCoverSrc(game);
  return { still, anim: still ? usable(game?.cover_card_anim_url) || original : "", original };
}

export function coverAnimError(event: Event): void {
  const src = (event.currentTarget as HTMLImageElement | null)?.getAttribute("src");
  if (src) failedSources.add(src);
}
