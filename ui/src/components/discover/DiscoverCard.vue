<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// One Discover card: the cover (loaded only near the screen), title, developer,
// engine and version, the game's status, one source cluster ("F95Zone +3"; the
// preview lists every site) and the library marker. Emits the card on
// activation so the parent can bind one handler for every card.
import { computed, onUnmounted, ref, watch, type ComponentPublicInstance } from "vue";
import { IconBookmarkFilled, IconStarFilled } from "@tabler/icons-vue";
import CoverPlaceholder from "../CoverPlaceholder.vue";
import type { DiscoverCard } from "../../services/api";
import { discoverCoverUrl, discoverSiteNames, discoverSourceSummary, discoverStatusLabel } from "../../utils/discoverView";
import { DISCOVER_LIBRARY_LABELS } from "../../utils/discoverActions";

const props = defineProps<{ card: DiscoverCard; primary: boolean }>();
const emit = defineEmits<{ activate: [card: DiscoverCard, element: HTMLElement] }>();

// The initials show until the cover has loaded; then the cover fades in over
// them and they go once the fade is done. A cover that fails keeps them.
const failed = ref(false);
const loaded = ref(false);
const covered = ref(false);
let fadeTimer: ReturnType<typeof setTimeout> | undefined;
watch(() => props.card.cover_url, () => {
  clearTimeout(fadeTimer);
  failed.value = false;
  loaded.value = false;
  covered.value = false;
});
onUnmounted(() => clearTimeout(fadeTimer));
const cover = computed(() => (failed.value ? "" : discoverCoverUrl(props.card.cover_url)));
const onLoad = () => {
  loaded.value = true;
  clearTimeout(fadeTimer);
  fadeTimer = setTimeout(() => { covered.value = true; }, 250);
};
// A card that scrolls back into view finds its cover in the cache: show it at
// once, with no fade.
const checkCached = (element: Element | ComponentPublicInstance | null) => {
  if (loaded.value || !(element instanceof HTMLImageElement)) return;
  if (element.complete && element.naturalWidth > 0) {
    loaded.value = true;
    covered.value = true;
  }
};
const siteList = computed(() => {
  const names = discoverSiteNames(props.card.sites);
  return names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0] ?? "";
});
const sources = computed(() => discoverSourceSummary(props.card.sites));
const status = computed(() => discoverStatusLabel(props.card.status));
// The Library's words: "Installed" or "Wishlist" (both are library games).
const marker = computed(() => {
  const state = props.card.library?.state;
  return state ? (DISCOVER_LIBRARY_LABELS[state] ?? "") : "";
});
const markerSpoken = computed(() => (props.card.library?.state === "wishlisted" ? "On your wishlist" : marker.value));
// The star: the weighted F95 rating, or the plain one while the review count is unknown.
const rating = computed(() => {
  const { score, rating: thread, rating_count: count, wrating } = props.card;
  if (score === null) return { text: "", title: "" };
  const text = `F95 rating ${score.toFixed(1)}`;
  const how = wrating !== null && thread !== null
    ? `, adjusted for ${count ?? 0} ${count === 1 ? "review" : "reviews"} (the thread shows ${thread.toFixed(1)})`
    : ", review count not known yet";
  return { text, title: text + how };
});
const label = computed(() =>
  [
    props.card.title,
    props.card.developer && `by ${props.card.developer}`,
    status.value,
    props.card.engine,
    markerSpoken.value,
    rating.value.text,
    siteList.value && `on ${siteList.value}`,
  ]
    .filter(Boolean)
    .join(". "),
);
const activate = (event: Event) => emit("activate", props.card, event.currentTarget as HTMLElement);
</script>

<template>
  <button
    type="button"
    class="discover-card rounded-xl text-left flex flex-col"
    :data-discover-key="card.key"
    :data-controller-primary="primary ? '' : undefined"
    :aria-label="label"
    @click="activate"
  >
    <div class="discover-card-cover relative w-full aspect-[16/9] rounded-t-xl overflow-hidden">
      <CoverPlaceholder v-if="!covered || !cover" :title="card.title" class="absolute inset-0" />
      <img
        v-if="cover"
        :ref="checkCached"
        :src="cover"
        alt=""
        loading="lazy"
        decoding="async"
        referrerpolicy="no-referrer"
        class="discover-card-image absolute inset-0 w-full h-full object-cover object-top"
        :class="{ 'discover-card-image--loaded': loaded }"
        @load="onLoad"
        @error="failed = true"
      />
      <span v-if="marker" class="discover-marker absolute top-2 left-2" :data-state="card.library?.state">
        <IconBookmarkFilled v-if="card.library?.state === 'wishlisted'" class="w-3 h-3" aria-hidden="true" />
        {{ marker }}
      </span>
      <span
        v-if="card.score !== null"
        class="discover-rating absolute top-2 right-2"
        :title="rating.title"
      >
        <IconStarFilled class="w-3 h-3" aria-hidden="true" />
        {{ card.score.toFixed(1) }}
      </span>
    </div>
    <div class="p-3 flex flex-col min-w-0 flex-1">
      <div class="discover-card-text flex flex-col gap-1">
        <p class="text-sm font-semibold leading-snug line-clamp-2">{{ card.title }}</p>
        <p class="discover-card-meta text-xs">
          <span v-if="card.developer" class="discover-card-dev">{{ card.developer }}</span>
          <span v-if="card.engine" class="shrink-0">{{ card.engine }}</span>
          <span v-if="card.version" class="discover-card-version">{{ card.version }}</span>
        </p>
      </div>
      <div class="discover-card-foot mt-auto pt-1.5" aria-hidden="true">
        <span v-if="status" class="discover-status" :data-status="card.status">{{ status }}</span>
        <span v-if="sources.lead" class="discover-sources" :title="`On ${siteList}`">
          {{ sources.lead }}<span v-if="sources.more" class="discover-sources-more">+{{ sources.more }}</span>
        </span>
      </div>
    </div>
  </button>
</template>

<style scoped>
.discover-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  color: var(--text-primary);
  transition: transform 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
}
.discover-card:hover {
  border-color: var(--brand);
  box-shadow: 0 4px 20px var(--brand-glow);
  transform: translateY(-2px);
}
:global(.motion-off) .discover-card:hover {
  box-shadow: var(--shadow-card);
  transform: none;
}
.discover-card:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 3px;
}
.discover-card-cover {
  background: linear-gradient(135deg, var(--bg-raised), var(--bg-inset));
}
.discover-card-image {
  opacity: 0;
  transition: opacity var(--motion-base) ease-out;
}
.discover-card-image--loaded {
  opacity: 1;
}
@media (prefers-reduced-motion: reduce) {
  .discover-card-image {
    transition: none;
  }
}
.discover-marker,
.discover-rating {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border-radius: 0.3rem;
  padding: 0.125rem 0.375rem;
  font-size: 0.75rem;
  font-weight: 700;
  line-height: 1.2;
}
.discover-marker {
  background: var(--brand);
  color: var(--on-brand);
}
/* Badges on the cover get a near-solid dark backing in both themes. The
   theme scrim is too thin in the light theme: over a bright cover the yellow
   fell to about 3.5:1. At 88% the yellow keeps 9:1 and white 13:1 on any cover. */
.discover-marker[data-state="wishlisted"],
.discover-rating {
  background: rgb(15 15 20 / 0.88);
  border: 1px solid var(--overlay-border);
}
.discover-marker[data-state="wishlisted"] {
  color: var(--overlay-control-text);
}
.discover-rating {
  color: #facc15;
  font-variant-numeric: tabular-nums;
}
/* Room for two title lines and the meta line on every card, so the windowed
   grid measures one row height; a one-line title leaves the space below. */
.discover-card-text {
  min-height: calc(2 * 1.375 * 0.875rem + 0.25rem + 1rem);
}
.discover-card-meta {
  display: flex;
  min-width: 0;
  min-height: 1rem;
  overflow: hidden;
  white-space: nowrap;
  color: var(--text-secondary);
}
.discover-card-meta > * + *::before {
  content: "·";
  content: "·" / "";
  padding: 0 0.35rem;
  color: var(--text-muted);
}
.discover-card-dev,
.discover-card-version {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.discover-card-version {
  flex-shrink: 0;
  max-width: 35%;
}
.discover-card-foot {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 1.6rem;
}
/* The Library's thread-status colors (GameRow pills, GameCard facts). */
.discover-status {
  display: inline-flex;
  align-items: center;
  border: 1px solid var(--border);
  border-radius: 0.3rem;
  padding: 0.15rem 0.4rem;
  background: var(--bg-raised);
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 700;
  line-height: 1.2;
  white-space: nowrap;
}
.discover-status[data-status="completed"] {
  color: var(--success-text);
}
.discover-status[data-status="onhold"] {
  color: var(--warning-text);
}
.discover-status[data-status="abandoned"] {
  color: var(--danger-text);
}
.discover-sources {
  margin-left: auto;
  display: inline-flex;
  align-items: baseline;
  gap: 0.2rem;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 600;
  white-space: nowrap;
}
.discover-sources-more {
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
</style>
