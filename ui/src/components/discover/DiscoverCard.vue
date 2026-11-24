<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// One Discover card: the cover (loaded only near the screen), title, developer,
// version, one badge per site and the library marker. Emits the card on
// activation so the parent can bind one handler for every card.
import { computed, ref, watch } from "vue";
import { IconHeartFilled, IconStarFilled } from "@tabler/icons-vue";
import CoverPlaceholder from "../CoverPlaceholder.vue";
import type { DiscoverCard } from "../../services/api";
import { DISCOVER_SITE_SHORT, DISCOVER_SITES, discoverCoverUrl } from "../../utils/discoverView";

const props = defineProps<{ card: DiscoverCard; primary: boolean }>();
const emit = defineEmits<{ activate: [card: DiscoverCard, element: HTMLElement] }>();

const failed = ref(false);
watch(() => props.card.cover_url, () => { failed.value = false; });
const cover = computed(() => (failed.value ? "" : discoverCoverUrl(props.card.cover_url)));
const siteNames = computed(() => props.card.sites.map((site) => DISCOVER_SITES.find((item) => item.key === site)?.label ?? site));
const marker = computed(() => {
  const state = props.card.library?.state;
  return state === "installed" ? "In library" : state === "wishlisted" ? "Wishlisted" : "";
});
const label = computed(() =>
  [props.card.title, props.card.developer && `by ${props.card.developer}`, marker.value, `on ${siteNames.value.join(", ")}`]
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
      <img
        v-if="cover"
        :src="cover"
        alt=""
        loading="lazy"
        decoding="async"
        referrerpolicy="no-referrer"
        class="absolute inset-0 w-full h-full object-cover object-top"
        @error="failed = true"
      />
      <CoverPlaceholder v-else :title="card.title" class="absolute inset-0" />
      <span v-if="marker" class="discover-marker absolute top-2 left-2" :data-state="card.library?.state">
        <IconHeartFilled v-if="card.library?.state === 'wishlisted'" class="w-3 h-3" aria-hidden="true" />
        {{ marker }}
      </span>
      <span v-if="card.wrating !== null" class="discover-rating absolute top-2 right-2" :title="`Weighted F95 rating from ${card.rating_count ?? 0} reviews`">
        <IconStarFilled class="w-3 h-3" aria-hidden="true" />
        {{ card.wrating.toFixed(1) }}
      </span>
    </div>
    <div class="p-3 flex flex-col gap-1 min-w-0 flex-1">
      <p class="discover-card-title text-sm font-semibold leading-snug line-clamp-2">{{ card.title }}</p>
      <p v-if="card.developer || card.version" class="text-xs truncate" style="color: var(--text-secondary)">
        <span v-if="card.developer">{{ card.developer }}</span>
        <span v-if="card.developer && card.version"> · </span>
        <span v-if="card.version">{{ card.version }}</span>
      </p>
      <div class="mt-auto pt-1 flex flex-wrap gap-1" aria-hidden="true">
        <span v-for="site in card.sites" :key="site" class="discover-site" :data-site="site">{{ DISCOVER_SITE_SHORT[site] }}</span>
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
.discover-marker,
.discover-rating {
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  font-size: 0.65rem;
  font-weight: 700;
  line-height: 1.2;
}
.discover-marker {
  background: var(--brand);
  color: var(--on-brand);
}
.discover-marker[data-state="wishlisted"] {
  background: var(--overlay-scrim-strong);
  border: 1px solid var(--overlay-border);
  color: var(--overlay-control-text);
}
/* The rating sits on the dark scrim in both themes, so it keeps the bright yellow. */
.discover-rating {
  background: var(--overlay-scrim-strong);
  border: 1px solid var(--overlay-border);
  color: #facc15;
}
.discover-site {
  border: 1px solid var(--border);
  border-radius: 0.3rem;
  padding: 0.05rem 0.35rem;
  background: var(--bg-raised);
  color: var(--text-secondary);
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.02em;
}
</style>
