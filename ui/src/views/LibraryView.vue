<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
<script setup lang="ts">
import { activeFilterChips as buildFilterChips, removeFilterValue, type FilterChip } from "../utils/libraryFilters";
import { confirmAction } from "../utils/confirm";
import { controllerFocusText, useControllerActions } from "../utils/controllerActions";
import {
  describeLibraryNoResults,
  getEffectiveLaunchTargets,
  libraryDisplayState,
  type EffectiveLaunchTarget,
} from "../utils/libraryGames";
import { discoverSession } from "../utils/discoverView";
import GameCard from "../components/library/GameCard.vue";
import GameRow from "../components/library/GameRow.vue";
import { runningGameIds, refreshRunningGames } from "../composables/useRunningGames";
import { ref, shallowRef, useTemplateRef, computed, nextTick, onMounted, onUnmounted, toRaw, watch } from "vue";
import { useEventListener } from "../composables/useEventListener";
import { gamesStore } from "../composables/useGamesStore";
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
import { toCssPx } from "../utils/interfaceScalePreference";
import TagColorPicker from "../components/TagColorPicker.vue";
import FilterPill from "../components/FilterPill.vue";
import { colorForTag, loadTagColors } from "../utils/tagColors";
import { useRoute, useRouter } from "vue-router";
import {
  IconArchive,
  IconBan,
  IconCheck,
  IconChevronDown,
  IconCircleCheck,
  IconPlayerPause,
  IconProgress,
  IconArrowLeft,
  IconArrowUp,
  IconCircleDot,
  IconClock,
  IconHistory,
  IconColumns2Filled,
  IconDeviceGamepad2,
  IconDeviceGamepad2Filled,
  IconFilterFilled,
  IconLibraryPlus,
  IconLayoutGridFilled,
  IconPanoramaHorizontalFilled,
  IconLayoutListFilled,
  IconFolderPlus,
  IconPlus,
  IconSparkles,
  IconZoom,
  IconHeartFilled,
  IconListCheck,
  IconSortAscending,
  IconSortDescending,
  IconX,
  IconAlertTriangle,
  IconRefresh,
  IconChevronRight,
  IconCompass,
  IconExternalLink,
  IconFileImport,
  IconPuzzle,
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
  LIST_ONLY_SORT_FIELDS,
  SORT_FIELD_LABELS,
  sortOrderLabel,
  VALID_SORT_FIELDS,
  type SortState,
  type FilterCollection,
  type FilterInstallState,
  type FilterMinRating,
  type FilterSections,
  type ThreadStateFilter,
  THREAD_STATE_FILTER_LABELS,
  THREAD_STATE_FILTER_VALUES,
  type LayoutMode,
  type CardSize,
  type LibraryFilterPreset,
  type LibraryViewState,
  type SortDir,
  type SortField,
} from "../utils/libraryViewState";
import { loadPlatformCapabilities } from "../utils/platformCapabilities";
import { filtersPaneMode, libraryFiltersPaneOpen } from "../utils/navLayout";
import { useWindowCssWidth } from "../utils/windowWidth";
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
import { modalStack, useModalKeyboard } from "../utils/modalKeyboard";
import { sandboxLaunchError } from "../utils/sandbox";
import { localeEmulatorLaunchError } from "../utils/localeEmulator";
import { openInstallArchive } from "../utils/installArchive";
import {
  LIBRARY_SOURCES,
  buildTagFilterOptions,
  compareLibraryGames,
  gameFolder,
  formatLibrarySummary,
  gameTags,
  hasAvailableUpdate,
  hasUnplayedUpdate,
  isUpdateSkipped,
  hasNewSinceFinished,
  matchesThreadStateFilter,
  matchesMinRatingFilter,
  isWishlisted,
  matchesGameSearch,
  matchesEngineFilter,
  matchesSourceFilter,
  matchesTagFilter,
  engineFilterKey,
  filterValueState,
  toggleExcludedFilterValue,
  toggleIncludedFilterValue,
  orderTagOptions,
  pinnedTagKeysFor,
  tagFilterKey,
} from "../utils/libraryGames";

interface UpdateNotice {
  type: "" | "success" | "error";
  message: string;
}

interface ModalUpdateState extends UpdateNotice {
  running: boolean;
}

import { normalizeLcThreadUrl, updateCheckUrl } from "../utils/lewdcorner";
import { extensionGameId, readStoreLinks, gamePageUrl } from "../utils/storeLinks";
import { launchNoticeMessage } from "../utils/webPlayer";
import { updateCheckRunning } from "../composables/useUpdateCheckState";

interface AddGamePayload {
  links?: StoreLink[];
  title: string;
  original_title?: string;
  exe_path: string;
  f95_url?: string;
  lc_url?: string;
  lc_dismissed?: string;
  thread_completed?: boolean;
  thread_onhold?: boolean;
  thread_states_seen_at?: string;
  thread_abandoned?: boolean;
  version?: string;
  cover_image?: string;
  tags?: string;
  rating?: string;
  developer?: string;
  engine?: string;
  launch_mode?: LaunchMode;
  command_line_args?: string;
  screenshots?: string[];
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
// The bridge's reason for the failed load, when it gave one.
const loadErrorCause = gamesStore.error;
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
const newCollectionInput = useTemplateRef<HTMLInputElement>("newCollectionInput");
const collectionTabsElement = useTemplateRef<HTMLElement>("collectionTabsElement");
const collectionTabMenu = ref<{ name: string; x: number; y: number } | null>(null);
const collectionTabMenuElement = useTemplateRef<HTMLElement>("collectionTabMenuElement");
// Saved filter presets listed after Quick filters in the filters pane.
const savedPresets = ref<LibraryFilterPreset[]>([]);
// True once Settings has answered, so a save can't treat "not loaded" as empty.
const presetsLoaded = ref(false);
const newPresetName = ref("");
const presetError = ref("");
// "Save as preset" in the active-filter chip row opens an inline name field.
const savePresetOpen = ref(false);
const savePresetButton = useTemplateRef<HTMLButtonElement>("savePresetButton");
const savePresetInput = useTemplateRef<HTMLInputElement>("savePresetInput");
const quickFiltersList = useTemplateRef<HTMLElement>("quickFiltersList");
const showAddModal = ref(false);
const showImportModal = ref(false);
const showDetailModal = ref(false);
const detailModal = ref<{ requestClose: () => Promise<boolean> } | null>(null);
const selectedGame = ref<GameRecord | null>(null);
const openLaunchTargetMenuId = ref<number | null>(null);
const searchInput = useTemplateRef<HTMLInputElement>("searchInput");
const tagSearchQuery = ref("");
const markingUpdatedIds = ref(new Set<number>());
const contextMenu = ref<{ game: GameRecord; x: number; y: number } | null>(
  null,
);
const contextMenuElement = useTemplateRef<HTMLElement>("contextMenuElement");
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
const librarySummary = computed(() => formatLibrarySummary(games.value.length, updateCount.value));
const collections: Array<{
  value: FilterCollection;
  label: string;
  icon: typeof IconClock;
}> = [
  { value: "All", label: "No quick filter", icon: IconLayoutGridFilled },
  { value: "Favorites", label: "Favorites", icon: IconHeartFilled },
  { value: "Updates available", label: "Updates available", icon: IconArrowUp },
  { value: "New since finished", label: "New since finished", icon: IconSparkles },
  { value: "Unplayed update", label: "Unplayed update", icon: IconCircleDot },
  { value: "Recently played", label: "Recently played", icon: IconHistory },
];

// Search & Filter state
const searchQuery = ref("");
const selection = useLibrarySelection();
const selectedIds = selection.selectedIds;
const selecting = ref(false);
const bulkBar = ref<InstanceType<typeof BulkActionBar> | null>(null);
const libraryControllerScope = useTemplateRef<HTMLElement>("libraryControllerScope");
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
const filterExcludedSources = ref<string[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterExcludedSources]);
const filterExcludedEngines = ref<string[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterExcludedEngines]);
const filterExcludedTags = ref<string[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterExcludedTags]);
const tagColorPicker = ref<{ tag: string; anchor: HTMLElement } | null>(null);
function openTagColorPicker(event: MouseEvent | KeyboardEvent, tag: string) {
  event.preventDefault();
  tagColorPicker.value = { tag, anchor: event.currentTarget as HTMLElement };
}
const filterInstallState = ref<FilterInstallState>(DEFAULT_LIBRARY_VIEW_STATE.filterInstallState);
const filterMinRating = ref<FilterMinRating>(DEFAULT_LIBRARY_VIEW_STATE.filterMinRating);
const filterThreadStates = ref<ThreadStateFilter[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterThreadStates]);
// The Thread state section's options, in the order of the stored values.
const threadStateIcons: Record<ThreadStateFilter, typeof IconCircleCheck> = {
  finished: IconCircleCheck,
  onhold: IconPlayerPause,
  abandoned: IconBan,
  ongoing: IconProgress,
};
const threadStateOptions = THREAD_STATE_FILTER_VALUES.map((value) => ({
  value,
  label: THREAD_STATE_FILTER_LABELS[value],
  icon: threadStateIcons[value],
}));
const toggleThreadStateFilter = (value: ThreadStateFilter): void => {
  const checked = !filterThreadStates.value.includes(value);
  const others = filterThreadStates.value.filter((entry) => entry !== value);
  // Keep the section's order so the chips and saved presets read the same way.
  filterThreadStates.value = THREAD_STATE_FILTER_VALUES.filter(
    (entry) => others.includes(entry) || (checked && entry === value),
  );
};
const cardSize = ref<CardSize>(DEFAULT_LIBRARY_VIEW_STATE.cardSize);
const cardScales = [0.75, 0.875, 1, 1.25, 1.5] as const;
const cardSizeNames = ["Smallest", "Small", "Medium", "Large", "Largest"] as const;
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
// The View menu's sort fields.
const setSortField = (field: string) => {
  if (VALID_SORT_FIELDS.has(field as SortField)) applySortState(chooseSortField({ sortBy: sortBy.value, sortDir: sortDir.value }, field as SortField));
};

const selectCollection = (collection: FilterCollection) => {
  filterCollection.value = collection;
  if (collection === "Recently played") {
    sortBy.value = "last_played";
    sortDir.value = "desc";
  }
};

// Wide windows show the filters pane beside the games, and its open state is
// saved. Narrower windows show it as a drawer over the games; the drawer always
// starts closed and its state is not saved (see utils/navLayout.ts).
const windowCssWidth = useWindowCssWidth();
const filtersAsDrawer = computed(() => filtersPaneMode(windowCssWidth.value) === "drawer");
const filtersDrawerOpen = ref(false);
const filtersPaneShown = computed(() => (filtersAsDrawer.value ? filtersDrawerOpen.value : !isFiltersCollapsed.value));
const filtersPane = useTemplateRef<HTMLElement>("filtersPane");
const closeFiltersDrawer = () => { filtersDrawerOpen.value = false; };
// Focus moves into the drawer, Tab stays in it, Escape closes it, and focus
// returns to the Filters button.
useModalKeyboard(filtersPane, () => filtersAsDrawer.value && filtersDrawerOpen.value, closeFiltersDrawer);
const toggleFiltersPane = () => {
  if (filtersAsDrawer.value) filtersDrawerOpen.value = !filtersDrawerOpen.value;
  else isFiltersCollapsed.value = !isFiltersCollapsed.value;
};
watch(filtersAsDrawer, (drawer) => {
  // A drawer that is open when the window widens stays open as the pane.
  if (!drawer && filtersDrawerOpen.value) isFiltersCollapsed.value = false;
  filtersDrawerOpen.value = false;
});
// The app sidebar collapses while the pane is open beside the games.
watch([filtersAsDrawer, isFiltersCollapsed], ([drawer, collapsed]) => { libraryFiltersPaneOpen.value = !drawer && !collapsed; });

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

// The View menu: layout, sort field, sort order and card size in one place.
// It and the list view column headers change the same sort state.
const SORT_MENU_FIELDS: SortField[] = [
  "title", "version", "play_status", "engine", "date_added", "last_played",
  "playtime_seconds", "rating", "weighted_rating", "own_rating",
];
const layoutOptions: Array<{ value: LayoutMode; label: string; icon: typeof IconClock }> = [
  { value: "grid", label: "Grid", icon: IconLayoutGridFilled },
  { value: "list", label: "List", icon: IconLayoutListFilled },
  { value: "compact", label: "Compact", icon: IconColumns2Filled },
  { value: "banner", label: "Banner", icon: IconPanoramaHorizontalFilled },
];
const currentLayout = computed(() => layoutOptions.find((option) => option.value === layoutMode.value) ?? layoutOptions[0]!);
const sortOrderText = computed(() => sortOrderLabel(sortBy.value, sortDir.value));
const sortSummary = computed(() => `${SORT_FIELD_LABELS[sortBy.value]}, ${sortOrderText.value}`);
const viewMenuItems = computed<MenuButtonItem[]>(() => [
  ...layoutOptions.map((option) => ({
    id: `layout:${option.value}`, label: option.label, icon: option.icon, group: "Layout", checked: layoutMode.value === option.value,
  })),
  // Version, status and engine are list columns; other layouts offer them only while they are the active sort.
  ...SORT_MENU_FIELDS.filter((field) => layoutMode.value === "list" || !LIST_ONLY_SORT_FIELDS.has(field) || sortBy.value === field).map((field) => ({
    id: `sort:${field}`, label: SORT_FIELD_LABELS[field], group: "Sort by", checked: sortBy.value === field,
  })),
  ...(["asc", "desc"] as const).map((dir) => ({
    id: `dir:${dir}`, label: sortOrderLabel(sortBy.value, dir), icon: dir === "asc" ? IconSortAscending : IconSortDescending, group: "Order", checked: sortDir.value === dir,
  })),
  ...(layoutMode.value === "list" ? [] : cardSizeNames.map((name, size) => ({
    id: `size:${size}`, label: name, group: "Card size", checked: cardSize.value === size,
  }))),
]);
const chooseViewMenuItem = (id: string) => {
  const [kind, value = ""] = id.split(":");
  if (kind === "layout" && layoutOptions.some((option) => option.value === value)) layoutMode.value = value as LayoutMode;
  else if (kind === "sort") setSortField(value);
  else if (kind === "dir" && (value === "asc" || value === "desc")) sortDir.value = value;
  else if (kind === "size") {
    const size = Number(value);
    if (size >= 0 && size < cardSizeNames.length) cardSize.value = size as CardSize;
  }
};
const addMenuItems: MenuButtonItem[] = [
  { id: "import", label: "Import folder", icon: IconFolderPlus },
  { id: "archive", label: "Install from archive", icon: IconArchive },
];
const chooseAddMenuItem = (id: string) => {
  if (id === "import") showImportModal.value = true;
  else if (id === "archive") openInstallArchive();
};

// Source, engine and tag pills: a click toggles include, and the pill's
// exclude button, Alt+click or the minus key toggles exclude. Tags are
// case-insensitive, so every spelling of the tag leaves both lists.
type FilterValueToggle = typeof toggleIncludedFilterValue;
const sourceState = (source: string) =>
  filterValueState(filterSources.value, filterExcludedSources.value, source);
const setSourceFilter = (toggle: FilterValueToggle, source: string) => {
  const next = toggle(filterSources.value, filterExcludedSources.value, source);
  filterSources.value = next.included;
  filterExcludedSources.value = next.excluded;
};
const engineState = (engine: string) =>
  filterValueState(filterEngines.value, filterExcludedEngines.value, engine, engineFilterKey);
const setEngineFilter = (toggle: FilterValueToggle, engine: string) => {
  const next = toggle(filterEngines.value, filterExcludedEngines.value, engine, engineFilterKey);
  filterEngines.value = next.included;
  filterExcludedEngines.value = next.excluded;
};
const tagState = (tag: string) =>
  filterValueState(filterTags.value, filterExcludedTags.value, tag, tagFilterKey);
const setTagFilter = (toggle: FilterValueToggle, tag: string) => {
  const next = toggle(filterTags.value, filterExcludedTags.value, tag, tagFilterKey);
  filterTags.value = next.included;
  filterExcludedTags.value = next.excluded;
};

// Status filters keep the spelling they were restored with, so the checkbox
// matches case-insensitively and toggling it clears every spelling of the name.
const statusFilterActive = (status: string): boolean =>
  hasStatusFilter(filterStatuses.value, status);

const toggleStatusFilterValue = (status: string): void => {
  filterStatuses.value = toggleStatusFilter(
    filterStatuses.value,
    status,
    !statusFilterActive(status),
  );
};

// Install state and rating are single-choice pills: a click picks the value,
// and a click on the picked value clears the filter.
const installStateOptions: Array<{ value: Exclude<FilterInstallState, "">; label: string }> = [
  { value: "installed", label: "Installed" },
  { value: "wishlist", label: "Wishlist" },
];
const toggleInstallState = (value: FilterInstallState): void => {
  filterInstallState.value = filterInstallState.value === value ? "" : value;
};
const ratingOptions: Array<{ value: Exclude<FilterMinRating, "">; label: string }> = [
  { value: "3.5", label: "3.5+" },
  { value: "4.0", label: "4.0+" },
  { value: "4.5", label: "4.5+" },
];
const toggleMinRating = (value: FilterMinRating): void => {
  filterMinRating.value = filterMinRating.value === value ? "" : value;
};

const normalizeEngineFilterValue = engineFilterKey;

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
const uniqueTags = computed(() => tagOptions.value.map((option) => option.label));

// Active tags sit in a pinned group at the top of the tag list. The group comes
// from a snapshot that only updates while the user is not working in the list,
// so a pill never moves under the pointer or focus between two clicks.
const pinnedTagKeys = ref<string[]>([]);
const tagList = useTemplateRef<HTMLElement>("tagList");
let tagListPointerInside = false;
let tagListFocusInside = false;
let tagListPointerDown = false;
let restoringTagFocus = false;
const settlePinnedTags = () => {
  const list = tagList.value;
  const active = document.activeElement;
  // A focused pill that changes group is a new element, so keep its focus.
  // The pill's exclude button has no data-tag; its pill's main button has.
  const focusedTag =
    list && active instanceof HTMLElement && list.contains(active)
      ? (active.closest(".filter-pill")?.querySelector<HTMLElement>("[data-tag]") ?? active).dataset.tag
      : undefined;
  pinnedTagKeys.value = pinnedTagKeysFor(filterTags.value, filterExcludedTags.value);
  if (focusedTag === undefined) return;
  void nextTick(() => {
    const pill = [...(tagList.value?.querySelectorAll<HTMLElement>("[data-tag]") ?? [])].find(
      (element) => element.dataset.tag === focusedTag,
    );
    if (!pill || pill === document.activeElement) return;
    restoringTagFocus = true;
    pill.focus({ preventScroll: true });
    restoringTagFocus = false;
  });
};
const orderedTags = computed(() =>
  orderTagOptions(tagOptions.value, pinnedTagKeys.value, tagSearchQuery.value),
);
const onTagListPointerEnter = () => { tagListPointerInside = true; };
const onTagListPointerLeave = () => {
  tagListPointerInside = false;
  if (!tagListFocusInside) settlePinnedTags();
};
// Pointer clicks also focus the pill. Only keyboard and controller focus keep
// the list engaged after the pointer leaves.
const onTagListPointerDown = () => {
  tagListPointerDown = true;
  tagListFocusInside = false;
};
const onTagListFocusIn = () => {
  if (!tagListPointerDown && !restoringTagFocus) tagListFocusInside = true;
  tagListPointerDown = false;
};
const onTagListFocusOut = (event: FocusEvent) => {
  const next = event.relatedTarget;
  if (next instanceof Node && tagList.value?.contains(next)) return;
  tagListFocusInside = false;
  tagListPointerDown = false;
  settlePinnedTags();
};
watch(
  [filterTags, filterExcludedTags],
  () => {
    if (!tagListPointerInside && !tagListFocusInside) settlePinnedTags();
  },
  { deep: true },
);
watch(tagSearchQuery, settlePinnedTags);
watch(filtersPaneShown, (shown) => { if (shown) settlePinnedTags(); });
watch(() => filterSections.value.tags, (open) => { if (open) settlePinnedTags(); });

const activeFilterChips = computed(() => buildFilterChips({
  statuses: filterStatuses.value, install: filterInstallState.value, minRating: filterMinRating.value, sources: filterSources.value,
  threadStates: filterThreadStates.value,
  engines: filterEngines.value, tags: filterTags.value, collection: filterCollection.value,
  excludedSources: filterExcludedSources.value, excludedEngines: filterExcludedEngines.value,
  excludedTags: filterExcludedTags.value,
  statusLabels: Object.fromEntries(allStatuses.value.map(status => [status.value.toLowerCase(), status.label])),
  sourceLabels: Object.fromEntries(LIBRARY_SOURCES.map(source => [source.key, source.label])),
}));
// One per chip, so the Filters badge, its name and the no-results text count
// the same things the chip row shows.
const activeFilterCount = computed(() => activeFilterChips.value.length);
const removeFilterChip = (chip: FilterChip) => {
  switch (chip.kind) {
    case "status": filterStatuses.value = removeFilterValue(filterStatuses.value, chip.value); break;
    case "install": filterInstallState.value = ""; break;
    case "rating": filterMinRating.value = ""; break;
    case "thread": filterThreadStates.value = filterThreadStates.value.filter((value) => value !== chip.value); break;
    case "source":
      if (chip.excluded) filterExcludedSources.value = removeFilterValue(filterExcludedSources.value, chip.value);
      else filterSources.value = removeFilterValue(filterSources.value, chip.value);
      break;
    case "engine":
      if (chip.excluded) filterExcludedEngines.value = removeFilterValue(filterExcludedEngines.value, chip.value);
      else filterEngines.value = removeFilterValue(filterEngines.value, chip.value);
      break;
    case "tag":
      if (chip.excluded) filterExcludedTags.value = removeFilterValue(filterExcludedTags.value, chip.value);
      else filterTags.value = removeFilterValue(filterTags.value, chip.value);
      break;
    case "collection": filterCollection.value = "All"; break;
  }
};
const clearFilters = () => {
  searchQuery.value = "";
  clearFilterValues();
};
// Clears every filter but keeps the search.
const clearFilterValues = () => {
  filterStatuses.value = [];
  filterInstallState.value = "";
  filterMinRating.value = "";
  filterThreadStates.value = [];
  clearSourceFilters();
  clearEngineFilters();
  clearTagFilters();
  filterCollection.value = "All";
};
const clearSourceFilters = () => {
  filterSources.value = [];
  filterExcludedSources.value = [];
};
const clearEngineFilters = () => {
  filterEngines.value = [];
  filterExcludedEngines.value = [];
};
const clearTagFilters = () => {
  filterTags.value = [];
  filterExcludedTags.value = [];
};

const applyLibraryViewState = (state: LibraryViewState) => {
  layoutMode.value = state.layoutMode;
  cardSize.value = state.cardSize;
  sortBy.value = state.sortBy;
  sortDir.value = state.sortDir;
  filterCollection.value = state.filterCollection;
  filterStatuses.value = [...state.filterStatuses];
  filterInstallState.value = state.filterInstallState;
  filterMinRating.value = state.filterMinRating;
  filterThreadStates.value = [...state.filterThreadStates];
  filterSources.value = [...state.filterSources];
  filterEngines.value = [...state.filterEngines];
  filterTags.value = [...state.filterTags];
  filterExcludedSources.value = [...state.filterExcludedSources];
  filterExcludedEngines.value = [...state.filterExcludedEngines];
  filterExcludedTags.value = [...state.filterExcludedTags];
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
    filterMinRating: filterMinRating.value,
    filterThreadStates: filterThreadStates.value,
    filterSources: filterSources.value,
    filterEngines: filterEngines.value,
    filterTags: filterTags.value,
    filterExcludedSources: filterExcludedSources.value,
    filterExcludedEngines: filterExcludedEngines.value,
    filterExcludedTags: filterExcludedTags.value,
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
  if (filterThreadStates.value.length)
    result = result.filter((game) => matchesThreadStateFilter(game, filterThreadStates.value));
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
  if (filterMinRating.value)
    result = result.filter((game) => matchesMinRatingFilter(game, filterMinRating.value));
  result = result.filter(
    (game) =>
      matchesSourceFilter(game, filterSources.value, filterExcludedSources.value) &&
      matchesEngineFilter(game, filterEngines.value, filterExcludedEngines.value) &&
      matchesTagFilter(game, filterTags.value, filterExcludedTags.value),
  );
  return result;
});

watch(() => filteredGames.value.map((game) => game.id), (ids) => selection.prune(ids));

// --- Empty and no-results states ----------------------------------------------
const activeCollectionGameCount = computed(() => {
  const key = activeCollection.value.toLowerCase();
  return games.value.filter(
    (game) =>
      !isPendingRemoval(game.id) &&
      !isPendingUninstallRemoval(game.id) &&
      (!key || (game.collections || []).some((name) => name.toLowerCase() === key)),
  ).length;
});
const otherFilterCount = computed(() => activeFilterCount.value - (filterCollection.value !== "All" ? 1 : 0));
const noResults = computed(() => describeLibraryNoResults({
  collection: activeCollection.value,
  collectionGameCount: activeCollectionGameCount.value,
  quickFilter: filterCollection.value,
  otherFilterCount: otherFilterCount.value,
  search: appliedSearch.value,
}));
const noResultsSearch = computed(() => appliedSearch.value.trim());
const noResultsIcon = computed(() => {
  if (noResults.value.kind === "collection-empty") return IconListCheck;
  if (noResults.value.kind === "quick-filter-empty") {
    return collections.find((item) => item.value === filterCollection.value)?.icon ?? IconZoom;
  }
  return IconZoom;
});
// "Look in Discover" only while the catalog is on and installed. Asked each
// time the state shows, because Settings can turn the catalog off.
const discoverAvailable = ref(false);
let discoverStatusRequest = 0;
watch(
  () => displayState.value === "no-results" && noResults.value.kind === "no-match" && !!noResultsSearch.value,
  async (needed) => {
    const request = ++discoverStatusRequest;
    if (!needed) return;
    try {
      const status = await api.getDiscoverStatus();
      if (request === discoverStatusRequest) {
        discoverAvailable.value = status.success !== false && status.enabled !== false && !!status.installed;
      }
    } catch {
      if (request === discoverStatusRequest) discoverAvailable.value = false;
    }
  },
  { immediate: true },
);
const lookInDiscover = () => {
  discoverSession.search = noResultsSearch.value;
  discoverSession.scrollTop = 0;
  void router.push("/discover");
};
const clearSearch = () => {
  searchQuery.value = "";
  searchInput.value?.focus();
};
const GETTING_STARTED_URL = "https://github.com/kirin-3/wLib/wiki/Getting-Started";
const openGettingStarted = async () => {
  try {
    const result = await api.openInBrowser(GETTING_STARTED_URL);
    if (!result.success) notifyError(`Could not open the user guide:\n\n${result.error || "Unknown error"}`);
  } catch (cause) {
    notifyError(`Could not open the user guide: ${String(cause)}`);
  }
};
// The first-run ways to add games, after the primary "Import a folder".
const firstRunActions: Array<{ id: string; label: string; detail: string; icon: typeof IconClock; run: () => void }> = [
  {
    id: "extension",
    label: "Use the browser extension",
    detail: "Add a game from its F95Zone page with one click.",
    icon: IconPuzzle,
    run: () => void router.push("/extension"),
  },
  {
    id: "external",
    label: "Import from another app",
    detail: "F95Checker, Atlas, Playnite and more.",
    icon: IconFileImport,
    run: () => void router.push("/import-export#external"),
  },
  {
    id: "manual",
    label: "Add one game",
    detail: "Enter a title, an executable or a thread link.",
    icon: IconPlus,
    run: () => { showAddModal.value = true; },
  },
];

// Only the cards near the visible area are in the page (see utils/libraryWindow.ts).
// Empty space above and below them keeps the full scroll height.
const libraryScroller = useTemplateRef<HTMLElement>("libraryScroller");
// The list scrolls inside its own wrapper (it also scrolls sideways).
const listScroller = useTemplateRef<HTMLElement>("listScroller");
const windowScroller = () => (layoutMode.value === "list" ? listScroller.value : libraryScroller.value);
// The grid div or the list tbody, whichever layout is shown.
const windowContainer = ref<HTMLElement | null>(null);
let windowMetrics: WindowMetrics | null = null;
// Set when the layout changes; only then is the pitch taken again (see nextMetrics).
let pitchStale = true;
let measuredWidth = -1;
const shownWindow = shallowRef<RenderWindow>(renderWindow(0, null, { top: 0, height: 0 }));
let windowView: WindowView = { top: 0, height: 0 };
// The focused card's game: its rows stay in the page while focus is near the screen.
let focusedGameId: number | null = null;
let focusMovedAt = -Infinity;
let focusHoldTimer: ReturnType<typeof setTimeout> | undefined;
const updateWindow = () => {
  const focus = focusedGameId === null ? -1 : filteredGames.value.findIndex((game) => game.id === focusedGameId);
  const moving = performance.now() - focusMovedAt < FOCUS_HOLD_MS;
  const next = renderWindow(filteredGames.value.length, windowMetrics, windowView, focus < 0 ? null : focus, moving);
  if (!sameWindow(next, shownWindow.value)) shownWindow.value = next;
};
const measureWindow = () => {
  const scroller = windowScroller();
  const container = windowContainer.value;
  if (!scroller || !container || !container.isConnected) {
    updateWindow();
    return;
  }
  if (container.clientWidth !== measuredWidth) {
    measuredWidth = container.clientWidth;
    pitchStale = true;
  }
  const style = getComputedStyle(container);
  const columns = layoutMode.value === "list" ? 1 : gridColumnCount(style.gridTemplateColumns);
  const items = container.querySelectorAll<HTMLElement>(":scope > [data-game-id]");
  // Rectangles are in viewport pixels; scroll offsets, the row gap and the
  // padding are in CSS pixels. They differ under the interface scale's zoom.
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
  const origin = toCssPx(container.getBoundingClientRect().top - scroller.getBoundingClientRect().top) - scroller.clientTop + scroller.scrollTop;
  windowView = { top: scroller.scrollTop - origin, height: scroller.clientHeight };
  updateWindow();
};
const renderedGames = computed(() => {
  const { start, end } = shownWindow.value;
  const list = filteredGames.value;
  return start === 0 && end >= list.length ? list : list.slice(start, end);
});
// Measure again after the shown cards change: the columns and the position come
// from them. The pitch is taken again only after a layout change.
watch([filteredGames, layoutMode, cardSize], () => {
  pitchStale = true;
  measureWindow();
}, { flush: "post" });
watch(shownWindow, measureWindow, { flush: "post" });
let windowResize: ResizeObserver | null = null;
watch([libraryScroller, listScroller, windowContainer], (elements) => {
  windowResize?.disconnect();
  if (typeof ResizeObserver === "undefined") return;
  windowResize ??= new ResizeObserver(() => measureWindow());
  for (const element of elements) if (element) windowResize.observe(element);
}, { flush: "post" });
onUnmounted(() => {
  windowResize?.disconnect();
  clearTimeout(focusHoldTimer);
});
const trackWindowFocus = (event: FocusEvent) => {
  const target = event.target instanceof Element ? event.target : null;
  const card = windowContainer.value && target && windowContainer.value.contains(target) ? target.closest<HTMLElement>("[data-game-id]") : null;
  const id = card ? Number(card.dataset.gameId) : null;
  if (id === focusedGameId) return;
  focusedGameId = id;
  if (id === null) return;
  // Keep the focused card while the smooth scroll catches up, then let the
  // distance limit apply again.
  focusMovedAt = performance.now();
  clearTimeout(focusHoldTimer);
  focusHoldTimer = setTimeout(updateWindow, FOCUS_HOLD_MS + 50);
  measureWindow();
};
const libraryGridStyle = computed(() => ({
  "--card-scale": cardScales[cardSize.value],
  paddingTop: `${shownWindow.value.padTop}px`,
  paddingBottom: `calc(3rem + ${shownWindow.value.padBottom}px)`,
}));
const handleCardClick = (game: GameRecord, event: MouseEvent | KeyboardEvent) => {
  const ids = filteredGames.value.map((item) => item.id);
  if (event.shiftKey) selection.selectRange(ids, game.id);
  else if (event.ctrlKey || event.metaKey || selecting.value || selectedIds.value.size) selection.toggle(game.id);
  else openDetail(game);
  if (!selectedIds.value.size) selecting.value = false;
};
const clearSelection = () => { selection.clear(); selecting.value = false; };
// Cards and rows expose their selected state only in selection mode, so a card
// that opens details is not announced as an unchecked item.
const selectionMode = computed(() => selecting.value || selectedIds.value.size > 0);
const libraryItemHint = computed(() =>
  selectionMode.value
    ? "Enter toggles selection. Shift+Enter selects a range. Shift+F10 opens actions."
    : "Enter opens details. Ctrl+Enter selects. Shift+F10 opens actions.",
);

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
const eligibleChecks = computed(() => selectedGames.value.filter(updateCheckUrl));
const eligibleF95Refreshes = computed(() => selectedGames.value.filter((game) => !!game.f95_url));
onMounted(() => {
  onWebviewReady(() => {
    void loadPlatformCapabilities().then((capabilities) => { bulkShowRuntime.value = capabilities.platform === "linux"; });
  });
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
    const ids = games.filter(updateCheckUrl).map((game) => game.id);
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
  filterMinRating.value = normalized.filterMinRating;
  filterThreadStates.value = [...normalized.filterThreadStates];
  filterSources.value = [...normalized.filterSources];
  filterEngines.value = [...normalized.filterEngines];
  filterTags.value = [...normalized.filterTags];
  filterExcludedSources.value = [...normalized.filterExcludedSources];
  filterExcludedEngines.value = [...normalized.filterExcludedEngines];
  filterExcludedTags.value = [...normalized.filterExcludedTags];
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
    filterMinRating: filterMinRating.value,
    filterThreadStates: [...filterThreadStates.value],
    filterSources: [...filterSources.value],
    filterEngines: [...filterEngines.value],
    filterTags: [...filterTags.value],
    filterExcludedSources: [...filterExcludedSources.value],
    filterExcludedEngines: [...filterExcludedEngines.value],
    filterExcludedTags: [...filterExcludedTags.value],
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
  const url = updateCheckUrl(game);
  if (!url) {
    return {
      type: "error" as const,
      message: `${game.title} has no active update thread.`,
    };
  }

  updatingId.value = game.id;
  try {
    const result = await api.checkForUpdates(url);
    if (result && result.success) {
      await loadGames();
      if (result.note) notify(result.note, "info");
      if (result.newly_abandoned) notify(`${game.title} was abandoned by its developer`, "info");
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
      const localeError = localeEmulatorLaunchError(result.code || result.error_code, result.error);
      if (localeError) {
        notify(localeError.message, "error", "Could not launch game", { label: localeError.action, run: () => { void router.push(localeError.route); } });
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
    if (action === "import" && (queryUrl || readQueryValue(q.lcurl) || readStoreLinks(readQueryValue(q.links)).length)) {
      await loadGames();
      if (route.query !== q) return;
      if (showDetailModal.value && detailModal.value) {
        const closed = await detailModal.value.requestClose();
        if (route.query !== q) return;
        if (!closed) {
          void router.replace({ path: "/", query: {} });
          return;
        }
      }
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
        if (route.query !== q) return;
        const lcUrl = normalizeLcThreadUrl(queryUrl);
        const gameId = extensionGameId(q.gameId);
        const match = games.value.find((g) =>
          gameId ? g.id === gameId : lcUrl ? normalizeLcThreadUrl(g.lc_url) === lcUrl : urlsMatchByThreadIdentity(g.f95_url, queryUrl),
        );
        if (match && showDetailModal.value && selectedGame.value && selectedGame.value.id !== match.id) {
          // Switch games through the modal's own close so unsaved edits get the
          // discard prompt and the next open reloads every per-game panel.
          const closed = await detailModal.value?.requestClose();
          if (route.query !== q) return;
          if (closed) {
            await nextTick();
            if (route.query !== q) return;
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
      if (route.query === q) void router.replace({ path: "/", query: {} });
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
      gameData.lc_url || "",
      "",
      gameData.lc_dismissed || "",
      gameData.thread_completed,
      gameData.screenshots || [],
      gameData.thread_onhold,
      gameData.thread_abandoned,
      gameData.thread_states_seen_at || "",
      gameData.original_title || "",
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
  if (event.target === windowScroller()) measureWindow();
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
    filterMinRating,
    filterThreadStates,
    filterSources,
    filterEngines,
    filterTags,
    filterExcludedSources,
    filterExcludedEngines,
    filterExcludedTags,
    isFiltersCollapsed,
    filterSections,
    activeCollection,
  ],
  () => {
    persistLibraryViewState();
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
  const normalized = normalizeLibraryViewState(
    { filterEngines: filterEngines.value, filterExcludedEngines: filterExcludedEngines.value },
    { validEngines: engines },
  );

  if (!sameStringArray(normalized.filterEngines, filterEngines.value)) {
    filterEngines.value = normalized.filterEngines;
  }
  if (!sameStringArray(normalized.filterExcludedEngines, filterExcludedEngines.value)) {
    filterExcludedEngines.value = normalized.filterExcludedEngines;
  }
});

watch(uniqueTags, (tags) => {
  const normalized = normalizeLibraryViewState(
    { filterTags: filterTags.value, filterExcludedTags: filterExcludedTags.value },
    { validTags: tags },
  );

  if (!sameStringArray(normalized.filterTags, filterTags.value)) {
    filterTags.value = normalized.filterTags;
  }
  if (!sameStringArray(normalized.filterExcludedTags, filterExcludedTags.value)) {
    filterExcludedTags.value = normalized.filterExcludedTags;
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
  libraryFiltersPaneOpen.value = !filtersAsDrawer.value && !isFiltersCollapsed.value;
  onWebviewReady(() => {
    void loadTagColors().catch(console.error);
    void loadGames();
    void loadRunningGames();
    void loadLibrarySettings();
  });
});
useEventListener(window, "wlib-refresh-library", handleLibraryRefresh);
useEventListener(window, "wlib-playtime-tick", handlePlaytimeTick);
useEventListener(document, "click", handleDocumentClick);
useEventListener(document, "keydown", handleLibraryKeyboard);
useEventListener(document, "scroll", handleLibraryScroll, true);
useEventListener(document, "focusin", trackWindowFocus);
useEventListener(window, "resize", handleDocumentClick);

onUnmounted(() => {
  libraryFiltersPaneOpen.value = false;
  settingsRequest++;
  if (modalUpdateTimeout) {
    clearTimeout(modalUpdateTimeout);
  }
});
</script>

<template>
  <div ref="libraryControllerScope" class="h-full flex overflow-hidden">
    <!-- Filters: a pane beside the games in wide windows, a drawer over them in narrow ones. -->
    <div
      v-if="filtersAsDrawer && filtersDrawerOpen"
      class="filters-backdrop"
      aria-hidden="true"
      @click="closeFiltersDrawer"
    ></div>
    <aside
      ref="filtersPane"
      :inert="!filtersPaneShown"
      :aria-hidden="!filtersPaneShown"
      :role="filtersAsDrawer ? 'dialog' : undefined"
      :aria-modal="filtersAsDrawer ? 'true' : undefined"
      :aria-labelledby="filtersAsDrawer ? 'library-filters-title' : undefined"
      :tabindex="filtersAsDrawer ? -1 : undefined"
      :class="
        filtersAsDrawer
          ? ['filters-pane filters-drawer', { 'filters-drawer--open': filtersDrawerOpen }]
          : ['filters-pane shrink-0 h-full collapse-width-transition', isFiltersCollapsed ? 'w-0 filters-pane-collapsed' : 'w-64']
      "
    >
      <div class="h-full flex flex-col overflow-y-auto" :class="filtersAsDrawer ? 'w-full' : 'w-64'">
        <div v-if="filtersAsDrawer" class="filters-drawer-head">
          <h2 id="library-filters-title" class="filters-drawer-title">Filters</h2>
          <button
            type="button"
            class="ui-icon-btn filters-drawer-close"
            aria-label="Close filters"
            title="Close filters"
            @click="closeFiltersDrawer"
          >
            <IconX class="w-5 h-5" aria-hidden="true" />
          </button>
        </div>
        <div class="px-4 pb-2 pt-4">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.collections"
              aria-controls="library-filter-collections"
              @click="toggleFilterSection('collections')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span>Quick filters</span>
            </button>
          </div>
          <div v-show="filterSections.collections" id="library-filter-collections" ref="quickFiltersList" class="space-y-1 mt-3">
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

        <div v-if="savedPresets.length" class="px-4 pb-2 pt-4">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.presets"
              aria-controls="library-filter-presets"
              @click="toggleFilterSection('presets')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span>Saved filters</span>
            </button>
          </div>
          <div v-show="filterSections.presets" id="library-filter-presets" class="space-y-1 mt-3">
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

        <!-- Every filter value below is a pill: a filled pill with a check is on,
             a red pill with a ban icon is excluded. The label beside each
             heading says how the picked values combine. -->
        <div class="px-4 pb-2 pt-4">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.status"
              aria-controls="library-filter-status"
              @click="toggleFilterSection('status')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span id="library-filter-status-label">Play status</span>
            </button>
            <span class="filter-rule" title="Shows games with any of the picked play statuses">any of</span>
            <button
              v-if="filterStatuses.length"
              type="button"
              class="filter-clear filter-clear--icon"
              aria-label="Clear play status filters"
              title="Clear play status filters"
              @click="filterStatuses = []"
            >
              <IconX class="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          </div>
          <div
            v-show="filterSections.status"
            id="library-filter-status"
            role="group"
            aria-labelledby="library-filter-status-label"
            class="filter-pill-list mt-3"
          >
            <FilterPill
              v-for="s in allStatuses"
              :key="s.value"
              :label="s.label"
              :state="statusFilterActive(s.value) ? 'include' : 'neutral'"
              @activate="toggleStatusFilterValue(s.value)"
            >
              <template #icon>
                <component :is="s.icon" class="filter-status-icon" :class="s.toneClass" aria-hidden="true" />
              </template>
            </FilterPill>
          </div>
        </div>

        <div class="px-4 pb-2 pt-4">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.install"
              aria-controls="library-filter-install"
              @click="toggleFilterSection('install')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span id="library-filter-install-label">Install state</span>
            </button>
            <span class="filter-rule" title="Click the picked value again to show all games">one of</span>
          </div>
          <div
            v-show="filterSections.install"
            id="library-filter-install"
            role="group"
            aria-labelledby="library-filter-install-label"
            class="filter-pill-list mt-3"
          >
            <FilterPill
              v-for="option in installStateOptions"
              :key="option.value"
              :label="option.label"
              :state="filterInstallState === option.value ? 'include' : 'neutral'"
              @activate="toggleInstallState(option.value)"
            />
          </div>
        </div>

        <div class="px-4 pb-2 pt-4">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.rating"
              aria-controls="library-filter-rating"
              @click="toggleFilterSection('rating')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span id="library-filter-rating-label">Weighted rating</span>
            </button>
            <span class="filter-rule" title="Shows games with at least this weighted F95 rating. Click the picked value again to show all games">one of</span>
          </div>
          <div
            v-show="filterSections.rating"
            id="library-filter-rating"
            role="group"
            aria-labelledby="library-filter-rating-label"
            class="filter-pill-list mt-3"
          >
            <FilterPill
              v-for="option in ratingOptions"
              :key="option.value"
              :label="option.label"
              :title="`Weighted F95 rating of ${option.value} or higher`"
              :state="filterMinRating === option.value ? 'include' : 'neutral'"
              @activate="toggleMinRating(option.value)"
            />
          </div>
        </div>

        <div class="px-4 pb-2 pt-4">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.threadStates"
              aria-controls="library-filter-thread"
              @click="toggleFilterSection('threadStates')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span id="library-filter-thread-label">Thread state</span>
            </button>
            <span class="filter-rule" title="Shows games in any of the picked thread states">any of</span>
            <button
              v-if="filterThreadStates.length"
              type="button"
              class="filter-clear filter-clear--icon"
              aria-label="Clear thread state filters"
              title="Clear thread state filters"
              @click="filterThreadStates = []"
            >
              <IconX class="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          </div>
          <div
            v-show="filterSections.threadStates"
            id="library-filter-thread"
            role="group"
            aria-labelledby="library-filter-thread-label"
            class="filter-pill-list mt-3"
          >
            <FilterPill
              v-for="option in threadStateOptions"
              :key="option.value"
              :label="option.label"
              :state="filterThreadStates.includes(option.value) ? 'include' : 'neutral'"
              @activate="toggleThreadStateFilter(option.value)"
            >
              <template #icon>
                <component :is="option.icon" aria-hidden="true" />
              </template>
            </FilterPill>
          </div>
        </div>

        <div class="px-4 pb-2 pt-4">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.sources"
              aria-controls="library-filter-sources"
              @click="toggleFilterSection('sources')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span id="library-filter-sources-label">Sources</span>
            </button>
            <span class="filter-rule" title="Shows games from any included source and from none of the excluded ones">any of</span>
            <button
              v-if="filterSources.length || filterExcludedSources.length"
              type="button"
              class="filter-clear filter-clear--icon"
              aria-label="Clear source filters"
              title="Clear source filters"
              @click="clearSourceFilters"
            >
              <IconX class="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          </div>
          <div
            v-show="filterSections.sources"
            id="library-filter-sources"
            role="group"
            aria-labelledby="library-filter-sources-label"
            class="filter-pill-list mt-3"
          >
            <FilterPill
              v-for="source in LIBRARY_SOURCES"
              :key="source.key"
              :label="source.label"
              :state="sourceState(source.key)"
              excludable
              @activate="setSourceFilter(toggleIncludedFilterValue, source.key)"
              @exclude="setSourceFilter(toggleExcludedFilterValue, source.key)"
            />
          </div>
        </div>

        <div class="px-4 pb-2 pt-4">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.engines"
              aria-controls="library-filter-engines"
              @click="toggleFilterSection('engines')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span id="library-filter-engines-label">Engines</span>
            </button>
            <span class="filter-rule" title="Shows games made with any included engine and with none of the excluded ones">any of</span>
            <button
              v-if="filterEngines.length || filterExcludedEngines.length"
              type="button"
              class="filter-clear filter-clear--icon"
              aria-label="Clear engine filters"
              title="Clear engine filters"
              @click="clearEngineFilters"
            >
              <IconX class="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          </div>
          <div
            v-show="filterSections.engines"
            id="library-filter-engines"
            role="group"
            aria-labelledby="library-filter-engines-label"
            class="filter-pill-list mt-3"
          >
            <FilterPill
              v-for="engine in uniqueEngines"
              :key="engine"
              :label="engine"
              :state="engineState(engine)"
              excludable
              @activate="setEngineFilter(toggleIncludedFilterValue, engine)"
              @exclude="setEngineFilter(toggleExcludedFilterValue, engine)"
            />
            <div
              v-if="!uniqueEngines.length"
              class="text-xs italic"
              style="color: var(--text-muted)"
            >
              No engines found
            </div>
          </div>
        </div>

        <div class="px-4 pb-6 pt-4 flex-1">
          <div class="filter-section-head">
            <button
              type="button"
              class="filter-section-toggle ui-hover-surface"
              :aria-expanded="filterSections.tags"
              aria-controls="library-filter-tags"
              @click="toggleFilterSection('tags')"
            >
              <IconChevronDown class="filter-section-chevron" aria-hidden="true" />
              <span id="library-filter-tags-label">Tags</span>
            </button>
            <span class="filter-rule" title="Shows games that have every included tag and none of the excluded ones">all of</span>
            <button
              v-if="filterTags.length || filterExcludedTags.length"
              type="button"
              class="filter-clear filter-clear--icon"
              aria-label="Clear tag filters"
              title="Clear tag filters"
              @click="clearTagFilters"
            >
              <IconX class="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          </div>
          <div v-show="filterSections.tags" id="library-filter-tags" class="mt-3">
            <input
              v-model="tagSearchQuery"
              type="search"
              aria-label="Search tags"
              placeholder="Search tags..."
              class="library-search-input w-full rounded-lg px-3 py-2 text-xs mb-3"
            />
            <div
              ref="tagList"
              role="group"
              aria-labelledby="library-filter-tags-label"
              class="filter-tag-list max-h-64 overflow-y-auto"
              @pointerenter="onTagListPointerEnter"
              @pointerleave="onTagListPointerLeave"
              @pointerdown="onTagListPointerDown"
              @focusin="onTagListFocusIn"
              @focusout="onTagListFocusOut"
            >
              <div
                v-if="orderedTags.pinned.length"
                role="group"
                aria-label="Active tags"
                class="pinned-tags filter-pill-list p-2 mb-2 rounded-lg"
              >
                <FilterPill
                  v-for="option in orderedTags.pinned"
                  :key="option.label"
                  :data-tag="option.label"
                  :label="option.label"
                  :count="option.count"
                  :color="colorForTag(option.label)"
                  :state="tagState(option.label)"
                  excludable
                  class="tag-pill"
                  @activate="setTagFilter(toggleIncludedFilterValue, option.label)"
                  @exclude="setTagFilter(toggleExcludedFilterValue, option.label)"
                  @contextmenu="openTagColorPicker($event, option.label)"
                  @keydown.shift.f10.prevent="openTagColorPicker($event, option.label)"
                />
              </div>
              <div class="filter-pill-list">
                <FilterPill
                  v-for="option in orderedTags.rest"
                  :key="option.label"
                  :data-tag="option.label"
                  :label="option.label"
                  :count="option.count"
                  :color="colorForTag(option.label)"
                  :state="tagState(option.label)"
                  excludable
                  class="tag-pill"
                  @activate="setTagFilter(toggleIncludedFilterValue, option.label)"
                  @exclude="setTagFilter(toggleExcludedFilterValue, option.label)"
                  @contextmenu="openTagColorPicker($event, option.label)"
                  @keydown.shift.f10.prevent="openTagColorPicker($event, option.label)"
                />
                <div
                  v-if="!orderedTags.rest.length && (tagSearchQuery.trim() || !orderedTags.pinned.length)"
                  class="text-xs italic"
                  style="color: var(--text-muted)"
                >
                  {{ uniqueTags.length ? "No matching tags" : "No tags found" }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </aside>

    <!-- Main Content Area -->
    <div ref="libraryScroller" class="library-scroller flex-1 min-w-0 p-5 overflow-y-auto flex flex-col relative">
      <header class="library-header mb-4 shrink-0">
        <div class="library-header-row">
          <h2
            class="ui-page-heading text-3xl font-bold tracking-tight shrink-0"
            style="color: var(--text-primary)"
          >
            <IconDeviceGamepad2 class="ui-page-heading-icon" />
            <span>Your Library</span>
          </h2>
          <p v-if="librarySummary" class="library-subtitle text-sm pl-3">
            {{ librarySummary }}
          </p>
          <!-- Always secondary: the first-run panel owns the primary action of an empty
               library, and with games, Play and the update markers lead. -->
          <MenuButton
            split
            class="library-add-menu"
            :items="addMenuItems"
            menu-label="More ways to add"
            button-label="More ways to add"
            main-label="Add game"
            align="end"
            @main="showAddModal = true"
            @select="chooseAddMenuItem"
          >
            <template #main>
              <IconLibraryPlus class="ui-action-icon" aria-hidden="true" />
              <span class="library-add-label">Add game</span>
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
              <button type="submit" class="ui-icon-btn p-1 rounded" title="Save name" aria-label="Save collection name">
                <IconCheck class="w-4 h-4" aria-hidden="true" />
              </button>
              <button
                type="button"
                class="ui-icon-btn p-1 rounded"
                title="Cancel rename"
                aria-label="Cancel rename"
                @click="cancelCollectionRename"
              >
                <IconX class="w-4 h-4" aria-hidden="true" />
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
            <button type="submit" class="ui-icon-btn p-1 rounded" title="Create collection" aria-label="Create collection">
              <IconCheck class="w-4 h-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              class="ui-icon-btn p-1 rounded"
              title="Cancel"
              aria-label="Cancel new collection"
              @click="cancelInlineCollectionCreate"
            >
              <IconX class="w-4 h-4" aria-hidden="true" />
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

      <!-- Search, Filters, Select and the View menu share one row; narrow columns put search on its own row. -->
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
            placeholder="Search titles, aliases, developer, tags or engine..."
            class="library-search-input w-full rounded-lg pl-10 pr-10 py-2 text-sm focus:outline-none"
          />
          <!-- Named after the field, so it differs from the no-results "Clear search" button. -->
          <button
            v-if="searchQuery"
            type="button"
            aria-label="Clear search field"
            title="Clear search field"
            class="ui-icon-btn absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded"
            @click="clearSearch"
          >
            <IconX class="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div class="library-toolbar-controls">
          <button
            @click="toggleFiltersPane"
            class="library-toolbar-btn ui-action-btn relative ui-hover-surface active:scale-95"
            :aria-expanded="filtersPaneShown"
            :aria-label="activeFilterCount > 0 ? `Filters, ${activeFilterCount} active` : 'Filters'"
            :title="filtersPaneShown ? 'Hide filters' : 'Show filters'"
          >
            <IconFilterFilled class="ui-action-icon" aria-hidden="true" />
            <span class="library-toolbar-label">Filters</span>
            <span
              v-if="activeFilterCount > 0"
              class="absolute -top-1.5 -right-1.5 min-w-5 h-5 rounded-full px-1 text-xs font-bold flex items-center justify-center"
              style="background: var(--brand); color: var(--on-brand)"
              aria-hidden="true"
            >
              {{ activeFilterCount }}
            </span>
          </button>
          <button
            type="button"
            class="library-toolbar-btn ui-action-btn ui-hover-surface active:scale-95"
            :aria-pressed="selectionMode"
            aria-label="Select"
            title="Select games for bulk actions"
            @click="selecting = !selecting; if (!selecting) selection.clear()"
          >
            <IconListCheck class="ui-action-icon" aria-hidden="true" />
            <span class="library-toolbar-label">Select</span>
          </button>

          <!-- Layout, sort, order and card size. The list column headers change the same sort. -->
          <MenuButton
            radio
            :columns="2"
            class="library-view-menu"
            align="end"
            :items="viewMenuItems"
            menu-label="View options"
            :button-label="`View: ${currentLayout.label}, sorted by ${sortSummary}`"
            :button-title="`View options. ${currentLayout.label} layout, sorted by ${sortSummary}`"
            @select="chooseViewMenuItem"
          >
            <component :is="currentLayout.icon" class="ui-action-icon" aria-hidden="true" />
            <span class="library-toolbar-label">View</span>
            <span class="library-view-sort">{{ sortSummary }}</span>
          </MenuButton>

          <span
            v-if="filteredGames.length !== games.length"
            class="library-result-count text-xs whitespace-nowrap"
            >{{ filteredGames.length }} of {{ games.length }}</span
          >
        </div>
      </div>

      <div v-if="activeFilterChips.length" class="flex flex-wrap items-center gap-2 pb-3" role="group" aria-label="Active filters">
        <button
          v-for="chip in activeFilterChips"
          :key="chip.key"
          class="filter-chip ui-action-btn px-3 py-1 rounded-full text-xs"
          :class="{ 'filter-chip--excluded': chip.excluded }"
          :aria-label="`Remove filter ${chip.label}`"
          @click="removeFilterChip(chip)"
        >
          <IconBan v-if="chip.excluded" class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          <span>{{ chip.label }}</span>
          <IconX class="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
        </button>
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
        <button class="filter-clear filter-clear--all ui-action-btn" @click="clearFilters">Clear all</button>
      </div>

      <!-- Keyboard help for the focused card or row (their aria-describedby). -->
      <p id="library-item-hint" class="sr-only">{{ libraryItemHint }}</p>
      <div v-if="displayState === 'error'" class="empty-state flex-1" role="alert">
        <IconAlertTriangle class="empty-state-icon" aria-hidden="true" />
        <h3 class="empty-state-title">{{ loadError }}</h3>
        <p v-if="loadErrorCause" class="library-load-cause">Cause: {{ loadErrorCause }}</p>
        <p class="empty-state-subtext">Retry, or restart wLib if this keeps happening.</p>
        <button
          class="ui-action-btn px-3 py-1.5 rounded-lg text-sm font-medium border active:scale-95 active:bg-[var(--bg-overlay)]"
          style="color: var(--text-primary); border-color: var(--border-hover)"
          @click="loadGames"
        >
          <IconRefresh class="ui-action-icon" aria-hidden="true" />
          Retry
        </button>
      </div>
      <div v-else-if="displayState === 'loading'" class="library-skeletons grid gap-5 pb-12"
        :class="layoutMode === 'list' ? 'library-skeletons--list' : `library-cards--${layoutMode}`"
        role="status" aria-label="Loading library" aria-busy="true">
        <div v-for="n in 12" :key="n" class="library-skeleton rounded-xl"
          :class="`library-skeleton--${layoutMode}`" aria-hidden="true"></div>
      </div>
      <section
        v-else-if="displayState === 'empty'"
        class="empty-state library-first-run flex-1"
        aria-labelledby="library-first-run-title"
      >
        <IconDeviceGamepad2Filled class="empty-state-icon" aria-hidden="true" />
        <h3 id="library-first-run-title" class="empty-state-title">Add your games</h3>
        <p class="empty-state-subtext library-first-run-lead">
          wLib keeps each game's version, updates and playtime in one place.
        </p>
        <button
          type="button"
          class="library-first-run-primary"
          data-controller-primary
          aria-describedby="library-first-run-primary-hint"
          @click="showImportModal = true"
        >
          <IconFolderPlus class="ui-action-icon" aria-hidden="true" />
          Import a folder of games
        </button>
        <p id="library-first-run-primary-hint" class="empty-state-hint">
          Each subfolder that holds a game becomes one game.
        </p>
        <ul class="library-first-run-list" aria-label="Other ways to add games">
          <li v-for="action in firstRunActions" :key="action.id">
            <button type="button" class="library-first-run-option" @click="action.run()">
              <component :is="action.icon" class="library-first-run-option-icon" aria-hidden="true" />
              <span class="library-first-run-option-text">
                <span class="library-first-run-option-label">{{ action.label }}</span>
                <span class="library-first-run-option-detail">{{ action.detail }}</span>
              </span>
              <IconChevronRight class="library-first-run-option-chevron" aria-hidden="true" />
            </button>
          </li>
        </ul>
        <button type="button" class="library-first-run-help" @click="openGettingStarted">
          New to wLib? Read the Getting Started guide
          <IconExternalLink class="library-first-run-help-icon" aria-hidden="true" />
          <span class="sr-only">(opens in your browser)</span>
        </button>
      </section>

      <div v-else-if="displayState === 'no-results'" class="empty-state library-no-results flex-1">
        <component :is="noResultsIcon" class="empty-state-icon" aria-hidden="true" />
        <div class="library-no-results-text" role="status">
          <h3 class="empty-state-title">{{ noResults.title }}</h3>
          <p v-if="noResults.detail" class="empty-state-subtext">{{ noResults.detail }}</p>
        </div>
        <div class="library-no-results-actions">
          <button
            v-if="noResults.kind === 'collection-empty'"
            type="button"
            class="library-no-results-btn ui-action-btn"
            data-controller-primary
            @click="activeCollection = ''"
          >
            Show all games
          </button>
          <template v-else>
            <button
              v-if="noResultsSearch"
              type="button"
              class="library-no-results-btn ui-action-btn"
              data-controller-primary
              @click="clearSearch"
            >
              <IconX class="ui-action-icon" aria-hidden="true" />
              Clear search
            </button>
            <button
              v-if="activeFilterCount"
              type="button"
              class="library-no-results-btn ui-action-btn"
              :data-controller-primary="noResultsSearch ? undefined : ''"
              @click="clearFilterValues"
            >
              <IconFilterFilled class="ui-action-icon" aria-hidden="true" />
              {{ noResults.kind === "quick-filter-empty" && !activeCollection ? "Show all games" : "Clear filters" }}
            </button>
            <button
              v-if="activeCollection && noResults.kind === 'no-match'"
              type="button"
              class="library-no-results-btn ui-action-btn"
              @click="activeCollection = ''"
            >
              Look in all games
            </button>
            <button
              v-if="discoverAvailable && noResultsSearch && noResults.kind === 'no-match'"
              type="button"
              class="library-no-results-btn ui-action-btn"
              @click="lookInDiscover"
            >
              <IconCompass class="ui-action-icon" aria-hidden="true" />
              Look in Discover
            </button>
          </template>
        </div>
      </div>

      <div v-else-if="layoutMode === 'list'" ref="listScroller" class="library-table-wrap pb-12">
        <table class="library-table library-window" role="grid" aria-label="Games" aria-multiselectable="true" :aria-rowcount="filteredGames.length + 1">
          <caption class="sr-only">
            Games in your library. Select a column header to sort.
          </caption>
          <thead>
            <tr aria-rowindex="1">
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
          <tbody ref="windowContainer">
            <tr v-if="shownWindow.padTop > 0" class="library-table-spacer" aria-hidden="true">
              <td :colspan="tableColumns.length + 1" :style="{ height: `${shownWindow.padTop}px` }"></td>
            </tr>
            <GameRow
              v-for="(game, index) in renderedGames"
              :key="game.id"
              :game="game"
              :row-index="shownWindow.start + index + 2"
              :move-state="gameMoves.moveStateFor(game.id).state"
              :move-percent="gameMoves.moveStateFor(game.id).percent"
              :selected="selectedIds.has(game.id)"
              :selection-mode="selectionMode"
              :running="runningGameIds.has(game.id)"
              :primary="shownWindow.start + index === shownWindow.primary"
              @activate="handleCardClick"
              @contextmenu="openContextMenu"
              @toggle-game="toggleGame"
              @target-select="launchTargetFromSelect"
            />
            <tr v-if="shownWindow.padBottom > 0" class="library-table-spacer" aria-hidden="true">
              <td :colspan="tableColumns.length + 1" :style="{ height: `${shownWindow.padBottom}px` }"></td>
            </tr>
          </tbody>
        </table>
      </div>

      <div
        v-else
        ref="windowContainer"
        role="grid"
        aria-label="Games"
        aria-multiselectable="true"
        :aria-rowcount="filteredGames.length"
        class="library-cards library-window grid gap-4 pb-12"
        :style="libraryGridStyle"
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
          :selection-mode="selectionMode"
          :row-index="shownWindow.start + index + 1"
          :primary="shownWindow.start + index === shownWindow.primary"
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
            <div role="separator" class="context-sep" />
            <button @click="selection.toggle(contextMenu.game.id); selecting = true; closeContextMenu()">Select</button>
            <button @click="runContextAction('play')">
              {{ isWishlisted(contextMenu.game) ? "Set up" : runningGameIds.has(contextMenu.game.id) ? "Stop" : "Play" }}
            </button>
            <div v-if="!(runningGameIds.has(contextMenu.game.id) && contextTools.length)" role="separator" class="context-sep" />
            <template v-if="runningGameIds.has(contextMenu.game.id) && contextTools.length">
              <div role="separator" class="context-sep" />
              <button v-for="tool in contextTools" :key="tool.id" type="button" class="context-item" :disabled="loadingCompanionId !== null" @click="launchContextTool(tool)">{{ tool.name }}</button>
              <div role="separator" class="context-sep" />
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
              {{ contextMenu.game.f95_url ? "Open F95 thread" : contextMenu.game.lc_url ? "Open LC thread" : "Open store page" }}
            </button>
            <button
              @click="runContextAction('check')"
              :disabled="!updateCheckUrl(contextMenu.game) || updatingId !== null"
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
            <div role="separator" class="context-sep" />
            <label
              class="context-status flex items-center justify-between gap-2"
              ><span class="whitespace-nowrap">Set status</span>
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
                class="context-status-select min-w-0 max-w-36"
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
            <div role="separator" class="context-sep" />
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
        :eligible-f95-refreshes="eligibleF95Refreshes.length"
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
  font-size: 0.75rem;
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
/* The cards move with the render window; scroll anchoring would fight it. */
.library-window {
  overflow-anchor: none;
}
.library-table-spacer td {
  padding: 0;
  border: 0;
}
/* Relative: the absolute sr-only text in the rows must anchor to this scroll
   area, or it stretches the outer library scroller past the list. */
.library-table-wrap {
  position: relative;
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
  font-size: 0.75rem;
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
.game-context-menu .context-sep {
  margin: 0.25rem 0.25rem;
  border-top: 1px solid var(--border);
}
.game-context-menu .context-status {
  padding: 0.3rem 0.5rem;
  font-size: 0.8rem;
}
.game-context-menu .context-status-select {
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--border);
  border-radius: 0.3rem;
  background: var(--bg-inset);
  color: var(--text-primary);
  font-size: 0.75rem;
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

/* Narrow windows: the pane slides in over the games from the left edge. */
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
/* The browser default placeholder is too faint (3.3:1 on white); the muted
   token keeps 4.5:1 or more in both themes. */
.library-search-input::placeholder {
  color: var(--text-muted);
  opacity: 1;
}
.library-load-cause {
  max-width: 60ch;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}

/* First run: one primary action, then the other ways in as a quiet list. */
.library-first-run {
  width: 100%;
  max-width: 30rem;
  margin-inline: auto;
}
.library-first-run-lead {
  max-width: 44ch;
  text-wrap: balance;
}
.library-first-run-primary {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  margin-top: 0.5rem;
  padding: 0.625rem 1.125rem;
  border-radius: 0.625rem;
  border: 1px solid var(--brand);
  background: var(--brand);
  color: var(--on-brand);
  font-size: 0.9375rem;
  font-weight: 600;
  box-shadow: var(--shadow-brand);
  transition: filter var(--motion-fast) ease-out, transform var(--motion-fast) ease-out;
}
.library-first-run-primary:hover { filter: brightness(1.08); }
.library-first-run-primary:active { transform: scale(0.97); }
.library-first-run-list {
  width: 100%;
  margin-top: 1rem;
  border: 1px solid var(--border);
  border-radius: 0.75rem;
  background: var(--bg-surface);
  overflow: hidden;
  text-align: left;
}
.library-first-run-list li + li { border-top: 1px solid var(--border); }
.library-first-run-option {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  padding: 0.75rem 1rem;
  color: var(--text-primary);
  text-align: left;
  transition: background-color var(--motion-fast) ease-out;
}
.library-first-run-option:hover { background: var(--bg-raised); }
.library-first-run-option:focus-visible { outline-offset: -2px; }
.library-first-run-option-icon {
  width: 1.25rem;
  height: 1.25rem;
  flex-shrink: 0;
  color: var(--text-secondary);
}
.library-first-run-option-text {
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
  min-width: 0;
  flex: 1;
}
.library-first-run-option-label {
  font-size: 0.875rem;
  font-weight: 600;
}
.library-first-run-option-detail {
  font-size: 0.8125rem;
  color: var(--text-secondary);
}
.library-first-run-option-chevron {
  width: 1rem;
  height: 1rem;
  flex-shrink: 0;
  color: var(--text-muted);
  transition: transform var(--motion-fast) ease-out, color var(--motion-fast) ease-out;
}
.library-first-run-option:hover .library-first-run-option-chevron {
  color: var(--text-primary);
  transform: translateX(2px);
}
.library-first-run-help {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  margin-top: 0.5rem;
  padding: 0.25rem 0.5rem;
  border-radius: 0.375rem;
  font-size: 0.875rem;
  color: var(--text-secondary);
  text-decoration: underline;
  text-decoration-color: var(--border-hover);
  text-underline-offset: 3px;
}
.library-first-run-help:hover {
  color: var(--text-primary);
  text-decoration-color: currentColor;
}
.library-first-run-help-icon { width: 0.875rem; height: 0.875rem; }

/* No results: the cause, then only the ways out that apply. */
.library-no-results-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.375rem;
  max-width: 52ch;
}
.library-no-results-text .empty-state-title {
  overflow-wrap: anywhere;
  text-wrap: balance;
}
.library-no-results-actions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0.5rem;
  margin-top: 0.25rem;
}
.library-no-results-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border-hover);
  border-radius: 0.5rem;
  color: var(--text-primary);
  font-size: 0.875rem;
  font-weight: 500;
}
.library-no-results-btn:hover {
  background: var(--bg-raised);
  border-color: var(--text-muted);
}
.library-no-results-btn:active {
  background: var(--bg-overlay);
  transform: scale(0.97);
}

/* Active-filter chips. An excluded value keeps the pill's ban icon and red
   tone, so the row reads the same way as the pane. */
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  color: var(--text-primary);
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

/* Filter section heading: the toggle, the match rule and a Clear button. */
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
  padding: 0.25rem 0.25rem;
  border-radius: 0.375rem;
  color: var(--text-muted);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-align: left;
  text-transform: uppercase;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.filter-section-toggle > span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.filter-section-toggle:focus-visible,
.filter-clear:focus-visible {
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
.filter-rule {
  flex-shrink: 0;
  color: var(--text-muted);
  font-size: 0.75rem;
  white-space: nowrap;
  cursor: help;
}
.filter-clear {
  flex-shrink: 0;
  padding: 0.125rem 0.25rem;
  border-radius: 0.3rem;
  color: var(--text-secondary);
  font-size: 0.75rem;
  font-weight: 500;
  transition: color 0.15s ease;
}
.filter-clear:hover {
  color: var(--danger-text);
}
.filter-clear--icon {
  display: grid;
  place-items: center;
  width: 1.5rem;
  height: 1.5rem;
  padding: 0;
}
.filter-clear--all {
  padding: 0.25rem 0.75rem;
}
/* The row gap leaves room for a pill's exclude button on its top corner. */
.filter-pill-list {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 0.375rem;
}
.filter-status-icon {
  color: var(--ui-status-text, currentColor);
}
/* Padding keeps the exclude button of the top and right pills inside the
   scroll area instead of clipping it or adding a sideways scroll bar. */
.filter-tag-list {
  overflow-x: hidden;
  padding: 0.5rem 0.5rem 0.25rem 0;
}
@media (prefers-reduced-motion: reduce) {
  .filter-section-chevron { transition: none; }
}

/* Heading row: the subtitle is hidden visually before the heading or "Add game" would wrap; screen readers keep it. */
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
/* Very narrow: a smaller heading and an icon-only "Add game" (its name stays). */
@container (max-width: 24rem) {
  .library-header-row h2 {
    font-size: 1.5rem;
  }
  .library-add-label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }
}
@container (max-width: 44rem) {
  .library-subtitle {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
}
.library-add-menu :deep(.menu-button-trigger) {
  font-size: 0.875rem;
  font-weight: 600;
}

/* One toolbar row. In a narrow column the search field takes its own row, and
   in a very narrow one the buttons drop their text (their names stay). */
.library-toolbar {
  container-type: inline-size;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
}
.library-toolbar-search {
  flex: 1 1 14rem;
  min-width: 0;
}
.library-toolbar-controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
  margin-left: auto;
}
.library-toolbar-btn {
  position: relative;
  min-height: 2.375rem;
  padding: 0.375rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  color: var(--text-primary);
  font-size: 0.875rem;
  font-weight: 500;
  white-space: nowrap;
}
.library-toolbar-btn[aria-pressed="true"] {
  background: var(--brand-glow);
  border-color: var(--brand-deep);
  color: var(--brand);
}
.library-view-menu :deep(.menu-button-toggle) {
  background: transparent;
  font-size: 0.875rem;
}
.library-view-menu :deep(.menu-button-trigger:hover:not(:disabled)) {
  background: var(--bg-overlay);
}
/* The current sort, beside "View", so the order is readable without opening the menu. */
.library-view-sort {
  max-width: 14rem;
  overflow: hidden;
  padding-left: 0.5rem;
  border-left: 1px solid var(--border);
  color: var(--text-secondary);
  font-weight: 400;
  text-overflow: ellipsis;
}
.library-result-count {
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
}
@container (max-width: 40rem) {
  .library-toolbar-search {
    flex-basis: 100%;
  }
  .library-toolbar-controls {
    flex: 1 1 auto;
    margin-left: 0;
  }
  .library-view-menu {
    margin-left: auto;
  }
}
@container (max-width: 30rem) {
  .library-view-sort {
    display: none;
  }
}
@container (max-width: 20rem) {
  .library-toolbar-label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
  }
}

.pinned-tags {
  background: var(--bg-raised);
  border: 1px solid var(--brand-deep);
}
</style>
