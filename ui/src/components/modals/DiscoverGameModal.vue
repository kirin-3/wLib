<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// The preview of one catalog game from Discover: details, screenshots, the
// sites it is on, and the Wishlist, Download and Open in library actions.
// It sends no request to F95 or LewdCorner; images load from the hosts the
// backend hands out (LC screenshots through local addresses).
import { computed, ref, useTemplateRef, watch } from "vue";
import { useRouter } from "vue-router";
import { IconDownload, IconExternalLink, IconHeart, IconLibrary, IconLoader2, IconStarFilled, IconX } from "@tabler/icons-vue";
import CoverPlaceholder from "../CoverPlaceholder.vue";
import ScreenshotLightbox from "../ScreenshotLightbox.vue";
import { api, type DiscoverGameDetails, type GameRecord } from "../../services/api";
import { gamesStore } from "../../composables/useGamesStore";
import { useUpdateDownloads } from "../../composables/useUpdateDownloads";
import { discardWhenPickerCloses } from "../../composables/useDiscoverDownload";
import { useModalKeyboard } from "../../utils/modalKeyboard";
import { libraryOpenQuery } from "../../utils/storeLinks";
import { notify } from "../../utils/toast";
import { DISCOVER_STATUS_LABELS, discoverActions, discoverDate } from "../../utils/discoverActions";
import { DISCOVER_SITES, discoverCoverUrl } from "../../utils/discoverView";

const props = defineProps<{ gameKey: string | null }>();
const emit = defineEmits<{ close: []; changed: [] }>();

const router = useRouter();
const downloads = useUpdateDownloads();
const dialog = useTemplateRef<HTMLElement>("dialog");
const isOpen = computed(() => props.gameKey !== null);
const close = () => emit("close");
useModalKeyboard(dialog, () => isOpen.value, close);

const game = ref<DiscoverGameDetails | null>(null);
const screenshots = ref<{ thumb: string; full: string }[]>([]);
const failedShots = ref(new Set<string>());
const loadError = ref("");
const actionError = ref("");
const busy = ref<"" | "wishlist" | "download">("");
const coverFailed = ref(false);
const lightboxIndex = ref<number | null>(null);
let request = 0;

async function load(key: string): Promise<void> {
  const ticket = ++request;
  loadError.value = "";
  const [details, shots] = await Promise.all([api.getDiscoverGame(key), api.getDiscoverScreenshots(key)]);
  if (ticket !== request) return;
  if (!details.success || !details.game) {
    loadError.value = details.error || "Could not load this game.";
    game.value = null;
    return;
  }
  game.value = details.game;
  screenshots.value = shots.success ? (shots.screenshots ?? []) : [];
}

watch(() => props.gameKey, (key) => {
  game.value = null;
  screenshots.value = [];
  failedShots.value = new Set();
  actionError.value = "";
  coverFailed.value = false;
  lightboxIndex.value = null;
  if (key) void load(key);
}, { immediate: true });

const actions = computed(() => (game.value ? discoverActions(game.value) : null));
const cover = computed(() => (game.value && !coverFailed.value ? discoverCoverUrl(game.value.cover_url) : ""));
const shownShots = computed(() => screenshots.value.filter((image) => !failedShots.value.has(image.full)));
const siteName = (site: string) => DISCOVER_SITES.find((item) => item.key === site)?.label ?? site;
const facts = computed(() => {
  const value = game.value;
  if (!value) return [];
  const rows: { label: string; value: string }[] = [];
  if (value.developer) rows.push({ label: "Developer", value: value.developer });
  if (value.version) rows.push({ label: "Version", value: value.version_site ? `${value.version} (${siteName(value.version_site)})` : value.version });
  if (value.status) rows.push({ label: "Status", value: DISCOVER_STATUS_LABELS[value.status] ?? value.status });
  if (value.engine) rows.push({ label: "Engine", value: value.engine });
  if (value.released_at) rows.push({ label: "Released", value: value.released_at });
  if (value.updated_ts) rows.push({ label: "Last update", value: discoverDate(value.updated_ts) });
  if (value.languages?.length) rows.push({ label: "Languages", value: value.languages.join(", ") });
  return rows;
});
const originalTitle = computed(() => {
  const value = game.value;
  return value?.original_title && value.original_title !== value.title ? value.original_title : "";
});

const refreshLibrary = async () => {
  await gamesStore.refresh();
  window.dispatchEvent(new Event("wlib-refresh-library"));
  emit("changed");
};

async function wishlist(): Promise<void> {
  if (!game.value || busy.value) return;
  busy.value = "wishlist";
  actionError.value = "";
  const result = await api.addDiscoverGame(game.value.key);
  busy.value = "";
  if (!result.success) {
    actionError.value = result.error || "Could not add the game.";
    return;
  }
  notify(`${game.value.title} is on your wishlist.`, "success");
  await refreshLibrary();
  if (props.gameKey) await load(props.gameKey);
}

async function download(): Promise<void> {
  const value = game.value;
  if (!value || busy.value || !actions.value?.download.enabled) return;
  busy.value = "download";
  actionError.value = "";
  let gameId = value.library?.game_id ?? null;
  let created = false;
  if (gameId === null) {
    const result = await api.addDiscoverGame(value.key);
    if (!result.success || typeof result.game_id !== "number") {
      busy.value = "";
      actionError.value = result.error || "Could not add the game.";
      return;
    }
    gameId = result.game_id;
    created = result.created === true;
  }
  await gamesStore.refresh();
  busy.value = "";
  const record: GameRecord | undefined = gamesStore.games.value.find((item) => item.id === gameId);
  if (!record) {
    actionError.value = "The game was added, but the library could not be read. Try again.";
    return;
  }
  window.dispatchEvent(new Event("wlib-refresh-library"));
  emit("changed");
  if (created) {
    discardWhenPickerCloses(record.id, downloads.selectedGame, () => {
      void refreshLibrary();
      if (props.gameKey) void load(props.gameKey);
    });
  }
  downloads.open(record);
  if (props.gameKey) void load(props.gameKey);
}

function openInLibrary(): void {
  const value = game.value;
  if (!value?.library) return;
  const f95Url = value.f95_id !== null ? `https://f95zone.to/threads/${value.f95_id}/` : "";
  close();
  void router.push({ path: "/", query: libraryOpenQuery({ id: value.library.game_id, f95_url: f95Url }) });
}

async function openSite(url: string): Promise<void> {
  if (!url) return;
  const result = await api.openInBrowser(url);
  if (!result.success) notify(result.error || "Could not open the page.", "error");
}

const markShotFailed = (image: { full: string }) => {
  failedShots.value = new Set([...failedShots.value, image.full]);
};
</script>

<template>
  <Teleport to="body">
    <div v-if="isOpen" class="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" aria-hidden="true" @click="close"></div>
      <section
        ref="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="discover-game-title"
        tabindex="-1"
        class="discover-dialog relative w-full max-w-3xl rounded-2xl overflow-hidden flex flex-col max-h-[calc(var(--app-vh)*0.92)] outline-none"
      >
        <button type="button" class="discover-close ui-icon-btn absolute top-3 right-3 z-10 p-1.5 rounded-lg" aria-label="Close" title="Close" @click="close">
          <IconX class="w-5 h-5" aria-hidden="true" />
        </button>

        <div v-if="!game" class="p-8 flex items-center gap-3" role="status">
          <template v-if="loadError">
            <p id="discover-game-title" class="text-sm">{{ loadError }}</p>
          </template>
          <template v-else>
            <IconLoader2 class="w-5 h-5 animate-spin" aria-hidden="true" />
            <span id="discover-game-title">Loading…</span>
          </template>
        </div>

        <div v-else class="overflow-y-auto min-h-0">
          <div class="discover-hero relative aspect-[16/7] w-full">
            <img v-if="cover" :src="cover" alt="" referrerpolicy="no-referrer" class="absolute inset-0 w-full h-full object-cover object-top" @error="coverFailed = true" />
            <CoverPlaceholder v-else :title="game.title" class="absolute inset-0" />
          </div>
          <div class="p-6 space-y-5">
            <div>
              <h2 id="discover-game-title" class="text-2xl font-bold leading-tight">{{ game.title }}</h2>
              <p v-if="originalTitle" class="text-sm mt-1" style="color: var(--text-secondary)">{{ originalTitle }}</p>
              <div class="flex flex-wrap items-center gap-2 mt-3">
                <span v-if="actions?.libraryLabel" class="discover-library-pill" :data-state="game.library?.state">{{ actions.libraryLabel }}</span>
                <span v-if="game.wrating !== null" class="discover-rating-pill" :title="`Weighted F95 rating; the thread shows ${game.rating?.toFixed(1)} from ${game.rating_count} reviews`">
                  <IconStarFilled class="w-3.5 h-3.5" aria-hidden="true" />
                  {{ game.wrating.toFixed(1) }} <span class="opacity-75">({{ game.rating_count }} reviews)</span>
                </span>
              </div>
            </div>

            <div class="flex flex-wrap gap-2">
              <button v-if="actions?.wishlist" type="button" class="discover-action" :disabled="busy !== ''" @click="wishlist">
                <IconLoader2 v-if="busy === 'wishlist'" class="w-4 h-4 animate-spin" aria-hidden="true" />
                <IconHeart v-else class="w-4 h-4" aria-hidden="true" />
                Wishlist
              </button>
              <button
                v-if="actions?.download.shown"
                type="button"
                class="discover-action discover-action--primary"
                :disabled="busy !== '' || !actions.download.enabled"
                :aria-describedby="actions.download.reason ? 'discover-download-reason' : undefined"
                @click="download"
              >
                <IconLoader2 v-if="busy === 'download'" class="w-4 h-4 animate-spin" aria-hidden="true" />
                <IconDownload v-else class="w-4 h-4" aria-hidden="true" />
                Download
              </button>
              <span v-if="actions?.download.reason" id="discover-download-reason" class="self-center text-xs" style="color: var(--text-muted)">{{ actions.download.reason }}</span>
              <button v-if="actions?.openInLibrary" type="button" class="discover-action" @click="openInLibrary">
                <IconLibrary class="w-4 h-4" aria-hidden="true" />
                Open in library
              </button>
            </div>
            <p v-if="actionError" role="alert" class="discover-error text-sm">{{ actionError }}</p>

            <dl v-if="facts.length" class="discover-facts">
              <template v-for="fact in facts" :key="fact.label">
                <dt>{{ fact.label }}</dt>
                <dd>{{ fact.value }}</dd>
              </template>
            </dl>

            <div v-if="shownShots.length">
              <h3 class="discover-label">Screenshots</h3>
              <div class="discover-strip" role="group" aria-label="Game screenshots">
                <button
                  v-for="(image, index) in shownShots"
                  :key="image.full"
                  type="button"
                  class="discover-thumb"
                  :aria-label="`View screenshot ${index + 1}`"
                  @click="lightboxIndex = index"
                >
                  <img :src="image.thumb" :alt="`Screenshot ${index + 1}`" loading="lazy" referrerpolicy="no-referrer" @error="markShotFailed(image)" />
                </button>
              </div>
            </div>

            <div v-if="game.overview">
              <h3 class="discover-label">Overview</h3>
              <p class="text-sm whitespace-pre-line leading-relaxed" style="color: var(--text-secondary)">{{ game.overview }}</p>
            </div>

            <div v-if="game.tags.length">
              <h3 class="discover-label">Tags</h3>
              <div class="flex flex-wrap gap-1.5">
                <span v-for="tag in game.tags" :key="tag" class="discover-tag">{{ tag }}</span>
              </div>
            </div>

            <div v-if="game.dlsite_genres?.length">
              <h3 class="discover-label">DLsite genres</h3>
              <div class="flex flex-wrap gap-1.5">
                <span v-for="genre in game.dlsite_genres" :key="genre" class="discover-tag">{{ genre }}</span>
              </div>
            </div>

            <div v-if="game.entries.length">
              <h3 class="discover-label">Found on</h3>
              <ul class="discover-sites">
                <li v-for="entry in game.entries" :key="`${entry.site}:${entry.id}`">
                  <div class="min-w-0">
                    <p class="text-sm font-semibold">
                      {{ entry.name }}
                      <span v-if="entry.site === 'lc' && entry.tier?.toLowerCase() === 'premium'" class="discover-plus">LC Plus</span>
                    </p>
                    <p class="text-xs truncate" style="color: var(--text-muted)">{{ entry.title }}</p>
                  </div>
                  <button v-if="entry.url" type="button" class="discover-action discover-action--small" :aria-label="`Open the ${entry.name} page in your browser`" @click="openSite(entry.url)">
                    <IconExternalLink class="w-4 h-4" aria-hidden="true" />
                    Open
                  </button>
                </li>
              </ul>
            </div>
          </div>
        </div>
      </section>
    </div>
    <ScreenshotLightbox :images="shownShots.map((image) => image.full)" :start-index="lightboxIndex" @close="lightboxIndex = null" />
  </Teleport>
</template>

<style scoped>
.discover-dialog {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
  color: var(--text-primary);
}
.discover-close {
  background: var(--overlay-scrim-strong);
  color: var(--overlay-control-text);
}
.discover-hero {
  background: linear-gradient(135deg, var(--bg-raised), var(--bg-inset));
}
.discover-label {
  margin-bottom: 0.5rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.discover-action {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  min-height: 2.5rem;
  padding: 0.45rem 0.9rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 0.875rem;
  font-weight: 600;
}
.discover-action:hover:not(:disabled) {
  background: var(--bg-overlay);
  border-color: var(--border-hover);
}
.discover-action--primary {
  background: var(--brand);
  border-color: var(--brand);
  color: var(--on-brand);
}
.discover-action--primary:hover:not(:disabled) {
  background: var(--brand);
  border-color: var(--brand);
  filter: brightness(1.08);
}
.discover-action--small {
  min-height: 2rem;
  padding: 0.25rem 0.6rem;
  font-size: 0.75rem;
  flex-shrink: 0;
}
.discover-action:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
.discover-action:focus-visible,
.discover-thumb:focus-visible,
.discover-close:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.discover-error {
  color: var(--danger-text);
}
.discover-library-pill,
.discover-rating-pill,
.discover-plus {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border-radius: 0.375rem;
  padding: 0.15rem 0.5rem;
  font-size: 0.75rem;
  font-weight: 700;
}
.discover-library-pill {
  background: var(--brand);
  color: var(--on-brand);
}
.discover-library-pill[data-state="wishlisted"] {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  color: var(--text-primary);
}
.discover-rating-pill {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  color: var(--text-primary);
}
.discover-rating-pill svg {
  color: var(--warning-text);
}
.discover-plus {
  margin-left: 0.35rem;
  padding: 0.05rem 0.35rem;
  font-size: 0.625rem;
  background: var(--brand-glow);
  border: 1px solid var(--brand-deep);
  color: var(--brand);
}
.discover-facts {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.35rem 1rem;
  font-size: 0.875rem;
}
.discover-facts dt {
  color: var(--text-muted);
}
.discover-facts dd {
  color: var(--text-primary);
  min-width: 0;
  overflow-wrap: anywhere;
}
.discover-strip {
  display: flex;
  gap: 0.5rem;
  overflow-x: auto;
  padding-bottom: 0.25rem;
}
.discover-thumb {
  flex: 0 0 auto;
  height: 6rem;
  border-radius: 0.5rem;
  overflow: hidden;
  border: 1px solid var(--border);
  background: var(--bg-inset);
  padding: 0;
}
.discover-thumb:hover {
  border-color: var(--brand);
}
.discover-thumb img {
  height: 6rem;
  width: auto;
  display: block;
  object-fit: cover;
}
.discover-tag {
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.15rem 0.55rem;
  background: var(--bg-raised);
  color: var(--text-secondary);
  font-size: 0.75rem;
}
.discover-sites {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.discover-sites li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--bg-inset);
}
</style>
