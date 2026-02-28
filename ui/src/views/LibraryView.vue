<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, onWebviewReady } from "../services/api";
import AddGameModal from "../components/modals/AddGameModal.vue";
import GameDetailModal from "../components/modals/GameDetailModal.vue";

const route = useRoute();
const router = useRouter();
const games = ref([]);
const showAddModal = ref(false);
const showDetailModal = ref(false);
const selectedGame = ref(null);

// Search & Filter state
const searchQuery = ref("");
const filterStatuses = ref([]);
const filterCollection = ref("All"); // "All" or "Favorites"
const filterEngines = ref([]);
const filterTags = ref([]);
const layoutMode = ref("grid"); // 'grid' or 'list'
const sortBy = ref("title");
const sortDir = ref("asc");

const toggleSort = (field) => {
  if (sortBy.value === field) {
    sortDir.value = sortDir.value === "asc" ? "desc" : "asc";
  } else {
    sortBy.value = field;
    sortDir.value = field === "title" ? "asc" : "desc"; // ratings default to high-first
  }
};

const allStatuses = [
  { value: "Playing", label: "🎮 Playing" },
  { value: "Completed", label: "✅ Completed" },
  { value: "On Hold", label: "⏸️ On Hold" },
  { value: "Plan to Play", label: "🗓️ Plan to Play" },
];

const toggleFilter = (arr, val) => {
  const idx = arr.indexOf(val);
  if (idx === -1) arr.push(val);
  else arr.splice(idx, 1);
};

// Derived unique values for filter pills
const uniqueEngines = computed(() => {
  const set = new Set(games.value.map((g) => g.engine).filter(Boolean));
  return [...set].sort();
});

const uniqueTags = computed(() => {
  const set = new Set();
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
    filterStatuses.value.length + filterEngines.value.length + filterTags.value.length + (filterCollection.value !== "All" ? 1 : 0),
);
const clearFilters = () => {
  filterStatuses.value = [];
  filterEngines.value = [];
  filterTags.value = [];
  filterCollection.value = "All";
};

const filteredGames = computed(() => {
  let result = [...games.value];
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
      return filterStatuses.value.includes(g.play_status);
    });
  }
  if (filterEngines.value.length) {
    result = result.filter((g) => filterEngines.value.includes(g.engine));
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
    let va = a[field],
      vb = b[field];
    if (field.includes("rating")) {
      // Parse rating numeric value for proper sorting
      const parseRating = (r) => {
        if (!r) return 0;
        const m = String(r).match(/([\d.]+)/);
        return m ? parseFloat(m[1]) : 0;
      };
      va = parseRating(va);
      vb = parseRating(vb);
    } else if (field === "playtime_seconds") {
      va = Number(va) || 0;
      vb = Number(vb) || 0;
    } else if (field === "last_played") {
      va = va ? new Date(va).getTime() : 0;
      vb = vb ? new Date(vb).getTime() : 0;
    } else if (field === "date_added") {
      va = va ? new Date(va).getTime() : 0;
      vb = vb ? new Date(vb).getTime() : 0;
    } else {
      va = (va || "").toString().toLowerCase();
      vb = (vb || "").toString().toLowerCase();
    }
    if (va < vb) return sortDir.value === "asc" ? -1 : 1;
    if (va > vb) return sortDir.value === "asc" ? 1 : -1;
    return 0;
  });
  return result;
});

const updatingId = ref(null);

const formatPlaytime = (seconds) => {
  if (!seconds) return "0.0 hrs";
  return (seconds / 3600).toFixed(1) + " hrs";
};

const formatPlayStatus = (status) => {
  switch (status) {
    case "Completed":
    case "completed":
      return "✅ Done";
    case "Playing":
    case "in_progress":
      return "🎮 Playing";
    case "replaying":
      return "🔄 Replay";
    case "On Hold":
      return "⏸️ On Hold";
    case "waiting_update":
      return "⏳ Waiting";
    case "abandoned":
      return "🚫";
    case "Plan to Play":
    default:
      return "Not Started";
  }
};

const loadGames = async () => {
  try {
    const data = await api.getGames();
    if (data) {
      games.value = data;
      if (selectedGame.value) {
        const updated = data.find((g) => g.id === selectedGame.value.id);
        if (updated) selectedGame.value = updated;
      }
    }
  } catch (e) {
    console.error("Failed to load games", e);
  }
};

const openDetail = (game) => {
  selectedGame.value = game;
  showDetailModal.value = true;
};

const handleGameUpdated = async () => {
  showDetailModal.value = false;
  selectedGame.value = null;
  await loadGames();
};

const handleGameDeleted = async () => {
  showDetailModal.value = false;
  selectedGame.value = null;
  await loadGames();
};

const launchFromModal = async (game) => {
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

const launchGameFast = async (game) => {
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
    if (q.action === "import" && q.f95url) {
      await loadGames();
      showAddModal.value = true;
    }
    if (q.action === "open" && q.f95url) {
      await loadGames();
      const match = games.value.find((g) => g.f95_url === q.f95url);
      if (match) openDetail(match);
    }
  },
  { immediate: true },
);

const handleAddGame = async (gameData) => {
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
    if (result && result.id) {
      showAddModal.value = false;
      router.replace({ path: "/", query: {} });
      await loadGames();
    }
  } catch (e) {
    console.error("Failed to add game", e);
  }
};

const checkUpdate = async (game, event) => {
  event.stopPropagation();
  if (!game.f95_url) return;

  updatingId.value = game.id;
  try {
    const result = await api.checkForUpdates(game.f95_url);
    if (result && result.success) {
      await loadGames();
    }
  } catch (e) {
    console.error("Update check failed", e);
  } finally {
    updatingId.value = null;
  }
};

onMounted(() => {
  window.addEventListener("wlib-refresh-library", loadGames);
  onWebviewReady(() => {
    loadGames();
  });
});

onUnmounted(() => {
  window.removeEventListener("wlib-refresh-library", loadGames);
});
</script>

<template>
  <div class="h-full flex overflow-hidden">
    <!-- Smart Collections Sidebar -->
    <aside class="w-64 shrink-0 flex flex-col h-full overflow-y-auto" style="background: var(--bg-surface); border-right: 1px solid var(--border);">
      <div class="p-6 pb-2">
        <h3 class="text-xs uppercase tracking-widest font-bold mb-3" style="color: var(--text-muted)">Smart Collections</h3>
        <div class="space-y-1">
          <button @click="filterCollection = 'All'" class="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors" :style="filterCollection === 'All' ? 'background: var(--bg-raised); color: var(--text-primary)' : 'color: var(--text-secondary); hover:background: var(--bg-overlay)'">
            <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
            All Games
          </button>
          <button @click="filterCollection = 'Favorites'" class="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors" :style="filterCollection === 'Favorites' ? 'background: var(--bg-raised); color: var(--text-primary)' : 'color: var(--text-secondary); hover:background: var(--bg-overlay)'">
            <svg class="w-4 h-4" viewBox="0 0 24 24" :fill="filterCollection === 'Favorites' ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon></svg>
            Favorites
          </button>
        </div>
      </div>

      <div class="p-6 pb-2 pt-4">
        <h3 class="text-xs uppercase tracking-widest font-bold mb-3" style="color: var(--text-muted)">Play Status</h3>
        <div class="space-y-1">
          <label v-for="s in allStatuses" :key="s.value" class="flex items-center gap-3 px-3 py-1.5 rounded-lg text-sm cursor-pointer hover:bg-white/5 transition-colors">
            <input type="checkbox" :value="s.value" v-model="filterStatuses" class="rounded border-gray-600 bg-transparent text-emerald-500 focus:ring-emerald-500 focus:ring-offset-gray-900" style="accent-color: var(--brand)" />
            <span style="color: var(--text-secondary)">{{ s.label }}</span>
          </label>
        </div>
      </div>

      <div class="p-6 pt-4 flex-1">
        <h3 class="text-xs uppercase tracking-widest font-bold mb-3 flex justify-between items-center" style="color: var(--text-muted)">
          Tags
          <span v-if="filterTags.length" @click="filterTags = []" class="cursor-pointer hover:text-red-400 text-[10px] normal-case tracking-normal">Clear</span>
        </h3>
        <div class="flex flex-wrap gap-1.5">
          <button v-for="tag in uniqueTags" :key="tag" @click="toggleFilter(filterTags, tag)" class="px-2.5 py-1 rounded-full text-[11px] font-medium transition-all" :style="filterTags.includes(tag) ? 'background: var(--brand-glow); border: 1px solid var(--brand-deep); color: var(--brand)' : 'background: var(--bg-raised); border: 1px solid var(--border); color: var(--text-secondary)'">
            {{ tag }}
          </button>
          <div v-if="!uniqueTags.length" class="text-xs italic" style="color: var(--text-muted)">No tags found</div>
        </div>
      </div>
    </aside>

    <!-- Main Content Area -->
    <div class="flex-1 p-8 overflow-y-auto flex flex-col relative">
    <header class="flex justify-between items-center mb-8">
      <div>
        <h2
          class="text-3xl font-bold mb-2 tracking-tight"
          style="color: var(--text-primary)"
        >
          Your Library
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

      <button
        @click="showAddModal = true"
        class="hover:brightness-110 text-white px-5 py-2.5 rounded-lg flex items-center gap-2 text-sm font-semibold transition-all"
        style="background: var(--brand); box-shadow: var(--shadow-brand)"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M5 12h14" />
          <path d="M12 5v14" />
        </svg>
        Add Game
      </button>
    </header>

    <!-- Search & Filter Bar -->
    <div class="mb-6 space-y-3">
      <!-- Search Input -->
      <div class="relative">
        <svg
          class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4"
          style="color: var(--text-muted)"
          xmlns="http://www.w3.org/2000/svg"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          stroke-width="2"
        >
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          v-model="searchQuery"
          type="text"
          placeholder="Search games..."
          class="w-full rounded-lg pl-10 pr-4 py-2.5 text-sm focus:outline-none transition-all"
          style="
            background: var(--bg-surface);
            border: 1px solid var(--border);
            color: var(--text-primary);
          "
          onfocus="
            this.style.borderColor = 'var(--brand)';
            this.style.boxShadow = '0 0 0 3px var(--brand-glow)';
          "
          onblur="
            this.style.borderColor = 'var(--border)';
            this.style.boxShadow = 'none';
          "
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
            v-for="s in [
              { key: 'title', label: 'A-Z' },
              { key: 'date_added', label: 'Newest' },
              { key: 'last_played', label: 'Recent' },
              { key: 'playtime_seconds', label: 'Playtime' },
              { key: 'f95_rating', label: 'F95 ★' },
              { key: 'own_rating', label: 'My ★' },
            ]"
            :key="s.key"
            @click="toggleSort(s.key)"
            class="px-2.5 py-1.5 text-xs font-medium transition-all flex items-center gap-1"
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
          class="text-xs hover:text-red-400 transition-colors flex items-center gap-1"
          style="color: var(--text-muted)"
        >
          <svg
            class="w-3 h-3"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path d="M18 6 6 18M6 6l12 12" />
          </svg>
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
              class="p-1.5 rounded-md transition-all"
              :style="
                layoutMode === 'grid'
                  ? 'background: var(--bg-overlay); color: var(--text-primary); box-shadow: 0 1px 2px rgba(0,0,0,0.1)'
                  : 'color: var(--text-muted)'
              "
              title="Grid View"
            >
              <svg
                class="w-4 h-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <rect x="3" y="3" width="7" height="7"></rect>
                <rect x="14" y="3" width="7" height="7"></rect>
                <rect x="14" y="14" width="7" height="7"></rect>
                <rect x="3" y="14" width="7" height="7"></rect>
              </svg>
            </button>
            <button
              @click="layoutMode = 'list'"
              class="p-1.5 rounded-md transition-all"
              :style="
                layoutMode === 'list'
                  ? 'background: var(--bg-overlay); color: var(--text-primary); box-shadow: 0 1px 2px rgba(0,0,0,0.1)'
                  : 'color: var(--text-muted)'
              "
              title="List View"
            >
              <svg
                class="w-4 h-4"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              >
                <line x1="8" y1="6" x2="21" y2="6"></line>
                <line x1="8" y1="12" x2="21" y2="12"></line>
                <line x1="8" y1="18" x2="21" y2="18"></line>
                <line x1="3" y1="6" x2="3.01" y2="6"></line>
                <line x1="3" y1="12" x2="3.01" y2="12"></line>
                <line x1="3" y1="18" x2="3.01" y2="18"></line>
              </svg>
            </button>
          </div>
        </div>
      </div>

    </div>

    <TransitionGroup
      name="list"
      tag="div"
      :class="[
        'grid gap-4 md:gap-6 pb-12',
        layoutMode === 'grid' ? 'grid-cols-1 lg:grid-cols-2' : 'grid-cols-1',
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
            : 'flex flex-row items-center w-full h-24 md:h-32 pr-2 md:pr-4'
        "
      >
        <!-- Cover Image -->
        <div
          :class="[
            layoutMode === 'grid'
              ? 'w-full lg:flex-1 h-56 lg:h-full'
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
          <svg
            v-else
            class="w-12 h-12 transition-colors"
            style="color: var(--border)"
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
            <line x1="3" x2="21" y1="9" y2="9" />
            <path d="M9 21V9" />
          </svg>

          <div
            class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center backdrop-blur-[2px]"
          >
            <button
              class="bg-white/10 hover:bg-white text-white hover:text-black rounded-full p-4 transition-all transform scale-90 group-hover:scale-100"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="24"
                height="24"
                viewBox="0 0 24 24"
                fill="currentColor"
                stroke="none"
              >
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
            </button>
          </div>

          <!-- Rating Badge (Top Left) -->
          <div
            v-if="game.rating"
            class="absolute top-3 left-3 bg-black/60 backdrop-blur-md text-yellow-400 text-xs font-bold px-2.5 py-1 rounded-lg border border-white/10 flex items-center gap-1"
          >
            <svg class="w-3.5 h-3.5 fill-yellow-400" viewBox="0 0 20 20">
              <path
                d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"
              />
            </svg>
            {{ game.rating }}
          </div>

          <!-- Update Button Overlay (Top Right) -->
          <button
            v-if="game.f95_url"
            @click.stop="checkUpdate(game, $event)"
            class="absolute top-3 right-3 bg-black/50 hover:bg-black/80 text-gray-300 hover:text-white p-2 rounded-lg backdrop-blur-md border border-white/10 transition-all opacity-0 group-hover:opacity-100 disabled:opacity-100 disabled:cursor-wait"
          >
            <svg
              v-if="updatingId !== game.id"
              class="w-4 h-4"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <path
                d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.45l4.8 4.8"
              />
            </svg>
            <svg
              v-else
              class="w-4 h-4 animate-spin"
              style="color: var(--brand)"
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <line x1="12" x2="12" y1="2" y2="6" />
              <line x1="12" x2="12" y1="18" y2="22" />
              <line x1="4.93" x2="7.76" y1="4.93" y2="7.76" />
              <line x1="16.24" x2="19.07" y1="16.24" y2="19.07" />
              <line x1="2" x2="6" y1="12" y2="12" />
              <line x1="18" x2="22" y1="12" y2="12" />
              <line x1="4.93" x2="7.76" y1="19.07" y2="16.24" />
              <line x1="16.24" x2="19.07" y1="7.76" y2="4.93" />
            </svg>
          </button>
        </div>

        <!-- Text Details Area -->
        <div
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
                class="flex items-center gap-1 px-2 py-0.5 md:py-1 bg-green-600/15 rounded-md border border-green-500/30 text-green-400 font-mono text-[10px] md:text-xs font-bold animate-pulse"
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
              class="text-xs font-medium whitespace-nowrap overflow-hidden text-ellipsis max-w-[50%]"
              style="color: var(--text-muted)"
              :title="game.play_status"
            >
              {{ formatPlayStatus(game.play_status) }}
            </div>
            <button
              @click.stop="launchGameFast(game)"
              class="play-btn text-white px-4 md:px-5 py-2 rounded-lg text-xs md:text-sm font-bold flex items-center gap-2 transition-all active:scale-95 shrink-0"
            >
              <svg
                class="w-3 h-3 md:w-4 md:h-4"
                viewBox="0 0 24 24"
                fill="currentColor"
              >
                <path d="M5 3l14 9-14 9V3z" />
              </svg>
              <span class="hidden md:inline">Play</span>
            </button>
            <div
              v-if="layoutMode === 'list'"
              class="hidden sm:block w-24 text-right text-[10px] font-medium whitespace-nowrap overflow-hidden text-ellipsis"
              style="color: var(--text-muted)"
              :title="game.play_status"
            >
              {{ formatPlayStatus(game.play_status) }}
            </div>
          </div>
        </div>
      </div>
    </TransitionGroup>

    <AddGameModal v-model="showAddModal" @save="handleAddGame" />
    <GameDetailModal
      v-model="showDetailModal"
      :game="selectedGame"
      @updated="handleGameUpdated"
      @deleted="handleGameDeleted"
      @launch="launchFromModal"
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
  box-shadow: var(--shadow-brand);
}
.play-btn:hover {
  filter: brightness(1.15);
}
</style>
