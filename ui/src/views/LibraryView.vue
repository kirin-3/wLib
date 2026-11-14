<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { activeFilterChips as buildFilterChips, removeFilterValue, type FilterChip } from "../utils/libraryFilters";
import { confirmAction } from "../utils/confirm";
import { controllerFocusText, useControllerActions } from "../utils/controllerActions";
import {
  getEffectiveLaunchTargets,
  libraryDisplayState,
  type EffectiveLaunchTarget,
} from "../utils/libraryGames";
import GameCard from "../components/library/GameCard.vue";
import GameRow from "../components/library/GameRow.vue";
import { runningGameIds, refreshRunningGames } from "../composables/useRunningGames";
import { ref, computed, nextTick, onMounted, onUnmounted, toRaw, watch } from "vue";
import { gamesStore } from "../composables/useGamesStore";
import { nextRenderLimit, RENDER_BATCH } from "../utils/gamesStore";
import { toCssPx } from "../utils/interfaceScalePreference";
import TagColorPicker from "../components/TagColorPicker.vue";
import { colorForTag, loadTagColors } from "../utils/tagColors";
import { useRoute, useRouter } from "vue-router";
import {
  IconArchive,
  IconCheck,
  IconArrowLeft,
  IconArrowUp,
  IconClock,
  IconColumns2Filled,
  IconDeviceGamepad2,
  IconDeviceGamepad2Filled,
  IconFilterFilled,
  IconLibraryPlus,
  IconLayoutGridFilled,
  IconPanoramaHorizontalFilled,
  IconLayoutListFilled,
  IconFolderPlus,
  IconPhotoFilled,
  IconPlus,
  IconSparkles,
  IconZoom,
  IconHeartFilled,
  IconListCheck,
  IconSortAscending,
  IconSortDescending,
  IconX,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import type { CompanionTool, GameRecord, LaunchMode, StoreLink } from "../services/api";
import AddGameModal from "../components/modals/AddGameModal.vue";
import ImportFolderModal from "../components/modals/ImportFolderModal.vue";
import GameDetailModal from "../components/modals/GameDetailModal.vue";
import MenuButton, { type MenuButtonItem } from "../components/MenuButton.vue";
import {
  getPlayStatusOptions,
  normalizePlayStatus,
} from "../utils/playStatus";
import {
  DEFAULT_FILTER_SECTIONS,
  DEFAULT_LIBRARY_VIEW_STATE,
  hasStatusFilter,
  normalizeActiveCollection,
  normalizeFilterAndSort,
  normalizeFilterPreset,
  normalizeLibraryViewState,
  readPresetsForSave,
  restoreSavedLibraryViewState,
  saveLibraryViewState,
  toggleStatusFilter,
  toggleSortState,
  chooseSortField,
  VALID_SORT_FIELDS,
  type SortState,
  type FilterCollection,
  type FilterInstallState,
  type FilterSections,
  type LayoutMode,
  type CardSize,
  type LibraryFilterPreset,
  type LibraryViewState,
  type SortDir,
  type SortField,
} from "../utils/libraryViewState";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { libraryFiltersPaneOpen } from "../utils/navLayout";
import { notify, notifyError, notifyUndo } from "../utils/toast";
import {
  addTagCsv, engineSnapshot, launchModeApplicable, launchModeSnapshot, removeTagCsv, runBulk, runBulkSaveBackup,
  runtimeSnapshot, sandboxSnapshot, statusSnapshot, tagsSnapshot, versionSnapshot, wineApplicable,
} from "../utils/bulkActions";
import BulkActionBar from "../components/BulkActionBar.vue";
import { isEngine } from "../utils/engines";
import BulkWineDialog, { type WineChoice } from "../components/BulkWineDialog.vue";
import { isPendingRemoval, scheduleRemoval } from "../utils/pendingRemovals";
import { isPendingUninstallRemoval, scheduleUninstall, withPendingUninstall } from "../utils/pendingUninstalls";
import UninstallDialog from "../components/UninstallDialog.vue";
import MoveGameDialog from "../components/MoveGameDialog.vue";
import WebMediaDialog from "../components/WebMediaDialog.vue";
import { useWebMediaConversion } from "../composables/useWebMediaConversion";
import { WEB_MEDIA_CONVERTING, WEB_MEDIA_UNSUPPORTED } from "../utils/webMedia";
import type { LaunchGameResponse, WebMediaInfo } from "../services/api";
import MoveQueuePanel from "../components/library/MoveQueuePanel.vue";
import { useGameMoves } from "../composables/useGameMoves";
import type { UninstallOptions } from "../services/api";
import { useLibrarySelection } from "../composables/useLibrarySelection";
import { modalStack } from "../utils/modalKeyboard";
import { sandboxLaunchError } from "../utils/sandbox";
import { openInstallArchive } from "../utils/installArchive";
import {
  LIBRARY_SOURCES,
  buildTagFilterOptions,
  compareLibraryGames,
  gameFolder,
  gameTags,
  hasAvailableUpdate,
  hasTagFilter,
  hasUnplayedUpdate,
  isUpdateSkipped,
  hasNewSinceFinished,
  isWishlisted,
  matchesGameSearch,
  matchesSourceFilter,
  matchesTagFilter,
  toggleTagFilter,
} from "../utils/libraryGames";

interface UpdateNotice {
  type: "" | "success" | "error";
  message: string;
}

interface ModalUpdateState extends UpdateNotice {
  running: boolean;
}

import { extensionGameId, readStoreLinks, gamePageUrl } from "../utils/storeLinks";
import { launchNoticeMessage } from "../utils/webPlayer";
import { updateCheckRunning } from "../composables/useUpdateCheckState";

interface AddGamePayload {
  links?: StoreLink[];
  title: string;
  exe_path: string;
  f95_url?: string;
  version?: string;
  cover_image?: string;
  tags?: string;
  rating?: string;
  developer?: string;
  engine?: string;
  launch_mode?: LaunchMode;
  command_line_args?: string;
}

interface PlaytimeTickDetail {
  gameId?: number;
  delta?: number;
  isFinal?: boolean;
  lastPlayedVersion?: string;
  sessionId?: number;
}

const readQueryValue = (value: unknown): string => {
  if (Array.isArray(value)) {
    return typeof value[0] === "string" ? value[0] : "";
  }
  return typeof value === "string" ? value : "";
};

const route = useRoute();
const router = useRouter();
// Shared with the other views. The list is shallow: change a game with
// gamesStore.patch(), never by editing the object.
const games = gamesStore.games;
const gamesLoaded = gamesStore.loaded;
const loadError = ref<string | null>(null);
const displayState = computed(() => libraryDisplayState(gamesLoaded.value, loadError.value, games.value.length, filteredGames.value.length));
const customStatuses = ref<string[]>([]);
// True once Settings has answered, so the custom status list is authoritative.
const customStatusesLoaded = ref(false);
// User-defined collections shown as tabs; activeCollection "" means "All".
const userCollections = ref<string[]>([]);
const activeCollection = ref(DEFAULT_LIBRARY_VIEW_STATE.activeCollection);
const newCollectionName = ref("");
const showNewCollectionInline = ref(false);
const collectionError = ref("");
const renamingCollection = ref<string | null>(null);
const renameCollectionDraft = ref("");
const newCollectionInput = ref<HTMLInputElement | null>(null);
const collectionTabsElement = ref<HTMLElement | null>(null);
const collectionTabMenu = ref<{ name: string; x: number; y: number } | null>(null);
const collectionTabMenuElement = ref<HTMLElement | null>(null);
// Saved filter presets listed after Quick filters in the filters pane.
const savedPresets = ref<LibraryFilterPreset[]>([]);
// True once Settings has answered, so a save can't treat "not loaded" as empty.
const presetsLoaded = ref(false);
const newPresetName = ref("");
const presetError = ref("");
// "Save as preset" in the active-filter chip row opens an inline name field.
const savePresetOpen = ref(false);
const savePresetButton = ref<HTMLButtonElement | null>(null);
const savePresetInput = ref<HTMLInputElement | null>(null);
const quickFiltersList = ref<HTMLElement | null>(null);
const showAddModal = ref(false);
const showImportModal = ref(false);
const showDetailModal = ref(false);
const detailModal = ref<{ requestClose: () => Promise<boolean> } | null>(null);
const selectedGame = ref<GameRecord | null>(null);
const openLaunchTargetMenuId = ref<number | null>(null);
const searchInput = ref<HTMLInputElement | null>(null);
const tagSearchQuery = ref("");
const markingUpdatedIds = ref(new Set<number>());
const contextMenu = ref<{ game: GameRecord; x: number; y: number } | null>(
  null,
);
const contextMenuElement = ref<HTMLElement | null>(null);
const contextTools = ref<CompanionTool[]>([]);
let contextToolsRequest = 0;
const loadingCompanionId = ref<number | null>(null);
const launchContextTool = async (tool: CompanionTool) => {
  const game = contextMenu.value?.game;
  if (!game || loadingCompanionId.value !== null) return;
  loadingCompanionId.value = tool.id;
  try {
    const result = await api.launchCompanionTool(game.id, tool.id);
    if (!result.success) notifyError(`Failed to launch ${tool.name}:\n\n${result.error || "Unknown error"}`);
    else closeContextMenu();
  } catch (cause) { notifyError(`Failed to launch ${tool.name}: ${String(cause)}`); }
  finally { loadingCompanionId.value = null; }
};
const loadContextTools = async (gameId: number) => {
  const request = ++contextToolsRequest;
  contextTools.value = [];
  if (!runningGameIds.value.has(gameId)) return;
  try {
    const capabilities = await loadPlatformCapabilities();
    if (!capabilities.companion_tools || request !== contextToolsRequest) return;
    const result = await api.listLaunchableCompanionTools(gameId);
    if (request !== contextToolsRequest || contextMenu.value?.game.id !== gameId || !runningGameIds.value.has(gameId)) return;
    contextTools.value = result.success ? result.tools || [] : [];
    await nextTick();
    if (request === contextToolsRequest && contextMenu.value && contextMenuElement.value) {
      contextMenu.value.y = Math.max(8, Math.min(contextMenu.value.y, window.innerHeight - contextMenuElement.value.getBoundingClientRect().height - 8));
    }
  } catch (cause) { console.error("Could not load custom tools", cause); }
};
// "actions" is the button list; "collections" switches the body to the
// membership checklist so no second popup needs positioning.
const contextMenuView = ref<"actions" | "collections">("actions");
const contextNewCollectionName = ref("");
const contextNewCollectionError = ref("");
let contextMenuTrigger: HTMLElement | null = null;

const tableColumns: Array<{ key: SortField; label: string; class?: string }> = [
  { key: "title", label: "Title" },
  { key: "version", label: "Version" },
  { key: "play_status", label: "Status" },
  { key: "engine", label: "Engine", class: "col-engine" },
  { key: "playtime_seconds", label: "Playtime" },
  { key: "last_played", label: "Last played", class: "col-last-played" },
];

const updateCount = computed(
  () => games.value.filter((game) => hasAvailableUpdate(game)).length,
);
const collections: Array<{
  value: FilterCollection;
  label: string;
  icon: typeof IconClock;
}> = [
  { value: "All", label: "All Games", icon: IconLayoutGridFilled },
  { value: "Favorites", label: "Favorites", icon: IconHeartFilled },
  { value: "Updates available", label: "Updates available", icon: IconArrowUp },
  { value: "New since finished", label: "New since finished", icon: IconSparkles },
  { value: "Unplayed update", label: "Unplayed update", icon: IconClock },
  { value: "Recently played", label: "Recently played", icon: IconClock },
];

// Search & Filter state
const searchQuery = ref("");
const selection = useLibrarySelection();
const selectedIds = selection.selectedIds;
const selecting = ref(false);
const bulkBar = ref<InstanceType<typeof BulkActionBar> | null>(null);
const libraryControllerScope = ref<HTMLElement | null>(null);
useControllerActions(libraryControllerScope, {
  play: () => {
    const card = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[data-game-id]");
    const game = games.value.find(item => String(item.id) === card?.dataset.gameId);
    if (game) void toggleGame(game);
  },
  back: () => { if (bulkBar.value?.closeMenu()) return true; if (savePresetOpen.value) { closeSavePreset(); return true; } if (!selectedIds.value.size && !selecting.value) return false; clearSelection(); return true; },
  menu: () => {
    if (selectedIds.value.size) bulkBar.value?.focusFirst();
    else (document.activeElement as HTMLElement | null)?.dispatchEvent(new KeyboardEvent("keydown", { key: "ContextMenu", bubbles: true, cancelable: true }));
  },
  search: () => { if (searchInput.value) { controllerFocusText(searchInput.value); searchInput.value.select(); } },
  prevTab: () => cycleControllerCollection(-1),
  nextTab: () => cycleControllerCollection(1),
  hints: () => [{ button: "A", label: selectedIds.value.size ? "Toggle selection" : "Open" }, { button: "B", label: selectedIds.value.size ? "Clear selection" : "Back" }, { button: "X", label: "Play" }, { button: "Y", label: "Search" }, { button: "Menu", label: selectedIds.value.size ? "Bulk actions" : "Actions" }, { button: "LB/RB", label: "Collections" }],
});
function cycleControllerCollection(step: number) {
  const tabs = [...(collectionTabsElement.value?.querySelectorAll<HTMLButtonElement>("[data-collection-tab]") ?? [])];
  if (!tabs.length) return;
  const current = tabs.findIndex(tab => tab.dataset.collection === activeCollection.value);
  const tab = tabs[(current + step + tabs.length) % tabs.length];
  if (tab) { activeCollection.value = tab.dataset.collection ?? ""; void scrollSelectedCollectionTabIntoView(); }
}
const filterStatuses = ref<string[]>([
  ...DEFAULT_LIBRARY_VIEW_STATE.filterStatuses,
]);
const filterCollection = ref<FilterCollection>(
  DEFAULT_LIBRARY_VIEW_STATE.filterCollection,
);
const filterSources = ref<string[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterSources]);
const filterEngines = ref<string[]>([
  ...DEFAULT_LIBRARY_VIEW_STATE.filterEngines,
]);
const filterTags = ref<string[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterTags]);
const tagColorPicker = ref<{ tag: string; anchor: HTMLElement } | null>(null);
function openTagColorPicker(event: MouseEvent | KeyboardEvent, tag: string) {
  event.preventDefault();
  tagColorPicker.value = { tag, anchor: event.currentTarget as HTMLElement };
}
const filterInstallState = ref<FilterInstallState>(DEFAULT_LIBRARY_VIEW_STATE.filterInstallState);
const cardSize = ref<CardSize>(DEFAULT_LIBRARY_VIEW_STATE.cardSize);
const cardScales = [0.75, 0.875, 1, 1.25, 1.5] as const;
const layoutMode = ref<LayoutMode>(DEFAULT_LIBRARY_VIEW_STATE.layoutMode);
const sortBy = ref<SortField>(DEFAULT_LIBRARY_VIEW_STATE.sortBy);
const sortDir = ref<SortDir>(DEFAULT_LIBRARY_VIEW_STATE.sortDir);

const isFiltersCollapsed = ref(DEFAULT_LIBRARY_VIEW_STATE.isFiltersCollapsed);
const filterSections = ref<FilterSections>({ ...DEFAULT_FILTER_SECTIONS });

const normalizeF95Url = (rawUrl: unknown): string => {
  if (typeof rawUrl !== "string") return "";

  const trimmed = rawUrl.trim();
  if (!trimmed) return "";

  try {
    const parsed = new URL(trimmed);
    const match = parsed.pathname.match(
      /^\/threads\/(?:(.+)\.)?(\d+)(?:\/.*)?$/,
    );
    if (match) {
      const slug = (match[1] || "").replace(/^\.+|\.+$/g, "");
      parsed.pathname = slug
        ? `/threads/${slug}.${match[2]}/`
        : `/threads/${match[2]}/`;
    }
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  } catch (_error) {
    return trimmed;
  }
};

const extractThreadIdFromUrl = (rawUrl: unknown): string => {
  const normalized = normalizeF95Url(rawUrl);
  const match = normalized.match(/\/threads\/(?:.+\.)?(\d+)\/?$/);
  return match ? (match[1] ?? "") : "";
};

const urlsMatchByThreadIdentity = (left: unknown, right: unknown): boolean => {
  const leftThreadId = extractThreadIdFromUrl(left);
  const rightThreadId = extractThreadIdFromUrl(right);

  if (leftThreadId && rightThreadId) {
    return leftThreadId === rightThreadId;
  }

  return normalizeF95Url(left) === normalizeF95Url(right);
};

const applySortState = (state: SortState) => {
  sortBy.value = state.sortBy;
  sortDir.value = state.sortDir;
};
// List view column headers.
const toggleSort = (field: SortField) => applySortState(toggleSortState({ sortBy: sortBy.value, sortDir: sortDir.value }, field));
// The sort menu in the other layouts; the direction button reverses the order.
const setSortField = (field: string) => {
  if (VALID_SORT_FIELDS.has(field as SortField)) applySortState(chooseSortField({ sortBy: sortBy.value, sortDir: sortDir.value }, field as SortField));
};
const reverseSortDir = () => { sortDir.value = sortDir.value === "asc" ? "desc" : "asc"; };

const selectCollection = (collection: FilterCollection) => {
  filterCollection.value = collection;
  if (collection === "Recently played") {
    sortBy.value = "last_played";
    sortDir.value = "desc";
  }
};

const toggleFiltersPane = () => {
  isFiltersCollapsed.value = !isFiltersCollapsed.value;
};
// The app sidebar collapses while the pane is open (see utils/navLayout.ts).
watch(isFiltersCollapsed, (collapsed) => { libraryFiltersPaneOpen.value = !collapsed; });

const toggleFilterSection = (section: keyof FilterSections) => {
  filterSections.value[section] = !filterSections.value[section];
};

const allStatuses = computed(() =>
  getPlayStatusOptions([
    ...customStatuses.value,
    ...games.value.map((game) =>
      normalizePlayStatus(game.play_status, game.status),
    ),
  ]),
);

const sortOptions: Array<{ key: SortField; label: string }> = [
  { key: "title", label: "A-Z" },
  { key: "date_added", label: "Newest" },
  { key: "last_played", label: "Recent" },
  { key: "playtime_seconds", label: "Playtime" },
  { key: "rating", label: "F95 rating" },
  { key: "own_rating", label: "My rating" },
];
// List view can also sort by these columns; the sort button names them when they are active.
const listOnlySortLabels: Partial<Record<SortField, string>> = { version: "Version", play_status: "Status", engine: "Engine" };
const sortFieldLabel = computed(() => sortOptions.find(option => option.key === sortBy.value)?.label ?? listOnlySortLabels[sortBy.value] ?? "Sort");
const sortMenuItems = computed<MenuButtonItem[]>(() => sortOptions.map(option => ({ id: option.key, label: option.label, checked: sortBy.value === option.key })));
const addMenuItems: MenuButtonItem[] = [
  { id: "import", label: "Import folder", icon: IconFolderPlus },
  { id: "archive", label: "Install from archive", icon: IconArchive },
];
const chooseAddMenuItem = (id: string) => {
  if (id === "import") showImportModal.value = true;
  else if (id === "archive") openInstallArchive();
};

const toggleFilter = (arr: string[], val: string) => {
  const idx = arr.indexOf(val);
  if (idx === -1) arr.push(val);
  else arr.splice(idx, 1);
};

// Tags are case-insensitive, so the pill's spelling may differ from a saved
// filter. Toggle every spelling and keep the visible one when selecting.
const toggleTag = (tag: string) => {
  filterTags.value = toggleTagFilter(
    filterTags.value,
    tag,
    !hasTagFilter(filterTags.value, tag),
  );
};

// Status filters keep the spelling they were restored with, so the checkbox
// matches case-insensitively and toggling it clears every spelling of the name.
const statusFilterActive = (status: string): boolean =>
  hasStatusFilter(filterStatuses.value, status);

const applyStatusFilter = (status: string, event: Event): void => {
  filterStatuses.value = toggleStatusFilter(
    filterStatuses.value,
    status,
    (event.target as HTMLInputElement).checked,
  );
};

const normalizeEngineFilterValue = (value: unknown): string =>
  typeof value === "string" ? value.trim() : "";

// Derived unique values for filter pills
const uniqueEngines = computed(() => {
  const set = new Set<string>();
  games.value.forEach((g) => {
    const engine = normalizeEngineFilterValue(g.engine);
    if (engine) set.add(engine);
  });
  return [...set].sort();
});

const tagOptions = computed(() => buildTagFilterOptions(games.value));
const tagCounts = computed(
  () => new Map(tagOptions.value.map((option) => [option.label, option.count])),
);
const uniqueTags = computed(() => tagOptions.value.map((option) => option.label));
const visibleTags = computed(() =>
  uniqueTags.value.filter((tag) =>
    tag.toLowerCase().includes(tagSearchQuery.value.trim().toLowerCase()),
  ),
);

const activeFilterCount = computed(
  () =>
    (filterCollection.value !== "All" ? 1 : 0) +
    (filterStatuses.value.length > 0 ? 1 : 0) +
    (filterInstallState.value ? 1 : 0) +
    (filterSources.value.length > 0 ? 1 : 0) +
    (filterEngines.value.length > 0 ? 1 : 0) +
    (filterTags.value.length > 0 ? 1 : 0),
);
const activeFilterChips = computed(() => buildFilterChips({
  statuses: filterStatuses.value, install: filterInstallState.value, sources: filterSources.value,
  engines: filterEngines.value, tags: filterTags.value, collection: filterCollection.value,
  statusLabels: Object.fromEntries(allStatuses.value.map(status => [status.value.toLowerCase(), status.label])),
  sourceLabels: Object.fromEntries(LIBRARY_SOURCES.map(source => [source.key, source.label])),
}));
const removeFilterChip = (chip: FilterChip) => {
  switch (chip.kind) {
    case "status": filterStatuses.value = removeFilterValue(filterStatuses.value, chip.value); break;
    case "install": filterInstallState.value = ""; break;
    case "source": filterSources.value = removeFilterValue(filterSources.value, chip.value); break;
    case "engine": filterEngines.value = removeFilterValue(filterEngines.value, chip.value); break;
    case "tag": filterTags.value = removeFilterValue(filterTags.value, chip.value); break;
    case "collection": filterCollection.value = "All"; break;
  }
};
const clearFilters = () => {
  searchQuery.value = "";
  filterStatuses.value = [];
  filterInstallState.value = "";
  filterSources.value = [];
  filterEngines.value = [];
  filterTags.value = [];
  filterCollection.value = "All";
};

const applyLibraryViewState = (state: LibraryViewState) => {
  layoutMode.value = state.layoutMode;
  cardSize.value = state.cardSize;
  sortBy.value = state.sortBy;
  sortDir.value = state.sortDir;
  filterCollection.value = state.filterCollection;
  filterStatuses.value = [...state.filterStatuses];
  filterInstallState.value = state.filterInstallState;
  filterSources.value = [...state.filterSources];
  filterEngines.value = [...state.filterEngines];
  filterTags.value = [...state.filterTags];
  isFiltersCollapsed.value = state.isFiltersCollapsed;
  filterSections.value = { ...state.filterSections };
  activeCollection.value = state.activeCollection;
};

const buildLibraryViewState = (): LibraryViewState =>
  normalizeLibraryViewState({
    layoutMode: layoutMode.value,
    cardSize: cardSize.value,
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    filterCollection: filterCollection.value,
    filterStatuses: filterStatuses.value,
    filterInstallState: filterInstallState.value,
    filterSources: filterSources.value,
    filterEngines: filterEngines.value,
    filterTags: filterTags.value,
    isFiltersCollapsed: isFiltersCollapsed.value,
    filterSections: filterSections.value,
    activeCollection: activeCollection.value,
  });

const sameStringArray = (
  left: readonly string[],
  right: readonly string[],
): boolean =>
  left.length === right.length &&
  left.every((value, index) => value === right[index]);

const persistLibraryViewState = () => {
  saveLibraryViewState(localStorage, buildLibraryViewState());
};

const restoreLibraryViewState = () => {
  applyLibraryViewState(restoreSavedLibraryViewState(localStorage));
};

// Sorting depends only on the games and the sort order, so typing in the
// search box re-filters an already sorted list instead of sorting again.
const sortedGames = computed(() =>
  [...games.value].sort((a, b) => compareLibraryGames(a, b, sortBy.value, sortDir.value)),
);
// The search filter follows typing after a short pause; clearing is instant.
const appliedSearch = ref("");
let searchTimer: ReturnType<typeof setTimeout> | undefined;
watch(searchQuery, (value) => {
  if (searchTimer) clearTimeout(searchTimer);
  if (!value.trim()) { appliedSearch.value = ""; return; }
  searchTimer = setTimeout(() => { appliedSearch.value = value; }, 150);
}, { immediate: true });
onUnmounted(() => { if (searchTimer) clearTimeout(searchTimer); });

const filteredGames = computed(() => {
  let result = sortedGames.value
    .filter((game) => !isPendingRemoval(game.id) && !isPendingUninstallRemoval(game.id))
    .map(withPendingUninstall);
  // The selected collection tab limits everything below it.
  if (activeCollection.value) {
    const activeKey = activeCollection.value.toLowerCase();
    result = result.filter((game) =>
      (game.collections || []).some(
        (name) => name.toLowerCase() === activeKey,
      ),
    );
  }
  if (appliedSearch.value.trim())
    result = result.filter((game) =>
      matchesGameSearch(game, appliedSearch.value),
    );
  if (filterCollection.value === "Favorites") {
    result = result.filter((g) => g.is_favorite);
  }
  if (filterCollection.value === "Updates available")
    result = result.filter((game) => hasAvailableUpdate(game));
  if (filterCollection.value === "Unplayed update")
    result = result.filter(hasUnplayedUpdate);
  if (filterCollection.value === "New since finished")
    result = result.filter(hasNewSinceFinished);
  if (filterCollection.value === "Recently played") {
    result = result.filter((game) => !!game.last_played);
  }
  if (filterStatuses.value.length) {
    result = result.filter((g) => {
      return filterStatuses.value.some(
        (status) =>
          status.toLowerCase() ===
          normalizePlayStatus(g.play_status, g.status).toLowerCase(),
      );
    });
  }
  if (filterInstallState.value === "wishlist")
    result = result.filter(isWishlisted);
  if (filterInstallState.value === "installed")
    result = result.filter((game) => !isWishlisted(game));
  result = result.filter((game) => matchesSourceFilter(game, filterSources.value));
  if (filterEngines.value.length) {
    result = result.filter((g) =>
      filterEngines.value.includes(normalizeEngineFilterValue(g.engine)),
    );
  }
  if (filterTags.value.length) {
    result = result.filter((game) => matchesTagFilter(game, filterTags.value));
  }
  return result;
});

watch(() => filteredGames.value.map((game) => game.id), (ids) => selection.prune(ids));

// Only the first cards are in the page; more are added as the user scrolls
// near the end. A large library would otherwise build every card at once.
const renderLimit = ref(RENDER_BATCH);
const renderedGames = computed(() =>
  filteredGames.value.length > renderLimit.value
    ? filteredGames.value.slice(0, renderLimit.value)
    : filteredGames.value,
);
const growRenderedGames = (scroller: EventTarget | null) => {
  const element = scroller instanceof Document ? scroller.scrollingElement : scroller;
  if (!(element instanceof HTMLElement) || !element.querySelector(".library-cards, .library-table")) return;
  renderLimit.value = nextRenderLimit(
    renderLimit.value,
    filteredGames.value.length,
    element.scrollHeight - element.scrollTop - element.clientHeight,
    element.clientHeight,
  );
};
// A tall window may show every rendered card with no scrollbar, so no scroll
// event would ever add more: add cards until the page is filled.
const fillRenderedGames = async () => {
  await nextTick();
  let scroller = document.querySelector(".library-cards, .library-table")?.parentElement ?? null;
  while (scroller && !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY)) scroller = scroller.parentElement;
  growRenderedGames(scroller ?? document);
};
watch([() => filteredGames.value.length, renderLimit, layoutMode, cardSize], () => void fillRenderedGames(), { flush: "post" });
const handleCardClick = (game: GameRecord, event: MouseEvent | KeyboardEvent) => {
  const ids = filteredGames.value.map((item) => item.id);
  if (event.shiftKey) selection.selectRange(ids, game.id);
  else if (event.ctrlKey || event.metaKey || selecting.value || selectedIds.value.size) selection.toggle(game.id);
  else openDetail(game);
  if (!selectedIds.value.size) selecting.value = false;
};
const clearSelection = () => { selection.clear(); selecting.value = false; };

const selectedGames = computed(() => filteredGames.value.filter((game) => selectedIds.value.has(game.id)));
const bulkBusy = ref(false);
// Kept current by the app-wide update-status poll.
const bulkChecking = updateCheckRunning;
const bulkSavesBusy = ref(false);
const bulkWineOpen = ref(false);
const uninstallOpen = ref(false);
const uninstallTargets = ref<GameRecord[]>([]);
const moveOpen = ref(false);
const moveTargets = ref<GameRecord[]>([]);
const gameMoves = useGameMoves();
// The built-in player's video dialog, opened from a launch result.
const webMediaOpen = ref(false);
const webMediaGame = ref<GameRecord | null>(null);
const webMediaTarget = ref("");
const webMediaInfo = ref<WebMediaInfo | null>(null);
const webMediaCode = ref("");
const webMediaDialog = ref<InstanceType<typeof WebMediaDialog> | null>(null);
const webMediaJobs = useWebMediaConversion();
const openUninstall = (targets: GameRecord[]) => {
  const installed = targets.filter((game) => !isWishlisted(game));
  if (!installed.length) { notifyError("No selected game is installed."); return; }
  uninstallTargets.value = installed;
  uninstallOpen.value = true;
};
const openMove = (targets: GameRecord[]) => {
  const installed = targets.filter((game) => !isWishlisted(game));
  if (!installed.length) { notifyError("No selected game is installed."); return; }
  moveTargets.value = installed;
  moveOpen.value = true;
};
const confirmUninstall = (targets: GameRecord[], options: UninstallOptions) => scheduleUninstall(targets, options);
const moveStarted = (queued: number) => {
  if (queued) notify(queued === 1 ? "The move runs in the background." : `${queued} games move in the background, one at a time.`, "info");
  gameMoves.refresh();
};
const bulkShowRuntime = ref(false);
const bulkTagSuggestions = computed(() => [...new Set(games.value.flatMap((game) => gameTags(game)))].sort((a, b) => a.localeCompare(b)));
const eligibleUpdates = computed(() => selectedGames.value.filter((game) => hasAvailableUpdate(game)));
const eligibleChecks = computed(() => selectedGames.value.filter((game) => !!game.f95_url));
onMounted(() => {
  void loadPlatformCapabilities().then((capabilities) => { bulkShowRuntime.value = capabilities.platform === "linux"; });
});
type BulkUpdate = (id: number, fields: Record<string, unknown>) => Promise<{ success?: boolean | undefined; error?: string | undefined }>;
const bulkUpdate: BulkUpdate = (id, fields) => api.updateGame(id, fields);
// Shared by the newer field actions: one library refresh, one summary toast, one Undo.
const runBulkFields = async (
  targets: GameRecord[],
  apply: (game: GameRecord) => Promise<{ success?: boolean | undefined; error?: string | undefined }> | null,
  snapshot: (game: GameRecord) => () => Promise<{ success?: boolean | undefined; error?: string | undefined }>,
) => {
  bulkBusy.value = true;
  try {
    const result = await api.withLibraryBatch(() => runBulk(targets, async (game) => apply(game), snapshot));
    if (result.changed) notifyUndo(`${result.changed} changed, ${result.skipped} skipped${result.failed ? `, ${result.failed} failed` : ""}.`, () => api.withLibraryBatch(result.undo));
    else notifyError(`No games changed; ${result.skipped} skipped, ${result.failed} failed.`);
  } finally { bulkBusy.value = false; }
};
const applyBulkWine = (choice: WineChoice) => {
  const running = runningGameIds.value;
  void runBulkFields(
    [...selectedGames.value],
    (game) => {
      if (!wineApplicable(game, running)) return null;
      const fields: Record<string, unknown> = {};
      if (choice.prefix.mode === "folder" && (game.custom_prefix || "") !== choice.prefix.path) fields.custom_prefix = choice.prefix.path;
      if (choice.prefix.mode === "default" && (game.custom_prefix || "") !== "") fields.custom_prefix = "";
      if (choice.runner.mode === "set" && (game.proton_version || "") !== choice.runner.path) fields.proton_version = choice.runner.path;
      if (choice.runner.mode === "global" && (game.proton_version || "") !== "") fields.proton_version = "";
      return Object.keys(fields).length ? bulkUpdate(game.id, fields) : null;
    },
    (game) => runtimeSnapshot(game, bulkUpdate),
  );
};
const bulkBackupSaves = async (targets: GameRecord[]) => {
  if (bulkSavesBusy.value) return;
  bulkSavesBusy.value = true;
  try {
    const counts = await runBulkSaveBackup(targets, (id) => api.backupGameSaves(id));
    const summary = `${counts.backedUp} backed up, ${counts.skipped} skipped${counts.failed ? `, ${counts.failed} failed` : ""}.`;
    if (counts.backedUp || !counts.failed) notify(summary, counts.failed ? "info" : "success", "Save backup");
    else notifyError(summary);
  } finally { bulkSavesBusy.value = false; }
};
const bulkAction = async (action: string, value = "") => {
  if (bulkBusy.value) return;
  const games = [...selectedGames.value];
  if (action === "remove") { scheduleRemoval(games); return; }
  if (action === "uninstall") { openUninstall(games); return; }
  if (action === "move") { openMove(games); return; }
  if (action === "backup-saves") { await bulkBackupSaves(games); return; }
  if (action === "launch-mode") {
    const running = runningGameIds.value;
    await runBulkFields(games, (game) => launchModeApplicable(game, value, running) ? bulkUpdate(game.id, { launch_mode: value }) : null, (game) => launchModeSnapshot(game, bulkUpdate));
    return;
  }
  if (action === "sandbox-on" || action === "sandbox-off") {
    const enable = action === "sandbox-on";
    const affected = games.filter((game) => !!game.sandbox_enabled !== enable).length;
    if (!enable && affected && !(await confirmAction({ title: "Turn sandbox off", message: `Turn off the sandbox for ${affected} game${affected === 1 ? "" : "s"}? They will launch with full access to your files.`, confirmLabel: "Sandbox off", danger: true }))) return;
    await runBulkFields(games, (game) => !!game.sandbox_enabled === enable ? null : bulkUpdate(game.id, { sandbox_enabled: enable }), (game) => sandboxSnapshot(game, bulkUpdate));
    return;
  }
  if (action === "add-tag" || action === "remove-tag") {
    await runBulkFields(games, (game) => {
      const csv = action === "add-tag" ? addTagCsv(game, value) : removeTagCsv(game, value);
      return csv === null ? null : bulkUpdate(game.id, { tags: csv, tags_user_edited: true });
    }, (game) => tagsSnapshot(game, bulkUpdate));
    return;
  }
  if (action === "engine") {
    await runBulkFields(games, (game) => isEngine(game.engine, value) ? null : bulkUpdate(game.id, { engine: value }), (game) => engineSnapshot(game, bulkUpdate));
    return;
  }
  if (action === "refresh-info") {
    const ids = games.filter((game) => !!game.f95_url).map((game) => game.id);
    if (!ids.length || bulkChecking.value) return;
    bulkBusy.value = true;
    try {
      const result = await api.checkAllUpdates(ids, { refreshInfo: true });
      if (!result.success) throw new Error(result.error || "Could not start the F95 info refresh");
      bulkChecking.value = true;
      notify(`Refreshing F95 info for ${ids.length} games; ${games.length - ids.length} skipped.`, "info", "F95 info refresh", { label: "Open Updates", run: () => { void router.push("/updates"); } });
    } catch (error) { notifyError(String(error)); } finally { bulkBusy.value = false; }
    return;
  }
  if (action === "check") {
    const ids = games.filter((game) => !!game.f95_url).map((game) => game.id);
    if (!ids.length || bulkChecking.value) return;
    bulkBusy.value = true;
    try {
      const result = await api.checkAllUpdates(ids);
      if (!result.success) throw new Error(result.error || "Could not start update check");
      bulkChecking.value = true;
      notify(`Checking ${ids.length} games; ${games.length - ids.length} skipped.`, "info", "Update check", { label: "Open Updates", run: () => { void router.push("/updates"); } });
    } catch (error) { notifyError(String(error)); } finally { bulkBusy.value = false; }
    return;
  }
  bulkBusy.value = true;
  try {
    // One library refresh for the whole batch, not one per game.
    const result = await api.withLibraryBatch(() => runBulk(games, async (game) => {
      if (action === "status") return normalizePlayStatus(game.play_status, game.status) === value ? null : api.updateGame(game.id, { play_status: value });
      if (action === "favorite" || action === "unfavorite") {
        const favorite = action === "favorite";
        return !!game.is_favorite === favorite ? null : api.updateGame(game.id, { is_favorite: favorite });
      }
      if (action === "add" || action === "remove-collection") {
        const old = game.collections || [];
        const has = old.some((name) => name.toLowerCase() === value.toLowerCase());
        if ((action === "add") === has) return null;
        return api.setGameCollections(game.id, action === "add" ? [...old, value] : old.filter((name) => name.toLowerCase() !== value.toLowerCase()));
      }
      if (!hasAvailableUpdate(game)) return null;
      return action === "updated" ? api.markGameUpdated(game) : api.updateGame(game.id, { skipped_version: game.latest_version || "" });
    }, (game) => {
      if (action === "status") return statusSnapshot(game, (id, fields) => api.updateGame(id, fields));
      if (action === "updated") return versionSnapshot(game, (id, fields) => api.updateGame(id, fields));
      if (action === "add" || action === "remove-collection") { const old = [...(game.collections || [])]; return () => api.setGameCollections(game.id, old); }
      const fields = action === "skip" ? { skipped_version: game.skipped_version || "" } : { is_favorite: !!game.is_favorite };
      return () => api.updateGame(game.id, fields);
    }));
    if (result.changed) notifyUndo(`${result.changed} changed, ${result.skipped} skipped${result.failed ? `, ${result.failed} failed` : ""}.`, () => api.withLibraryBatch(result.undo));
    else notifyError(`No games changed; ${result.skipped} skipped, ${result.failed} failed.`);
  } finally { bulkBusy.value = false; }
};

// Tab counts show each collection's size and ignore the active filters.
const collectionCounts = computed(() => {
  const valid = new Set(userCollections.value.map((name) => name.toLowerCase()));
  const counts = new Map<string, number>();
  games.value.forEach((game) =>
    (game.collections || []).forEach((name) => {
      const key = name.toLowerCase();
      if (!valid.has(key)) return;
      counts.set(key, (counts.get(key) || 0) + 1);
    }),
  );
  return counts;
});

const collectionCount = (name: string): number =>
  collectionCounts.value.get(name.toLowerCase()) || 0;

const applyCollectionsFromPayload = (collections: string[] | undefined) => {
  if (Array.isArray(collections)) userCollections.value = [...collections];
};

const startInlineCollectionCreate = async () => {
  collectionError.value = "";
  showNewCollectionInline.value = true;
  await nextTick();
  newCollectionInput.value?.focus();
};

const cancelInlineCollectionCreate = () => {
  showNewCollectionInline.value = false;
  newCollectionName.value = "";
  collectionError.value = "";
};

const createCollectionFromTab = async () => {
  const name = newCollectionName.value.trim();
  if (!name) {
    collectionError.value = "Enter a collection name.";
    return;
  }
  const result = await api.createCollection(name);
  if (!result.success) {
    collectionError.value = result.error || "Could not create the collection.";
    return;
  }
  applyCollectionsFromPayload(result.collections);
  cancelInlineCollectionCreate();
};

let collectionTabMenuTrigger: HTMLElement | null = null;
const openCollectionTabMenu = async (name: string, event: MouseEvent | KeyboardEvent) => {
  event.preventDefault();
  collectionTabMenuTrigger =
    (event.target as HTMLElement)?.closest?.("button") ||
    (event.currentTarget as HTMLElement) ||
    null;
  const rect = collectionTabMenuTrigger?.getBoundingClientRect?.();
  const rawX = event instanceof MouseEvent && event.clientX ? event.clientX : rect ? rect.left : 8;
  const rawY = event instanceof MouseEvent && event.clientY ? event.clientY : rect ? rect.bottom : 8;
  collectionTabMenu.value = { name, x: Math.max(8, rawX), y: Math.max(8, rawY) };
  await nextTick();
  // The menu is 15rem wide: clamp against its measured size so Rename and
  // Delete stay inside the window next to a tab near the right edge.
  const menu = collectionTabMenuElement.value;
  if (collectionTabMenu.value && menu) {
    collectionTabMenu.value = {
      ...collectionTabMenu.value,
      x: Math.max(
        8,
        Math.min(collectionTabMenu.value.x, window.innerWidth - menu.getBoundingClientRect().width - 8),
      ),
      y: Math.max(
        8,
        Math.min(collectionTabMenu.value.y, window.innerHeight - menu.getBoundingClientRect().height - 8),
      ),
    };
  }
  menu?.querySelector<HTMLButtonElement>("button")?.focus();
};

const closeCollectionTabMenu = (restoreFocus = true) => {
  if (!collectionTabMenu.value) return;
  collectionTabMenu.value = null;
  if (restoreFocus) collectionTabMenuTrigger?.focus();
};

const startCollectionRename = async (name: string) => {
  closeCollectionTabMenu(false);
  renamingCollection.value = name;
  renameCollectionDraft.value = name;
  await nextTick();
  const input = collectionTabsElement.value?.querySelector<HTMLInputElement>(
    "[data-rename-input]",
  );
  input?.focus();
  input?.select();
};

const cancelCollectionRename = () => {
  renamingCollection.value = null;
  renameCollectionDraft.value = "";
  collectionError.value = "";
};

const commitCollectionRename = async () => {
  const oldName = renamingCollection.value;
  const newName = renameCollectionDraft.value.trim();
  if (!oldName) return;
  if (!newName || newName === oldName) {
    cancelCollectionRename();
    return;
  }
  const result = await api.renameCollection(oldName, newName);
  if (!result.success) {
    collectionError.value = result.error || "Could not rename the collection.";
    return;
  }
  applyCollectionsFromPayload(result.collections);
  if (activeCollection.value.toLowerCase() === oldName.toLowerCase()) {
    activeCollection.value = newName;
  }
  cancelCollectionRename();
};

const deleteCollectionFromTab = async (name: string) => {
  closeCollectionTabMenu(false);
  const count = collectionCount(name);
  const membershipNote = count
    ? ` Its ${count} game${count === 1 ? "" : "s"} stay in your library.`
    : " Games stay in your library.";
  if (!(await confirmAction({ title: "Delete collection", message: `Delete the collection "${name}"?${membershipNote}`, confirmLabel: "Delete", danger: true }))) return;
  const result = await api.deleteCollection(name);
  if (!result.success) {
    notifyError(result.error || "Could not delete the collection.");
    return;
  }
  if (activeCollection.value.toLowerCase() === name.toLowerCase()) {
    activeCollection.value = "";
  }
  applyCollectionsFromPayload(result.collections);
};

// Arrow keys move focus and selection across the tab strip.
const handleCollectionTabsKeyboard = (event: KeyboardEvent) => {
  const target = event.target as HTMLElement | null;
  // Leave caret keys alone in the inline rename/create controls. No tab button
  // lives inside that form, so this also stops the keys from stealing focus
  // while a rename is being typed.
  if (target?.tagName === "INPUT" || target?.closest("form")) return;
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [
    ...(collectionTabsElement.value?.querySelectorAll<HTMLButtonElement>(
      "[data-collection-tab]",
    ) ?? []),
  ];
  if (!tabs.length) return;
  event.preventDefault();
  const index = tabs.indexOf(document.activeElement as HTMLButtonElement);
  let next: number;
  if (event.key === "ArrowLeft") next = index === -1 ? 0 : (index - 1 + tabs.length) % tabs.length;
  else if (event.key === "ArrowRight") next = index === -1 ? 0 : (index + 1) % tabs.length;
  else if (event.key === "Home") next = 0;
  else next = tabs.length - 1;
  const tab = tabs[next];
  tab?.focus();
  activeCollection.value = tab?.dataset.collection ?? "";
};

const scrollSelectedCollectionTabIntoView = async () => {
  await nextTick();
  collectionTabsElement.value
    ?.querySelector('[aria-selected="true"]')
    ?.scrollIntoView({ block: "nearest", inline: "nearest" });
};

const gameInCollection = (game: GameRecord, name: string): boolean => {
  const key = name.toLowerCase();
  return (game.collections || []).some((entry) => entry.toLowerCase() === key);
};

// The context menu holds its own copy of the game, so membership is written to
// both it and the library list. Updating before the save lets a second toggle
// build on the first even while the first is still in flight.
const setLocalMembership = (gameId: number, names: string[]) => {
  gamesStore.patch(gameId, { collections: names });
  if (contextMenu.value?.game.id === gameId) contextMenu.value.game.collections = names;
};

const toggleGameCollection = async (game: GameRecord, name: string) => {
  const key = name.toLowerCase();
  const current =
    games.value.find((entry) => entry.id === game.id)?.collections || game.collections || [];
  const next = current.some((entry) => entry.toLowerCase() === key)
    ? current.filter((entry) => entry.toLowerCase() !== key)
    : [...current, name];
  setLocalMembership(game.id, next);
  const result = await api.setGameCollections(game.id, next);
  if (!result.success) {
    notifyError(result.error || "Could not update the collection.");
    // Undo the optimistic change first so the open menu is right even if the
    // reload fails, then take whatever the database actually stored.
    setLocalMembership(game.id, current);
    await loadGames();
    const refreshed = games.value.find((entry) => entry.id === game.id);
    if (refreshed) setLocalMembership(game.id, refreshed.collections || []);
  }
};

const createCollectionForGame = async (game: GameRecord, name: string) => {
  const result = await api.createCollection(name, game.id);
  if (!result.success) return result.error || "Could not create the collection.";
  applyCollectionsFromPayload(result.collections);
  await loadGames();
  const refreshed = games.value.find((entry) => entry.id === game.id);
  if (refreshed) setLocalMembership(game.id, refreshed.collections || []);
  return "";
};

const contextCreateForGame = async (game: GameRecord) => {
  const name = contextNewCollectionName.value.trim();
  if (!name) {
    contextNewCollectionError.value = "Enter a collection name.";
    return;
  }
  const error = await createCollectionForGame(game, name);
  if (error) {
    contextNewCollectionError.value = error;
    return;
  }
  contextNewCollectionName.value = "";
  contextNewCollectionError.value = "";
};

// Saved filter presets.
const applyFilterPreset = (preset: LibraryFilterPreset) => {
  const normalized = normalizeFilterPreset(preset, {
    validEngines: uniqueEngines.value,
    validTags: uniqueTags.value,
    validStatuses: allStatuses.value.map((status) => status.value),
  });
  if (!normalized) return;
  filterCollection.value = normalized.filterCollection;
  filterStatuses.value = [...normalized.filterStatuses];
  filterInstallState.value = normalized.filterInstallState;
  filterSources.value = [...normalized.filterSources];
  filterEngines.value = [...normalized.filterEngines];
  filterTags.value = [...normalized.filterTags];
  sortBy.value = normalized.sortBy;
  sortDir.value = normalized.sortDir;
};

const saveFilterPreset = async () => {
  const name = newPresetName.value.trim();
  if (!name || name.length > 40) {
    presetError.value = "Preset names must contain 1–40 characters.";
    return;
  }
  if (!presetsLoaded.value) {
    const loaded = await readPresetsForSave(
      () => api.getSettings(),
      savedPresets.value,
      false,
    );
    if (!loaded.loaded) {
      presetError.value =
        "Could not load your saved filters, so the preset was not saved. Try again.";
      return;
    }
    savedPresets.value = loaded.presets;
    presetsLoaded.value = true;
  }
  const existing = savedPresets.value.find(
    (preset) => preset.name.toLowerCase() === name.toLowerCase(),
  );
  if (existing && !(await confirmAction({ title: "Replace saved filter", message: `Replace the saved filter "${name}"?`, confirmLabel: "Replace", danger: true }))) return;
  const preset: LibraryFilterPreset = {
    name,
    filterCollection: filterCollection.value,
    filterStatuses: [...filterStatuses.value],
    filterInstallState: filterInstallState.value,
    filterSources: [...filterSources.value],
    filterEngines: [...filterEngines.value],
    filterTags: [...filterTags.value],
    sortBy: sortBy.value,
    sortDir: sortDir.value,
  };
  const next = existing
    ? savedPresets.value.map((entry) => (entry === existing ? preset : entry))
    : [...savedPresets.value, preset];
  const result = await api.saveLibraryFilterPresets(next);
  if (!result.success) {
    presetError.value = result.error || "Could not save the filter.";
    return;
  }
  savedPresets.value = next;
  newPresetName.value = "";
  presetError.value = "";
  savePresetOpen.value = false;
  void nextTick(() => savePresetButton.value?.focus());
};

const openSavePreset = async () => {
  presetError.value = "";
  savePresetOpen.value = true;
  await nextTick();
  if (savePresetInput.value) controllerFocusText(savePresetInput.value);
};

// The chip row (and its save field) disappears when the last filter is removed.
watch(() => activeFilterChips.value.length, (count) => {
  if (!count && savePresetOpen.value) { savePresetOpen.value = false; newPresetName.value = ""; presetError.value = ""; }
});

const closeSavePreset = () => {
  savePresetOpen.value = false;
  newPresetName.value = "";
  presetError.value = "";
  void nextTick(() => savePresetButton.value?.focus());
};

const deleteFilterPreset = async (name: string) => {
  if (!(await confirmAction({ title: "Delete saved filter", message: `Delete the saved filter "${name}"?`, confirmLabel: "Delete", danger: true }))) return;
  const next = savedPresets.value.filter((preset) => preset.name !== name);
  const result = await api.saveLibraryFilterPresets(next);
  if (!result.success) {
    presetError.value = result.error || "Could not delete the filter.";
    return;
  }
  savedPresets.value = next;
  presetError.value = "";
  // The section hides with its last preset, so focus moves to Quick filters.
  if (!next.length) {
    await nextTick();
    quickFiltersList.value?.querySelector<HTMLElement>("button")?.focus();
  }
};

const updatingId = ref<number | null>(null);
const modalUpdateState = ref<ModalUpdateState>({
  running: false,
  type: "",
  message: "",
});
let modalUpdateTimeout: ReturnType<typeof setTimeout> | null = null;
// The game the modal's update check and its notice belong to.
const modalUpdateGameId = ref<number | null>(null);
const idleUpdateState: ModalUpdateState = { running: false, type: "", message: "" };
const shownModalUpdateState = computed(() =>
  selectedGame.value && modalUpdateGameId.value === selectedGame.value.id ? modalUpdateState.value : idleUpdateState,
);

const clearModalUpdateState = () => {
  if (modalUpdateTimeout) {
    clearTimeout(modalUpdateTimeout);
    modalUpdateTimeout = null;
  }
  modalUpdateState.value = { running: false, type: "", message: "" };
};

const showModalUpdateNotice = (type: UpdateNotice["type"], message: string) => {
  if (modalUpdateTimeout) {
    clearTimeout(modalUpdateTimeout);
  }
  modalUpdateState.value = { running: false, type, message };
  modalUpdateTimeout = setTimeout(() => {
    modalUpdateState.value = { running: false, type: "", message: "" };
    modalUpdateTimeout = null;
  }, 5000);
};

const closeLaunchTargetMenus = () => {
  openLaunchTargetMenuId.value = null;
};

const toggleLaunchTargetMenu = (game: GameRecord) => {
  openLaunchTargetMenuId.value =
    openLaunchTargetMenuId.value === game.id ? null : game.id;
};

const loadGames = async () => {
  if (!gamesLoaded.value) loadError.value = null;
  if (!(await gamesStore.refresh())) {
    if (!gamesLoaded.value) loadError.value = "Your library could not be loaded.";
    return;
  }
  loadError.value = null;
  if (selectedGame.value) {
    const selectedId = selectedGame.value.id;
    const updated = games.value.find((g) => g.id === selectedId);
    // An unchanged game keeps its object, so the open modal is left alone.
    if (updated && updated !== toRaw(selectedGame.value)) selectedGame.value = updated;
  }
};

const loadRunningGames = () => refreshRunningGames(() => api.getRunningGames());

// Sync and imports can replace the collection list and presets, not just games.
let settingsRequest = 0;
const loadLibrarySettings = async () => {
  const request = ++settingsRequest;
  try {
    const settings = await api.getSettings();
    // A slow older read must not overwrite collections or presets that a
    // newer sync/import refresh already applied.
    if (request !== settingsRequest) return;
    customStatuses.value = settings.custom_play_statuses || [];
    customStatusesLoaded.value = true;
    applyCollectionsFromPayload(settings.collections);
    savedPresets.value = settings.library_filter_presets || [];
    presetsLoaded.value = true;
  } catch (error) {
    console.error("Failed to load library settings", error);
  }
};

const handleLibraryRefresh = () => {
  void loadGames();
  void loadRunningGames();
  void loadLibrarySettings();
};

const openDetail = (game: GameRecord) => {
  closeContextMenu(false);
  clearModalUpdateState();
  selectedGame.value = game;
  showDetailModal.value = true;
};

const handleGameUpdated = async () => {
  clearModalUpdateState();
  showDetailModal.value = false;
  selectedGame.value = null;
  await loadGames();
};



const handleLaunchTargetsChanged = async () => {
  await loadGames();
};

const runSingleGameUpdateCheck = async (game: GameRecord) => {
  if (!game.f95_url) {
    return {
      type: "error" as const,
      message: `${game.title}: missing F95 URL.`,
    };
  }

  updatingId.value = game.id;
  try {
    const result = await api.checkForUpdates(game.f95_url);
    if (result && result.success) {
      await loadGames();
      if (result.newly_finished) {
        notify(`${game.title} is finished`, "success");
        return { type: "" as const, message: "" };
      }
      if (result.has_update) {
        return {
          type: "success" as const,
          message: `${game.title}: new version ${result.version} available.`,
        };
      }

      return {
        type: "success" as const,
        message: `${game.title}: no new update found (${result.version}).`,
      };
    }

    const reason = result?.error || "Update check failed";
    return {
      type: "error" as const,
      message: `${game.title}: ${reason}`,
    };
  } catch (e) {
    console.error("Update check failed", e);
    return {
      type: "error" as const,
      message: `${game.title}: ${String(e) || "Update check failed"}`,
    };
  } finally {
    updatingId.value = null;
  }
};

const launchGameFast = async (game: GameRecord, exePath = game.exe_path) => {
  if (isWishlisted(game)) {
    openDetail(game);
    return;
  }
  closeLaunchTargetMenus();
  try {
    const result = await api.launchGame(
      game.id,
      exePath,
      game.command_line_args || "",
      game.run_japanese_locale || false,
      game.run_wayland || false,
      game.auto_inject_ce || false,
      game.custom_prefix || "",
      game.proton_version || "",
      game.launch_mode || "auto",
    );
    if (result && !result.success) {
      if (result.code === WEB_MEDIA_UNSUPPORTED || result.code === WEB_MEDIA_CONVERTING) {
        openWebMediaDialog(game, exePath, result);
        return;
      }
      if (result.code === 'umu_missing' || result.code === 'umu_runtime_missing') {
        notify(result.error || 'Proton needs umu setup.', 'error', 'Could not launch game', { label: 'Open Proton settings', run: () => { void router.push('/settings#umu'); } });
        return;
      }
      const sandboxError = sandboxLaunchError(result.code || result.error_code, result.error);
      if (sandboxError) notify(sandboxError.message, "error", "Could not launch game", sandboxError.openSettings ? { label: "Open Sandbox settings", run: () => { void router.push('/settings#sandbox'); } } : undefined);
      else notifyError(`Failed to launch game:\n\n${result.error}`);
    } else if (result?.success) {
      if (result.notice) notify(launchNoticeMessage(result.notice), "info", "Game launched");
      // Reconcile with the registry: HTML is untracked and short commands may already have exited.
      await loadRunningGames();
    }
  } catch (e) {
    console.error("Launch failed", e);
  }
};

const openWebMediaDialog = (game: GameRecord, exePath: string, result: LaunchGameResponse) => {
  webMediaGame.value = game;
  webMediaTarget.value = exePath;
  webMediaInfo.value = result.media ?? null;
  webMediaCode.value = result.code || "";
  if (result.code === WEB_MEDIA_CONVERTING) webMediaJobs.follow(game.id, game.title, () => void launchGameFast(game, exePath));
  webMediaOpen.value = true;
};

const relaunchWebMediaGame = async () => {
  const game = webMediaGame.value;
  webMediaOpen.value = false;
  if (game) await launchGameFast(game, webMediaTarget.value);
};

const webMediaConvert = async () => {
  const game = webMediaGame.value;
  if (!game) return;
  const exePath = webMediaTarget.value;
  try {
    const result = await webMediaJobs.start(game.id, game.title, () => void launchGameFast(game, exePath));
    if (!result.success) {
      notifyError(result.error || "Could not start the conversion.");
      webMediaOpen.value = false;
      return;
    }
    webMediaDialog.value?.showProgress();
  } catch (error) {
    notifyError(String(error));
    webMediaOpen.value = false;
  }
};

const webMediaUseBrowser = async () => {
  const game = webMediaGame.value;
  if (!game) return;
  try {
    const result = await api.updateGame(game.id, { html_player: "browser" });
    if (result.success === false) { notifyError(result.error || "Could not change where the game opens."); webMediaOpen.value = false; return; }
    await loadGames();
    await relaunchWebMediaGame();
  } catch (error) {
    notifyError(String(error));
    webMediaOpen.value = false;
  }
};

const webMediaPlayAnyway = async () => {
  const game = webMediaGame.value;
  if (!game) return;
  try {
    const result = await api.setWebMediaPlayAnyway(game.id);
    if (result.success === false) { notifyError(result.error || "Could not start the game."); webMediaOpen.value = false; return; }
    await relaunchWebMediaGame();
  } catch (error) {
    notifyError(String(error));
    webMediaOpen.value = false;
  }
};

const stopGame = async (gameId: number) => {
  try {
    const result = await api.stopGame(gameId);
    if (result.success === false) notifyError(result.error || "Could not stop game");
  } catch (error) {
    notifyError(String(error));
  }
};

const toggleGame = (game: GameRecord) => isWishlisted(game)
  ? openDetail(game)
  : runningGameIds.value.has(game.id) ? stopGame(game.id) : launchGameFast(game);

const launchSelectedTarget = async (
  game: GameRecord,
  target: EffectiveLaunchTarget,
) => {
  closeLaunchTargetMenus();
  await launchGameFast(game, target.exe_path);
};

const launchTargetFromSelect = (game: GameRecord, event: Event) => {
  const select = event.target as HTMLSelectElement;
  const target = getEffectiveLaunchTargets(game).find(
    (entry) => entry.id === select.value,
  );
  select.value = "";
  if (target) void launchSelectedTarget(game, target);
};

// Handle incoming extension import
watch(
  () => route.query,
  async (q) => {
    if (!q) return;
    const action = readQueryValue(q.action);
    const queryUrl = readQueryValue(q.f95url);
    if (action === "import" && (queryUrl || readStoreLinks(readQueryValue(q.links)).length)) {
      await loadGames();
      // Keep the import query until the dialog closes so its archive hand-off
      // can identify the extension entry point and retain all seed metadata.
      showAddModal.value = true;
    }
    // Ctrl+N from any view (App.vue).
    if (action === "add") {
      showAddModal.value = true;
      void router.replace({ path: "/", query: {} });
    }
    if (action === "open" && (queryUrl || extensionGameId(q.gameId))) {
      // Fetch directly: on mount, the view's own loadGames() supersedes one awaited here.
      try {
        await gamesStore.refresh();
        const match = games.value.find((g) =>
          extensionGameId(q.gameId) ? g.id === extensionGameId(q.gameId) : urlsMatchByThreadIdentity(g.f95_url, queryUrl),
        );
        if (match && showDetailModal.value && selectedGame.value && selectedGame.value.id !== match.id) {
          // Switch games through the modal's own close so unsaved edits get the
          // discard prompt and the next open reloads every per-game panel.
          if (await detailModal.value?.requestClose()) {
            await nextTick();
            openDetail(match);
          }
        } else if (match) {
          openDetail(match);
        }
      } catch (e) {
        console.error("Failed to open game from URL", e);
      }
      // Consume the request: an identical route push is ignored as a duplicate,
      // so a leftover query would swallow the next "Open in wLib" for this game.
      void router.replace({ path: "/", query: {} });
    }
  },
  { immediate: true },
);

const addingGame = ref(false);

const handleAddGame = async (gameData: AddGamePayload) => {
  if (addingGame.value) return;
  addingGame.value = true;
  try {
    const result = await api.addGame(
      gameData.title,
      gameData.exe_path,
      gameData.f95_url || "",
      gameData.version || "",
      gameData.cover_image || "",
      gameData.tags || "",
      gameData.rating || "",
      gameData.developer || "",
      gameData.engine || "",
      false, // run_japanese_locale
      false, // run_wayland
      false, // auto_inject_ce
      "", // custom_prefix
      "", // proton_version
      gameData.launch_mode || "auto",
      gameData.command_line_args || "",
      gameData.links || [],
    );
    if (result && result.success === false) {
      notifyError(`Failed to add game:\n\n${result.error || "Unknown error"}`);
      return;
    }

    if (result && result.id) {
      showAddModal.value = false;
      router.replace({ path: "/", query: {} });
      await loadGames();
    }
  } catch (e) {
    console.error("Failed to add game", e);
  } finally {
    addingGame.value = false;
  }
};

const checkUpdate = async (game: GameRecord) => {
  if (updatingId.value !== null) return;
  const feedback = await runSingleGameUpdateCheck(game);
  if (feedback.type === "error") notifyError(feedback.message);
  else if (feedback.type === "success") notify(feedback.message, "success");
};

const markAsUpdated = async (game: GameRecord) => {
  if (markingUpdatedIds.value.has(game.id)) return;
  markingUpdatedIds.value.add(game.id);
  try {
    const result = await runBulk([game], (item) => api.markGameUpdated(item),
      (item) => versionSnapshot(item, (id, fields) => api.updateGame(id, fields)));
    if (result.failed || !result.changed) throw new Error("Could not mark game as updated");
    notifyUndo(`${game.title}: installed version set to ${game.latest_version?.trim()}.`, result.undo);
  } catch (error) {
    notifyError(String(error));
  } finally {
    markingUpdatedIds.value.delete(game.id);
  }
};

const toggleSkippedVersion = async (game: GameRecord) => {
  const skipped = isUpdateSkipped(game);
  const result = await runBulk([game], (item) => api.updateGame(item.id, {
    skipped_version: skipped ? "" : item.latest_version || "",
  }), (item) => {
    const previous = item.skipped_version || "";
    return () => api.updateGame(item.id, { skipped_version: previous });
  });
  if (result.failed) throw new Error("Could not change skipped version");
  notifyUndo(skipped ? `${game.title}: stopped skipping ${game.skipped_version}.`
    : `${game.title}: skipping version ${game.latest_version}.`, result.undo);
};

const closeContextMenu = (restoreFocus = true) => {
  if (!contextMenu.value) return;
  contextMenu.value = null;
  contextToolsRequest++;
  contextTools.value = [];
  contextMenuView.value = "actions";
  contextNewCollectionName.value = "";
  contextNewCollectionError.value = "";
  if (restoreFocus) contextMenuTrigger?.focus();
};

const openContextMenu = async (
  game: GameRecord,
  event: MouseEvent | KeyboardEvent,
) => {
  if (
    event instanceof KeyboardEvent &&
    event.key !== "ContextMenu" &&
    !(event.shiftKey && event.key === "F10")
  )
    return;
  event.preventDefault();
  closeLaunchTargetMenus();
  contextMenuTrigger =
    (event.target as HTMLElement).closest<HTMLButtonElement>("button") ||
    (event.currentTarget as HTMLElement);
  const rect = contextMenuTrigger.getBoundingClientRect();
  contextMenu.value = {
    game,
    x: event instanceof MouseEvent ? event.clientX : rect.left,
    y: event instanceof MouseEvent ? event.clientY : rect.bottom,
  };
  void loadContextTools(game.id);
  await nextTick();
  if (!contextMenu.value || !contextMenuElement.value) return;
  const menu = contextMenuElement.value;
  contextMenu.value.x = Math.max(
    8,
    Math.min(contextMenu.value.x, window.innerWidth - menu.getBoundingClientRect().width - 8),
  );
  contextMenu.value.y = Math.max(
    8,
    Math.min(contextMenu.value.y, window.innerHeight - menu.getBoundingClientRect().height - 8),
  );
  menu.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
};

const handleContextMenuKeyboard = (event: KeyboardEvent) => {
  const targetTag = (event.target as HTMLElement).tagName;
  if ((targetTag === "SELECT" || targetTag === "INPUT") && event.key !== "Tab")
    return;
  // The collections view also holds a checkbox and a text input; without them
  // Tab would only ever cycle between Back and Add.
  const controls = [
    ...contextMenuElement.value!.querySelectorAll<HTMLElement>(
      "button:not(:disabled), select, input:not(:disabled)",
    ),
  ];
  const index = controls.indexOf(document.activeElement as HTMLElement);
  let next: number;
  if (event.key === "ArrowDown" || (event.key === "Tab" && !event.shiftKey))
    next = (index + 1) % controls.length;
  else if (event.key === "ArrowUp" || (event.key === "Tab" && event.shiftKey))
    next = (index - 1 + controls.length) % controls.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = controls.length - 1;
  else return;
  event.preventDefault();
  controls[next]?.focus();
};

const runContextAction = async (
  action:
    | "play"
    | "folder"
    | "thread"
    | "check"
    | "mark-updated"
    | "skip-version"
    | "favorite"
    | "remove"
    | "status",
  status = "",
) => {
  const game = contextMenu.value?.game;
  if (!game) return;
  closeContextMenu();
  try {
    if (action === "play") return await toggleGame(game);
    if (action === "check") return await checkUpdate(game);
    if (action === "mark-updated") return await markAsUpdated(game);
    if (action === "skip-version") return await toggleSkippedVersion(game);
    if (action === "remove") { scheduleRemoval([game]); return; }
    const result =
      action === "folder"
        ? await api.openFolder(gameFolder(game.exe_path))
        : action === "thread"
          ? await api.openInBrowser(gamePageUrl(game))
          : await api.updateGame(
                game.id,
                action === "favorite"
                  ? { is_favorite: !game.is_favorite }
                  : { play_status: status },
              );
    if (!result.success) notifyError(result.error || "Game action failed");
  } catch (error) {
    notifyError(String(error));
  }
};

const handleDocumentClick = () => {
  closeLaunchTargetMenus();
  closeCollectionTabMenu(false);
  closeContextMenu(false);
};
const handleLibraryScroll = (event: Event) => {
  growRenderedGames(event.target);
  if (!contextMenuElement.value?.contains(event.target as Node))
    closeContextMenu(false);
  if (!collectionTabMenuElement.value?.contains(event.target as Node))
    closeCollectionTabMenu(false);
};
const handleLibraryKeyboard = (event: KeyboardEvent) => {
  if (modalStack.length) return;
  if (contextMenu.value && event.key === "Escape") {
    event.preventDefault();
    closeContextMenu();
    return;
  }
  if (collectionTabMenu.value && event.key === "Escape") {
    event.preventDefault();
    closeCollectionTabMenu();
    return;
  }
  if (showAddModal.value || showImportModal.value || showDetailModal.value || contextMenu.value || collectionTabMenu.value) return;
  if (event.key === "Escape" && openLaunchTargetMenuId.value !== null) {
    closeLaunchTargetMenus();
    event.preventDefault();
    return;
  }
  if (event.key === "Escape" && (selectedIds.value.size || selecting.value)) {
    clearSelection(); event.preventDefault(); return;
  }
  const typing =
    event.target instanceof Element &&
    event.target.closest("input, textarea, select, [contenteditable='true']");
  if (
    ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") ||
    (event.key === "/" &&
      !typing &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey)
  ) {
    if (!searchInput.value) return;
    event.preventDefault();
    searchInput.value.focus();
    searchInput.value.select();
  }
};

const handleModalUpdateCheck = async (gameId: number) => {
  const game = games.value.find((entry) => entry.id === gameId) || selectedGame.value;
  if (!game) return;

  modalUpdateGameId.value = game.id;
  modalUpdateState.value = { running: true, type: "", message: "" };
  const feedback = await runSingleGameUpdateCheck(game);
  if (modalUpdateGameId.value !== game.id) return;
  if (!showDetailModal.value || selectedGame.value?.id !== game.id) {
    // Another game is open now: report the result as a toast, not on it.
    clearModalUpdateState();
    if (feedback.type === "error") notifyError(feedback.message);
    else if (feedback.type === "success") notify(feedback.message, "success");
    return;
  }
  showModalUpdateNotice(feedback.type, feedback.message);
};

const handlePlaytimeTick = (event: Event) => {
  const detail = (event as CustomEvent<PlaytimeTickDetail>).detail || {};
  const gameId = Number(detail.gameId);
  const delta = Number(detail.delta);
  if (!Number.isInteger(gameId) || !Number.isFinite(delta) || delta < 0) return;

  const game = games.value.find((g) => g.id === gameId);
  if (!game) return;

  const played = {
    playtime_seconds: (Number(game.playtime_seconds) || 0) + delta,
    last_played: new Date().toISOString(),
    ...(typeof detail.lastPlayedVersion === "string" ? { last_played_version: detail.lastPlayedVersion } : {}),
  };
  gamesStore.patch(gameId, played);

  if (selectedGame.value && selectedGame.value.id === gameId) {
    selectedGame.value = {
      ...selectedGame.value,
      ...played,
      last_played_version: played.last_played_version || game.last_played_version || "",
    };
  }
};

watch(
  [
    layoutMode,
    cardSize,
    sortBy,
    sortDir,
    filterCollection,
    filterStatuses,
    filterInstallState,
    filterSources,
    filterEngines,
    filterTags,
    isFiltersCollapsed,
    filterSections,
    activeCollection,
  ],
  () => {
    persistLibraryViewState();
  },
  { deep: true },
);

// A new filter, sort or layout starts again from the first cards.
watch(
  [layoutMode, sortBy, sortDir, filterCollection, filterStatuses, filterInstallState, filterSources, filterEngines, filterTags, activeCollection, searchQuery],
  () => {
    renderLimit.value = RENDER_BATCH;
  },
  { deep: true },
);

// A saved tab naming a deleted or renamed collection falls back to "All".
watch(userCollections, (collections) => {
  const normalized = normalizeActiveCollection(activeCollection.value, collections);
  if (normalized !== activeCollection.value) activeCollection.value = normalized;
});

watch(activeCollection, () => {
  void scrollSelectedCollectionTabIntoView();
});

watch(uniqueEngines, (engines) => {
  const normalizedEngines = normalizeLibraryViewState(
    { filterEngines: filterEngines.value },
    { validEngines: engines },
  ).filterEngines;

  if (!sameStringArray(normalizedEngines, filterEngines.value)) {
    filterEngines.value = normalizedEngines;
  }
});

watch(uniqueTags, (tags) => {
  const normalizedTags = normalizeLibraryViewState(
    { filterTags: filterTags.value },
    { validTags: tags },
  ).filterTags;

  if (!sameStringArray(normalizedTags, filterTags.value)) {
    filterTags.value = normalizedTags;
  }
});

// A custom status deleted in Settings (or a preset from another device) leaves
// the saved name in the view state. Unlike engines and tags it has no pill to
// uncheck, so drop it here or the library shows "No games found" with every
// filter looking clear.
watch(allStatuses, (statuses) => {
  // Wait for both sources of status names: games may still be loading, and a
  // custom status with no games yet only comes from Settings.
  if (!games.value.length || !customStatusesLoaded.value) return;
  const normalizedStatuses = normalizeFilterAndSort(
    { filterStatuses: filterStatuses.value },
    { validStatuses: statuses.map((status) => status.value) },
  ).filterStatuses;

  if (!sameStringArray(normalizedStatuses, filterStatuses.value)) {
    filterStatuses.value = normalizedStatuses;
  }
});

watch(showDetailModal, (open) => {
  if (!open) {
    clearModalUpdateState();
    selectedGame.value = null;
    void loadGames();
  }
});

watch(showImportModal, (open) => {
  if (!open) {
    void loadGames();
  }
});

onMounted(() => {
  restoreLibraryViewState();
  libraryFiltersPaneOpen.value = !isFiltersCollapsed.value;

  window.addEventListener("wlib-refresh-library", handleLibraryRefresh);
  window.addEventListener("wlib-playtime-tick", handlePlaytimeTick);
  document.addEventListener("click", handleDocumentClick);
  document.addEventListener("keydown", handleLibraryKeyboard);
  document.addEventListener("scroll", handleLibraryScroll, true);
  window.addEventListener("resize", handleDocumentClick);
  window.addEventListener("resize", fillRenderedGames);
  onWebviewReady(() => {
    void loadTagColors().catch(console.error);
    void loadGames();
    void loadRunningGames();
    void loadLibrarySettings();
  });
});

onUnmounted(() => {
  libraryFiltersPaneOpen.value = false;
  settingsRequest++;
  window.removeEventListener("wlib-refresh-library", handleLibraryRefresh);
  window.removeEventListener("wlib-playtime-tick", handlePlaytimeTick);
  window.removeEventListener("resize", fillRenderedGames);
  document.removeEventListener("click", handleDocumentClick);
  document.removeEventListener("keydown", handleLibraryKeyboard);
  document.removeEventListener("scroll", handleLibraryScroll, true);
  window.removeEventListener("resize", handleDocumentClick);
  if (modalUpdateTimeout) {
    clearTimeout(modalUpdateTimeout);
  }
});
</script>

<template>
  <div ref="libraryControllerScope" class="h-full flex overflow-hidden">
    <!-- Filters Sidebar -->
    <aside
      :inert="isFiltersCollapsed"
      :aria-hidden="isFiltersCollapsed"
      :class="[
        'filters-pane shrink-0 h-full collapse-width-transition',
        isFiltersCollapsed ? 'w-0 filters-pane-collapsed' : 'w-64',
      ]"
    >
      <div class="w-64 h-full flex flex-col overflow-y-auto">
        <div class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('collections')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Quick filters</span>
            <span class="text-sm">{{
              filterSections.collections ? "▾" : "▸"
            }}</span>
          </button>
          <div v-show="filterSections.collections" ref="quickFiltersList" class="space-y-1 mt-3">
            <button
              v-for="collection in collections"
              :key="collection.value"
              @click="selectCollection(collection.value)"
              :aria-pressed="filterCollection === collection.value"
              class="w-full min-h-11 flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ui-hover-surface"
              :style="
                filterCollection === collection.value
                  ? 'background: var(--bg-raised); color: var(--text-primary)'
                  : 'color: var(--text-secondary)'
              "
            >
              <component :is="collection.icon" class="w-4 h-4 shrink-0" />
              <span>{{ collection.label }}</span>
              <span
                v-if="collection.value === 'Updates available'"
                class="collection-count ml-auto"
                >{{ updateCount }}</span
              >
            </button>
          </div>
        </div>

        <div v-if="savedPresets.length" class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('presets')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Saved filters</span>
            <span class="text-sm">{{
              filterSections.presets ? "▾" : "▸"
            }}</span>
          </button>
          <div v-show="filterSections.presets" class="space-y-1 mt-3">
            <div
              v-for="preset in savedPresets"
              :key="preset.name"
              class="flex items-center gap-1"
            >
              <button
                @click="applyFilterPreset(preset)"
                class="flex-1 min-w-0 flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors ui-hover-surface text-left"
                style="color: var(--text-secondary)"
                :title="`Apply the saved filter '${preset.name}'`"
              >
                <IconSparkles class="w-4 h-4 shrink-0" />
                <span class="truncate">{{ preset.name }}</span>
              </button>
              <button
                @click="deleteFilterPreset(preset.name)"
                class="ui-icon-btn p-1.5 rounded-lg shrink-0"
                style="color: var(--text-muted)"
                :aria-label="`Delete the saved filter ${preset.name}`"
                title="Delete saved filter"
              >
                <IconX class="w-4 h-4" />
              </button>
            </div>
            <p
              v-if="presetError && !savePresetOpen"
              role="alert"
              class="px-3 text-xs"
              style="color: var(--danger-text)"
            >
              {{ presetError }}
            </p>
          </div>
        </div>

        <div class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('status')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Play Status</span>
            <span class="text-sm">{{ filterSections.status ? "▾" : "▸" }}</span>
          </button>
          <div v-show="filterSections.status" class="space-y-1 mt-3">
            <label
              v-for="s in allStatuses"
              :key="s.value"
              class="flex items-center gap-3 px-3 py-1.5 rounded-lg text-sm cursor-pointer transition-colors ui-hover-surface"
            >
              <input
                type="checkbox"
                :checked="statusFilterActive(s.value)"
                @change="applyStatusFilter(s.value, $event)"
                class="filters-checkbox rounded"
              />
              <span class="ui-status-option" :class="s.toneClass">
                <component :is="s.icon" class="ui-status-icon" />
                <span>{{ s.label }}</span>
              </span>
            </label>
          </div>
        </div>

        <div class="p-6 pb-2 pt-4">
          <label class="block px-2 text-xs uppercase tracking-widest font-bold" style="color: var(--text-muted)">
            Install state
            <select v-model="filterInstallState" class="library-search-input mt-3 w-full rounded-lg px-3 py-2 text-sm normal-case tracking-normal font-medium">
              <option value="">All</option>
              <option value="installed">Installed</option>
              <option value="wishlist">Wishlist</option>
            </select>
          </label>
        </div>

        <div class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('sources')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Sources</span>
            <span class="text-sm">{{ filterSections.sources ? "▾" : "▸" }}</span>
          </button>
          <div v-show="filterSections.sources" class="mt-3">
            <div class="mb-2 flex justify-end">
              <button v-if="filterSources.length" @click="filterSources = []"
                class="cursor-pointer hover:text-red-400 text-[0.625rem] normal-case tracking-normal transition-colors"
                style="color: var(--text-muted)">Clear</button>
            </div>
            <div class="flex flex-wrap gap-1.5">
              <button v-for="source in LIBRARY_SOURCES" :key="source.key"
                @click="toggleFilter(filterSources, source.key)"
                class="filter-tag-btn px-2.5 py-1 rounded-full text-[0.6875rem] font-medium"
                :aria-pressed="filterSources.includes(source.key)"
                :style="filterSources.includes(source.key)
                  ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                  : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'">
                {{ source.label }}
              </button>
            </div>
          </div>
        </div>

        <div class="p-6 pb-2 pt-4">
          <button
            @click="toggleFilterSection('engines')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Engines</span>
            <span class="text-sm">{{ filterSections.engines ? "▾" : "▸" }}</span>
          </button>
          <div v-show="filterSections.engines" class="mt-3">
            <div class="mb-2 flex justify-end">
              <button
                v-if="filterEngines.length"
                @click="filterEngines = []"
                class="cursor-pointer hover:text-red-400 text-[0.625rem] normal-case tracking-normal transition-colors"
                style="color: var(--text-muted)"
              >
                Clear
              </button>
            </div>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="engine in uniqueEngines"
                :key="engine"
                @click="toggleFilter(filterEngines, engine)"
                class="filter-tag-btn px-2.5 py-1 rounded-full text-[0.6875rem] font-medium"
                :style="
                  filterEngines.includes(engine)
                    ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                    : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'
                "
              >
                {{ engine }}
              </button>
              <div
                v-if="!uniqueEngines.length"
                class="text-xs italic"
                style="color: var(--text-muted)"
              >
                No engines found
              </div>
            </div>
          </div>
        </div>

        <div class="p-6 pt-4 flex-1">
          <button
            @click="toggleFilterSection('tags')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Tags</span>
            <span class="text-sm">{{ filterSections.tags ? "▾" : "▸" }}</span>
          </button>
          <div v-show="filterSections.tags" class="mt-3">
            <input
              v-model="tagSearchQuery"
              type="search"
              aria-label="Search tags"
              placeholder="Search tags..."
              class="library-search-input w-full rounded-lg px-3 py-2 text-xs mb-2"
            />
            <p class="text-xs mb-2" style="color: var(--text-muted)">
              Matches all selected tags.
            </p>
            <div class="mb-2 flex justify-end">
              <button
                v-if="filterTags.length"
                @click="filterTags = []"
                class="cursor-pointer hover:text-red-400 text-[0.625rem] normal-case tracking-normal transition-colors"
                style="color: var(--text-muted)"
              >
                Clear
              </button>
            </div>
            <div class="flex flex-wrap gap-1.5 max-h-64 overflow-y-auto">
              <button
                v-for="tag in visibleTags"
                :key="tag"
                @click="toggleTag(tag)"
                @contextmenu="openTagColorPicker($event, tag)"
                @keydown.shift.f10.prevent="openTagColorPicker($event, tag)"
                :data-tag-color="colorForTag(tag)"
                :aria-pressed="hasTagFilter(filterTags, tag)"
                class="tag-pill filter-tag-btn px-2.5 py-1 rounded-full text-[0.6875rem] font-medium"
                :style="
                  colorForTag(tag) ? undefined : hasTagFilter(filterTags, tag)
                    ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                    : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'
                "
              >
                <IconCheck v-if="colorForTag(tag) && hasTagFilter(filterTags, tag)" class="inline w-3 h-3 mr-1" aria-hidden="true" />
                {{ tag }}
                <span class="ml-1 opacity-70">{{ tagCounts.get(tag) }}</span>
              </button>
              <div
                v-if="!visibleTags.length"
                class="text-xs italic"
                style="color: var(--text-muted)"
              >
                {{ uniqueTags.length ? "No matching tags" : "No tags found" }}
              </div>
            </div>
          </div>
        </div>
      </div>
    </aside>

    <!-- Main Content Area -->
    <div class="flex-1 min-w-0 p-5 overflow-y-auto flex flex-col relative">
      <header class="library-header mb-4 shrink-0">
        <div class="library-header-row">
          <h2
            class="ui-page-heading text-3xl font-bold tracking-tight shrink-0"
            style="color: var(--text-primary)"
          >
            <IconDeviceGamepad2 class="ui-page-heading-icon" />
            <span>Your Library</span>
          </h2>
          <p class="library-subtitle text-sm pl-3" aria-hidden="true">
            Manage and play your imported games.
          </p>
          <p class="sr-only">Manage and play your imported games.</p>
          <MenuButton
            split
            class="library-add-menu"
            :items="addMenuItems"
            menu-label="More ways to add"
            button-label="More ways to add"
            align="end"
            @main="showAddModal = true"
            @select="chooseAddMenuItem"
          >
            <template #main>
              <IconLibraryPlus class="ui-action-icon" />
              Add game
            </template>
          </MenuButton>
        </div>
      </header>

      <!-- Collection tabs -->
      <div class="mb-3 shrink-0">
        <div
          ref="collectionTabsElement"
          class="flex items-center gap-1.5 overflow-x-auto collections-tab-strip"
          role="tablist"
          aria-label="Collections"
          @keydown="handleCollectionTabsKeyboard"
        >
          <button
            data-collection-tab=""
            role="tab"
            :aria-selected="activeCollection === ''"
            class="collection-tab shrink-0"
            title="Every game in your library"
            @click="activeCollection = ''"
          >
            All
          </button>
          <template v-for="name in userCollections" :key="name">
            <form
              v-if="renamingCollection === name"
              class="shrink-0 flex items-center gap-1"
              @submit.prevent="commitCollectionRename"
            >
              <input
                data-rename-input
                v-model="renameCollectionDraft"
                type="text"
                aria-label="Collection name"
                class="library-search-input rounded-lg px-2 py-1 text-sm w-36"
                @keydown.escape.prevent="cancelCollectionRename"
              />
              <button type="submit" class="ui-icon-btn p-1 rounded" title="Save name">
                <IconCheck class="w-4 h-4" />
              </button>
              <button
                type="button"
                class="ui-icon-btn p-1 rounded"
                title="Cancel rename"
                @click="cancelCollectionRename"
              >
                <IconX class="w-4 h-4" />
              </button>
            </form>
            <button
              v-else
              data-collection-tab=""
              :data-collection="name"
              role="tab"
              :aria-selected="activeCollection === name"
              class="collection-tab shrink-0"
              :title="`${name} — Shift+F10 to rename or delete`"
              aria-haspopup="menu"
              @click="activeCollection = name"
              @contextmenu.prevent.stop="openCollectionTabMenu(name, $event)"
              @keydown.shift.f10.prevent.stop="openCollectionTabMenu(name, $event)"
              @keydown.context-menu.prevent.stop="openCollectionTabMenu(name, $event)"
            >
              <span class="truncate max-w-40">{{ name }}</span>
              <span class="collection-count">{{ collectionCount(name) }}</span>
            </button>
          </template>
          <form
            v-if="showNewCollectionInline"
            class="shrink-0 flex items-center gap-1"
            @submit.prevent="createCollectionFromTab"
          >
            <input
              ref="newCollectionInput"
              v-model="newCollectionName"
              type="text"
              aria-label="New collection name"
              placeholder="Collection name"
              class="library-search-input rounded-lg px-2 py-1 text-sm w-36"
              @keydown.escape.prevent="cancelInlineCollectionCreate"
            />
            <button type="submit" class="ui-icon-btn p-1 rounded" title="Create collection">
              <IconCheck class="w-4 h-4" />
            </button>
            <button
              type="button"
              class="ui-icon-btn p-1 rounded"
              title="Cancel"
              @click="cancelInlineCollectionCreate"
            >
              <IconX class="w-4 h-4" />
            </button>
          </form>
          <button
            v-else
            class="collection-tab collection-tab-add shrink-0"
            title="New collection"
            aria-label="New collection"
            @click="startInlineCollectionCreate"
          >
            <IconPlus class="w-4 h-4" />
          </button>
        </div>
        <p
          v-if="collectionError"
          role="alert"
          class="text-xs mt-1"
          style="color: var(--danger-text)"
        >
          {{ collectionError }}
        </p>
      </div>

      <!-- Search, sort and view controls share one wrapping row -->
      <div class="library-toolbar mb-4 shrink-0">
        <div class="library-toolbar-search relative">
          <IconZoom
            class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4"
            style="color: var(--text-muted)"
          />
          <input
            ref="searchInput"
            v-model="searchQuery"
            type="text"
            aria-label="Search games"
            placeholder="Search title, developer, tags or engine..."
            class="library-search-input w-full rounded-lg pl-10 pr-10 py-2 text-sm focus:outline-none"
          />
          <button
            v-if="searchQuery"
            @click="
              searchQuery = '';
              searchInput?.focus();
            "
            aria-label="Clear search"
            class="ui-icon-btn absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded"
          >
            <IconX class="w-4 h-4" />
          </button>
        </div>

        <div class="library-toolbar-controls">
          <div v-if="layoutMode !== 'list'" class="flex items-center">
            <MenuButton
              radio
              class="library-sort-menu"
              :items="sortMenuItems"
              menu-label="Sort by"
              :button-label="`Sort by: ${sortFieldLabel}`"
              button-title="Sort by"
              @select="setSortField"
            >
              {{ sortFieldLabel }}
            </MenuButton>
            <button
              type="button"
              class="library-toolbar-btn library-sort-dir ui-action-btn ui-hover-surface"
              :aria-label="sortDir === 'asc' ? 'Sort direction: ascending' : 'Sort direction: descending'"
              :title="sortDir === 'asc' ? 'Ascending. Click to reverse.' : 'Descending. Click to reverse.'"
              @click="reverseSortDir"
            >
              <IconSortAscending v-if="sortDir === 'asc'" class="ui-action-icon" />
              <IconSortDescending v-else class="ui-action-icon" />
            </button>
          </div>

          <button
            @click="toggleFiltersPane"
            class="library-toolbar-btn ui-action-btn relative ui-hover-surface active:scale-95"
            :aria-expanded="!isFiltersCollapsed"
            :title="
              isFiltersCollapsed ? 'Show Filters Pane' : 'Hide Filters Pane'
            "
          >
            <IconFilterFilled class="ui-action-icon" />
            <span>Filters</span>
            <span
              v-if="activeFilterCount > 0"
              class="absolute -top-1.5 -right-1.5 min-w-5 h-5 rounded-full px-1 text-[0.625rem] font-bold flex items-center justify-center"
              style="background: var(--brand); color: var(--on-brand)"
            >
              {{ activeFilterCount }}
            </span>
          </button>
          <button
            type="button"
            class="library-toolbar-btn ui-action-btn ui-hover-surface active:scale-95"
            :aria-pressed="selecting || selectedIds.size > 0"
            @click="selecting = !selecting; if (!selecting) selection.clear()"
          >
            <IconListCheck class="ui-action-icon" />
            <span>Select</span>
          </button>

          <span class="text-xs whitespace-nowrap" style="color: var(--text-muted)"
            >{{ filteredGames.length }} game{{
              filteredGames.length !== 1 ? "s" : ""
            }}</span
          >

          <div v-if="layoutMode !== 'list'" class="flex items-center gap-2" style="color: var(--text-muted)">
            <IconPhotoFilled class="size-3" aria-hidden="true" />
            <input v-model.number="cardSize" type="range" min="0" max="4" step="1" aria-label="Card size"
              class="card-size-control w-20 cursor-pointer" />
            <IconPhotoFilled class="size-5" aria-hidden="true" />
          </div>

          <!-- Layout Toggle -->
          <div
            class="flex items-center rounded-lg p-1"
            style="
              background: var(--bg-surface);
              border: 1px solid var(--border);
            "
          >
            <button
              @click="layoutMode = 'grid'"
              class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm active:scale-95 active:bg-[var(--bg-overlay)]"
              :style="
                layoutMode === 'grid'
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'color: var(--text-muted)'
              "
              title="Grid view" aria-label="Grid view" :aria-pressed="layoutMode === 'grid'"
            >
              <IconColumns2Filled class="ui-action-icon" />
            </button>
            <button
              @click="layoutMode = 'list'"
              class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm active:scale-95 active:bg-[var(--bg-overlay)]"
              :style="
                layoutMode === 'list'
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'color: var(--text-muted)'
              "
              title="List view" aria-label="List view" :aria-pressed="layoutMode === 'list'"
            >
              <IconLayoutListFilled class="ui-action-icon" />
            </button>
            <button
              @click="layoutMode = 'compact'"
              class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm active:scale-95 active:bg-[var(--bg-overlay)]"
              :style="
                layoutMode === 'compact'
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'color: var(--text-muted)'
              "
              title="Compact view" aria-label="Compact view" :aria-pressed="layoutMode === 'compact'"
            >
              <IconLayoutGridFilled class="ui-action-icon opacity-90" />
            </button>
            <button
              @click="layoutMode = 'banner'"
              class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm active:scale-95 active:bg-[var(--bg-overlay)]"
              :style="
                layoutMode === 'banner'
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'color: var(--text-muted)'
              "
              title="Banner view" aria-label="Banner view" :aria-pressed="layoutMode === 'banner'"
            >
              <IconPanoramaHorizontalFilled class="ui-action-icon" />
            </button>
          </div>
        </div>
      </div>

      <div v-if="activeFilterChips.length" class="flex flex-wrap items-center gap-2 px-6 pb-3" role="group" aria-label="Active filters">
        <button v-for="chip in activeFilterChips" :key="chip.key" class="ui-action-btn ui-hover-surface px-3 py-1 rounded-full text-xs" style="color: var(--text-primary); border: 1px solid var(--border); background: var(--bg-raised)" :aria-label="`Remove filter ${chip.label}`" @click="removeFilterChip(chip)">{{ chip.label }} <span aria-hidden="true">×</span></button>
        <button
          v-if="!savePresetOpen"
          ref="savePresetButton"
          class="ui-action-btn px-3 py-1 text-xs"
          style="color: var(--text-secondary)"
          title="Save the current filters and sort under a name"
          @click="openSavePreset"
        >Save as preset</button>
        <form
          v-else
          class="flex items-center gap-1"
          @submit.prevent="saveFilterPreset"
          @keydown.esc.stop.prevent="closeSavePreset"
        >
          <input
            ref="savePresetInput"
            v-model="newPresetName"
            type="text"
            aria-label="Saved filter name"
            placeholder="Preset name"
            maxlength="40"
            class="library-search-input w-40 rounded-lg px-2 py-1 text-xs"
            :aria-invalid="!!presetError"
            :aria-describedby="presetError ? 'save-preset-error' : undefined"
          />
          <button
            type="submit"
            class="ui-action-btn px-2 py-1 rounded-lg text-xs font-semibold border active:scale-95 active:bg-[var(--bg-overlay)]"
            style="color: var(--text-secondary); border-color: var(--border)"
            title="Save the current filters and sort under this name"
          >Save</button>
          <span v-if="presetError" id="save-preset-error" role="alert" class="text-xs" style="color: var(--danger-text)">{{ presetError }}</span>
        </form>
        <button class="ui-action-btn px-3 py-1 text-xs hover:text-red-400" style="color: var(--text-muted)" @click="clearFilters">Clear all</button>
      </div>

      <div v-if="displayState === 'error'" class="empty-state flex-1" role="alert">
        <h3 class="empty-state-title">{{ loadError }}</h3>
        <button class="ui-action-btn px-3 py-2 rounded-lg" @click="loadGames">Retry</button>
      </div>
      <div v-else-if="displayState === 'loading'" class="library-skeletons grid gap-5 pb-12"
        :class="layoutMode === 'list' ? 'library-skeletons--list' : `library-cards--${layoutMode}`"
        role="status" aria-label="Loading library" aria-busy="true">
        <div v-for="n in 12" :key="n" class="library-skeleton rounded-xl"
          :class="`library-skeleton--${layoutMode}`" aria-hidden="true"></div>
      </div>
      <div v-else-if="displayState === 'empty'" class="empty-state flex-1">
        <IconDeviceGamepad2Filled class="empty-state-icon" />
        <h3 class="empty-state-title">Your library is empty</h3>
        <p class="empty-state-subtext">Add your first game to get started</p>
        <p class="empty-state-hint">
          Use the Add game button above to import your first title.
        </p>
      </div>

      <div v-else-if="displayState === 'no-results'" class="empty-state flex-1">
        <IconZoom class="empty-state-icon" />
        <h3 class="empty-state-title">No games found</h3>
        <p class="empty-state-subtext">Try adjusting your search or filters</p>
        <button
          @click="clearFilters"
          class="ui-action-btn px-3 py-1.5 rounded-lg text-sm font-medium border active:scale-95 active:bg-[var(--bg-overlay)]"
          style="color: var(--text-secondary); border-color: var(--border)"
        >
          Clear filters
        </button>
      </div>

      <div v-else-if="layoutMode === 'list'" class="library-table-wrap pb-12">
        <table class="library-table" role="grid" aria-label="Games" aria-multiselectable="true">
          <caption class="sr-only">
            Games in your library. Select a column header to sort.
          </caption>
          <thead>
            <tr>
              <th
                v-for="column in tableColumns"
                :key="column.key"
                :class="column.class"
                scope="col"
                :aria-sort="
                  sortBy === column.key
                    ? sortDir === 'asc'
                      ? 'ascending'
                      : 'descending'
                    : 'none'
                "
              >
                <button
                  @click="toggleSort(column.key)"
                  class="w-full text-left py-3 whitespace-nowrap"
                >
                  {{ column.label }}
                  <span v-if="sortBy === column.key" aria-hidden="true">{{
                    sortDir === "asc" ? "↑" : "↓"
                  }}</span>
                </button>
              </th>
              <th scope="col"><span class="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            <GameRow
              v-for="(game, index) in renderedGames"
              :key="game.id"
              :game="game"
              :move-state="gameMoves.moveStateFor(game.id).state"
              :move-percent="gameMoves.moveStateFor(game.id).percent"
              :selected="selectedIds.has(game.id)"
              :running="runningGameIds.has(game.id)"
              :primary="index === 0"
              @activate="handleCardClick"
              @contextmenu="openContextMenu"
              @toggle-game="toggleGame"
              @target-select="launchTargetFromSelect"
            />
          </tbody>
        </table>
      </div>

      <div
        v-else
        class="library-cards grid gap-4 pb-12"
        :style="{ '--card-scale': cardScales[cardSize] }"
        :class="
          layoutMode === 'compact'
            ? 'library-cards--compact'
            : layoutMode === 'banner'
              ? 'library-cards--banner'
              : 'library-cards--grid'
        "
      >
        <GameCard
          v-for="(game, index) in renderedGames"
          :key="game.id"
          :game="game"
          :move-state="gameMoves.moveStateFor(game.id).state"
          :move-percent="gameMoves.moveStateFor(game.id).percent"
          :layout-mode="layoutMode"
          :selected="selectedIds.has(game.id)"
          :running="runningGameIds.has(game.id)"
          :menu-open="openLaunchTargetMenuId === game.id"
          :updating="updatingId === game.id"
          :update-busy="updatingId !== null"
          :selection-mode="selecting || selectedIds.size > 0"
          :primary="index === 0"
          @activate="handleCardClick"
          @contextmenu="openContextMenu"
          @toggle-game="toggleGame"
          @toggle-menu="toggleLaunchTargetMenu"
          @launch-target="launchSelectedTarget"
          @check-update="checkUpdate"
        />
      </div>

      <Teleport to="body">
        <div
          v-if="contextMenu"
          ref="contextMenuElement"
          data-controller-select
          role="dialog"
          :aria-label="`Actions for ${contextMenu.game.title}`"
          class="game-context-menu"
          :style="{ left: `${toCssPx(contextMenu.x)}px`, top: `${toCssPx(contextMenu.y)}px` }"
          @click.stop
          @contextmenu.prevent
          @keydown="handleContextMenuKeyboard"
        >
          <p
            class="truncate px-2 py-1 text-xs font-bold"
            :title="contextMenu.game.title"
          >
            {{ contextMenu.game.title }}
          </p>
          <template v-if="contextMenuView === 'actions'">
            <button @click="selection.toggle(contextMenu.game.id); selecting = true; closeContextMenu()">Select</button>
            <button @click="runContextAction('play')">
              {{ isWishlisted(contextMenu.game) ? "Set up" : runningGameIds.has(contextMenu.game.id) ? "Stop" : "Play" }}
            </button>
            <template v-if="runningGameIds.has(contextMenu.game.id) && contextTools.length">
              <div role="separator" class="my-1 border-t" style="border-color: var(--border)" />
              <button v-for="tool in contextTools" :key="tool.id" type="button" class="context-item" :disabled="loadingCompanionId !== null" @click="launchContextTool(tool)">{{ tool.name }}</button>
              <div role="separator" class="my-1 border-t" style="border-color: var(--border)" />
            </template>
            <button
              @click="runContextAction('folder')"
              :disabled="!gameFolder(contextMenu.game.exe_path)"
            >
              Open folder
            </button>
            <button
              v-if="gamePageUrl(contextMenu.game)"
              @click="runContextAction('thread')"
            >
              {{ contextMenu.game.f95_url ? "Open F95 thread" : "Open store page" }}
            </button>
            <button
              @click="runContextAction('check')"
              :disabled="!contextMenu.game.f95_url || updatingId !== null"
            >
              Check update
            </button>
            <button
              v-if="hasAvailableUpdate(contextMenu.game)"
              @click="runContextAction('mark-updated')"
              :disabled="markingUpdatedIds.has(contextMenu.game.id)"
            >
              Mark as updated to {{ contextMenu.game.latest_version }}
            </button>
            <button
              v-if="isUpdateSkipped(contextMenu.game) || hasAvailableUpdate(contextMenu.game)"
              class="min-h-11"
              @click="runContextAction('skip-version')"
            >{{ isUpdateSkipped(contextMenu.game) ? `Stop skipping ${contextMenu.game.skipped_version}` : `Skip version ${contextMenu.game.latest_version}` }}</button>
            <label
              class="flex items-center justify-between gap-2 px-2 py-1.5 text-xs"
              >Set status
              <select
                :value="
                  normalizePlayStatus(
                    contextMenu.game.play_status,
                    contextMenu.game.status,
                  )
                "
                @change="
                  runContextAction(
                    'status',
                    ($event.target as HTMLSelectElement).value,
                  )
                "
                class="min-w-0 max-w-36 rounded px-1 py-1"
                style="background: var(--bg-raised); color: var(--text-primary)"
              >
                <option
                  v-for="status in allStatuses"
                  :key="status.value"
                  :value="status.value"
                >
                  {{ status.label }}
                </option>
              </select>
            </label>
            <button @click="contextMenuView = 'collections'">
              Collections…
            </button>
            <button @click="runContextAction('favorite')">
              {{ contextMenu.game.is_favorite ? "Unfavorite" : "Favorite" }}
            </button>
            <button
              @click="runContextAction('remove')"
              style="color: var(--danger-text)"
            >
              Remove
            </button>
          </template>
          <template v-else>
            <button
              class="flex items-center gap-2"
              @click="contextMenuView = 'actions'"
            >
              <IconArrowLeft class="w-3.5 h-3.5" />
              Back
            </button>
            <div class="max-h-64 overflow-y-auto">
              <p
                v-if="!userCollections.length"
                class="px-2 py-1.5 text-xs"
                style="color: var(--text-muted)"
              >
                No collections yet.
              </p>
              <label
                v-for="name in userCollections"
                :key="name"
                class="flex items-center gap-2 px-2 py-1.5 text-xs cursor-pointer rounded ui-hover-surface"
                :style="
                  gameInCollection(contextMenu.game, name)
                    ? 'color: var(--text-primary)'
                    : 'color: var(--text-secondary)'
                "
              >
                <input
                  type="checkbox"
                  :checked="gameInCollection(contextMenu.game, name)"
                  @change="toggleGameCollection(contextMenu.game, name)"
                  class="rounded"
                />
                <span class="truncate">{{ name }}</span>
              </label>
            </div>
            <form
              class="flex gap-1 px-1 pt-1"
              @submit.prevent="
                contextCreateForGame(contextMenu.game)
              "
            >
              <input
                v-model="contextNewCollectionName"
                type="text"
                aria-label="New collection name"
                placeholder="New collection"
                class="library-search-input min-w-0 flex-1 rounded px-1.5 py-1 text-xs"
              />
              <button
                type="submit"
                class="rounded px-2 py-1 text-xs font-semibold border"
                style="color: var(--text-secondary); border-color: var(--border)"
              >
                Add
              </button>
            </form>
            <p
              v-if="contextNewCollectionError"
              role="alert"
              class="px-2 py-1 text-xs"
              style="color: var(--danger-text)"
            >
              {{ contextNewCollectionError }}
            </p>
          </template>
        </div>

        <!-- Collection tab menu -->
        <div
          v-if="collectionTabMenu"
          ref="collectionTabMenuElement"
          role="menu"
          :aria-label="`Actions for collection ${collectionTabMenu.name}`"
          class="game-context-menu"
          :style="{
            left: `${toCssPx(collectionTabMenu.x)}px`,
            top: `${toCssPx(collectionTabMenu.y)}px`,
          }"
          @click.stop
          @contextmenu.prevent
        >
          <p class="truncate px-2 py-1 text-xs font-bold" :title="collectionTabMenu.name">
            {{ collectionTabMenu.name }}
          </p>
          <button role="menuitem" @click="startCollectionRename(collectionTabMenu.name)">
            Rename
          </button>
          <button
            role="menuitem"
            style="color: var(--danger-text)"
            @click="deleteCollectionFromTab(collectionTabMenu.name)"
          >
            Delete
          </button>
        </div>
      </Teleport>

      <div v-if="selectedIds.size || gameMoves.moveStatus.value?.running" class="library-bottom-stack">
      <MoveQueuePanel :status="gameMoves.moveStatus.value" @cancel="gameMoves.cancel" />
      <BulkActionBar
        v-if="selectedIds.size"
        ref="bulkBar"
        :games="selectedGames"
        :statuses="allStatuses"
        :collections="userCollections"
        :tag-suggestions="bulkTagSuggestions"
        :running-ids="runningGameIds"
        :show-runtime="bulkShowRuntime"
        :busy="bulkBusy"
        :checking="bulkChecking"
        :saves-busy="bulkSavesBusy"
        :eligible-checks="eligibleChecks.length"
        :eligible-updates="eligibleUpdates.length"
        @select-all="selection.selectAll(filteredGames.map(game => game.id))"
        @done="clearSelection"
        @action="bulkAction"
        @wine="bulkWineOpen = true"
      />
      </div>
      <BulkWineDialog v-model="bulkWineOpen" :games="selectedGames" :running-ids="runningGameIds" @apply="applyBulkWine" />
      <UninstallDialog v-model="uninstallOpen" :games="uninstallTargets" @confirm="confirmUninstall" />
      <MoveGameDialog v-model="moveOpen" :games="moveTargets" @started="moveStarted" />
      <WebMediaDialog
        ref="webMediaDialog"
        v-model="webMediaOpen"
        :game="webMediaGame"
        :media="webMediaInfo"
        :code="webMediaCode"
        @convert="webMediaConvert"
        @use-browser="webMediaUseBrowser"
        @play-anyway="webMediaPlayAnyway"
      />
      <ImportFolderModal v-model="showImportModal" />
      <AddGameModal
        v-model="showAddModal"
        :saving="addingGame"
        @save="handleAddGame"
      />
      <GameDetailModal
        ref="detailModal"
        v-model="showDetailModal"
        :game="selectedGame"
        :is-running="!!selectedGame && runningGameIds.has(selectedGame.id)"
        :update-check-state="shownModalUpdateState"
        @updated="handleGameUpdated"
        :moving="!!selectedGame && !!gameMoves.moveStateFor(selectedGame.id).state"
        @remove="scheduleRemoval([$event])"
        @uninstall="openUninstall([$event])"
        @move="openMove([$event])"
        @launch="launchGameFast"
        @stop="stopGame"
        @check-updates="handleModalUpdateCheck"
        @targets-changed="handleLaunchTargetsChanged"
      />
    </div>
    <TagColorPicker v-if="tagColorPicker" :key="tagColorPicker.tag" :tag="tagColorPicker.tag" :anchor="tagColorPicker.anchor" @close="tagColorPicker = null" />
  </div>
</template>

<style scoped>
/* The move panel sits above the bulk bar; both stay after the last row, so neither covers it. */
.library-bottom-stack { position: sticky; bottom: 0; z-index: 40; display: flex; flex-direction: column; gap: 0.5rem; }
.library-skeletons--list { grid-template-columns: 1fr; gap: 8px; }
.library-skeleton { background: var(--bg-surface); border: 1px solid var(--border); animation: skeleton-pulse 1.5s ease-in-out infinite alternate; }
.library-skeleton--grid { height: 340px; }
.library-skeleton--compact { aspect-ratio: 3/4; }
.library-skeleton--banner { aspect-ratio: 16/7; }
.library-skeleton--list { height: 64px; }
@keyframes skeleton-pulse { to { opacity: 0.5; } }
:global(.motion-off .library-skeleton) { animation: none; }
@media (prefers-reduced-motion: reduce) { .library-skeleton { animation: none; } }

.card-size-control { accent-color: var(--brand); }
.card-size-control:focus-visible { outline: 2px solid var(--brand); outline-offset: 4px; }

.library-cards--grid {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, calc(220px * var(--card-scale, 1))), 1fr));
}
.library-cards--compact {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, calc(180px * var(--card-scale, 1))), 1fr));
}
.library-cards--banner {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, calc(420px * var(--card-scale, 1))), 1fr));
}
.collection-count {
  background: var(--brand);
  color: var(--on-brand);
  border-radius: 0.3rem;
  padding: 0.15rem 0.35rem;
  font-size: 0.65rem;
  font-weight: 700;
}
.collections-tab-strip {
  scrollbar-width: thin;
}
.collection-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.35rem 0.75rem;
  border: 1px solid transparent;
  border-radius: 0.5rem;
  font-size: 0.875rem;
  font-weight: 500;
  white-space: nowrap;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.collection-tab:not(.collection-tab-add) {
  border-color: var(--border);
  background: var(--bg-surface);
  color: var(--text-secondary);
}
.collection-tab[aria-selected="true"] {
  border-color: var(--brand-deep);
  background: var(--brand-glow-strong);
  color: var(--text-primary);
}
.collection-tab:hover {
  background: var(--bg-overlay);
  color: var(--text-primary);
}
.collection-tab:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 2px;
}
.collection-tab-add {
  color: var(--text-muted);
}
.collection-tab .collection-count {
  background: var(--bg-overlay);
  color: var(--text-secondary);
}
.collection-tab[aria-selected="true"] .collection-count {
  background: var(--brand);
  color: var(--on-brand);
}
.library-table-wrap {
  overflow-x: auto;
  container-type: inline-size;
}
/* Thresholds tuned to the list width at 1200x800 with filters open. */
@container (max-width: 780px) {
  .col-engine {
    display: none;
  }
}
@container (max-width: 690px) {
  .col-last-played {
    display: none;
  }
}
.library-table {
  width: 100%;
  border-collapse: collapse;
  color: var(--text-secondary);
  background: var(--bg-surface);
  font-size: 0.8rem;
}
.library-table th {
  padding: 0.4rem 0.5rem;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
.library-table button:focus-visible,
.game-context-menu :is(button, select):focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 3px;
}
.library-table th {
  color: var(--text-muted);
  font-size: 0.7rem;
}
.game-context-menu {
  position: fixed;
  z-index: 100;
  width: 15rem;
  max-width: calc(var(--app-vw) - 16px);
  max-height: calc(var(--app-vh) - 16px);
  overflow-y: auto;
  padding: 0.4rem;
  border: 1px solid var(--border-hover);
  border-radius: 0.5rem;
  background: var(--bg-surface);
  color: var(--text-primary);
  box-shadow: var(--shadow-card);
}
.game-context-menu > button {
  display: block;
  width: 100%;
  padding: 0.45rem 0.5rem;
  border-radius: 0.3rem;
  text-align: left;
  font-size: 0.8rem;
}
.game-context-menu > button:hover {
  background: var(--bg-raised);
}
.game-context-menu button:disabled {
  opacity: 0.5;
  cursor: wait;
}

.filters-pane {
  background: var(--bg-surface);
  border-right: 1px solid var(--border);
}

.filters-pane-collapsed {
  border-right: 0 solid transparent;
}

.library-search-input {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  color: var(--text-primary);
  transition: border-color 0.15s ease, box-shadow 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}
.library-search-input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.filters-checkbox {
  border-color: var(--border);
  background: transparent;
}

.filters-checkbox:focus {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}

/* Heading row: the subtitle is hidden before the heading or "Add game" would wrap; an sr-only copy stays. */
.library-header {
  container-type: inline-size;
}
.library-header-row {
  display: flex;
  align-items: center;
  gap: 1rem;
}
.library-subtitle {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--text-secondary);
  border-left: 2px solid var(--brand);
}
.library-add-menu {
  margin-left: auto;
}
@container (max-width: 44rem) {
  .library-subtitle {
    display: none;
  }
}
.library-add-menu :deep(.menu-button-trigger) {
  border-color: var(--brand);
  background: var(--brand);
  color: var(--on-brand);
  font-size: 0.875rem;
  font-weight: 600;
}
.library-add-menu :deep(.menu-button-main) {
  box-shadow: var(--shadow-brand);
}
.library-add-menu :deep(.menu-button-toggle) {
  border-left-color: color-mix(in srgb, var(--on-brand) 35%, var(--brand));
}
.library-add-menu :deep(.menu-button-trigger:hover:not(:disabled)) {
  border-color: var(--brand);
  background: var(--brand);
  filter: brightness(1.08);
}

/* One toolbar row; the view controls wrap as a group before the search field gets too small. */
.library-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}
.library-toolbar-search {
  flex: 1 1 12rem;
  min-width: 12rem;
}
.library-toolbar-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
  margin-left: auto;
}
.library-toolbar-btn {
  min-height: 2.375rem;
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  color: var(--text-primary);
  font-size: 0.875rem;
  font-weight: 500;
}
.library-toolbar-btn[aria-pressed="true"] {
  background: var(--brand-glow);
  border-color: var(--brand-deep);
  color: var(--brand);
}
.library-sort-menu :deep(.menu-button-toggle) {
  border-top-right-radius: 0;
  border-bottom-right-radius: 0;
  background: transparent;
  font-size: 0.875rem;
}
.library-sort-menu :deep(.menu-button-trigger:hover:not(:disabled)) {
  background: var(--bg-overlay);
}
.library-sort-dir {
  margin-left: -1px;
  padding-inline: 0.5rem;
  border-top-left-radius: 0;
  border-bottom-left-radius: 0;
}

.filter-tag-btn {
  transition: background-color 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
</style>
