<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// Discover: browse, search and filter the installed game catalog. Results come
// from the device-local Discover index in pages; only the cards near the
// visible area are in the page (utils/libraryWindow.ts), as in the Library.
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, useTemplateRef, watch } from "vue";
import { useRouter } from "vue-router";
import { IconCompass, IconFilterFilled, IconLoader2, IconX, IconZoom } from "@tabler/icons-vue";
import FilterPill from "../components/FilterPill.vue";
import DiscoverCardView from "../components/discover/DiscoverCard.vue";
import DiscoverGameModal from "../components/modals/DiscoverGameModal.vue";
import { api, type DiscoverCard, type DiscoverFiltersResponse, type DiscoverStatusResponse } from "../services/api";
import { controllerFocusText, useControllerActions } from "../utils/controllerActions";
import { toCssPx } from "../utils/interfaceScalePreference";
import {
  FOCUS_HOLD_MS,
  gridColumnCount,
  measuredPitch,
  nextMetrics,
  renderWindow,
  sameWindow,
  type RenderWindow,
  type WindowMetrics,
  type WindowView,
} from "../utils/libraryWindow";
import {
  DISCOVER_SITES,
  DISCOVER_SORTS,
  DISCOVER_STATUSES,
  activeDiscoverFilterCount,
  clearDiscoverFilters,
  cycleSite,
  cycleTag,
  discoverCountLabel,
  discoverQuery,
  discoverQueryKey,
  discoverSession,
  readDiscoverView,
  siteState,
  tagState,
  toggleEngine,
  toggleStatus,
  writeDiscoverView,
  type DiscoverViewState,
} from "../utils/discoverView";

const router = useRouter();

const view = ref<DiscoverViewState>(readDiscoverView());
watch(view, (state) => writeDiscoverView(state), { deep: true });
const search = ref(discoverSession.search);
watch(search, (text) => { discoverSession.search = text; });

const status = ref<DiscoverStatusResponse | null>(null);
const filterOptions = ref<DiscoverFiltersResponse | null>(null);
const games = shallowRef<DiscoverCard[]>([]);
const total = ref(0);
const nextOffset = ref<number | null>(null);
const loading = ref(false);
const loadError = ref("");
const tagSearch = ref("");

const ready = computed(() => status.value?.ready === true);
const catalogOff = computed(() => status.value !== null && status.value.enabled === false);
const notInstalled = computed(() => status.value !== null && status.value.enabled !== false && !status.value.installed);
const building = computed(() => status.value !== null && !!status.value.installed && !status.value.ready);
const filterCount = computed(() => activeDiscoverFilterCount(view.value));
const query = computed(() => discoverQuery(view.value, search.value));
const queryKey = computed(() => discoverQueryKey(query.value));

const tagOptions = computed(() => {
  const text = tagSearch.value.trim().toLowerCase();
  const tags = filterOptions.value?.tags ?? [];
  const chosen = new Set([...view.value.includeTags, ...view.value.excludeTags]);
  const matching = tags.filter((tag) => !text || tag.name.toLowerCase().includes(text));
  // Chosen tags first, then by game count; a long list shows the first 80.
  return matching
    .slice()
    .sort((a, b) => Number(chosen.has(b.name)) - Number(chosen.has(a.name)) || b.count - a.count || a.name.localeCompare(b.name))
    .slice(0, 80);
});

// --- Loading results ---------------------------------------------------------
let request = 0;
async function loadPage(offset: number): Promise<void> {
  const ticket = ++request;
  loading.value = true;
  loadError.value = "";
  const key = queryKey.value;
  const result = await api.searchDiscover(discoverQuery(view.value, search.value, offset));
  if (ticket !== request) return;
  loading.value = false;
  if (!result.success) {
    loadError.value = result.error || "Could not load the catalog.";
    if (result.error_code === "not_ready" || result.error_code === "catalog_off") void refreshStatus();
    return;
  }
  const page = result.games ?? [];
  games.value = offset === 0 ? page : [...games.value.slice(0, offset), ...page];
  total.value = result.total ?? games.value.length;
  nextOffset.value = result.next_offset ?? null;
  Object.assign(discoverSession, { queryKey: key, games: games.value, total: total.value, nextOffset: nextOffset.value });
}

function loadFirstPage(): void {
  discoverSession.scrollTop = 0;
  scroller.value?.scrollTo({ top: 0 });
  void loadPage(0);
}

/** Load the same range again after the library changed, so markers stay right. */
async function refreshLoaded(): Promise<void> {
  if (!ready.value || loading.value) return;
  const count = Math.max(games.value.length, 1);
  const ticket = ++request;
  const fresh: DiscoverCard[] = [];
  let totalCount = total.value;
  let next: number | null = null;
  for (let offset = 0; offset < count; offset += 120) {
    const result = await api.searchDiscover(discoverQuery(view.value, search.value, offset));
    if (ticket !== request || !result.success) return;
    fresh.push(...(result.games ?? []));
    totalCount = result.total ?? fresh.length;
    next = result.next_offset ?? null;
    if (next === null) break;
  }
  games.value = fresh;
  total.value = totalCount;
  nextOffset.value = next;
  Object.assign(discoverSession, { queryKey: queryKey.value, games: fresh, total: totalCount, nextOffset: next });
}

let searchTimer: ReturnType<typeof setTimeout> | undefined;
// Set while the latest change is typed text: typing waits a moment, a filter
// click loads at once.
let typed = false;
watch(search, () => { typed = true; }, { flush: "sync" });
watch(queryKey, () => {
  if (!ready.value) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadFirstPage, typed ? 250 : 0);
  typed = false;
});

let statusTimer: ReturnType<typeof setTimeout> | undefined;
async function refreshStatus(): Promise<void> {
  clearTimeout(statusTimer);
  const result = await api.getDiscoverStatus();
  const wasReady = ready.value;
  status.value = result;
  if (result.ready) {
    if (!wasReady) await onReady();
  } else if (result.installed) {
    // The index builds on the catalog thread; look again soon.
    statusTimer = setTimeout(() => void refreshStatus(), 2000);
  }
}

async function onReady(): Promise<void> {
  void api.getDiscoverFilters().then((result) => { if (result.success) filterOptions.value = result; });
  if (discoverSession.queryKey === queryKey.value && discoverSession.games.length) {
    games.value = discoverSession.games;
    total.value = discoverSession.total;
    nextOffset.value = discoverSession.nextOffset;
    await nextTick();
    measureWindow();
    scroller.value?.scrollTo({ top: discoverSession.scrollTop });
    void refreshLoaded();
  } else {
    void loadPage(0);
  }
}

function clearAll(): void {
  search.value = "";
  view.value = clearDiscoverFilters(view.value);
}

// --- The card window -----------------------------------------------------------
const scroller = useTemplateRef<HTMLElement>("scroller");
const windowContainer = useTemplateRef<HTMLElement>("windowContainer");
let windowMetrics: WindowMetrics | null = null;
let pitchStale = true;
let measuredWidth = -1;
let windowView: WindowView = { top: 0, height: 0 };
const shownWindow = shallowRef<RenderWindow>(renderWindow(0, null, { top: 0, height: 0 }));
let focusedKey: string | null = null;
let focusMovedAt = -Infinity;
let focusHoldTimer: ReturnType<typeof setTimeout> | undefined;

const updateWindow = () => {
  const focus = focusedKey === null ? -1 : games.value.findIndex((game) => game.key === focusedKey);
  const moving = performance.now() - focusMovedAt < FOCUS_HOLD_MS;
  const next = renderWindow(games.value.length, windowMetrics, windowView, focus < 0 ? null : focus, moving);
  if (!sameWindow(next, shownWindow.value)) shownWindow.value = next;
  // Load the next page before the last loaded row comes into view.
  const columns = windowMetrics?.columns ?? 1;
  if (nextOffset.value !== null && !loading.value && next.end >= games.value.length - columns * 2) void loadPage(nextOffset.value);
};
function measureWindow(): void {
  const element = scroller.value;
  const container = windowContainer.value;
  if (!element || !container || !container.isConnected) {
    updateWindow();
    return;
  }
  if (container.clientWidth !== measuredWidth) {
    measuredWidth = container.clientWidth;
    pitchStale = true;
  }
  const style = getComputedStyle(container);
  const columns = gridColumnCount(style.gridTemplateColumns);
  const items = container.querySelectorAll<HTMLElement>(":scope > [data-discover-key]");
  const firstItem = items[0];
  if (firstItem) {
    const first = firstItem.getBoundingClientRect();
    const rows = Math.ceil(items.length / columns);
    const lastRowTop = items[(rows - 1) * columns]?.getBoundingClientRect().top ?? first.top;
    const pitch = measuredPitch(toCssPx(first.top), toCssPx(lastRowTop), rows, toCssPx(first.height), Number.parseFloat(style.rowGap) || 0);
    if (pitch > 0) {
      windowMetrics = nextMetrics(windowMetrics, { columns, pitch }, pitchStale);
      pitchStale = false;
    }
  }
  const origin = toCssPx(container.getBoundingClientRect().top - element.getBoundingClientRect().top) - element.clientTop + element.scrollTop;
  windowView = { top: element.scrollTop - origin, height: element.clientHeight };
  updateWindow();
}
const renderedGames = computed(() => {
  const { start, end } = shownWindow.value;
  return start === 0 && end >= games.value.length ? games.value : games.value.slice(start, end);
});
watch(games, () => { pitchStale = true; measureWindow(); }, { flush: "post" });
watch(shownWindow, measureWindow, { flush: "post" });
let resize: ResizeObserver | null = null;
watch([scroller, windowContainer], (elements) => {
  resize?.disconnect();
  if (typeof ResizeObserver === "undefined") return;
  resize ??= new ResizeObserver(() => measureWindow());
  for (const element of elements) if (element) resize.observe(element);
}, { flush: "post" });
const onScroll = () => {
  if (scroller.value) discoverSession.scrollTop = scroller.value.scrollTop;
  measureWindow();
};
const trackFocus = (event: FocusEvent) => {
  const target = event.target instanceof Element ? event.target.closest<HTMLElement>("[data-discover-key]") : null;
  const key = target?.dataset.discoverKey ?? null;
  if (key === focusedKey) return;
  focusedKey = key;
  if (key === null) return;
  focusMovedAt = performance.now();
  clearTimeout(focusHoldTimer);
  focusHoldTimer = setTimeout(updateWindow, FOCUS_HOLD_MS + 50);
  measureWindow();
};
const gridStyle = computed(() => ({
  paddingTop: `${shownWindow.value.padTop}px`,
  paddingBottom: `calc(3rem + ${shownWindow.value.padBottom}px)`,
}));

// --- Controller ----------------------------------------------------------------
const scope = useTemplateRef<HTMLElement>("scope");
const searchInput = useTemplateRef<HTMLInputElement>("searchInput");
useControllerActions(scope, {
  search: () => { if (searchInput.value) { controllerFocusText(searchInput.value); searchInput.value.select(); } },
  hints: () => [{ button: "A", label: "Preview" }, { button: "B", label: "Back" }, { button: "Y", label: "Search" }, { button: "LT/RT", label: "Page" }],
});

const onLibraryChanged = () => { void refreshLoaded(); };
onMounted(() => {
  window.addEventListener("wlib-refresh-library", onLibraryChanged);
  void refreshStatus();
});
onUnmounted(() => {
  window.removeEventListener("wlib-refresh-library", onLibraryChanged);
  resize?.disconnect();
  clearTimeout(searchTimer);
  clearTimeout(statusTimer);
  clearTimeout(focusHoldTimer);
  request++;
});

// The preview dialog for the chosen card; focus returns to `previewOrigin`.
const previewKey = ref<string | null>(null);
let previewOrigin: HTMLElement | null = null;
const openPreview = (card: DiscoverCard, element: HTMLElement) => {
  previewOrigin = element;
  previewKey.value = card.key;
};
const closePreview = () => {
  previewKey.value = null;
  const key = previewOrigin?.dataset.discoverKey;
  const target = previewOrigin?.isConnected ? previewOrigin : key ? scroller.value?.querySelector<HTMLElement>(`[data-discover-key="${CSS.escape(key)}"]`) : null;
  target?.focus({ preventScroll: true });
  previewOrigin = null;
};
</script>

<template>
  <div ref="scope" class="h-full flex overflow-hidden">
    <aside
      v-if="ready"
      :inert="!view.filtersOpen"
      :aria-hidden="!view.filtersOpen"
      :class="['discover-filters shrink-0 h-full collapse-width-transition', view.filtersOpen ? 'w-64' : 'w-0 discover-filters-collapsed']"
      aria-label="Discover filters"
    >
      <div class="w-64 h-full flex flex-col overflow-y-auto p-6 pt-4 gap-6">
        <p class="discover-legend text-[0.6875rem] leading-snug">Pills: click once to show, twice to hide, three times to clear.</p>
        <section>
          <h3 class="discover-filter-heading">Sources</h3>
          <div class="flex flex-wrap gap-1.5 mt-3">
            <FilterPill
              v-for="site in DISCOVER_SITES"
              :key="site.key"
              :label="site.label"
              :state="siteState(view, site.key)"
              @activate="view = cycleSite(view, site.key)"
            />
          </div>
        </section>
        <section>
          <h3 class="discover-filter-heading">Status</h3>
          <div class="space-y-1 mt-3">
            <label v-for="option in DISCOVER_STATUSES" :key="option.key" class="discover-check flex items-center gap-3 px-3 py-1.5 rounded-lg text-sm cursor-pointer ui-hover-surface">
              <input type="checkbox" class="rounded" :checked="view.statuses.includes(option.key)" @change="view = toggleStatus(view, option.key)" />
              <span>{{ option.label }}</span>
              <span class="ml-auto text-xs" style="color: var(--text-muted)">{{ filterOptions?.statuses?.find((item) => item.key === option.key)?.count ?? "" }}</span>
            </label>
          </div>
        </section>
        <section>
          <label class="discover-filter-heading block">
            F95 rating
            <select v-model="view.minRating" aria-label="Minimum weighted F95 rating" class="discover-input mt-3 w-full rounded-lg px-3 py-2 text-sm normal-case tracking-normal font-medium">
              <option value="">Any</option>
              <option value="3.5">3.5+ weighted</option>
              <option value="4.0">4.0+ weighted</option>
              <option value="4.5">4.5+ weighted</option>
            </select>
          </label>
        </section>
        <section v-if="filterOptions?.engines?.length">
          <h3 class="discover-filter-heading">Engine</h3>
          <div class="flex flex-wrap gap-1.5 mt-3">
            <FilterPill
              v-for="engine in filterOptions.engines"
              :key="engine.name"
              :label="engine.name"
              :count="engine.count"
              :state="view.includeEngines.includes(engine.name) ? 'include' : 'neutral'"
              @activate="view = toggleEngine(view, engine.name)"
            />
          </div>
        </section>
        <section v-if="filterOptions?.tags?.length">
          <h3 class="discover-filter-heading">Tags</h3>
          <p class="text-xs mt-2" style="color: var(--text-muted)">F95Zone and LewdCorner tags. Every shown tag is required.</p>
          <input v-model="tagSearch" type="text" aria-label="Search tags" placeholder="Search tags..." class="discover-input mt-3 w-full rounded-lg px-3 py-2 text-xs" />
          <div class="flex flex-wrap gap-1.5 mt-3">
            <FilterPill
              v-for="tag in tagOptions"
              :key="tag.key"
              :label="tag.name"
              :count="tag.count"
              :state="tagState(view, tag.name)"
              @activate="view = cycleTag(view, tag.name)"
            />
            <p v-if="!tagOptions.length" class="text-xs italic" style="color: var(--text-muted)">No matching tags</p>
          </div>
        </section>
      </div>
    </aside>

    <div ref="scroller" class="flex-1 min-w-0 p-5 overflow-y-auto flex flex-col relative" @scroll.passive="onScroll" @focusin="trackFocus">
      <header class="discover-header mb-4 shrink-0">
        <div class="discover-header-row">
          <h2 class="ui-page-heading text-3xl font-bold tracking-tight shrink-0" style="color: var(--text-primary)">
            <IconCompass class="ui-page-heading-icon" />
            <span>Discover</span>
          </h2>
          <p class="discover-subtitle text-sm pl-3" aria-hidden="true">Browse the game catalog and find new games.</p>
          <p class="sr-only">Browse the game catalog and find new games.</p>
        </div>
      </header>

      <div v-if="catalogOff || notInstalled" class="discover-state rounded-xl p-6 max-w-xl" role="status">
        <p class="font-semibold">{{ catalogOff ? "The game catalog is turned off." : "The game catalog is not downloaded yet." }}</p>
        <p class="text-sm mt-2" style="color: var(--text-secondary)">
          Discover shows the games of the wLib catalog. {{ catalogOff ? "Turn the catalog on to use it." : "wLib downloads it in the background; you can check now in Settings." }}
        </p>
        <button type="button" class="ui-setup-btn mt-4 px-4 py-2 rounded-lg text-sm font-semibold" data-controller-primary @click="router.push('/settings#catalog')">Open catalog settings</button>
      </div>

      <div v-else-if="status === null || building" class="discover-state rounded-xl p-6 max-w-xl flex items-center gap-3" role="status">
        <IconLoader2 class="w-5 h-5 animate-spin shrink-0" aria-hidden="true" />
        <span>{{ status === null ? "Loading Discover…" : "Preparing the catalog for Discover. This takes a few seconds…" }}</span>
      </div>

      <template v-else>
        <div class="discover-toolbar mb-4 shrink-0">
          <div class="discover-toolbar-search relative">
            <IconZoom class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style="color: var(--text-muted)" aria-hidden="true" />
            <input
              ref="searchInput"
              v-model="search"
              type="text"
              aria-label="Search the catalog"
              placeholder="Search titles, original titles or developers..."
              class="discover-input w-full rounded-lg pl-10 pr-10 py-2 text-sm focus:outline-none"
            />
            <button v-if="search" type="button" aria-label="Clear search" class="ui-icon-btn absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded" @click="search = ''; searchInput?.focus()">
              <IconX class="w-4 h-4" />
            </button>
          </div>
          <div class="discover-toolbar-controls">
            <label class="flex items-center gap-2 text-sm">
              <span style="color: var(--text-secondary)">Sort</span>
              <select v-model="view.sort" aria-label="Sort by" class="discover-input rounded-lg px-3 py-2 text-sm">
                <option v-for="sort in DISCOVER_SORTS" :key="sort.key" :value="sort.key">{{ sort.label }}</option>
              </select>
            </label>
            <button type="button" class="discover-toolbar-btn ui-action-btn ui-hover-surface" :aria-pressed="view.hideLibrary" @click="view.hideLibrary = !view.hideLibrary">
              Hide my games
            </button>
            <button
              type="button"
              class="discover-toolbar-btn ui-action-btn relative ui-hover-surface"
              :aria-expanded="view.filtersOpen"
              :title="view.filtersOpen ? 'Hide Filters Pane' : 'Show Filters Pane'"
              @click="view.filtersOpen = !view.filtersOpen"
            >
              <IconFilterFilled class="ui-action-icon" />
              <span>Filters</span>
              <span v-if="filterCount > 0" class="discover-filter-count absolute -top-1.5 -right-1.5 min-w-5 h-5 rounded-full px-1 text-[0.625rem] font-bold flex items-center justify-center">{{ filterCount }}</span>
            </button>
          </div>
        </div>

        <p class="discover-count text-sm mb-3 shrink-0" role="status" aria-live="polite">
          <template v-if="loadError">{{ loadError }}</template>
          <template v-else-if="loading && !games.length">Searching…</template>
          <template v-else>{{ discoverCountLabel(total) }}</template>
        </p>

        <div v-if="!loading && !loadError && total === 0" class="discover-state rounded-xl p-6 max-w-xl">
          <p class="font-semibold">No games match your search and filters.</p>
          <button type="button" class="ui-setup-btn mt-4 px-4 py-2 rounded-lg text-sm font-semibold" data-controller-primary @click="clearAll">Clear filters</button>
        </div>

        <div v-else ref="windowContainer" class="discover-cards grid gap-4" :style="gridStyle">
          <DiscoverCardView
            v-for="(card, index) in renderedGames"
            :key="card.key"
            :card="card"
            :primary="shownWindow.start + index === shownWindow.primary"
            @activate="openPreview"
          />
        </div>
      </template>
    </div>
    <DiscoverGameModal :game-key="previewKey" @close="closePreview" @changed="refreshLoaded" />
  </div>
</template>

<style scoped>
.discover-filters {
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
}
.discover-filters-collapsed {
  border-right: 0 solid transparent;
}
.discover-legend {
  color: var(--text-muted);
}
.discover-filter-heading {
  padding-inline: 0.5rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}
.discover-check {
  color: var(--text-secondary);
}
.discover-input {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.discover-input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}
.discover-header {
  container-type: inline-size;
}
.discover-header-row {
  display: flex;
  align-items: center;
  gap: 1rem;
}
.discover-subtitle {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--text-secondary);
  border-left: 2px solid var(--brand);
}
@container (max-width: 36rem) {
  .discover-subtitle {
    display: none;
  }
}
.discover-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}
.discover-toolbar-search {
  flex: 1 1 12rem;
  min-width: 12rem;
}
.discover-toolbar-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  margin-left: auto;
}
.discover-toolbar-btn {
  min-height: 2.375rem;
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  color: var(--text-primary);
  font-size: 0.875rem;
  font-weight: 500;
}
.discover-toolbar-btn[aria-pressed="true"] {
  background: var(--brand-glow);
  border-color: var(--brand-deep);
  color: var(--brand);
}
.discover-filter-count {
  background: var(--brand);
  color: var(--on-brand);
}
.discover-count {
  color: var(--text-secondary);
}
.discover-state {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  color: var(--text-primary);
}
.discover-cards {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr));
}
</style>
