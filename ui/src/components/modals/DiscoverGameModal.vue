<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// The preview of one catalog game from Discover: details, screenshots, the
// sites it is on, and the Add to wishlist, Download and Open in library actions.
// It sends no request to F95 or LewdCorner; images load from the hosts the
// backend hands out (LC screenshots through local addresses).
import { computed, nextTick, onBeforeUnmount, ref, useTemplateRef, watch } from "vue";
import { useRouter } from "vue-router";
import {
  IconBookmarkFilled,
  IconBookmarkPlus,
  IconCheck,
  IconChevronDown,
  IconChevronUp,
  IconDownload,
  IconExternalLink,
  IconLibrary,
  IconLoader2,
  IconRefresh,
  IconStarFilled,
  IconX,
} from "@tabler/icons-vue";
import CoverPlaceholder from "../CoverPlaceholder.vue";
import ScreenshotLightbox from "../ScreenshotLightbox.vue";
import { api, type DiscoverGameDetails, type GameRecord } from "../../services/api";
import { gamesStore } from "../../composables/useGamesStore";
import { useUpdateDownloads } from "../../composables/useUpdateDownloads";
import { discardWhenPickerCloses } from "../../composables/useDiscoverDownload";
import { useModalKeyboard } from "../../utils/modalKeyboard";
import { libraryOpenQuery } from "../../utils/storeLinks";
import { notify, notifyError, notifyUndo } from "../../utils/toast";
import {
  DISCOVER_STATUS_LABELS,
  discoverActions,
  discoverDate,
  discoverEntryTitle,
  discoverFocusAfterWishlist,
  discoverLoadError,
  discoverRatingNote,
} from "../../utils/discoverActions";
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
// Thumbnails and the cover fade in once loaded; until then a still frame of the
// final size holds their place.
const loadedShots = ref(new Set<string>());
// The address of the hero image that has loaded; a new address fades in again.
const heroLoaded = ref("");
const loadError = ref<{ title: string; detail: string } | null>(null);
const actionError = ref("");
const busy = ref<"" | "wishlist" | "download">("");
const coverFailed = ref(false);
const lightboxIndex = ref<number | null>(null);
// Read by screen readers when a header action changes; the region stays in the
// dialog, so the text is announced even when the toast is not.
const announcement = ref("");
const announce = async (text: string) => {
  announcement.value = "";
  await nextTick();
  announcement.value = text;
};
let request = 0;

async function load(key: string): Promise<void> {
  const ticket = ++request;
  loadError.value = null;
  const [details, shots] = await Promise.all([api.getDiscoverGame(key), api.getDiscoverScreenshots(key)]);
  if (ticket !== request) return;
  if (!details.success || !details.game) {
    // Errors without a code are database messages: logged, and shown as the cause.
    if (!details.error_code && details.error) console.warn("Discover game failed to load:", details.error);
    loadError.value = discoverLoadError(details);
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
  loadedShots.value = new Set();
  actionError.value = "";
  coverFailed.value = false;
  heroLoaded.value = "";
  lightboxIndex.value = null;
  announcement.value = "";
  if (key) void load(key);
}, { immediate: true });

const actions = computed(() => (game.value ? discoverActions(game.value) : null));
const cover = computed(() => (game.value && !coverFailed.value ? discoverCoverUrl(game.value.cover_url) : ""));
const shownShots = computed(() => screenshots.value.filter((image) => !failedShots.value.has(image.full)));
// The wide image at the top: the cover, else the first screenshot. A cover
// that is still loading keeps the full frame; only a cover that is missing or
// failed hands the frame to a screenshot. With neither, a thin band of the
// title's color stays, so a sparse game does not open on an empty slab. The
// screenshot also stays first in the strip below: the strip is the full set,
// in the site's order, and the frame crops the image.
const hero = computed<{ src: string; shot: { full: string } | null } | null>(() => {
  if (cover.value) return { src: cover.value, shot: null };
  const first = shownShots.value[0];
  return first ? { src: first.full, shot: first } : null;
});
const heroFailed = (image: { src: string; shot: { full: string } | null }) => {
  if (image.shot) markShotFailed(image.shot);
  else coverFailed.value = true;
};
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
// The weighted rating says in words what changed it; there is no tooltip to read.
const ratingNote = computed(() => (game.value ? discoverRatingNote(game.value) : ""));

// A long overview folds to seven lines. Show more appears only when the text
// is cut: the fold is measured on the folded text when it loads and when its
// width changes.
const overview = useTemplateRef<HTMLElement>("overview");
const overviewMore = useTemplateRef<HTMLElement>("overviewMore");
const overviewOpen = ref(false);
const overviewFolds = ref(false);
const measureOverview = () => {
  const element = overview.value;
  if (!element || overviewOpen.value) return;
  overviewFolds.value = element.scrollHeight > element.clientHeight + 1;
};
let overviewObserver: ResizeObserver | null = null;
watch(overview, (element) => {
  overviewObserver?.disconnect();
  overviewObserver = null;
  if (!element || typeof ResizeObserver === "undefined") return;
  overviewObserver = new ResizeObserver(measureOverview);
  overviewObserver.observe(element);
});
onBeforeUnmount(() => overviewObserver?.disconnect());
watch(() => game.value?.overview, async () => {
  overviewOpen.value = false;
  overviewFolds.value = false;
  await nextTick();
  measureOverview();
});
// Show less can move the button above the view; it stays in view and keeps focus.
const toggleOverview = async () => {
  overviewOpen.value = !overviewOpen.value;
  if (overviewOpen.value) return;
  await nextTick();
  overviewMore.value?.scrollIntoView({ block: "nearest" });
};
const originalTitle = computed(() => {
  const value = game.value;
  return value?.original_title && value.original_title !== value.title ? value.original_title : "";
});

const refreshLibrary = async () => {
  await gamesStore.refresh();
  window.dispatchEvent(new Event("wlib-refresh-library"));
  emit("changed");
};

// The header action that takes focus when Add to wishlist goes away.
const focusAfterWishlist = () => {
  const target = discoverFocusAfterWishlist(actions.value);
  const element = target === "dialog" ? null : dialog.value?.querySelector<HTMLElement>(`[data-discover-action="${target}"]`);
  (element ?? dialog.value)?.focus();
};

// Undo removes the game again, only when this Add to wishlist created it; the
// backend keeps a game that has a download or a game file by then.
async function undoWishlist(gameId: number, title: string, key: string): Promise<void> {
  const result = await api.discardDiscoverGame(gameId);
  if (!result.success) {
    notifyError(result.error || `Could not remove ${title} from your wishlist.`);
    return;
  }
  if (!result.removed) {
    notify(`${title} stays in your library: it has a download or game files now.`, "info");
    return;
  }
  await refreshLibrary();
  if (props.gameKey !== key) return;
  await load(key);
  if (props.gameKey !== key) return;
  void announce("Removed from your wishlist.");
  await nextTick();
  // Focus was on the toast's Undo button, outside this dialog, and that toast
  // is going away.
  if (!dialog.value?.contains(document.activeElement)) dialog.value?.querySelector<HTMLElement>('[data-discover-action="wishlist"]')?.focus();
}

// The modal can close or show another game while a request runs: results for
// a game that is no longer shown change the library, but not this dialog.
async function wishlist(): Promise<void> {
  const value = game.value;
  const key = props.gameKey;
  if (!value || !key || busy.value) return;
  busy.value = "wishlist";
  actionError.value = "";
  try {
    const result = await api.addDiscoverGame(value.key);
    if (!result.success) {
      if (props.gameKey === key) actionError.value = result.error || "Could not add the game.";
      return;
    }
    const message = `${value.title} is on your wishlist.`;
    if (result.created === true && typeof result.game_id === "number") {
      const gameId = result.game_id;
      notifyUndo(message, () => undoWishlist(gameId, value.title, key));
    } else {
      notify(message, "success");
    }
    await refreshLibrary();
    if (props.gameKey !== key) return;
    await load(key);
    if (props.gameKey !== key) return;
    void announce("Added to your wishlist.");
  } finally {
    busy.value = "";
  }
  // The Add to wishlist button is gone; focus goes to the next header action.
  if (props.gameKey === key) {
    await nextTick();
    if (!dialog.value?.contains(document.activeElement) || document.activeElement === dialog.value) focusAfterWishlist();
  }
}

async function download(): Promise<void> {
  const value = game.value;
  const key = props.gameKey;
  if (!value || !key || busy.value || !actions.value?.download.enabled) return;
  busy.value = "download";
  actionError.value = "";
  const shown = () => props.gameKey === key;
  let gameId = value.library?.game_id ?? null;
  let created = false;
  if (gameId === null) {
    const result = await api.addDiscoverGame(value.key);
    if (!result.success || typeof result.game_id !== "number") {
      busy.value = "";
      if (shown()) actionError.value = result.error || "Could not add the game.";
      return;
    }
    gameId = result.game_id;
    created = result.created === true;
  }
  await gamesStore.refresh();
  busy.value = "";
  const record: GameRecord | undefined = gamesStore.games.value.find((item) => item.id === gameId);
  if (!record) {
    if (shown()) actionError.value = "wLib added the game, but could not read your library. Click Download again.";
    return;
  }
  window.dispatchEvent(new Event("wlib-refresh-library"));
  emit("changed");
  // The build picker opens even when this dialog closed meanwhile: the game is
  // added, and closing the picker without a download removes it again.
  if (created) {
    discardWhenPickerCloses(record.id, downloads.selectedGame, () => {
      void refreshLibrary();
      if (props.gameKey) void load(props.gameKey);
    });
  }
  downloads.open(record);
  if (shown()) void load(key);
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
const markShotLoaded = (image: { full: string }) => {
  loadedShots.value = new Set([...loadedShots.value, image.full]);
};

// The button goes away while the game loads, so focus moves to the dialog.
const retryLoad = () => {
  if (!props.gameKey) return;
  dialog.value?.focus();
  void load(props.gameKey);
};
</script>

<template>
  <Teleport to="body">
    <div v-if="isOpen" class="fixed inset-0 z-50 flex items-center justify-center p-4">
      <!-- The same plain scrim as the Library game dialog (no backdrop blur). -->
      <div class="absolute inset-0 bg-black/85" aria-hidden="true" @click="close"></div>
      <section
        ref="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="discover-game-title"
        tabindex="-1"
        class="modal-content relative flex w-full max-w-3xl max-h-[min(calc(var(--app-vh)*0.9),52rem)] flex-col overflow-hidden rounded-xl outline-none"
      >
        <!-- The title, the actions and Close stay in view while the details
             below scroll. Close is a cell of the header grid, not a floating
             button, so it never covers the title, the actions or the content.
             It stays first in the DOM, so it is still the first Tab stop. -->
        <header class="discover-header" :class="{ 'discover-header--alone': !game }">
          <button
            type="button"
            class="discover-close modal-close-btn transition-colors"
            aria-label="Close"
            title="Close (Esc)"
            @click="close"
          >
            <IconX class="ui-action-icon" aria-hidden="true" />
          </button>

          <div v-if="!game && loadError" class="discover-heading">
            <div role="alert">
              <p id="discover-game-title" class="discover-state-title font-semibold">{{ loadError.title }}</p>
              <p v-if="loadError.detail" class="discover-load-detail mt-1 text-sm">{{ loadError.detail }}</p>
            </div>
            <button type="button" class="modal-toolbar-action ui-action-btn mt-4" @click="retryLoad">
              <IconRefresh class="ui-action-icon" aria-hidden="true" />
              Try again
            </button>
          </div>
          <div v-else-if="!game" class="discover-heading discover-loading flex items-center gap-3" role="status">
            <IconLoader2 class="w-5 h-5 animate-spin" aria-hidden="true" />
            <span id="discover-game-title">Loading the game…</span>
          </div>

          <template v-else>
            <div class="discover-heading">
              <h2 id="discover-game-title" class="discover-title" :title="game.title">{{ game.title }}</h2>
              <p v-if="originalTitle" class="discover-subtitle" :title="originalTitle">{{ originalTitle }}</p>
            </div>
            <div class="discover-actions">
              <!-- Busy and unavailable actions use aria-disabled, not disabled: the
                   button keeps focus while it works, and Tab still reaches it and
                   reads why Download is off. The click handlers check both. -->
              <button
                v-if="actions?.wishlist"
                type="button"
                class="modal-toolbar-action ui-action-btn"
                data-discover-action="wishlist"
                :aria-disabled="busy !== '' ? 'true' : undefined"
                :aria-busy="busy === 'wishlist' ? 'true' : undefined"
                @click="wishlist"
              >
                <IconLoader2 v-if="busy === 'wishlist'" class="ui-action-icon animate-spin" aria-hidden="true" />
                <IconBookmarkPlus v-else class="ui-action-icon" aria-hidden="true" />
                Add to wishlist
              </button>
              <button
                v-if="actions?.download.shown"
                type="button"
                class="modal-toolbar-action modal-toolbar-action--primary ui-action-btn"
                :class="{ 'modal-toolbar-action--unavailable': !actions.download.enabled }"
                data-discover-action="download"
                :aria-disabled="busy !== '' || !actions.download.enabled ? 'true' : undefined"
                :aria-busy="busy === 'download' ? 'true' : undefined"
                :aria-describedby="actions.download.reason || actions.download.note ? 'discover-download-note' : undefined"
                @click="download"
              >
                <IconLoader2 v-if="busy === 'download'" class="ui-action-icon animate-spin" aria-hidden="true" />
                <IconDownload v-else class="ui-action-icon" aria-hidden="true" />
                {{ actions.download.label }}
              </button>
              <button v-if="actions?.openInLibrary" type="button" class="modal-toolbar-action ui-action-btn" data-discover-action="openInLibrary" @click="openInLibrary">
                <IconLibrary class="ui-action-icon" aria-hidden="true" />
                Open in library
              </button>
            </div>
            <!-- Why Download is off, or what it also does for a new game. -->
            <p
              v-if="actions?.download.shown && (actions.download.reason || actions.download.note)"
              id="discover-download-note"
              class="discover-note text-xs"
            >{{ actions.download.reason || actions.download.note }}</p>
            <p v-if="actionError" role="alert" class="discover-error text-sm">{{ actionError }}</p>
          </template>
        </header>

        <template v-if="game">
          <div class="discover-body min-h-0 flex-1 overflow-y-auto">
            <!-- A fixed frame, so the details do not move while the image loads;
                 the thin band is used only when no image is known. -->
            <div class="discover-hero relative w-full" :class="hero ? 'discover-hero--image' : 'discover-hero--band'">
              <CoverPlaceholder v-if="!hero || heroLoaded !== hero.src" :title="game.title" class="absolute inset-0" />
              <img
                v-if="hero"
                :key="hero.src"
                :src="hero.src"
                alt=""
                referrerpolicy="no-referrer"
                class="discover-fade absolute inset-0 w-full h-full object-cover object-top"
                :class="{ 'discover-fade--in': heroLoaded === hero.src }"
                @load="heroLoaded = hero.src"
                @error="heroFailed(hero)"
              />
            </div>

            <div class="discover-sections">
              <div v-if="actions?.libraryLabel || game.score !== null" class="flex flex-wrap items-center gap-2">
                <span v-if="actions?.libraryLabel" class="discover-pill discover-library-pill">
                  <IconBookmarkFilled v-if="game.library?.state === 'wishlisted'" class="w-3.5 h-3.5" aria-hidden="true" />
                  <IconCheck v-else class="w-3.5 h-3.5" aria-hidden="true" />
                  {{ actions.libraryLabel }}
                </span>
                <span v-if="game.score !== null" class="discover-pill discover-rating-pill">
                  <IconStarFilled class="w-3.5 h-3.5" aria-hidden="true" />
                  {{ game.score.toFixed(1) }} <span class="discover-rating-note">{{ ratingNote }}</span>
                </span>
              </div>

              <section v-if="shownShots.length" aria-labelledby="discover-shots-label">
                <h3 id="discover-shots-label" class="discover-label">Screenshots</h3>
                <div class="discover-strip" role="group" aria-label="Game screenshots">
                  <button
                    v-for="(image, index) in shownShots"
                    :key="image.full"
                    type="button"
                    class="discover-thumb"
                    :aria-label="`View screenshot ${index + 1}`"
                    @click="lightboxIndex = index"
                  >
                    <img
                      :src="image.thumb"
                      :alt="`Screenshot ${index + 1}`"
                      loading="lazy"
                      referrerpolicy="no-referrer"
                      class="discover-fade"
                      :class="{ 'discover-fade--in': loadedShots.has(image.full) }"
                      @load="markShotLoaded(image)"
                      @error="markShotFailed(image)"
                    />
                  </button>
                </div>
              </section>

              <!-- One section: the thread tags, then DLsite's genres as a second
                   group that names its source. -->
              <section v-if="game.tags.length || game.dlsite_genres?.length" aria-labelledby="discover-tags-label">
                <h3 id="discover-tags-label" class="discover-label">Tags</h3>
                <ul v-if="game.tags.length" class="discover-chips" aria-label="Thread tags">
                  <li v-for="tag in game.tags" :key="tag" class="discover-tag">{{ tag }}</li>
                </ul>
                <div v-if="game.dlsite_genres?.length" :class="{ 'discover-genres--after': game.tags.length }">
                  <h4 id="discover-genres-label" class="discover-sublabel">DLsite genres</h4>
                  <ul class="discover-chips" aria-labelledby="discover-genres-label">
                    <li v-for="genre in game.dlsite_genres" :key="genre" class="discover-tag discover-tag--dlsite">{{ genre }}</li>
                  </ul>
                </div>
              </section>

              <section v-if="game.overview?.trim()" aria-labelledby="discover-overview-label">
                <h3 id="discover-overview-label" class="discover-label">Overview</h3>
                <p
                  id="discover-overview-text"
                  ref="overview"
                  class="discover-overview"
                  :class="{ 'discover-overview--folded': !overviewOpen, 'discover-overview--cut': !overviewOpen && overviewFolds }"
                >{{ game.overview }}</p>
                <button
                  v-if="overviewFolds"
                  ref="overviewMore"
                  type="button"
                  class="discover-more"
                  aria-controls="discover-overview-text"
                  :aria-expanded="overviewOpen ? 'true' : 'false'"
                  @click="toggleOverview"
                >
                  <IconChevronUp v-if="overviewOpen" class="w-4 h-4" aria-hidden="true" />
                  <IconChevronDown v-else class="w-4 h-4" aria-hidden="true" />
                  {{ overviewOpen ? "Show less" : "Show more" }}
                </button>
              </section>

              <section v-if="facts.length" aria-labelledby="discover-facts-label">
                <h3 id="discover-facts-label" class="discover-label">Details</h3>
                <dl class="discover-facts">
                  <div v-for="fact in facts" :key="fact.label" class="discover-fact">
                    <dt>{{ fact.label }}</dt>
                    <dd>{{ fact.value }}</dd>
                  </div>
                </dl>
              </section>

              <section v-if="game.entries.length" aria-labelledby="discover-sites-label">
                <h3 id="discover-sites-label" class="discover-label">Found on</h3>
                <ul>
                  <!-- The site's own title shows only when it differs from the
                       dialog's title (a Japanese DLsite title, for example). -->
                  <li v-for="entry in game.entries" :key="`${entry.site}:${entry.id}`" class="discover-site">
                    <div class="discover-site-text">
                      <span class="discover-site-name">{{ entry.name }}</span>
                      <span v-if="entry.site === 'lc' && entry.tier?.toLowerCase() === 'premium'" class="discover-plus">LC Plus members only</span>
                      <span v-if="discoverEntryTitle(entry.title, game.title)" class="discover-site-title">{{ discoverEntryTitle(entry.title, game.title) }}</span>
                    </div>
                    <button v-if="entry.url" type="button" class="modal-toolbar-action ui-action-btn shrink-0" :aria-label="`Open the ${entry.name} page in your browser`" @click="openSite(entry.url)">
                      <IconExternalLink class="ui-action-icon" aria-hidden="true" />
                      Open
                    </button>
                  </li>
                </ul>
              </section>
            </div>
          </div>
        </template>
        <p class="sr-only" aria-live="polite">{{ announcement }}</p>
      </section>
    </div>
    <ScreenshotLightbox :images="shownShots.map((image) => image.full)" :start-index="lightboxIndex" @close="lightboxIndex = null" />
  </Teleport>
</template>

<style scoped>
/* The dialog shell and the buttons match the Library game dialog
   (GameDetailModal.vue): same frame, close button and toolbar actions. */
.modal-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
  color: var(--text-primary);
  contain: layout paint;
}
.modal-close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.375rem;
  height: 2.375rem;
  border-radius: 0.5rem;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  color: var(--text-muted);
}
.modal-close-btn:hover {
  background: var(--bg-overlay);
  color: var(--text-primary);
}
.modal-toolbar-action {
  min-height: 2.375rem;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 0.75rem;
  font-weight: 500;
}
.modal-toolbar-action:hover:not(:disabled, [aria-disabled="true"]) {
  background: var(--bg-overlay);
  border-color: var(--border-hover);
}
.modal-toolbar-action:is(:disabled, [aria-disabled="true"]) {
  opacity: 0.6;
  cursor: not-allowed;
}
/* Download is the one filled action. */
.modal-toolbar-action--primary {
  background: var(--brand);
  border-color: var(--brand);
  color: var(--on-brand);
  font-weight: 600;
}
.modal-toolbar-action--primary:hover:not(:disabled, [aria-disabled="true"]) {
  background: var(--brand);
  border-color: var(--brand);
  filter: brightness(1.08);
}
/* A Download that cannot run turns grey, as a disabled filled button does
   elsewhere in the app (.ui-btn-solid in style.css): a faded brand color still
   looks clickable. The note under the actions says why. */
.modal-toolbar-action--unavailable[aria-disabled="true"] {
  background: var(--bg-overlay);
  border-color: transparent;
  color: var(--text-muted);
  opacity: 0.55;
  filter: none;
}
/* Opacity would fade the focus ring too: a focused unavailable or busy action
   drops it and keeps its grey colors, so the ring shows at full strength. */
.modal-toolbar-action[aria-disabled="true"]:focus-visible {
  opacity: 1;
}
.modal-toolbar-action:focus-visible,
.discover-thumb:focus-visible,
.discover-more:focus-visible,
.modal-close-btn:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}

/* The header does not scroll. Narrow windows: the title and Close share the
   first row and the actions take the whole second row. From 768px the actions
   sit between the title and Close (at most two actions, so the title keeps
   about 20rem). The note and the error take full rows under them; they are
   placed only when shown, so no empty row adds a gap. */
.discover-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas:
    "heading close"
    "actions actions";
  align-items: start;
  gap: 0.75rem 1rem;
  flex-shrink: 0;
  padding: 1rem;
  border-bottom: 1px solid var(--border);
}
/* Loading and load errors: no actions row and nothing under the header. */
.discover-header--alone {
  grid-template-areas: "heading close";
  border-bottom: none;
}
.discover-heading {
  grid-area: heading;
  min-width: 0;
}
.discover-close {
  grid-area: close;
}
/* Centers the loading line and the error title on Close. */
.discover-loading {
  min-height: 2.5rem;
}
.discover-state-title {
  padding-top: 0.5rem;
}
.discover-title {
  /* Centers the first line on the 2.5rem Close button beside it. */
  padding-top: 0.55rem;
  font-size: 1.125rem;
  font-weight: 700;
  line-height: 1.3;
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.discover-subtitle {
  margin-top: 0.125rem;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 0.875rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* The header's side padding matches the sections below it. */
@media (min-width: 641px) {
  .discover-header {
    padding: 1.25rem 1.5rem;
  }
  .discover-header--alone {
    padding: 1.5rem;
  }
}
@media (min-width: 768px) {
  .discover-header {
    grid-template-columns: minmax(0, 1fr) auto auto;
    grid-template-areas: "heading actions close";
    gap: 0.75rem 1.25rem;
  }
  .discover-header--alone {
    grid-template-areas: "heading . close";
  }
  .discover-loading {
    min-height: 2.375rem;
  }
  .discover-state-title {
    padding-top: 0.45rem;
  }
  /* The larger title, centered on the 2.375rem buttons. */
  .discover-title {
    padding-top: 0.3rem;
    font-size: 1.375rem;
  }
}
.discover-actions {
  grid-area: actions;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.discover-error {
  grid-column: 1 / -1;
  color: var(--danger-text);
}
.discover-note {
  grid-column: 1 / -1;
  max-width: 65ch;
  color: var(--text-secondary);
  line-height: 1.45;
}
.discover-load-detail {
  max-width: 65ch;
  color: var(--text-secondary);
}

.discover-hero {
  background: linear-gradient(135deg, var(--bg-raised), var(--bg-inset));
}
/* 16:7, but never more than 30% of the window: on short windows the top of
   the screenshot strip stays in view. The image is cropped from its top. */
.discover-hero--image {
  aspect-ratio: 16 / 7;
  max-height: calc(var(--app-vh) * 0.3);
}
/* No cover and no screenshot: a thin band in the title's color, with small
   initials, instead of an empty 16:7 slab. */
.discover-hero--band {
  height: 4rem;
}
/* Sections sit 1.75rem apart; a heading sits 0.5rem over its content. */
.discover-sections {
  display: flex;
  flex-direction: column;
  gap: 1.75rem;
  padding: 1.5rem;
}
/* Section headings: the Library dialog's label (sentence case, secondary
   color), one weight up because here they are the only structure. */
.discover-label {
  margin-bottom: 0.5rem;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
  line-height: 1rem;
}
/* About 80 characters a line: Inter's "0" is wider than its average letter. */
.discover-overview {
  max-width: 60ch;
  color: var(--text-secondary);
  font-size: 0.875rem;
  line-height: 1.625;
  white-space: pre-line;
  overflow-wrap: anywhere;
}
/* Seven lines. A height, not line-clamp: line-clamp puts its "…" on the
   empty line between two paragraphs. The last line fades out only when text
   is cut. */
.discover-overview--folded {
  max-height: calc(7 * 1.625em);
  overflow: hidden;
}
.discover-overview--cut {
  mask-image: linear-gradient(to bottom, #000 calc(100% - 3.25em), transparent);
}
/* A text button: no frame, so it does not compete with the header actions.
   The negative margin keeps the label on the text's edge; the padding is the
   hit area. No animation: the text opens at once. */
.discover-more {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  min-height: 2rem;
  margin: 0.25rem 0 0 -0.5rem;
  padding: 0.25rem 0.5rem;
  border-radius: 0.5rem;
  color: var(--text-primary);
  font-size: 0.8125rem;
  font-weight: 600;
}
.discover-more:hover {
  background: var(--bg-raised);
}
.discover-more:active {
  background: var(--bg-overlay);
}
/* The state and rating pills match the Library's state pills (GameRow.vue):
   outlined and quiet, so Download stays the only filled control. */
.discover-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border: 1px solid var(--border);
  border-radius: 0.3rem;
  padding: 0.15rem 0.45rem;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 0.75rem;
  font-weight: 700;
  line-height: 1.2;
}
.discover-library-pill svg {
  color: var(--text-secondary);
}
.discover-rating-note {
  color: var(--text-secondary);
  font-weight: 500;
}
.discover-rating-pill svg {
  color: var(--warning-text);
}
.discover-plus {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--brand-deep);
  border-radius: 0.3rem;
  padding: 0.05rem 0.35rem;
  background: var(--brand-glow);
  /* The accent alone on its own tint is under 4.5:1 in the light theme; a
     share of the text color keeps every accent above it in both themes. */
  color: color-mix(in oklab, var(--brand) 70%, var(--text-primary));
  font-size: 0.75rem;
  font-weight: 700;
  line-height: 1.2;
}
/* Label over value, two to three across, so seven facts take three rows. */
.discover-facts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem 1.5rem;
  font-size: 0.875rem;
}
@media (min-width: 641px) {
  .discover-facts {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}
.discover-fact {
  min-width: 0;
}
.discover-facts dt {
  color: var(--text-muted);
  font-size: 0.75rem;
}
.discover-facts dd {
  margin-top: 0.125rem;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}
/* The padding keeps a focused thumbnail's ring (2px plus a 2px offset) inside
   the scroll box, which clips on both axes; the margin puts the thumbnails
   back on the section's edge. */
.discover-strip {
  display: flex;
  gap: 0.5rem;
  overflow-x: auto;
  margin: -0.25rem;
  padding: 0.25rem 0.25rem 0.5rem;
}
/* A fixed 16:9 frame, so the strip has its final size before the images load. */
.discover-thumb {
  flex: 0 0 auto;
  width: calc(6rem * 16 / 9);
  height: 6rem;
  border-radius: 0.5rem;
  overflow: hidden;
  border: 1px solid var(--border);
  background: linear-gradient(135deg, var(--bg-raised), var(--bg-inset));
  padding: 0;
}
.discover-thumb:hover {
  border-color: var(--brand);
}
.discover-thumb img {
  width: 100%;
  height: 100%;
  display: block;
  object-fit: cover;
}
.discover-fade {
  opacity: 0;
  transition: opacity var(--motion-base) ease-out;
}
.discover-fade--in {
  opacity: 1;
}
@media (prefers-reduced-motion: reduce) {
  .discover-fade {
    transition: none;
  }
}
.discover-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
}
.discover-tag {
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.15rem 0.55rem;
  background: var(--bg-raised);
  color: var(--text-secondary);
  font-size: 0.75rem;
  line-height: 1.2;
}
/* DLsite's genres: the same chip without the fill, under their own label. */
.discover-tag--dlsite {
  background: transparent;
}
.discover-genres--after {
  margin-top: 0.875rem;
}
.discover-sublabel {
  margin-bottom: 0.375rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 500;
}
/* A plain list with rules between the rows, not a stack of cards. */
.discover-site {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  min-height: 3rem;
  padding: 0.375rem 0;
}
.discover-site + .discover-site {
  border-top: 1px solid var(--border);
}
/* The first row sits under its heading like any section's content. */
.discover-site:first-child {
  padding-top: 0;
}
.discover-site:last-child {
  padding-bottom: 0;
}
.discover-site-text {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.125rem 0.5rem;
  min-width: 0;
}
.discover-site-name {
  font-size: 0.875rem;
  font-weight: 600;
}
.discover-site-title {
  flex-basis: 100%;
  color: var(--text-secondary);
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}

/* Narrow windows: tap targets of at least 40px. */
@media (max-width: 767px) {
  .modal-close-btn {
    width: 2.5rem;
    height: 2.5rem;
  }
  .modal-toolbar-action,
  .discover-more {
    min-height: 2.5rem;
  }
}
@media (max-width: 640px) {
  .discover-sections {
    padding: 1rem;
  }
}
</style>
