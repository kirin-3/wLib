<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import {
  IconColumns2Filled,
  IconDeviceGamepad2,
  IconDeviceGamepad2Filled,
  IconFilterFilled,
  IconLibraryPlus,
  IconLayoutGridFilled,
  IconLayoutListFilled,
  IconLoader2,
  IconPhotoFilled,
  IconPlayerPlayFilled,
  IconRefresh,
  IconZoom,
  IconStarFilled,
  IconX,
} from "@tabler/icons-vue";
import { api, onWebviewReady } from "../services/api";
import type { GameRecord } from "../services/api";
import AddGameModal from "../components/modals/AddGameModal.vue";
import GameDetailModal from "../components/modals/GameDetailModal.vue";
import {
  PLAY_STATUS_OPTIONS,
  getPlayStatusMeta,
  normalizePlayStatus,
} from "../utils/playStatus";
import {
  DEFAULT_FILTER_SECTIONS,
  DEFAULT_LIBRARY_VIEW_STATE,
  clearLegacyLibraryViewState,
  normalizeLibraryViewState,
  readLibraryViewState,
  saveLibraryViewState,
  type FilterCollection,
  type FilterSections,
  type LayoutMode,
  type LibraryViewState,
  type SortDir,
  type SortField,
} from "../utils/libraryViewState";

interface UpdateNotice {
  type: "" | "success" | "error";
  message: string;
}

interface ModalUpdateState extends UpdateNotice {
  running: boolean;
}

interface AddGamePayload {
  title: string;
  exe_path: string;
  f95_url?: string;
  version?: string;
  cover_image?: string;
  tags?: string;
  rating?: string;
  developer?: string;
  engine?: string;
}

interface PlaytimeTickDetail {
  gameId?: number;
  delta?: number;
}

const readQueryValue = (value: unknown): string => {
  if (Array.isArray(value)) {
    return typeof value[0] === "string" ? value[0] : "";
  }
  return typeof value === "string" ? value : "";
};

const route = useRoute();
const router = useRouter();
const games = ref<GameRecord[]>([]);
const showAddModal = ref(false);
const showDetailModal = ref(false);
const selectedGame = ref<GameRecord | null>(null);

// Search & Filter state
const searchQuery = ref("");
const filterStatuses = ref<string[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterStatuses]);
const filterCollection = ref<FilterCollection>(DEFAULT_LIBRARY_VIEW_STATE.filterCollection);
const filterEngines = ref<string[]>([]);
const filterTags = ref<string[]>([...DEFAULT_LIBRARY_VIEW_STATE.filterTags]);
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
    const match = parsed.pathname.match(/^\/threads\/(?:(.+)\.)?(\d+)(?:\/.*)?$/);
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

const toggleSort = (field: SortField) => {
  if (sortBy.value === field) {
    sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
  } else {
    sortBy.value = field;
    sortDir.value = field === "title" ? "asc" : "desc"; // ratings default to high-first
  }
};

const toggleFiltersPane = () => {
  isFiltersCollapsed.value = !isFiltersCollapsed.value;
};

const toggleFilterSection = (section: keyof FilterSections) => {
  filterSections.value[section] = !filterSections.value[section];
};

const allStatuses = PLAY_STATUS_OPTIONS;

const sortOptions: Array<{ key: SortField; label: string }> = [
  { key: "title", label: "A-Z" },
  { key: "date_added", label: "Newest" },
  { key: "last_played", label: "Recent" },
  { key: "playtime_seconds", label: "Playtime" },
  { key: "rating", label: "F95 ★" },
  { key: "own_rating", label: "My ★" },
];

const toggleFilter = (arr: string[], val: string) => {
  const idx = arr.indexOf(val);
  if (idx === -1) arr.push(val);
  else arr.splice(idx, 1);
};

// Derived unique values for filter pills
const uniqueEngines = computed(() => {
  const set = new Set(
    games.value
      .map((g) => g.engine)
      .filter((engine): engine is string => typeof engine === "string" && !!engine),
  );
  return [...set].sort();
});

const uniqueTags = computed(() => {
  const set = new Set<string>();
  games.value.forEach((g) => {
    if (g.tags) {
      const list = typeof g.tags === "string" ? g.tags.split(",") : g.tags;
      list.forEach((t) => {
        const trimmed = t.trim();
        if (trimmed) set.add(trimmed);
      });
    }
  });
  return [...set].sort();
});

const activeFilterCount = computed(
  () =>
    (filterCollection.value !== "All" ? 1 : 0) +
    (filterStatuses.value.length > 0 ? 1 : 0) +
    (filterTags.value.length > 0 ? 1 : 0),
);
const clearFilters = () => {
  searchQuery.value = "";
  filterStatuses.value = [];
  filterEngines.value = [];
  filterTags.value = [];
  filterCollection.value = "All";
};

const applyLibraryViewState = (state: LibraryViewState) => {
  layoutMode.value = state.layoutMode;
  sortBy.value = state.sortBy;
  sortDir.value = state.sortDir;
  filterCollection.value = state.filterCollection;
  filterStatuses.value = [...state.filterStatuses];
  filterTags.value = [...state.filterTags];
  isFiltersCollapsed.value = state.isFiltersCollapsed;
  filterSections.value = { ...state.filterSections };
};

const buildLibraryViewState = (): LibraryViewState =>
  normalizeLibraryViewState({
    layoutMode: layoutMode.value,
    sortBy: sortBy.value,
    sortDir: sortDir.value,
    filterCollection: filterCollection.value,
    filterStatuses: filterStatuses.value,
    filterTags: filterTags.value,
    isFiltersCollapsed: isFiltersCollapsed.value,
    filterSections: filterSections.value,
  });

const sameStringArray = (left: readonly string[], right: readonly string[]): boolean =>
  left.length === right.length && left.every((value, index) => value === right[index]);

const persistLibraryViewState = () => {
  saveLibraryViewState(localStorage, buildLibraryViewState());
};

const restoreLibraryViewState = () => {
  const restored = readLibraryViewState(localStorage);
  applyLibraryViewState(restored.state);

  if (restored.source === "legacy") {
    persistLibraryViewState();
    clearLegacyLibraryViewState(localStorage);
  }
};

const filteredGames = computed(() => {
  let result = [...games.value];
  const parseF95Rating = (rating: unknown): number => {
    if (!rating) return 0;
    const match = String(rating).match(/([\d.]+)/);
    return match ? parseFloat(match[1] ?? "0") : 0;
  };
  const ownRatingAverage = (game: GameRecord): number => {
    const total =
      (Number(game.rating_graphics) || 0) +
      (Number(game.rating_story) || 0) +
      (Number(game.rating_fappability) || 0) +
      (Number(game.rating_gameplay) || 0);
    return total / 4;
  };
  const q = searchQuery.value.toLowerCase();
  if (q) {
    result = result.filter((g) => {
      const t = (g.title || "").toLowerCase();
      const d = (g.developer || "").toLowerCase();
      return t.includes(q) || d.includes(q);
    });
  }
  if (filterCollection.value === 'Favorites') {
    result = result.filter((g) => g.is_favorite);
  }
  if (filterStatuses.value.length) {
    result = result.filter((g) => {
      return filterStatuses.value.includes(
        normalizePlayStatus(g.play_status, g.status),
      );
    });
  }
  if (filterEngines.value.length) {
    result = result.filter((g) => filterEngines.value.includes(g.engine || ""));
  }
  if (filterTags.value.length) {
    result = result.filter((g) => {
      const gameTags =
        typeof g.tags === "string"
          ? g.tags.split(",").map((t) => t.trim())
          : g.tags || [];
      // Intersect: require ALL selected tags to be present in the game's tags
      return filterTags.value.every((ft) => gameTags.includes(ft));
    });
  }
  // Sort
  result.sort((a, b) => {
    const field = sortBy.value;
    let va: string | number;
    let vb: string | number;
    if (field === "own_rating") {
      va = ownRatingAverage(a);
      vb = ownRatingAverage(b);
    } else if (field === "rating") {
      va = parseF95Rating(a.rating);
      vb = parseF95Rating(b.rating);
    } else if (field === "playtime_seconds") {
      va = Number(a.playtime_seconds) || 0;
      vb = Number(b.playtime_seconds) || 0;
    } else if (field === "last_played") {
      va = a.last_played ? new Date(a.last_played).getTime() : 0;
      vb = b.last_played ? new Date(b.last_played).getTime() : 0;
    } else if (field === "date_added") {
      va = a.date_added ? new Date(a.date_added).getTime() : 0;
      vb = b.date_added ? new Date(b.date_added).getTime() : 0;
    } else {
      va = String(a.title || "").toLowerCase();
      vb = String(b.title || "").toLowerCase();
    }
    if (va < vb) return sortDir.value === "asc" ? -1 : 1;
    if (va > vb) return sortDir.value === "asc" ? 1 : -1;
    return 0;
  });
  return result;
});

const updatingId = ref<number | null>(null);
const updateNotice = ref<UpdateNotice>({ type: "", message: "" });
let updateNoticeTimeout: ReturnType<typeof setTimeout> | null = null;
const modalUpdateState = ref<ModalUpdateState>({
  running: false,
  type: "",
  message: "",
});
let modalUpdateTimeout: ReturnType<typeof setTimeout> | null = null;

const showUpdateNotice = (type: UpdateNotice["type"], message: string) => {
  updateNotice.value = { type, message };
  if (updateNoticeTimeout) {
    clearTimeout(updateNoticeTimeout);
  }
  updateNoticeTimeout = setTimeout(() => {
    updateNotice.value = { type: "", message: "" };
  }, 3500);
};

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

const formatPlaytime = (seconds: number | null | undefined): string => {
  if (!seconds) return "0.0 hrs";
  return (seconds / 3600).toFixed(1) + " hrs";
};

const getGamePlayStatusMeta = (game: GameRecord) => {
  return getPlayStatusMeta(game.play_status, game.status);
};

const loadGames = async () => {
  try {
    const data = await api.getGames();
    if (data) {
      games.value = data;
      if (selectedGame.value) {
        const selectedId = selectedGame.value.id;
        const updated = data.find((g) => g.id === selectedId);
        if (updated) selectedGame.value = updated;
      }
    }
  } catch (e) {
    console.error("Failed to load games", e);
  }
};

const openDetail = (game: GameRecord) => {
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

const handleGameDeleted = async () => {
  clearModalUpdateState();
  showDetailModal.value = false;
  selectedGame.value = null;
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

const launchFromModal = async (game: GameRecord) => {
  try {
    const result = await api.launchGame(
      game.id,
      game.exe_path,
      game.command_line_args || "",
      game.run_japanese_locale || false,
      game.run_wayland || false,
      game.auto_inject_ce || false,
      game.custom_prefix || "",
      game.proton_version || "",
    );
    if (result && !result.success) {
      alert(`Failed to launch game:\n\n${result.error}`);
    }
  } catch (e) {
    console.error("Launch failed", e);
  }
};

const launchGameFast = async (game: GameRecord) => {
  try {
    const result = await api.launchGame(
      game.id,
      game.exe_path,
      game.command_line_args || "",
      game.run_japanese_locale || false,
      game.run_wayland || false,
      game.auto_inject_ce || false,
      game.custom_prefix || "",
      game.proton_version || "",
    );
    if (result && !result.success) {
      alert(`Failed to launch game:\n\n${result.error}`);
    }
  } catch (e) {
    console.error("Launch failed", e);
  }
};

// Handle incoming extension import
watch(
  () => route.query,
  async (q) => {
    if (!q) return;
    const action = readQueryValue(q.action);
    const queryUrl = readQueryValue(q.f95url);
    if (action === "import" && queryUrl) {
      await loadGames();
      showAddModal.value = true;
    }
    if (action === "open" && queryUrl) {
      await loadGames();
      const match = games.value.find((g) =>
        urlsMatchByThreadIdentity(g.f95_url, queryUrl),
      );
      if (match) openDetail(match);
    }
  },
  { immediate: true },
);

const handleAddGame = async (gameData: AddGamePayload) => {
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
    );
    if (result && result.success === false) {
      alert(`Failed to add game:\n\n${result.error || "Unknown error"}`);
      return;
    }

    if (result && result.id) {
      showAddModal.value = false;
      router.replace({ path: "/", query: {} });
      await loadGames();
    }
  } catch (e) {
    console.error("Failed to add game", e);
  }
};

const checkUpdate = async (game: GameRecord, event: Event) => {
  event.stopPropagation();
  const feedback = await runSingleGameUpdateCheck(game);
  showUpdateNotice(feedback.type, feedback.message);
};

const handleModalUpdateCheck = async (gameId: number) => {
  const game = games.value.find((entry) => entry.id === gameId) || selectedGame.value;
  if (!game) return;

  modalUpdateState.value = { running: true, type: "", message: "" };
  const feedback = await runSingleGameUpdateCheck(game);
  showModalUpdateNotice(feedback.type, feedback.message);
};

const handlePlaytimeTick = (event: Event) => {
  const detail = (event as CustomEvent<PlaytimeTickDetail>).detail || {};
  const gameId = Number(detail.gameId);
  const delta = Number(detail.delta);
  if (!Number.isInteger(gameId) || !Number.isFinite(delta) || delta <= 0) return;

  const game = games.value.find((g) => g.id === gameId);
  if (!game) return;

  game.playtime_seconds = (Number(game.playtime_seconds) || 0) + delta;
  game.last_played = new Date().toISOString();

  if (selectedGame.value && selectedGame.value.id === gameId) {
    selectedGame.value = {
      ...selectedGame.value,
      playtime_seconds: game.playtime_seconds,
      last_played: game.last_played,
    };
  }
};

watch(
  [
    layoutMode,
    sortBy,
    sortDir,
    filterCollection,
    filterStatuses,
    filterTags,
    isFiltersCollapsed,
    filterSections,
  ],
  () => {
    persistLibraryViewState();
  },
  { deep: true },
);

watch(uniqueTags, (tags) => {
  const normalizedTags = normalizeLibraryViewState(
    { filterTags: filterTags.value },
    { validTags: tags },
  ).filterTags;

  if (!sameStringArray(normalizedTags, filterTags.value)) {
    filterTags.value = normalizedTags;
  }
});

watch(showDetailModal, (open) => {
  if (!open) {
    clearModalUpdateState();
  }
});

onMounted(() => {
  restoreLibraryViewState();

  window.addEventListener("wlib-refresh-library", loadGames);
  window.addEventListener("wlib-playtime-tick", handlePlaytimeTick);
  onWebviewReady(() => {
    loadGames();
  });
});

onUnmounted(() => {
  window.removeEventListener("wlib-refresh-library", loadGames);
  window.removeEventListener("wlib-playtime-tick", handlePlaytimeTick);
  if (updateNoticeTimeout) {
    clearTimeout(updateNoticeTimeout);
  }
  if (modalUpdateTimeout) {
    clearTimeout(modalUpdateTimeout);
  }
});
</script>

<template>
  <div class="h-full flex overflow-hidden">
    <!-- Smart Collections Sidebar -->
    <aside
      :class="[
        'filters-pane shrink-0 h-full collapse-width-transition',
        isFiltersCollapsed ? 'w-0 filters-pane-collapsed' : 'w-64',
      ]"
    >
      <div class="w-64 h-full flex flex-col overflow-y-auto">
        <div class="p-6 pb-2">
          <button
            @click="toggleFilterSection('collections')"
            class="w-full flex items-center justify-between text-xs uppercase tracking-widest font-bold px-2 py-1 rounded-md transition-colors ui-hover-surface"
            style="color: var(--text-muted)"
          >
            <span>Smart Collections</span>
            <span class="text-sm">{{
              filterSections.collections ? "▾" : "▸"
            }}</span>
          </button>
          <div v-show="filterSections.collections" class="space-y-1 mt-3">
            <button
              @click="filterCollection = 'All'"
              class="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              :style="
                filterCollection === 'All'
                  ? 'background: var(--bg-raised); color: var(--text-primary)'
                  : 'color: var(--text-secondary); hover:background: var(--bg-overlay)'
              "
            >
              <IconLayoutGridFilled class="w-4 h-4" />
              All Games
            </button>
            <button
              @click="filterCollection = 'Favorites'"
              class="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              :style="
                filterCollection === 'Favorites'
                  ? 'background: var(--bg-raised); color: var(--text-primary)'
                  : 'color: var(--text-secondary); hover:background: var(--bg-overlay)'
              "
            >
              <IconStarFilled class="w-4 h-4" />
              Favorites
            </button>
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
                :value="s.value"
                v-model="filterStatuses"
                class="filters-checkbox rounded"
              />
              <span class="ui-status-option" :class="s.toneClass">
                <component :is="s.icon" class="ui-status-icon" />
                <span>{{ s.label }}</span>
              </span>
            </label>
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
            <div class="mb-2 flex justify-end">
              <button
                v-if="filterTags.length"
                @click="filterTags = []"
                class="cursor-pointer hover:text-red-400 text-[10px] normal-case tracking-normal transition-colors"
                style="color: var(--text-muted)"
              >
                Clear
              </button>
            </div>
            <div class="flex flex-wrap gap-1.5">
              <button
                v-for="tag in uniqueTags"
                :key="tag"
                @click="toggleFilter(filterTags, tag)"
                class="px-2.5 py-1 rounded-full text-[11px] font-medium transition-all"
                :style="
                  filterTags.includes(tag)
                    ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)'
                    : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'
                "
              >
                {{ tag }}
              </button>
              <div
                v-if="!uniqueTags.length"
                class="text-xs italic"
                style="color: var(--text-muted)"
              >
                No tags found
              </div>
            </div>
          </div>
        </div>
      </div>
    </aside>

    <!-- Main Content Area -->
    <div class="flex-1 p-8 overflow-y-auto flex flex-col relative">
    <header class="flex justify-between items-center mb-8">
      <div>
        <h2
          class="ui-page-heading text-3xl font-bold mb-2 tracking-tight"
          style="color: var(--text-primary)"
        >
          <IconDeviceGamepad2 class="ui-page-heading-icon" />
          <span>Your Library</span>
        </h2>
        <p
          class="text-sm pl-3"
          style="
            color: var(--text-secondary);
            border-left: 2px solid var(--brand);
          "
        >
          Manage and play your imported games.
        </p>
      </div>

      <div class="flex items-center gap-3">
        <button
          @click="toggleFiltersPane"
          class="ui-action-btn relative px-3 py-1.5 rounded-lg text-sm font-medium transition-all ui-hover-surface active:scale-95 active:bg-[var(--bg-overlay)]"
          style="color: var(--text-primary); border: 1px solid var(--border)"
          :title="isFiltersCollapsed ? 'Show Filters Pane' : 'Hide Filters Pane'"
        >
          <IconFilterFilled class="ui-action-icon" />
          <span>Filters</span>
          <span
            v-if="activeFilterCount > 0"
            class="absolute -top-1.5 -right-1.5 min-w-5 h-5 rounded-full px-1 text-[10px] font-bold flex items-center justify-center"
            style="background: var(--brand); color: var(--text-inverse)"
          >
            {{ activeFilterCount }}
          </span>
        </button>

        <button
          @click="showAddModal = true"
          class="ui-action-btn hover:brightness-110 px-3 py-1.5 rounded-lg text-sm font-semibold transition-all active:scale-95"
          style="background: var(--brand); color: var(--text-inverse); box-shadow: var(--shadow-brand)"
        >
          <IconLibraryPlus class="ui-action-icon" />
          Add Game
        </button>
      </div>
    </header>

    <!-- Search & Filter Bar -->
    <div v-if="games.length > 0" class="mb-6 space-y-3">
      <div
        v-if="updateNotice.message"
        class="rounded-lg px-3 py-2 text-sm"
        :style="
          updateNotice.type === 'error'
            ? 'background: rgba(239, 68, 68, 0.15); border: 1px solid rgba(239, 68, 68, 0.35); color: #fecaca;'
            : 'background: rgba(34, 197, 94, 0.15); border: 1px solid rgba(34, 197, 94, 0.35); color: #bbf7d0;'
        "
      >
        {{ updateNotice.message }}
      </div>

      <!-- Search Input -->
      <div class="relative">
        <IconZoom
          class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4"
          style="color: var(--text-muted)"
        />
        <input
          v-model="searchQuery"
          type="text"
          placeholder="Search games..."
          class="library-search-input w-full rounded-lg pl-10 pr-4 py-2.5 text-sm focus:outline-none transition-all"
        />
      </div>

      <!-- Filter Toggle, Sort & Counter -->
      <div class="flex items-center gap-3">

        <!-- Sort Buttons -->
        <div
          class="flex items-center rounded-lg overflow-hidden"
          style="border: 1px solid var(--border)"
        >
          <button
            v-for="s in sortOptions"
            :key="s.key"
            @click="toggleSort(s.key)"
            class="px-3 py-1.5 rounded-lg text-sm font-medium transition-all flex items-center gap-1 active:scale-95 active:bg-[var(--bg-overlay)]"
            :style="
              sortBy === s.key
                ? 'background: var(--bg-overlay); color: var(--text-primary)'
                : 'background: var(--bg-surface); color: var(--text-muted)'
            "
          >
            {{ s.label }}
            <svg
              v-if="sortBy === s.key"
              class="w-3 h-3"
              :class="sortDir === 'desc' ? 'rotate-180' : ''"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2.5"
            >
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        </div>

        <button
          v-if="activeFilterCount > 0"
          @click="clearFilters"
          class="ui-action-btn px-3 py-1.5 rounded-lg text-sm hover:text-red-400 transition-all border active:scale-95 active:bg-[var(--bg-overlay)]"
          style="color: var(--text-muted); border-color: var(--border)"
        >
          <IconX class="ui-action-icon" />
          Clear all
        </button>

        <div class="ml-auto flex items-center gap-4">
          <span class="text-xs" style="color: var(--text-muted)"
            >{{ filteredGames.length }} game{{
              filteredGames.length !== 1 ? "s" : ""
            }}</span
          >

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
              class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm transition-all active:scale-95 active:bg-[var(--bg-overlay)]"
              :style="
                layoutMode === 'grid'
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'color: var(--text-muted)'
              "
              title="Grid View"
            >
              <IconColumns2Filled class="ui-action-icon" />
            </button>
            <button
              @click="layoutMode = 'list'"
              class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm transition-all active:scale-95 active:bg-[var(--bg-overlay)]"
              :style="
                layoutMode === 'list'
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'color: var(--text-muted)'
              "
              title="List View"
            >
              <IconLayoutListFilled class="ui-action-icon" />
            </button>
            <button
              @click="layoutMode = 'compact'"
              class="ui-icon-btn px-3 py-1.5 rounded-lg text-sm transition-all active:scale-95 active:bg-[var(--bg-overlay)]"
              :style="
                layoutMode === 'compact'
                  ? 'background: var(--bg-overlay); color: var(--text-primary)'
                  : 'color: var(--text-muted)'
              "
              title="Compact View"
            >
              <IconLayoutGridFilled class="ui-action-icon opacity-90" />
            </button>
          </div>
        </div>
      </div>

    </div>

    <div v-if="games.length === 0" class="empty-state flex-1">
      <IconDeviceGamepad2Filled class="empty-state-icon" />
      <h3 class="empty-state-title">Your library is empty</h3>
      <p class="empty-state-subtext">Add your first game to get started</p>
      <p class="empty-state-hint">
        Use the Add Game button above to import your first title.
      </p>
    </div>

    <div v-else-if="filteredGames.length === 0" class="empty-state flex-1">
      <IconZoom class="empty-state-icon" />
      <h3 class="empty-state-title">No games found</h3>
      <p class="empty-state-subtext">Try adjusting your search or filters</p>
      <button
        @click="clearFilters"
        class="px-3 py-1.5 rounded-lg text-sm font-medium transition-all border active:scale-95 active:bg-[var(--bg-overlay)]"
        style="color: var(--text-secondary); border-color: var(--border)"
      >
        Clear filters
      </button>
    </div>

    <TransitionGroup v-else
      name="list"
      tag="div"
      :class="[
        'grid gap-4 md:gap-6 pb-12',
        layoutMode === 'grid'
          ? 'grid-cols-1 lg:grid-cols-2'
          : layoutMode === 'compact'
            ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-3'
            : 'grid-cols-1',
      ]"
    >
      <!-- Game Cards Grid -->
      <div
        v-for="game in filteredGames"
        :key="game.id"
        @click="openDetail(game)"
        class="game-card group rounded-xl overflow-hidden transition-all duration-300 cursor-pointer"
        :class="
          layoutMode === 'grid'
            ? 'flex flex-col lg:flex-row w-full h-auto min-h-[14rem] lg:h-56'
            : layoutMode === 'compact'
              ? 'relative w-full aspect-[16/9]'
              : 'flex flex-row items-center w-full h-24 md:h-32 pr-2 md:pr-4'
        "
      >
        <!-- Cover Image -->
        <div
          :class="[
            layoutMode === 'grid'
              ? 'w-full lg:flex-1 h-56 lg:h-full'
              : layoutMode === 'compact'
                ? 'w-full h-full'
                : 'w-24 md:w-36 lg:w-48 h-full',
            'flex items-center justify-center relative shadow-inner overflow-hidden shrink-0',
          ]"
          style="
            background: linear-gradient(
              135deg,
              var(--bg-raised),
              var(--bg-inset)
            );
          "
        >
          <img
            v-if="game.cover_image_path"
            :src="game.cover_image_path"
            :alt="game.title"
            class="absolute inset-0 w-full h-full object-cover object-top"
          />
          <IconPhotoFilled
            v-else
            class="w-12 h-12 transition-colors"
            style="color: var(--border)"
          />

          <div
            v-if="layoutMode !== 'compact'"
            class="card-image-overlay absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center backdrop-blur-[2px]"
          >
            <button
              class="card-overlay-play-btn rounded-full p-4 transition-all transform scale-90 group-hover:scale-100"
            >
              <IconPlayerPlayFilled class="w-6 h-6" />
            </button>
          </div>

          <!-- Rating Badge (Top Left) -->
          <div
            v-if="layoutMode !== 'compact' && game.rating"
            class="rating-badge absolute top-3 left-3 backdrop-blur-md text-xs font-bold px-2.5 py-1 rounded-lg flex items-center gap-1"
          >
            <IconStarFilled class="w-3.5 h-3.5" />
            {{ game.rating }}
          </div>

          <!-- Update Button Overlay (Top Right) -->
            <button
              v-if="layoutMode !== 'compact' && game.f95_url"
              @click.stop="checkUpdate(game, $event)"
              :disabled="updatingId === game.id"
              class="update-overlay-btn ui-icon-btn absolute top-3 right-3 p-2 rounded-lg backdrop-blur-md transition-all opacity-0 pointer-events-none group-hover:opacity-100 group-hover:pointer-events-auto disabled:opacity-100 disabled:cursor-wait disabled:pointer-events-none"
            >
              <IconRefresh
                v-if="updatingId !== game.id"
                class="ui-action-icon"
              />
              <IconLoader2
                v-else
                class="ui-action-icon animate-spin"
                style="color: var(--brand)"
              />
            </button>

          <div v-if="layoutMode === 'compact'" class="compact-image-overlay absolute inset-0"></div>

          <div
            v-if="layoutMode === 'compact'"
            class="absolute inset-x-0 bottom-0 z-10 p-2 flex items-end justify-between gap-2"
          >
            <h3
              class="compact-title text-xs md:text-sm font-bold leading-tight"
              :title="game.title"
            >
              {{ game.title }}
            </h3>
            <button
              @click.stop="launchGameFast(game)"
              class="compact-play-btn rounded-md p-2 shrink-0 transition-all active:scale-95"
              :title="`Play ${game.title}`"
            >
              <IconPlayerPlayFilled class="w-3.5 h-3.5" />
            </button>
          </div>
        </div>

        <!-- Text Details Area -->
        <div
          v-if="layoutMode !== 'compact'"
          :class="[
            'p-4 md:p-5 flex shrink-0 min-w-0',
            layoutMode === 'grid'
              ? 'flex-col justify-between w-full lg:w-72 xl:w-80 h-full'
              : 'flex-1 flex-row items-center justify-between gap-4 h-full',
          ]"
        >
          <!-- Left/Top Section (Text & Tags) -->
          <div
            :class="[
              'min-w-0 flex flex-col',
              layoutMode === 'grid' ? '' : 'justify-center h-full',
            ]"
          >
            <h3
              :class="[
                'font-bold truncate mb-1',
                layoutMode === 'grid'
                  ? 'text-lg md:text-xl lg:text-2xl'
                  : 'text-base md:text-xl',
              ]"
              style="color: var(--text-primary)"
              :title="game.title"
            >
              {{ game.title }}
            </h3>
            <p
              v-if="game.developer"
              :class="[
                'truncate mb-2 md:mb-3',
                layoutMode === 'grid'
                  ? 'text-xs md:text-sm'
                  : 'text-[10px] md:text-xs',
              ]"
              style="color: var(--text-muted)"
            >
              by {{ game.developer }}
            </p>

            <div
              :class="[
                'flex items-center gap-1.5',
                layoutMode === 'grid' ? 'mt-2' : '',
              ]"
            >
              <div
                class="flex items-center gap-1.5 px-2 py-0.5 md:py-1 rounded-md"
                style="
                  background: var(--bg-raised);
                  border: 1px solid var(--border);
                "
              >
                <span
                  class="font-mono text-[10px] md:text-xs font-bold"
                  style="color: var(--brand)"
                  >{{ game.version || "Unknown" }}</span
                >
              </div>
              <div
                v-if="
                  game.latest_version && game.latest_version !== game.version
                "
                class="update-version-badge flex items-center gap-1 px-2 py-0.5 md:py-1 rounded-md font-mono text-[10px] md:text-xs font-bold animate-pulse"
              >
                ⬆ {{ game.latest_version }}
              </div>
              <div
                v-if="game.playtime_seconds"
                class="flex items-center gap-1.5 px-2 py-0.5 md:py-1 rounded-md"
                style="background: var(--bg-raised); border: 1px solid var(--border);"
              >
                <span class="font-mono text-[10px] md:text-xs font-bold" style="color: var(--text-secondary)">
                  ⏱ {{ formatPlaytime(game.playtime_seconds) }}
                </span>
              </div>
            </div>
          </div>

          <!-- Right/Bottom Section (Play Button) -->
          <div
            :class="[
              'flex items-center shrink-0',
              layoutMode === 'grid'
                ? 'pt-4 justify-between mt-auto'
                : 'gap-4 md:gap-6',
            ]"
          >
            <div
              v-if="layoutMode === 'grid'"
              class="ui-status-chip px-2.5 py-1 text-xs font-medium whitespace-nowrap overflow-hidden text-ellipsis max-w-[52%]"
              :class="getGamePlayStatusMeta(game).toneClass"
              :title="getGamePlayStatusMeta(game).label"
            >
              <component :is="getGamePlayStatusMeta(game).icon" class="ui-status-icon" />
              <span class="truncate">{{ getGamePlayStatusMeta(game).label }}</span>
            </div>
            <button
              @click.stop="launchGameFast(game)"
              class="play-btn ui-action-btn px-4 md:px-5 py-2 rounded-lg text-xs md:text-sm font-bold transition-all active:scale-95 shrink-0"
            >
              <IconPlayerPlayFilled class="ui-action-icon" />
              <span class="hidden md:inline">Play</span>
            </button>
            <div
              v-if="layoutMode === 'list'"
              class="hidden sm:inline-flex w-28 justify-end text-[10px] font-medium whitespace-nowrap overflow-hidden text-ellipsis"
              :title="getGamePlayStatusMeta(game).label"
            >
              <span class="ui-status-inline" :class="getGamePlayStatusMeta(game).toneClass">
                <component :is="getGamePlayStatusMeta(game).icon" class="ui-status-icon" />
                <span class="truncate">{{ getGamePlayStatusMeta(game).label }}</span>
              </span>
            </div>
          </div>
        </div>
      </div>
    </TransitionGroup>

    <AddGameModal v-model="showAddModal" @save="handleAddGame" />
    <GameDetailModal
      v-model="showDetailModal"
      :game="selectedGame"
      :update-check-state="modalUpdateState"
      @updated="handleGameUpdated"
      @deleted="handleGameDeleted"
      @launch="launchFromModal"
      @check-updates="handleModalUpdateCheck"
    />
  </div>
  </div>
</template>

<style scoped>
.list-move,
.list-enter-active,
.list-leave-active {
  transition: all 0.4s ease;
}
.list-enter-from,
.list-leave-to {
  opacity: 0;
  transform: translateY(20px) scale(0.95);
}
.list-leave-active {
  position: absolute;
}

.game-card {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
}
.game-card:hover {
  border-color: var(--brand);
  box-shadow: 0 4px 20px var(--brand-glow);
  transform: translateY(-2px);
}

.play-btn {
  background: var(--brand);
  color: var(--text-inverse);
  box-shadow: var(--shadow-brand);
}
.play-btn:hover {
  filter: brightness(1.15);
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
}
.library-search-input:focus {
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.filters-checkbox {
  accent-color: var(--brand);
  border-color: var(--border);
  background: transparent;
}

.filters-checkbox:focus {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}

.card-image-overlay {
  background: var(--overlay-scrim);
}

.card-overlay-play-btn {
  background: var(--overlay-control);
  color: var(--overlay-control-text);
}

.card-overlay-play-btn:hover {
  background: var(--overlay-control-hover-bg);
  color: var(--overlay-control-hover-text);
}

.compact-image-overlay {
  background: linear-gradient(
    to top,
    rgba(0, 0, 0, 0.85) 0%,
    rgba(0, 0, 0, 0.58) 42%,
    rgba(0, 0, 0, 0.24) 70%,
    rgba(0, 0, 0, 0.08) 100%
  );
}

.compact-title {
  color: #f8fafc;
  text-shadow:
    0 1px 2px rgba(0, 0, 0, 0.95),
    0 0 8px rgba(0, 0, 0, 0.85);
  -webkit-text-stroke: 0.4px rgba(0, 0, 0, 0.65);
  max-height: 2.6em;
  overflow: hidden;
}

.compact-play-btn {
  background: rgba(17, 24, 39, 0.62);
  border: 1px solid rgba(255, 255, 255, 0.28);
  color: #f8fafc;
}

.compact-play-btn:hover {
  background: rgba(17, 24, 39, 0.78);
  border-color: rgba(255, 255, 255, 0.42);
}

.rating-badge {
  background: var(--overlay-scrim);
  border: 1px solid var(--overlay-border);
  color: var(--rating-accent);
}

.rating-badge svg {
  fill: currentColor;
}

.update-overlay-btn {
  background: var(--overlay-scrim-strong);
  border: 1px solid var(--overlay-border);
  color: var(--text-secondary);
}

.update-overlay-btn:hover {
  background: var(--overlay-scrim);
  color: var(--text-primary);
}

.update-version-badge {
  background: var(--success-bg);
  border: 1px solid var(--success-border);
  color: var(--success-text);
}
</style>
