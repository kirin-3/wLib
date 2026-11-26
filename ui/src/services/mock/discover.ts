// SPDX-License-Identifier: GPL-3.0-or-later
// Browser-preview mocks for the Discover page (get_discover_status,
// get_discover_filters, search_discover, get_discover_game,
// get_discover_screenshots, add_discover_game, discard_discover_game).
//
// The mock catalog is generated: 240 games over the five sites, so paging,
// filters and sorts can be tried. Set localStorage "wlib-mock-discover" to
// "building" to see the loading state.
import type {
  DiscoverCard,
  DiscoverGameDetails,
  DiscoverQuery,
  DiscoverSite,
  DiscoverStatus,
  GameRecord,
  StoreLink,
} from "../api";
import { readMockGames } from "./games.ts";

const SITES: DiscoverSite[] = ["f95", "lc", "dlsite", "vndb", "ryuugames"];
const SITE_NAMES: Record<DiscoverSite, string> = {
  f95: "F95Zone", lc: "LewdCorner", dlsite: "DLsite", vndb: "VNDB", ryuugames: "Ryuugames",
};
const TAGS = [
  "big tits", "sandbox", "male protagonist", "romance", "fantasy", "corruption", "harem", "3dcg",
  "animated", "vaginal sex", "oral sex", "female protagonist", "monster girl", "milf", "voyeurism", "humor",
  "adventure", "drama", "slice of life", "school setting", "rpg", "turn based combat", "management", "dating sim",
  "sci-fi", "horror", "mystery", "netorare", "cheating", "teasing", "handjob", "groping",
  "superpowers", "magic", "graphic violence", "2dcg", "pixel art", "point & click", "visual novel", "multiple endings",
];
const ENGINES = ["Ren'Py", "Unity", "RPGM", "HTML", "Others"];
const STATUSES: DiscoverStatus[] = ["ongoing", "completed", "onhold", "abandoned"];
const WORDS = ["Echo", "Summer", "Night", "Academy", "Witch", "Island", "Lust", "Dream", "Shadow", "Garden", "Royal", "Star"];
const PAGE = 120;

interface MockEntry extends DiscoverCard {
  tags: string[];
}

const keyTag = (name: string): string => name.toLowerCase().replace(/[^a-z0-9]/g, "");

const buildCatalog = (): MockEntry[] => {
  const games: MockEntry[] = [];
  for (let i = 0; i < 240; i += 1) {
    const sites = SITES.filter((_site, index) => (i + index) % (index + 2) === 0);
    if (!sites.length) sites.push(SITES[i % SITES.length]!);
    const thread = sites.includes("f95") || sites.includes("lc");
    const f95 = sites.includes("f95");
    const title = `${WORDS[i % WORDS.length]} ${WORDS[(i * 7 + 3) % WORDS.length]} ${i + 1}`;
    // Every seventh F95 game has no review count yet, like most of the real catalog.
    const ratingCount = f95 && i % 7 !== 3 ? (i * 37) % 900 : 0;
    const rating = f95 ? 2.5 + ((i * 13) % 25) / 10 : null;
    games.push({
      key: `${sites[0]}:${1000 + i}`,
      title,
      developer: `Studio ${String.fromCharCode(65 + (i % 26))}`,
      version: thread ? `v0.${i % 9}.${i % 4}` : "",
      version_site: thread ? (f95 ? "f95" : "lc") : "",
      // LC-first games have no cover but have screenshots; VNDB-first games
      // that are on no thread site have neither.
      cover_url: sites[0] === "lc" || (sites[0] === "vndb" && !thread) ? "" : `https://picsum.photos/seed/wlib-discover-${i}/600/340`,
      sites,
      f95_id: f95 ? 100000 + i : null,
      lc_id: sites.includes("lc") ? 50000 + i : null,
      lc_tier: sites.includes("lc") ? (i % 5 === 0 ? "Premium" : "Free") : "",
      dlsite_id: sites.includes("dlsite") ? `RJ0${String(1200000 + i)}` : "",
      vndb_id: sites.includes("vndb") ? `v${7000 + i}` : "",
      ryuu_id: sites.includes("ryuugames") ? 300 + i : null,
      status: thread ? STATUSES[i % STATUSES.length]! : "",
      engine: thread ? ENGINES[i % ENGINES.length]! : "",
      rating,
      rating_count: f95 ? ratingCount : null,
      wrating: rating !== null && ratingCount > 0 ? (ratingCount * rating + 25 * 3.2) / (ratingCount + 25) : null,
      score: rating !== null && ratingCount > 0 ? (ratingCount * rating + 25 * 3.2) / (ratingCount + 25) : rating,
      released_at: `20${String(10 + (i % 16)).padStart(2, "0")}-${String((i % 12) + 1).padStart(2, "0")}-15`,
      updated_ts: 1_790_000_000 - i * 86_400,
      library: null,
      tags: thread ? TAGS.filter((_tag, index) => (i + index) % (3 + (index % 7)) === 0) : [],
    });
  }
  return games;
};

const CATALOG = buildCatalog();

const DLSITE_TITLE = "プレビューのゲーム";
// Every third thread game has an overview long enough to fold (Show more).
const LONG_OVERVIEW = [
  "You move to a quiet coastal town for the summer, to look after your aunt's old guesthouse while she is away.",
  "The guesthouse is falling apart, the money is running out, and the guests who still come each have their own reasons to stay. Fix the rooms, plan the meals and keep the books, or the bank takes the house at the end of the season.",
  "Each guest has a story of their own: the painter who never finishes a canvas, the student hiding from her family, the retired captain who knows more about the town than he says. Spend time with them and their stories change what happens in the town.",
  "This version adds the second month of the season, two new guests, a reworked calendar and many fixes. Saves from older versions are not compatible.",
].join("\n\n");

const normalize = (text: string): string =>
  text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();

const threadId = (url: unknown, host: RegExp): string => {
  const match = String(url || "").match(host);
  return match ? match[1]! : "";
};

const libraryMark = (entry: DiscoverCard, games: GameRecord[]): DiscoverCard["library"] => {
  const match = games.find((game) =>
    (entry.f95_id !== null && threadId(game.f95_url, /f95zone\.to\/threads\/(?:[^/]*\.)?(\d+)/) === String(entry.f95_id)) ||
    (entry.lc_id !== null && threadId(game.lc_url, /lewdcorner\.com\/threads\/(?:[^/]*\.)?(\d+)/) === String(entry.lc_id)) ||
    (game.links || []).some((link) =>
      (link.source === "dlsite" && link.id === entry.dlsite_id) || (link.source === "vndb" && link.id === entry.vndb_id)));
  return match ? { game_id: match.id, state: match.exe_path.trim() ? "installed" : "wishlisted" } : null;
};

// While the mock library is empty, two cards in the first row show the
// "Installed" and "Wishlist" markers. Their game ids are not in the mock
// library, so "Open in library" finds no game there.
const DEMO_MARKS: Record<string, DiscoverCard["library"]> = {
  "f95:1002": { game_id: -1, state: "installed" },
  "f95:1004": { game_id: -2, state: "wishlisted" },
};

const shownMark = (entry: DiscoverCard, games: GameRecord[]): DiscoverCard["library"] =>
  libraryMark(entry, games) ?? (games.length ? null : DEMO_MARKS[entry.key] ?? null);

const card = (entry: MockEntry, games: GameRecord[]): DiscoverCard => {
  const { tags: _tags, ...rest } = entry;
  return { ...rest, library: shownMark(entry, games) };
};

const SORTS: Record<string, (a: DiscoverCard, b: DiscoverCard) => number> = {
  updated: (a, b) => b.updated_ts - a.updated_ts,
  rating: (a, b) => (b.score ?? -1) - (a.score ?? -1),
  released: (a, b) => b.released_at.localeCompare(a.released_at),
  title: () => 0,
};

const links = (entry: MockEntry): StoreLink[] => [
  ...(entry.dlsite_id ? [{ source: "dlsite", id: entry.dlsite_id, url: `https://www.dlsite.com/maniax/work/=/product_id/${entry.dlsite_id}.html` }] : []),
  ...(entry.vndb_id ? [{ source: "vndb", id: entry.vndb_id, url: `https://vndb.org/${entry.vndb_id}` }] : []),
] as StoreLink[];

export class MockDiscover {
  private readonly created = new Set<number>();

  respond(
    method: string,
    args: unknown[],
    library: {
      addGame: (title: string, f95Url: string, lcUrl: string, version: string, links: StoreLink[]) => { success?: boolean; id?: number; attached?: boolean; error?: string };
      deleteGame: (id: number) => void;
      catalogEnabled: boolean;
    },
  ): unknown {
    const building = (() => {
      try {
        return localStorage.getItem("wlib-mock-discover") === "building";
      } catch {
        return false;
      }
    })();
    if (!library.catalogEnabled) {
      if (method === "get_discover_status") return { success: true, mock: true, enabled: false, installed: false, ready: false, building: false, error: "", built_at: null };
      return { success: false, error: "The game catalog is turned off", error_code: "catalog_off" };
    }
    if (method === "get_discover_status") {
      return { success: true, mock: true, enabled: true, installed: true, ready: !building, building, error: "", built_at: "2026-10-27T05:58:40Z" };
    }
    if (building) return { success: false, error: "The game catalog is still loading", error_code: "not_ready" };
    const games = readMockGames();
    const find = (key: unknown): MockEntry | undefined => CATALOG.find((entry) => entry.key === key);
    switch (method) {
      case "get_discover_filters": {
        const count = (test: (entry: MockEntry) => boolean): number => CATALOG.filter(test).length;
        return {
          success: true,
          tags: TAGS.map((name) => ({ key: keyTag(name), name, count: count((entry) => entry.tags.includes(name)) }))
            .sort((a, b) => a.name.localeCompare(b.name)),
          engines: ENGINES.map((name) => ({ name, count: count((entry) => entry.engine === name) })),
          statuses: STATUSES.map((key) => ({ key, count: count((entry) => entry.status === key) })),
        };
      }
      case "search_discover": {
        const query = (args[0] ?? {}) as DiscoverQuery;
        const words = normalize(query.search || "").split(" ").filter(Boolean);
        const include = new Set(query.include_tags?.map(keyTag));
        const exclude = new Set(query.exclude_tags?.map(keyTag));
        const matches = CATALOG.filter((entry) => {
          const text = normalize(`${entry.title} ${entry.developer}`);
          const tags = new Set(entry.tags.map(keyTag));
          return words.every((word) => text.includes(word))
            && (!query.include_sites?.length || entry.sites.some((site) => query.include_sites!.includes(site)))
            && (!query.exclude_sites?.length || entry.sites.some((site) => !query.exclude_sites!.includes(site)))
            && [...include].every((tag) => tags.has(tag))
            && ![...exclude].some((tag) => tags.has(tag))
            && (!query.statuses?.length || query.statuses.includes(entry.status as DiscoverStatus))
            && (!query.engines?.length || query.engines.includes(entry.engine))
            && (!query.min_rating || (entry.score ?? 0) >= query.min_rating)
            && (!query.hide_library || !shownMark(entry, games));
        }).map((entry) => card(entry, games));
        const sort = SORTS[query.sort || "updated"] ?? SORTS.updated!;
        matches.sort((a, b) => sort(a, b) || a.title.localeCompare(b.title) || a.key.localeCompare(b.key));
        const offset = Math.max(0, query.offset || 0);
        const page = matches.slice(offset, offset + Math.min(query.limit || PAGE, PAGE));
        const end = offset + page.length;
        return { success: true, mock: true, games: page, total: matches.length, next_offset: end < matches.length ? end : null };
      }
      case "get_discover_game": {
        const entry = find(args[0]);
        if (!entry) return { success: false, error: "This game is no longer in the catalog", error_code: "unknown_game" };
        const thread = entry.f95_id !== null || entry.lc_id !== null;
        const premiumOnly = entry.f95_id === null && entry.lc_tier === "Premium";
        const game: DiscoverGameDetails = {
          ...card(entry, games),
          tags: entry.tags,
          entry_ids: [],
          entries: entry.sites.map((site) => ({
            site,
            name: SITE_NAMES[site],
            id: entry.key.split(":")[1]!,
            // DLsite lists a game under its Japanese title.
            title: site === "dlsite" ? DLSITE_TITLE : entry.title,
            url: site === "f95" ? `https://f95zone.to/threads/${entry.f95_id}/`
              : site === "lc" ? `https://lewdcorner.com/threads/${entry.lc_id}/`
                : site === "dlsite" ? `https://www.dlsite.com/maniax/work/=/product_id/${entry.dlsite_id}.html`
                  : site === "vndb" ? `https://vndb.org/${entry.vndb_id}`
                    : `https://www.ryuugames.com/?p=${entry.ryuu_id}`,
            ...(site === "lc" ? { tier: entry.lc_tier } : {}),
          })),
          ...(thread ? { overview: CATALOG.indexOf(entry) % 3 === 2 ? LONG_OVERVIEW : "A preview overview from the thread's first post.\nIt can span lines.", languages: ["English"] } : {}),
          ...(entry.dlsite_id ? { dlsite_genres: ["Big Breasts", "Fantasy"], original_title: DLSITE_TITLE } : {}),
          download: { available: thread && !premiumOnly, reason: premiumOnly ? "Needs LC Plus" : "" },
        };
        return { success: true, mock: true, game };
      }
      case "get_discover_screenshots": {
        const entry = find(args[0]);
        if (!entry) return { success: false, error: "This game is no longer in the catalog", error_code: "unknown_game" };
        const index = CATALOG.indexOf(entry);
        const screenshots = entry.f95_id === null && entry.lc_id === null ? [] : [0, 1, 2, 3].map((shot) => {
          const url = `https://picsum.photos/seed/wlib-discover-${index}-${shot}`;
          return { thumb: `${url}/320/180`, full: `${url}/1280/720` };
        });
        return { success: true, mock: true, screenshots };
      }
      case "add_discover_game": {
        const entry = find(args[0]);
        if (!entry) return { success: false, error: "This game is no longer in the catalog", error_code: "unknown_game" };
        const mark = libraryMark(entry, games);
        if (mark) return { success: true, mock: true, game_id: mark.game_id, created: false };
        const result = library.addGame(
          entry.title,
          entry.f95_id !== null ? `https://f95zone.to/threads/${entry.f95_id}/` : "",
          entry.f95_id === null && entry.lc_id !== null ? `https://lewdcorner.com/threads/${entry.lc_id}/` : "",
          entry.version,
          links(entry),
        );
        if (!result.success || typeof result.id !== "number") return result;
        const created = !result.attached;
        if (created) this.created.add(result.id);
        return { success: true, mock: true, game_id: result.id, created };
      }
      case "discard_discover_game": {
        const id = Number(args[0]);
        if (!Number.isInteger(id) || id < 0) return { success: false, error: "Invalid game id" };
        const game = games.find((item) => item.id === id);
        if (!this.created.has(id) || !game || game.exe_path.trim()) {
          this.created.delete(id);
          return { success: true, removed: false };
        }
        this.created.delete(id);
        library.deleteGame(id);
        return { success: true, removed: true };
      }
      default:
        return undefined;
    }
  }
}
