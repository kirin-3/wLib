<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, onWebviewReady } from '../services/api'
import AddGameModal from '../components/modals/AddGameModal.vue'
import GameDetailModal from '../components/modals/GameDetailModal.vue'

const route = useRoute()
const router = useRouter()
const games = ref([])
const showAddModal = ref(false)
const showDetailModal = ref(false)
const selectedGame = ref(null)

// Search & Filter state
const searchQuery = ref('')
const filterStatuses = ref([])
const filterEngines = ref([])
const filterTags = ref([])
const showFilters = ref(false)
const layoutMode = ref('grid') // 'grid' or 'list'
const sortBy = ref('title')
const sortDir = ref('asc')

const toggleSort = (field) => {
    if (sortBy.value === field) {
        sortDir.value = sortDir.value === 'asc' ? 'desc' : 'asc'
    } else {
        sortBy.value = field
        sortDir.value = field === 'title' ? 'asc' : 'desc' // ratings default to high-first
    }
}

const allStatuses = [
    { value: '__none__', label: 'Not Started' },
    { value: 'completed', label: '✅ Completed' },
    { value: 'in_progress', label: '🎮 In Progress' },
    { value: 'replaying', label: '🔄 Replaying' },
    { value: 'waiting_update', label: '⏳ Waiting' },
    { value: 'abandoned', label: '🚫 Abandoned' },
]

const toggleFilter = (arr, val) => {
    const idx = arr.indexOf(val)
    if (idx === -1) arr.push(val)
    else arr.splice(idx, 1)
}

// Derived unique values for filter pills
const uniqueEngines = computed(() => {
    const set = new Set(games.value.map(g => g.engine).filter(Boolean))
    return [...set].sort()
})

const uniqueTags = computed(() => {
    const set = new Set()
    games.value.forEach(g => {
        if (g.tags) {
            const list = typeof g.tags === 'string' ? g.tags.split(',') : g.tags
            list.forEach(t => { const trimmed = t.trim(); if (trimmed) set.add(trimmed) })
        }
    })
    return [...set].sort()
})

const filteredGames = computed(() => {
    let result = games.value
    const q = searchQuery.value.toLowerCase().trim()
    if (q) {
        result = result.filter(g => (g.title || '').toLowerCase().includes(q))
    }
    if (filterStatuses.value.length) {
        result = result.filter(g => {
            if (filterStatuses.value.includes('__none__') && !g.status) return true
            return filterStatuses.value.includes(g.status)
        })
    }
    if (filterEngines.value.length) {
        result = result.filter(g => filterEngines.value.includes(g.engine))
    }
    if (filterTags.value.length) {
        result = result.filter(g => {
            if (!g.tags) return false
            const list = typeof g.tags === 'string' ? g.tags.split(',').map(t => t.trim()) : g.tags
            return filterTags.value.some(ft => list.includes(ft))
        })
    }
    // Sort
    const dir = sortDir.value === 'asc' ? 1 : -1
    result = [...result].sort((a, b) => {
        if (sortBy.value === 'title') {
            return dir * (a.title || '').localeCompare(b.title || '')
        } else if (sortBy.value === 'f95_rating') {
            // Parse rating like "4.6/5 (112 Votes)" to float
            const ra = parseFloat(a.rating) || 0
            const rb = parseFloat(b.rating) || 0
            return dir * (ra - rb)
        } else if (sortBy.value === 'own_rating') {
            const avgA = ((a.rating_graphics || 0) + (a.rating_story || 0) + (a.rating_fappability || 0) + (a.rating_gameplay || 0)) / 4
            const avgB = ((b.rating_graphics || 0) + (b.rating_story || 0) + (b.rating_fappability || 0) + (b.rating_gameplay || 0)) / 4
            return dir * (avgA - avgB)
        }
        return 0
    })
    return result
})

const activeFilterCount = computed(() => {
    return filterStatuses.value.length + filterEngines.value.length + filterTags.value.length
})

const clearFilters = () => {
    searchQuery.value = ''
    filterStatuses.value = []
    filterEngines.value = []
    filterTags.value = []
}

// Auto-open modal if navigated from extension
watch(() => route.query, async (query) => {
    if (query.action === 'import') {
        showAddModal.value = true
    } else if (query.action === 'open' && query.f95url) {
        // Wait for games to be loaded if they aren't yet
        if (games.value.length === 0) {
            await loadGames()
        }
        const match = games.value.find(g => g.f95_url === query.f95url)
        if (match) {
            selectedGame.value = match
            showDetailModal.value = true
        }
        // Clear query so it doesn't re-trigger
        router.replace({ query: {} })
    }
}, { immediate: true })

const loadGames = async () => {
    try {
        const data = await api.getGames()
        games.value = data || []
    } catch (e) {
        console.error("Failed to load games", e)
    }
}

const openDetail = (game) => {
    selectedGame.value = game
    showDetailModal.value = true
}

const handleGameUpdated = async () => {
    await loadGames()
}

const handleGameDeleted = async () => {
    await loadGames()
}

const launchFromModal = async (exePath, args, runJapaneseLocale, runWayland) => {
    if (!exePath) return;
    try {
        const result = await api.launchGame(exePath, args, runJapaneseLocale, runWayland)
        if (result && !result.success) {
            alert("Failed to launch game:\n\n" + (result.error || "Unknown error"))
        }
    } catch (e) {
        alert("Failed to launch game:\n\n" + e)
    }
}

const launchGameFast = async (game) => {
    if (!game.exe_path) return;
    try {
        const result = await api.launchGame(game.exe_path, game.command_line_args, game.run_japanese_locale, game.run_wayland)
        if (result && !result.success) {
            alert("Failed to launch game:\n\n" + (result.error || "Unknown error"))
        }
    } catch (e) {
        alert("Failed to launch game:\n\n" + e)
    }
}

const updatingId = ref(null)

const handleAddGame = async (gameData) => {
    try {
        await api.addGame(
            gameData.title, 
            gameData.exe_path, 
            gameData.f95_url,
            gameData.cover_image,
            gameData.tags,
            gameData.rating,
            gameData.developer,
            gameData.engine
        )
        await loadGames() // Refresh list
    } catch (e) {
        console.error("Failed to add game", e)
    }
}

const checkUpdate = async (game, event) => {
    event.stopPropagation() // Don't trigger launch when clicking update button
    if (!game.f95_url) return;
    
    updatingId.value = game.id
    try {
        const result = await api.checkForUpdates(game.f95_url)
        if (result && result.success) {
            // Reload games to pick up latest_version from DB
            await loadGames()
        }
    } catch (e) {
        console.error("Update check failed", e)
    } finally {
        updatingId.value = null
    }
}

onMounted(() => {
    onWebviewReady(() => {
        loadGames()
    })
})
</script>


<template>
  <div class="p-8 h-full flex flex-col">
    
    <header class="flex justify-between items-center mb-8">
      <div>
        <h2 class="text-3xl font-bold text-white mb-2 tracking-tight">Your Library</h2>
        <p class="text-gray-400 text-sm border-l-2 border-blue-500 pl-3">Manage and play your imported games.</p>
      </div>
      
      <button @click="showAddModal = true"
        class="bg-blue-600 hover:bg-blue-500 text-white px-5 py-2.5 rounded-lg flex items-center gap-2 text-sm font-medium transition-all shadow-lg shadow-blue-900/20">
        <svg xmlns="http://www.w3.org/-2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
        Add Game
      </button>
    </header>

    <!-- Search & Filter Bar -->
    <div class="mb-6 space-y-3">
      <!-- Search Input -->
      <div class="relative">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
        <input v-model="searchQuery" type="text" placeholder="Search games..."
          class="w-full bg-[#1a1a20] border border-[#2d2d34] rounded-lg pl-10 pr-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-1 focus:ring-blue-500/50 focus:border-blue-500 transition-all" />
      </div>
      
      <!-- Filter Toggle, Sort & Counter -->
      <div class="flex items-center gap-3">
        <button @click="showFilters = !showFilters"
          :class="['flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-all', showFilters || activeFilterCount > 0 ? 'bg-blue-600/10 border-blue-500/30 text-blue-400' : 'bg-[#1a1a20] border-[#2d2d34] text-gray-400 hover:border-gray-500']">
          <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z"/></svg>
          Filters
          <span v-if="activeFilterCount" class="bg-blue-600 text-white rounded-full w-4 h-4 text-[10px] flex items-center justify-center">{{ activeFilterCount }}</span>
        </button>
        
        <!-- Sort Buttons -->
        <div class="flex items-center border border-[#2d2d34] rounded-lg overflow-hidden">
          <button v-for="s in [{key:'title',label:'A-Z'},{key:'f95_rating',label:'F95 ★'},{key:'own_rating',label:'My ★'}]" :key="s.key"
            @click="toggleSort(s.key)"
            :class="['px-2.5 py-1.5 text-xs font-medium transition-all flex items-center gap-1', sortBy === s.key ? 'bg-[#2d2d34] text-white' : 'bg-[#1a1a20] text-gray-500 hover:text-gray-300']">
            {{ s.label }}
            <svg v-if="sortBy === s.key" class="w-3 h-3" :class="sortDir === 'desc' ? 'rotate-180' : ''" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
          </button>
        </div>
        
        <button v-if="activeFilterCount > 0" @click="clearFilters"
          class="text-xs text-gray-500 hover:text-red-400 transition-colors flex items-center gap-1">
          <svg class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
          Clear all
        </button>
        
        <div class="ml-auto flex items-center gap-4">
          <span class="text-xs text-gray-600">{{ filteredGames.length }} game{{ filteredGames.length !== 1 ? 's' : '' }}</span>
          
          <!-- Layout Toggle -->
          <div class="flex items-center bg-[#1a1a20] rounded-lg border border-[#2d2d34] p-1">
            <button @click="layoutMode = 'grid'" :class="['p-1.5 rounded-md transition-all', layoutMode === 'grid' ? 'bg-[#2d2d34] text-white shadow-sm' : 'text-gray-500 hover:text-gray-300']" title="Grid View">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
            </button>
            <button @click="layoutMode = 'list'" :class="['p-1.5 rounded-md transition-all', layoutMode === 'list' ? 'bg-[#2d2d34] text-white shadow-sm' : 'text-gray-500 hover:text-gray-300']" title="List View">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>
            </button>
          </div>
        </div>
      </div>
      
      <!-- Filter Pills (collapsible) -->
      <div v-if="showFilters" class="space-y-3 pt-2">
        <!-- Status Pills -->
        <div class="flex flex-wrap items-center gap-1.5">
          <span class="text-[10px] uppercase tracking-wider text-gray-600 font-semibold w-14 shrink-0">Status</span>
          <button v-for="s in allStatuses" :key="s.value" @click="toggleFilter(filterStatuses, s.value)"
            :class="['px-2.5 py-1 rounded-full text-xs border transition-all', filterStatuses.includes(s.value) ? 'bg-blue-600/20 border-blue-500 text-blue-400' : 'bg-[#1a1a20] border-[#2d2d34] text-gray-500 hover:border-gray-500']">
            {{ s.label }}
          </button>
        </div>
        
        <!-- Engine Pills -->
        <div v-if="uniqueEngines.length" class="flex flex-wrap items-center gap-1.5">
          <span class="text-[10px] uppercase tracking-wider text-gray-600 font-semibold w-14 shrink-0">Engine</span>
          <button v-for="eng in uniqueEngines" :key="eng" @click="toggleFilter(filterEngines, eng)"
            :class="['px-2.5 py-1 rounded-full text-xs border transition-all', filterEngines.includes(eng) ? 'bg-purple-600/20 border-purple-500 text-purple-400' : 'bg-[#1a1a20] border-[#2d2d34] text-gray-500 hover:border-gray-500']">
            {{ eng }}
          </button>
        </div>
        
        <!-- Tag Pills -->
        <div v-if="uniqueTags.length" class="flex flex-wrap items-center gap-1.5">
          <span class="text-[10px] uppercase tracking-wider text-gray-600 font-semibold w-14 shrink-0">Tags</span>
          <button v-for="tag in uniqueTags" :key="tag" @click="toggleFilter(filterTags, tag)"
            :class="['px-2.5 py-1 rounded-full text-xs border transition-all', filterTags.includes(tag) ? 'bg-emerald-600/20 border-emerald-500 text-emerald-400' : 'bg-[#1a1a20] border-[#2d2d34] text-gray-500 hover:border-gray-500']">
            {{ tag }}
          </button>
        </div>
      </div>
    </div>

    <TransitionGroup name="list" tag="div" :class="['grid gap-4 md:gap-6 pb-12', layoutMode === 'grid' ? 'grid-cols-1 lg:grid-cols-2' : 'grid-cols-1']">
      <!-- Game Cards Grid -->
      <div v-for="game in filteredGames" :key="game.id" @click="openDetail(game)"
           :class="['group bg-[#1a1a20] rounded-xl overflow-hidden border border-[#2d2d34] hover:border-blue-500/50 transition-all duration-300 shadow-xl cursor-pointer hover:-translate-y-1',
                    layoutMode === 'grid' ? 'flex flex-col lg:flex-row w-full h-auto min-h-[14rem] lg:h-56' : 'flex flex-row items-center w-full h-24 md:h-32 pr-2 md:pr-4']">
        
        <!-- Cover Image -->
        <div :class="[layoutMode === 'grid' ? 'w-full lg:flex-1 h-56 lg:h-full' : 'w-24 md:w-36 lg:w-48 h-full', 'bg-gradient-to-br from-[#202028] to-[#15151a] flex items-center justify-center relative shadow-inner overflow-hidden shrink-0']">
          <!-- Actual cover image if available -->
          <img v-if="game.cover_image_path" :src="game.cover_image_path" :alt="game.title"
            class="absolute inset-0 w-full h-full object-cover object-top" />
          <!-- Fallback placeholder icon -->
          <svg v-else class="w-12 h-12 text-[#2d2d34] group-hover:text-blue-500/20 transition-colors" xmlns="http://www.w3.org/-2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><line x1="3" x2="21" y1="9" y2="9"/><path d="M9 21V9"/></svg>
          
          <div class="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center backdrop-blur-[2px]">
             <button class="bg-white/10 hover:bg-white text-white hover:text-black rounded-full p-4 transition-all transform scale-90 group-hover:scale-100">
               <svg xmlns="http://www.w3.org/-2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>
             </button>
          </div>

          <!-- Rating Badge (Top Left) -->
          <div v-if="game.rating" class="absolute top-3 left-3 bg-black/60 backdrop-blur-md text-yellow-400 text-xs font-bold px-2.5 py-1 rounded-lg border border-white/10 flex items-center gap-1">
            <svg class="w-3.5 h-3.5 fill-yellow-400" viewBox="0 0 20 20"><path d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"/></svg>
            {{ game.rating }}
          </div>

          <!-- Update Button Overlay (Top Right) -->
          <button v-if="game.f95_url" @click.stop="checkUpdate(game, $event)" 
            class="absolute top-3 right-3 bg-black/50 hover:bg-black/80 text-gray-300 hover:text-white p-2 rounded-lg backdrop-blur-md border border-white/10 transition-all opacity-0 group-hover:opacity-100 disabled:opacity-100 disabled:cursor-wait">
            <svg v-if="updatingId !== game.id" class="w-4 h-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.45l4.8 4.8"/></svg>
            <svg v-else class="w-4 h-4 animate-spin text-blue-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" x2="12" y1="2" y2="6"/><line x1="12" x2="12" y1="18" y2="22"/><line x1="4.93" x2="7.76" y1="4.93" y2="7.76"/><line x1="16.24" x2="19.07" y1="16.24" y2="19.07"/><line x1="2" x2="6" y1="12" y2="12"/><line x1="18" x2="22" y1="12" y2="12"/><line x1="4.93" x2="7.76" y1="19.07" y2="16.24"/><line x1="16.24" x2="19.07" y1="7.76" y2="4.93"/></svg>
          </button>
        </div>

        <!-- Text Details Area -->
        <div :class="['p-4 md:p-5 flex shrink-0 min-w-0', layoutMode === 'grid' ? 'flex-col justify-between w-full lg:w-72 xl:w-80 h-full' : 'flex-1 flex-row items-center justify-between gap-4 h-full']">

          <!-- Left/Top Section (Text & Tags) -->
          <div :class="['min-w-0 flex flex-col', layoutMode === 'grid' ? '' : 'justify-center h-full']">
            <h3 :class="['font-bold text-gray-100 truncate mb-1', layoutMode === 'grid' ? 'text-lg md:text-xl lg:text-2xl' : 'text-base md:text-xl']" :title="game.title">{{ game.title }}</h3>
            <p v-if="game.developer" :class="['text-gray-500 truncate mb-2 md:mb-3', layoutMode === 'grid' ? 'text-xs md:text-sm' : 'text-[10px] md:text-xs']">by {{ game.developer }}</p>
            
            <div :class="['flex items-center gap-1.5', layoutMode === 'grid' ? 'mt-2' : '']">
              <div class="flex items-center gap-1.5 px-2 py-0.5 md:py-1 bg-[#25252e] rounded-md border border-[#33333d]">
                <span class="text-blue-400 font-mono text-[10px] md:text-xs font-bold">{{ game.version || 'Unknown' }}</span>
              </div>
              <div v-if="game.latest_version && game.latest_version !== game.version" 
                class="flex items-center gap-1 px-2 py-0.5 md:py-1 bg-green-600/15 rounded-md border border-green-500/30 text-green-400 font-mono text-[10px] md:text-xs font-bold animate-pulse">
                ⬆ {{ game.latest_version }}
              </div>
            </div>
          </div>
            
          <!-- Right/Bottom Section (Play Button) -->
          <div :class="['flex items-center shrink-0', layoutMode === 'grid' ? 'pt-4 justify-between mt-auto' : 'gap-4 md:gap-6']">
            <div v-if="layoutMode === 'grid'" class="text-gray-500 text-xs font-medium whitespace-nowrap overflow-hidden text-ellipsis max-w-[50%]" :title="game.status">
               {{ game.status === 'completed' ? '✅ Done' : game.status === 'in_progress' ? '🎮 Playing' : game.status === 'replaying' ? '🔄 Replay' : game.status === 'waiting_update' ? '⏳ Waiting' : game.status === 'abandoned' ? '🚫' : 'Not Started' }}
            </div>
            <button @click.stop="launchGameFast(game)" 
              class="bg-blue-600 hover:bg-blue-500 text-white px-4 md:px-5 py-2 rounded-lg text-xs md:text-sm font-bold shadow-lg shadow-blue-900/20 flex items-center gap-2 transition-transform active:scale-95 shrink-0">
              <svg class="w-3 h-3 md:w-4 md:h-4" viewBox="0 0 24 24" fill="currentColor"><path d="M5 3l14 9-14 9V3z"/></svg>
              <span class="hidden md:inline">Play</span>
            </button>
            <div v-if="layoutMode === 'list'" class="hidden sm:block w-24 text-right text-gray-500 text-[10px] font-medium whitespace-nowrap overflow-hidden text-ellipsis" :title="game.status">
               {{ game.status === 'completed' ? '✅ Done' : game.status === 'in_progress' ? '🎮 Playing' : game.status === 'replaying' ? '🔄 Replay' : game.status === 'waiting_update' ? '⏳ Waiting' : game.status === 'abandoned' ? '🚫' : 'Not Started' }}
            </div>
          </div>
        </div>

      </div>
    </TransitionGroup>
    
    <AddGameModal v-model="showAddModal" @save="handleAddGame" />
    <GameDetailModal v-model="showDetailModal" :game="selectedGame" 
      @updated="handleGameUpdated" @deleted="handleGameDeleted" @launch="launchFromModal" />
    
  </div>
</template>

<style scoped>
.list-move, /* apply transition to moving elements */
.list-enter-active,
.list-leave-active {
  transition: all 0.4s ease;
}

.list-enter-from,
.list-leave-to {
  opacity: 0;
  transform: translateY(20px) scale(0.95);
}

/* ensure leaving items are taken out of layout flow so that moving
   animations can be calculated correctly. */
.list-leave-active {
  position: absolute;
}
</style>
