<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
// Discover: browse, search and filter the installed game catalog. Results come
// from the device-local Discover index in pages; only the cards near the
// visible area are in the page (utils/libraryWindow.ts), as in the Library.
import { computed, nextTick, onMounted, onUnmounted, ref, shallowRef, useTemplateRef, watch } from "vue";
import { useRouter } from "vue-router";
import { IconChevronDown, IconCompass, IconFilterFilled, IconLoader2, IconRefresh, IconX, IconZoom } from "@tabler/icons-vue";
import FilterPill from "../components/FilterPill.vue";
import DiscoverCardView from "../components/discover/DiscoverCard.vue";
import DiscoverGameModal from "../components/modals/DiscoverGameModal.vue";
import { useFiltersPane } from "../composables/useFiltersPane";
import { api, type DiscoverCard, type DiscoverFiltersResponse, type DiscoverStatusResponse } from "../services/api";
import { controllerFocusText, useControllerActions } from "../utils/controllerActions";
import { toCssPx } from "../utils/interfaceScalePreference";
import { useWindowCssWidth } from "../utils/windowWidth";
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
  HIDE_LIBRARY_LABEL,
  activeDiscoverFilterCount,
  clearDiscoverFilters,
  describeDiscoverNoResults,
  discoverCountLabel,
  discoverFilterChips,
  discoverQuery,
  discoverQueryKey,
  discoverResultsView,
  discoverSectionCounts,
  discoverSession,
  discoverTagList,
  excludeSite,
  excludeTag,
  includeSite,
  includeTag,
  readDiscoverView,
  removeDiscoverFilter,
  siteState,
  tagState,
  toggleDiscoverSection,
  toggleEngine,
  toggleStatus,
  writeDiscoverView,
  type DiscoverFilterChip,
  type DiscoverFilterSection,
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
/** The running request loads the first page of a new query. */
const loadingFirstPage = ref(false);
const loadError = ref("");
/** The offset of the page that failed, so Try again loads that page. */
const loadErrorOffset = ref(0);
const retrying = ref(false);
const tagSearch = ref("");

const ready = computed(() => status.value?.ready === true);
const catalogOff = computed(() => status.value !== null && status.value.enabled === false);
const notInstalled = computed(() => status.value !== null && status.value.enabled !== false && !status.value.installed);
const building = computed(() => status.value !== null && !!status.value.installed && !status.value.ready);
const filterCount = computed(() => activeDiscoverFilterCount(view.value));
const filterChips = computed(() => discoverFilterChips(view.value));
const noResults = computed(() => describeDiscoverNoResults(search.value, filterChips.value));
// The sites the catalog covers, and the date of the installed catalog.
const subtitle = computed(() => {
  const names = DISCOVER_SITES.map((site) => site.label);
  const sites = `Games from ${names.slice(0, -1).join(", ")} and ${names.at(-1)}.`;
  const built = status.value?.built_at ? new Date(status.value.built_at) : null;
  if (!built || Number.isNaN(built.getTime())) return sites;
  return `${sites} Catalog from ${built.toLocaleDateString(undefined, { dateStyle: "medium" })}.`;
});
// The full search hint needs about 23rem; a phone-width window gets the short one.
const windowWidth = useWindowCssWidth();
const searchPlaceholder = computed(() =>
  windowWidth.value < 480 ? "Search title or developer…" : "Search by title, original title or developer…",
);
const filtersButtonLabel = computed(() => (filterCount.value > 0 ? `Filters, ${filterCount.value} active` : "Filters"));
const query = computed(() => discoverQuery(view.value, search.value));
const queryKey = computed(() => discoverQueryKey(query.value));

// The top tags, every chosen tag, and the rest after "Show all". The full
// list is a few hundred pills at most, so it has no cap.
const showAllTags = ref(false);
const tagList = computed(() => discoverTagList(filterOptions.value?.tags ?? [], view.value, tagSearch.value, showAllTags.value));
const sectionCounts = computed(() => discoverSectionCounts(view.value));
const toggleSection = (section: DiscoverFilterSection) => { view.value = toggleDiscoverSection(view.value, section); };
/** "No filters on", "1 filter on", "3 filters on". */
const filtersOnLabel = (count: number) => (count === 0 ? "No filters on" : count === 1 ? "1 filter on" : `${count} filters on`);

// The filter pane: beside the cards in wide windows (its open state is saved),
// a drawer over them in narrow ones, as in the Library.
const filtersPane = useTemplateRef<HTMLElement>("filtersPane");
const inlineFiltersOpen = computed({
  get: () => view.value.filtersOpen,
  set: (open: boolean) => { view.value.filtersOpen = open; },
});
const { asDrawer: filtersAsDrawer, drawerOpen: filtersDrawerOpen, shown: filtersShown, closeDrawer: closeFiltersDrawer, toggle: toggleFilters } =
  useFiltersPane(filtersPane, inlineFiltersOpen);

// --- Loading results ---------------------------------------------------------
let request = 0;
/**
 * Load one page. True when this page loaded. `keepError` leaves the error card
 * on screen while Try again runs, so its button keeps focus.
 */
async function loadPage(offset: number, keepError = false): Promise<boolean> {
  const ticket = ++request;
  loading.value = true;
  loadingFirstPage.value = offset === 0;
  if (!keepError) loadError.value = "";
  const key = queryKey.value;
  const result = await api.searchDiscover(discoverQuery(view.value, search.value, offset));
  if (ticket !== request) return false;
  loading.value = false;
  loadingFirstPage.value = false;
  if (!result.success) {
    // The "not ready" and "turned off" messages are plain text; any other
    // error is a database message that means nothing to the user.
    const known = result.error_code === "not_ready" || result.error_code === "catalog_off";
    if (!known && result.error) console.warn("Discover search failed:", result.error);
    loadError.value = known && result.error ? result.error : "Could not search the catalog";
    loadErrorOffset.value = offset;
    if (known) void refreshStatus();
    return false;
  }
  loadError.value = "";
  const page = result.games ?? [];
  games.value = offset === 0 ? page : [...games.value.slice(0, offset), ...page];
  total.value = result.total ?? games.value.length;
  nextOffset.value = result.next_offset ?? null;
  Object.assign(discoverSession, { queryKey: key, games: games.value, total: total.value, nextOffset: nextOffset.value });
  return true;
}

const results = computed(() =>
  discoverResultsView({
    loading: loading.value,
    firstPage: loadingFirstPage.value,
    error: loadError.value,
    errorOffset: loadErrorOffset.value,
    total: total.value,
    shown: games.value.length,
  }),
);

// The old cards dim only when the new page takes a moment, so a fast answer
// does not flash. A timer, not a CSS delay: the motion setting turns off
// transitions, and the delay must stay.
const refreshCue = ref(false);
let refreshCueTimer: ReturnType<typeof setTimeout> | undefined;
watch(() => results.value.refreshing, (refreshing) => {
  clearTimeout(refreshCueTimer);
  if (refreshing) refreshCueTimer = setTimeout(() => { refreshCue.value = true; }, 150);
  else refreshCue.value = false;
});

/** Try the failed page again. On success, focus goes to its first card. */
async function retryLoad(): Promise<void> {
  if (retrying.value) return;
  retrying.value = true;
  const offset = loadErrorOffset.value;
  if (offset === 0) {
    discoverSession.scrollTop = 0;
    scroller.value?.scrollTo({ top: 0 });
  }
  const loaded = await loadPage(offset, true);
  retrying.value = false;
  if (!loaded) return;
  await nextTick();
  const key = games.value[offset]?.key;
  if (key) scroller.value?.querySelector<HTMLElement>(`[data-discover-key="${CSS.escape(key)}"]`)?.focus();
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

function clearSearch(): void {
  search.value = "";
  void nextTick(() => searchInput.value?.focus());
}

const filtersButton = useTemplateRef<HTMLButtonElement>("filtersButton");
const chipRow = useTemplateRef<HTMLElement>("chipRow");

/**
 * Clear every filter. The chip row and the no-results actions go away with
 * the filters, so their buttons move focus to the Filters button; the pane's
 * Clear all stays and keeps focus.
 */
function clearFilters(moveFocus = true): void {
  if (!filterCount.value) return;
  view.value = clearDiscoverFilters(view.value);
  if (moveFocus) void nextTick(() => filtersButton.value?.focus());
}

/** Remove one chip's filter and put focus on the chip that takes its place. */
function removeChip(chip: DiscoverFilterChip, index: number): void {
  view.value = removeDiscoverFilter(view.value, chip);
  void nextTick(() => {
    const chips = chipRow.value?.querySelectorAll<HTMLElement>("[data-filter-chip]");
    const next = chips?.[Math.min(index, chips.length - 1)];
    (next ?? filtersButton.value)?.focus();
  });
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
  // After an error, only Try again loads more, so scrolling does not repeat a failing request.
  if (nextOffset.value !== null && !loading.value && !loadError.value && next.end >= games.value.length - columns * 2) {
    void loadPage(nextOffset.value);
  }
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
  // The next-page error card brings its own bottom space.
  paddingBottom: `calc(${results.value.moreError ? "1rem" : "3rem"} + ${shownWindow.value.padBottom}px)`,
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
  clearTimeout(refreshCueTimer);
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
    <!-- Filters: a pane beside the cards in wide windows, a drawer over them in narrow ones. -->
    <div v-if="ready && filtersAsDrawer && filtersDrawerOpen" class="filters-backdrop" aria-hidden="true" @click="closeFiltersDrawer"></div>
    <aside
      v-if="ready"
      ref="filtersPane"
      :inert="!filtersShown"
      :aria-hidden="!filtersShown"
      :role="filtersAsDrawer ? 'dialog' : undefined"
      :aria-modal="filtersAsDrawer ? 'true' : undefined"
      :aria-labelledby="filtersAsDrawer ? 'discover-filters-title' : undefined"
      :aria-label="filtersAsDrawer ? undefined : 'Discover filters'"
      :tabindex="filtersAsDrawer ? -1 : undefined"
      :class="
        filtersAsDrawer
          ? ['discover-filters filters-drawer', { 'filters-drawer--open': filtersDrawerOpen }]
          : ['discover-filters shrink-0 h-full collapse-width-transition', view.filtersOpen ? 'w-64' : 'w-0 discover-filters-collapsed']
      "
    >
      <div class="h-full overflow-y-auto overflow-x-hidden" :class="filtersAsDrawer ? 'w-full' : 'w-64'">
        <div v-if="filtersAsDrawer" class="filters-drawer-head">
          <h2 id="discover-filters-title" class="filters-drawer-title">Filters</h2>
          <button type="button" class="ui-icon-btn filters-drawer-close" aria-label="Close filters" title="Close filters" @click="closeFiltersDrawer">
            <IconX class="w-5 h-5" aria-hidden="true" />
          </button>
        </div>
        <div class="discover-filters-body">
          <div class="discover-pane-head">
            <span class="discover-pane-count">{{ filtersOnLabel(filterCount) }}</span>
            <!-- aria-disabled, not disabled: the button keeps focus after a clear. -->
            <button
              type="button"
              class="filter-clear ui-action-btn"
              :aria-disabled="filterCount === 0"
              @click="clearFilters(false)"
            >Clear all</button>
          </div>
          <section>
            <div class="filter-section-head">
              <button
                type="button"
                class="filter-section-toggle ui-hover-surface"
                :aria-expanded="view.sections.sources"
                aria-controls="discover-filter-sources"
                @click="toggleSection('sources')"
              >
                <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
                <span id="discover-filter-sources-label" class="filter-section-name">Sources</span>
                <span v-if="sectionCounts.sources" class="filter-section-count" aria-hidden="true">{{ sectionCounts.sources }}</span>
                <span v-if="sectionCounts.sources" class="sr-only">, {{ filtersOnLabel(sectionCounts.sources) }}</span>
              </button>
            </div>
            <div v-show="view.sections.sources" id="discover-filter-sources" class="discover-pill-list mt-3" role="group" aria-labelledby="discover-filter-sources-label">
              <FilterPill
                v-for="site in DISCOVER_SITES"
                :key="site.key"
                :label="site.label"
                :state="siteState(view, site.key)"
                excludable
                @activate="view = includeSite(view, site.key)"
                @exclude="view = excludeSite(view, site.key)"
              />
            </div>
          </section>
          <section>
            <div class="filter-section-head">
              <button
                type="button"
                class="filter-section-toggle ui-hover-surface"
                :aria-expanded="view.sections.status"
                aria-controls="discover-filter-status"
                @click="toggleSection('status')"
              >
                <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
                <span id="discover-filter-status-label" class="filter-section-name">Status</span>
                <span v-if="sectionCounts.status" class="filter-section-count" aria-hidden="true">{{ sectionCounts.status }}</span>
                <span v-if="sectionCounts.status" class="sr-only">, {{ filtersOnLabel(sectionCounts.status) }}</span>
              </button>
            </div>
            <div v-show="view.sections.status" id="discover-filter-status">
              <p id="discover-filter-status-hint" class="discover-filter-hint">Counts are for the whole catalog.</p>
              <div class="space-y-1 mt-3" role="group" aria-labelledby="discover-filter-status-label" aria-describedby="discover-filter-status-hint">
                <label v-for="option in DISCOVER_STATUSES" :key="option.key" class="discover-check flex items-center gap-3 px-3 py-1.5 rounded-lg text-sm cursor-pointer ui-hover-surface">
                  <input type="checkbox" class="rounded" :checked="view.statuses.includes(option.key)" @change="view = toggleStatus(view, option.key)" />
                  <span>{{ option.label }}</span>
                  <span class="ml-auto text-xs tabular-nums" style="color: var(--text-muted)">{{ filterOptions?.statuses?.find((item) => item.key === option.key)?.count ?? "" }}</span>
                </label>
              </div>
            </div>
          </section>
          <section>
            <div class="filter-section-head">
              <button
                type="button"
                class="filter-section-toggle ui-hover-surface"
                :aria-expanded="view.sections.rating"
                aria-controls="discover-filter-rating-body"
                @click="toggleSection('rating')"
              >
                <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
                <span id="discover-filter-rating-label" class="filter-section-name">F95 rating</span>
                <span v-if="sectionCounts.rating" class="filter-section-count" aria-hidden="true">{{ view.minRating }}+</span>
                <span v-if="sectionCounts.rating" class="sr-only">, {{ view.minRating }} and up</span>
              </button>
            </div>
            <div v-show="view.sections.rating" id="discover-filter-rating-body">
              <p id="discover-filter-rating-hint" class="discover-filter-hint">The star score on each card. Games with few reviews score closer to average.</p>
              <select
                id="discover-filter-rating"
                v-model="view.minRating"
                aria-labelledby="discover-filter-rating-label"
                aria-describedby="discover-filter-rating-hint"
                class="discover-input mt-3 w-full rounded-lg px-3 py-2 text-sm font-medium"
              >
                <option value="">Any rating</option>
                <option value="3.5">3.5 and up</option>
                <option value="4.0">4.0 and up</option>
                <option value="4.5">4.5 and up</option>
              </select>
            </div>
          </section>
          <section v-if="filterOptions?.engines?.length">
            <div class="filter-section-head">
              <button
                type="button"
                class="filter-section-toggle ui-hover-surface"
                :aria-expanded="view.sections.engines"
                aria-controls="discover-filter-engines"
                @click="toggleSection('engines')"
              >
                <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
                <span id="discover-filter-engine-label" class="filter-section-name">Engine</span>
                <span v-if="sectionCounts.engines" class="filter-section-count" aria-hidden="true">{{ sectionCounts.engines }}</span>
                <span v-if="sectionCounts.engines" class="sr-only">, {{ filtersOnLabel(sectionCounts.engines) }}</span>
              </button>
            </div>
            <div v-show="view.sections.engines" id="discover-filter-engines" class="discover-pill-list mt-3" role="group" aria-labelledby="discover-filter-engine-label">
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
            <div class="filter-section-head">
              <button
                type="button"
                class="filter-section-toggle ui-hover-surface"
                :aria-expanded="view.sections.tags"
                aria-controls="discover-filter-tags"
                @click="toggleSection('tags')"
              >
                <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
                <span id="discover-filter-tags-label" class="filter-section-name">Tags</span>
                <span v-if="sectionCounts.tags" class="filter-section-count" aria-hidden="true">{{ sectionCounts.tags }}</span>
                <span v-if="sectionCounts.tags" class="sr-only">, {{ filtersOnLabel(sectionCounts.tags) }}</span>
              </button>
            </div>
            <div v-show="view.sections.tags" id="discover-filter-tags">
              <p id="discover-filter-tags-hint" class="discover-filter-hint">F95Zone and LewdCorner tags. A game must have every included tag and no excluded tag.</p>
              <input v-model="tagSearch" type="search" aria-label="Search all tags" placeholder="Search all tags…" class="discover-input mt-3 w-full rounded-lg px-3 py-2 text-xs" />
              <div id="discover-filter-tag-list" class="discover-pill-list mt-3" role="group" aria-labelledby="discover-filter-tags-label" aria-describedby="discover-filter-tags-hint">
                <FilterPill
                  v-for="tag in tagList.shown"
                  :key="tag.key"
                  :label="tag.name"
                  :count="tag.count"
                  :state="tagState(view, tag.name)"
                  excludable
                  @activate="view = includeTag(view, tag.name)"
                  @exclude="view = excludeTag(view, tag.name)"
                />
                <p v-if="!tagList.shown.length" class="text-xs italic" style="color: var(--text-muted)">No matching tags</p>
              </div>
              <button
                v-if="tagList.foldable"
                type="button"
                class="discover-tags-more ui-action-btn ui-hover-surface mt-3"
                :aria-expanded="showAllTags"
                aria-controls="discover-filter-tag-list"
                @click="showAllTags = !showAllTags"
              >
                <IconChevronDown class="discover-tags-more-icon" :class="{ 'discover-tags-more-icon--up': showAllTags }" aria-hidden="true" />
                {{ showAllTags ? "Show fewer" : `Show all (${tagList.total})` }}
              </button>
            </div>
          </section>
        </div>
      </div>
    </aside>

    <div ref="scroller" class="flex-1 min-w-0 p-5 overflow-y-auto flex flex-col relative" @scroll.passive="onScroll" @focusin="trackFocus">
      <header class="discover-header mb-4 shrink-0">
        <div class="discover-header-row">
          <h2 class="ui-page-heading text-3xl font-bold tracking-tight shrink-0" style="color: var(--text-primary)">
            <IconCompass class="ui-page-heading-icon" />
            <span>Discover</span>
          </h2>
          <p class="discover-subtitle text-sm pl-3" aria-hidden="true" :title="subtitle">{{ subtitle }}</p>
          <p class="sr-only">{{ subtitle }}</p>
        </div>
      </header>

      <div v-if="catalogOff || notInstalled" class="discover-state rounded-xl p-6 max-w-xl" role="status">
        <p class="font-semibold">{{ catalogOff ? "The game catalog is turned off." : "The game catalog is not downloaded yet." }}</p>
        <p class="text-sm mt-2" style="color: var(--text-secondary)">
          {{ catalogOff
            ? "Discover shows the games in the wLib game catalog. Turn on the catalog in Settings to use Discover."
            : "wLib downloads it in the background. To get it now, open the catalog settings and click Check now." }}
        </p>
        <button type="button" class="ui-setup-btn mt-4 px-4 py-2 rounded-lg text-sm font-semibold" data-controller-primary @click="router.push('/settings#catalog')">Open catalog settings</button>
      </div>

      <div v-else-if="status === null || building" class="discover-state rounded-xl p-6 max-w-xl flex items-center gap-3" role="status">
        <IconLoader2 class="w-5 h-5 animate-spin shrink-0" aria-hidden="true" />
        <span>{{ status === null ? "Loading Discover…" : "Getting the catalog ready for Discover. This takes a few seconds…" }}</span>
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
              :placeholder="searchPlaceholder"
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
            <button
              type="button"
              class="discover-toolbar-btn ui-action-btn ui-hover-surface"
              :aria-pressed="view.hideLibrary"
              title="Hide the games in your library: installed and on your wishlist"
              @click="view.hideLibrary = !view.hideLibrary"
            >
              {{ HIDE_LIBRARY_LABEL }}
            </button>
            <button
              ref="filtersButton"
              type="button"
              class="discover-toolbar-btn ui-action-btn relative ui-hover-surface"
              :aria-expanded="filtersShown"
              :aria-label="filtersButtonLabel"
              :title="filtersShown ? 'Hide filters' : 'Show filters'"
              @click="toggleFilters"
            >
              <IconFilterFilled class="ui-action-icon" />
              <span>Filters</span>
              <span v-if="filterCount > 0" aria-hidden="true" class="discover-filter-count absolute -top-1.5 -right-1.5 min-w-5 h-5 rounded-full px-1 text-xs font-bold tabular-nums flex items-center justify-center">{{ filterCount }}</span>
            </button>
          </div>
        </div>

        <!-- The count is the live region; it changes once per answer. The
             spinner sits outside it, so a refresh is not read out twice. The
             error card has its own alert, so the count goes quiet then. -->
        <div class="discover-count-row shrink-0" :class="{ 'discover-count-row--quiet': results.body === 'error' }">
          <p class="discover-count text-sm" role="status" aria-live="polite">
            <template v-if="results.body === 'error'"></template>
            <template v-else-if="results.searching">Searching…</template>
            <template v-else>{{ discoverCountLabel(total) }}</template>
          </p>
          <IconLoader2 v-if="refreshCue" class="discover-count-spinner" aria-hidden="true" />
        </div>

        <div v-if="filterChips.length" ref="chipRow" class="discover-chips mb-4 shrink-0" role="group" aria-label="Active filters">
          <button
            v-for="(chip, index) in filterChips"
            :key="chip.key"
            type="button"
            data-filter-chip
            class="filter-chip ui-action-btn"
            :class="{ 'filter-chip--excluded': chip.excluded }"
            :aria-label="chip.removeLabel"
            :title="chip.removeLabel"
            @click="removeChip(chip, index)"
          >
            <span class="filter-chip-label">{{ chip.label }}</span>
            <IconX class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          </button>
          <button type="button" class="filter-clear ui-action-btn" @click="clearFilters()">Clear all</button>
        </div>

        <div v-if="results.body === 'error'" class="discover-state rounded-xl p-6 max-w-xl">
          <div role="alert">
            <p class="font-semibold discover-state-title">{{ loadError }}</p>
            <p class="text-sm mt-2 discover-state-detail">Your search and filters stay as they are. If this keeps happening, restart wLib.</p>
          </div>
          <div class="discover-state-actions mt-4">
            <button
              type="button"
              class="discover-retry ui-setup-btn px-4 py-2 rounded-lg text-sm font-semibold"
              data-controller-primary
              :aria-disabled="retrying"
              @click="retryLoad"
            >
              <IconLoader2 v-if="retrying" class="ui-action-icon discover-retry-spinner" aria-hidden="true" />
              <IconRefresh v-else class="ui-action-icon" aria-hidden="true" />
              {{ retrying ? "Trying again…" : "Try again" }}
            </button>
          </div>
        </div>

        <div v-else-if="results.body === 'empty'" class="discover-state rounded-xl p-6 max-w-xl">
          <div role="status">
            <p class="font-semibold discover-state-title">{{ noResults.title }}</p>
            <p v-if="noResults.detail" class="text-sm mt-2 discover-state-detail">{{ noResults.detail }}</p>
          </div>
          <div v-if="search.trim() || filterCount" class="discover-state-actions mt-4">
            <button
              v-if="search.trim()"
              type="button"
              class="ui-setup-btn px-4 py-2 rounded-lg text-sm font-semibold"
              data-controller-primary
              @click="clearSearch"
            >
              <IconX class="ui-action-icon" aria-hidden="true" />
              Clear search
            </button>
            <button
              v-if="filterCount"
              type="button"
              :class="search.trim() ? 'discover-toolbar-btn ui-action-btn ui-hover-surface' : 'ui-setup-btn px-4 py-2 rounded-lg text-sm font-semibold'"
              :data-controller-primary="search.trim() ? undefined : ''"
              @click="clearFilters()"
            >
              <IconFilterFilled class="ui-action-icon" aria-hidden="true" />
              Clear filters
            </button>
          </div>
        </div>

        <div
          v-else
          ref="windowContainer"
          class="discover-cards grid gap-4"
          :class="{ 'discover-cards--refreshing': refreshCue }"
          :style="gridStyle"
          :aria-busy="results.refreshing || undefined"
        >
          <DiscoverCardView
            v-for="(card, index) in renderedGames"
            :key="card.key"
            :card="card"
            :primary="shownWindow.start + index === shownWindow.primary"
            @activate="openPreview"
          />
        </div>

        <!-- A next page failed: the loaded cards stay, the error goes under them. -->
        <div v-if="results.moreError" class="discover-state rounded-xl p-6 max-w-xl mb-12 shrink-0">
          <div role="alert">
            <p class="font-semibold discover-state-title">Could not load more games</p>
            <p class="text-sm mt-2 discover-state-detail">The games above stay. If this keeps happening, restart wLib.</p>
          </div>
          <div class="discover-state-actions mt-4">
            <button
              type="button"
              class="discover-retry ui-setup-btn px-4 py-2 rounded-lg text-sm font-semibold"
              :aria-disabled="retrying"
              @click="retryLoad"
            >
              <IconLoader2 v-if="retrying" class="ui-action-icon discover-retry-spinner" aria-hidden="true" />
              <IconRefresh v-else class="ui-action-icon" aria-hidden="true" />
              {{ retrying ? "Trying again…" : "Try again" }}
            </button>
          </div>
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
/* The top padding matches the page's, so the pane's first row lines up
   with the page heading. */
.discover-filters-body {
  display: flex;
  flex-direction: column;
  gap: 1.25rem;
  padding: 1.25rem 1.25rem 1.5rem;
}
/* Narrow windows: the pane slides in over the cards from the left edge, as
   in the Library (LibraryView.vue has the same rules). */
.filters-backdrop {
  position: fixed;
  inset: 0;
  z-index: 45;
  background: var(--overlay-scrim);
}
.filters-drawer {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 46;
  width: min(20rem, calc(var(--app-vw) - 3rem));
  height: var(--app-vh);
  border-right: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
  transform: translateX(-100%);
  visibility: hidden;
  transition: transform var(--motion-slow) cubic-bezier(0.16, 1, 0.3, 1), visibility 0s linear var(--motion-slow);
}
.filters-drawer--open {
  transform: none;
  visibility: visible;
  transition: transform var(--motion-slow) cubic-bezier(0.16, 1, 0.3, 1), visibility 0s;
}
@media (prefers-reduced-motion: reduce) {
  .filters-drawer,
  .filters-drawer--open {
    transition: none;
  }
}
.filters-drawer-head {
  position: sticky;
  top: 0;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.75rem 0.75rem 0.5rem 1rem;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border);
}
.filters-drawer-title {
  color: var(--text-primary);
  font-size: 1rem;
  font-weight: 700;
}
.filters-drawer-close {
  display: grid;
  place-items: center;
  width: 2.5rem;
  height: 2.5rem;
  border-radius: 0.5rem;
  color: var(--text-secondary);
}
/* Section headings fold their section, as in the Library. A folded section
   with a filter on keeps its count on the heading. */
.filter-section-head {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  min-height: 1.75rem;
}
.filter-section-toggle {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: 0.375rem;
  min-width: 0;
  padding: 0.25rem;
  border-radius: 0.375rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-align: left;
  text-transform: uppercase;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.filter-section-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.filter-section-toggle:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.filter-section-chevron {
  width: 0.875rem;
  height: 0.875rem;
  flex-shrink: 0;
  transition: transform 0.15s ease-out;
}
.filter-section-toggle[aria-expanded="false"] .filter-section-chevron {
  transform: rotate(-90deg);
}
.filter-section-count {
  flex-shrink: 0;
  min-width: 1.25rem;
  padding: 0 0.375rem;
  border-radius: 9999px;
  background: var(--brand-glow);
  color: var(--brand);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
  line-height: 1.25rem;
  text-align: center;
  text-transform: none;
}
/* "Show all (N)" / "Show fewer" under the tag pills. */
.discover-tags-more {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  min-height: 2rem;
  padding: 0.25rem 0.625rem 0.25rem 0.5rem;
  border-radius: 0.5rem;
  color: var(--text-secondary);
  font-size: 0.8125rem;
  font-weight: 500;
}
.discover-tags-more:hover {
  color: var(--text-primary);
}
.discover-tags-more:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.discover-tags-more-icon {
  width: 0.875rem;
  height: 0.875rem;
  transition: transform 0.15s ease-out;
}
.discover-tags-more-icon--up {
  transform: rotate(180deg);
}
@media (prefers-reduced-motion: reduce) {
  .filter-section-chevron,
  .discover-tags-more-icon {
    transition: none;
  }
}
/* The pane's first row: how many filters are on, and Clear all. */
/* As tall as the page heading's line (text-3xl), so both centers line up. */
.discover-pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  min-height: 2.25rem;
  padding-inline-start: 0.25rem;
}
.filters-drawer .discover-pane-head {
  min-height: 1.75rem;
}
.discover-pane-count {
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-variant-numeric: tabular-nums;
}
/* The row gap leaves room for a pill's exclude button on its top corner;
   the pane's padding keeps the top-right one inside the scroll area. */
.discover-pill-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.375rem;
}
/* Clear controls, as in the Library's filter pane. */
.filter-clear {
  flex-shrink: 0;
  padding: 0.25rem 0.75rem;
  border-radius: 0.3rem;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 500;
  transition: color 0.15s ease;
}
.filter-clear:hover:not([aria-disabled="true"]) {
  color: var(--danger-text);
}
.filter-clear[aria-disabled="true"] {
  color: var(--text-muted);
  cursor: default;
}
.filter-clear:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
/* Active-filter chips, as in the Library. An excluded value keeps the
   pill's red tone, so the row reads the same way as the pane. */
.discover-chips {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  max-width: 100%;
  padding: 0.25rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 9999px;
  background: var(--bg-raised);
  color: var(--text-primary);
  font-size: 0.75rem;
}
.filter-chip:hover {
  border-color: var(--border-hover);
  background: var(--bg-overlay);
}
.filter-chip--excluded,
.filter-chip--excluded:hover {
  border-color: var(--danger-text);
  background: var(--danger-bg);
  color: var(--danger-text);
}
.filter-chip:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.filter-chip-label {
  min-width: 0;
  overflow-wrap: anywhere;
  text-align: start;
}
.discover-filter-hint {
  margin-top: 0.5rem;
  padding-inline: 0.25rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  line-height: 1.4;
  text-wrap: pretty;
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
/* The global placeholder is half the text color (3.3:1 on white); the muted
   token keeps 4.5:1 or more on the input in both themes. */
.discover-input::placeholder {
  color: var(--text-muted);
  opacity: 1;
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
/* Wide enough for the whole placeholder. When the controls do not fit
   beside it, they wrap to the row below and the search keeps the full row. */
.discover-toolbar-search {
  flex: 1 1 23rem;
  min-width: min(100%, 23rem);
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
/* On: "Hide library games" is pressed, or the filter pane is shown. */
.discover-toolbar-btn[aria-pressed="true"],
.discover-toolbar-btn[aria-expanded="true"] {
  background: var(--brand-glow);
  border-color: var(--brand-deep);
  /* The accent on its own glow is about 3.8:1 in the light theme; a step
     toward the text color keeps it 4.5:1 or more for any accent. */
  color: color-mix(in oklab, var(--brand) 70%, var(--text-primary));
}
.discover-toolbar-btn[aria-pressed="true"]:hover,
.discover-toolbar-btn[aria-expanded="true"]:hover {
  border-color: var(--brand);
}
.discover-filter-count {
  background: var(--brand);
  color: var(--on-brand);
}
.discover-count-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 1.25rem;
  margin-bottom: 0.75rem;
}
/* No visible count beside the error card; the live region stays in place. */
.discover-count-row--quiet {
  min-height: 0;
  margin-bottom: 0;
}
.discover-count {
  color: var(--text-secondary);
}
.discover-count-spinner {
  width: 0.875rem;
  height: 0.875rem;
  flex-shrink: 0;
  color: var(--text-muted);
  animation: discover-spin 0.9s linear infinite;
}
.discover-retry[aria-disabled="true"] {
  cursor: progress;
}
.discover-retry-spinner {
  animation: discover-spin 0.9s linear infinite;
}
@keyframes discover-spin {
  to {
    transform: rotate(360deg);
  }
}
/* The motion setting and the system setting stop the turn; the icon stays. */
:global(.motion-off) .discover-count-spinner,
:global(.motion-off) .discover-retry-spinner {
  animation: none;
}
@media (prefers-reduced-motion: reduce) {
  .discover-count-spinner,
  .discover-retry-spinner {
    animation: none;
  }
}
.discover-state {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  color: var(--text-primary);
}
.discover-state-title,
.discover-state-detail {
  overflow-wrap: anywhere;
}
.discover-state-detail {
  color: var(--text-secondary);
}
.discover-state-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
}
.discover-state-actions > button {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
}
.discover-cards {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, 220px), 1fr));
  transition: opacity var(--motion-fast) ease-out;
}
/* A new query is loading: the old cards stay, dimmed, until it answers. */
.discover-cards--refreshing {
  opacity: 0.55;
}
</style>
