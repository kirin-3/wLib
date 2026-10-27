// SPDX-License-Identifier: GPL-3.0-or-later
import { reactive } from "vue";

interface CoverGame {
  cover_local_url?: string;
  cover_image_path?: string;
}

// Replacement files have fresh URLs. A failed URL is tried only once, across
// all cover views, and a newly chosen cover is always eligible again.
const failedSources = reactive(new Set<string>());

export function gameCoverSrc(game: CoverGame | null | undefined): string {
  return [game?.cover_local_url, game?.cover_image_path]
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
