<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { api, onWebviewReady } from '../services/api'

const games = ref([])
const status = ref({ running: false, total: 0, checked: 0, current: '', results: [] })
const lastCheckTime = ref('')
const autoCheckFreq = ref('weekly')
const lastAutoCheck = ref('')
let pollInterval = null

const loadGames = async () => {
    try {
        games.value = await api.getGames() || []
    } catch (e) {
        console.error('Failed to load games', e)
    }
}

const loadAutoCheckSetting = async () => {
    try {
        const s = await api.getAutoCheckSetting()
        if (s) {
            autoCheckFreq.value = s.frequency || 'weekly'
            lastAutoCheck.value = s.last_check || ''
        }
    } catch (e) {
        console.error('Failed to load auto-check setting', e)
    }
}

const setFrequency = async (freq) => {
    autoCheckFreq.value = freq
    await api.setAutoCheckSetting(freq)
}

const startCheck = async () => {
    try {
        const result = await api.checkAllUpdates()
        if (result && result.success) {
            lastCheckTime.value = new Date().toLocaleTimeString()
            startPolling()
        }
    } catch (e) {
        console.error('Failed to start update check', e)
    }
}

const cancelCheck = async () => {
    try {
        await api.cancelUpdateCheck()
    } catch (e) {
        console.error('Failed to cancel', e)
    }
}

const pollStatus = async () => {
    try {
        const s = await api.getUpdateStatus()
        if (s) {
            status.value = s
            if (!s.running && pollInterval) {
                clearInterval(pollInterval)
                pollInterval = null
                await loadGames()
                await loadAutoCheckSetting()
            }
        }
    } catch (e) {
        console.error('Poll failed', e)
    }
}

const startPolling = () => {
    if (pollInterval) clearInterval(pollInterval)
    pollInterval = setInterval(pollStatus, 2000)
    pollStatus()
}

const openInBrowser = async (url) => {
    if (url) await api.openInBrowser(url)
}

const progress = computed(() => {
    if (!status.value.total) return 0
    return Math.round((status.value.checked / status.value.total) * 100)
})

const gamesWithUpdates = computed(() => {
    return status.value.results.filter(r => r.has_update)
})

const gamesChecked = computed(() => {
    return status.value.results.filter(r => !r.error)
})

const gamesWithErrors = computed(() => {
    return status.value.results.filter(r => r.error)
})

const persistedUpdates = computed(() => {
    return games.value.filter(g => g.latest_version && g.version && g.latest_version.trim() !== g.version.trim())
})

const formatLastCheck = computed(() => {
    if (!lastAutoCheck.value) return 'Never'
    try {
        const d = new Date(lastAutoCheck.value)
        return d.toLocaleDateString() + ' ' + d.toLocaleTimeString()
    } catch { return lastAutoCheck.value }
})

onMounted(() => {
    onWebviewReady(async () => {
        await loadGames()
        await loadAutoCheckSetting()
        // Check if an update was already running
        const s = await api.getUpdateStatus()
        if (s && s.running) {
            status.value = s
            startPolling()
        } else if (s) {
            status.value = s
        }
        // Trigger auto-check if due
        try {
            const autoResult = await api.maybeAutoCheck()
            if (autoResult && autoResult.triggered) {
                startPolling()
            }
        } catch (e) {
            console.error('Auto-check trigger failed', e)
        }
    })
})

onUnmounted(() => {
    if (pollInterval) clearInterval(pollInterval)
})
</script>

<template>
  <div class="p-8 max-w-4xl pb-12">
    <header class="mb-8">
      <h2 class="text-3xl font-bold text-white mb-2 tracking-tight">Updates</h2>
      <p class="text-gray-400 text-sm border-l-2 border-green-500 pl-3">Check F95Zone for new game versions.</p>
    </header>

    <div class="space-y-6">

      <!-- Auto-Check Setting -->
      <section class="bg-[#1a1a20] rounded-xl border border-[#2d2d34] p-6">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-lg font-semibold text-white flex items-center gap-2">
            <svg class="w-5 h-5 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
            Automatic Checking
          </h3>
        </div>
        
        <div class="flex items-center gap-3 mb-3">
          <span class="text-sm text-gray-400">Frequency:</span>
          <div class="flex items-center border border-[#2d2d34] rounded-lg overflow-hidden">
            <button v-for="opt in [{key:'weekly',label:'Weekly'},{key:'monthly',label:'Monthly'},{key:'off',label:'Off'}]" :key="opt.key"
              @click="setFrequency(opt.key)"
              :class="['px-3 py-1.5 text-xs font-medium transition-all', autoCheckFreq === opt.key ? 'bg-[#2d2d34] text-white' : 'bg-[#1a1a20] text-gray-500 hover:text-gray-300']">
              {{ opt.label }}
            </button>
          </div>
        </div>
        <p class="text-xs text-gray-600">Last checked: {{ formatLastCheck }}</p>
      </section>

      <!-- Check All Button / Progress -->
      <section class="bg-[#1a1a20] rounded-xl border border-[#2d2d34] p-6">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-lg font-semibold text-white flex items-center gap-2">
            <svg class="w-5 h-5 text-green-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.45l4.8 4.8"/></svg>
            Manual Check
          </h3>
          
          <div class="flex items-center gap-3">
            <button v-if="!status.running" @click="startCheck"
              class="bg-green-600 hover:bg-green-500 text-white px-5 py-2 rounded-lg text-sm font-bold transition-all shadow-lg shadow-green-900/20 flex items-center gap-2">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.45l4.8 4.8"/></svg>
              Check All Games
            </button>
            <button v-else @click="cancelCheck"
              class="bg-red-600/20 hover:bg-red-600/30 text-red-400 px-5 py-2 rounded-lg text-sm font-bold transition-all border border-red-500/30 flex items-center gap-2">
              <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
              Cancel
            </button>
          </div>
        </div>

        <!-- Progress Bar -->
        <div v-if="status.running || status.checked > 0" class="space-y-2">
          <div class="flex items-center justify-between text-xs text-gray-400">
            <span v-if="status.running" class="flex items-center gap-2">
              <svg class="w-3 h-3 animate-spin text-green-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83"/></svg>
              Checking: {{ status.current }}
            </span>
            <span v-else class="text-green-400">✓ Complete</span>
            <span>{{ status.checked }}/{{ status.total }}</span>
          </div>
          <div class="h-2 bg-[#25252e] rounded-full overflow-hidden">
            <div class="h-full bg-gradient-to-r from-green-500 to-emerald-400 rounded-full transition-all duration-500"
              :style="{ width: progress + '%' }"></div>
          </div>
        </div>

        <p v-if="!status.running && status.total === 0" class="text-sm text-gray-500">
          Click "Check All Games" to scan F95Zone for new versions. Games are checked one-by-one with a 15-second delay to avoid rate limiting.
        </p>
      </section>

      <!-- Updates Available -->
      <section v-if="persistedUpdates.length > 0" class="bg-[#1a1a20] rounded-xl border border-green-500/20 p-6">
        <h3 class="text-lg font-semibold text-green-400 mb-4 flex items-center gap-2">
          <svg class="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 19V5M5 12l7-7 7 7"/></svg>
          Updates Available ({{ persistedUpdates.length }})
        </h3>
        
        <TransitionGroup name="list" tag="div" class="space-y-2">
          <div v-for="game in persistedUpdates" :key="game.id"
            class="list-item flex items-center justify-between bg-[#202028] rounded-lg px-4 py-3 border border-[#2d2d34]">
            <div class="flex items-center gap-3 min-w-0">
              <img v-if="game.cover_image_path" :src="game.cover_image_path" class="w-10 h-10 rounded-lg object-cover shrink-0" />
              <div class="w-6 h-6 bg-[#2d2d34] rounded shrink-0 flex items-center justify-center" v-else>
                <svg class="w-3 h-3 text-gray-600" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="18" height="18" x="3" y="3" rx="2" ry="2"/></svg>
              </div>
              <div class="min-w-0">
                <p class="text-sm font-semibold text-white truncate">{{ game.title }}</p>
                <p class="text-xs text-gray-500">{{ game.developer }}</p>
              </div>
            </div>
            <div class="flex items-center gap-3 shrink-0">
              <div class="text-right">
                <span class="text-xs text-gray-500 font-mono">{{ game.version }}</span>
                <span class="text-xs text-gray-600 mx-1">→</span>
                <span class="text-xs text-green-400 font-mono font-bold">{{ game.latest_version }}</span>
              </div>
              <button v-if="game.f95_url" @click="openInBrowser(game.f95_url)"
                class="text-purple-400 hover:text-purple-300 p-1.5 rounded-lg hover:bg-purple-500/10 transition-all" title="Open F95Zone thread">
                <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg>
              </button>
            </div>
          </div>
        </TransitionGroup>
      </section>

      <!-- Check Results -->
      <section v-if="!status.running && status.results.length > 0" class="bg-[#1a1a20] rounded-xl border border-[#2d2d34] p-6">
        <h3 class="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg class="w-5 h-5 text-blue-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" x2="8" y1="13" y2="13"/><line x1="16" x2="8" y1="17" y2="17"/></svg>
          Last Check Results
        </h3>

        <div class="grid grid-cols-3 gap-4 mb-4">
          <div class="bg-[#202028] rounded-lg p-3 text-center border border-[#2d2d34]">
            <p class="text-2xl font-bold text-white">{{ gamesChecked.length }}</p>
            <p class="text-xs text-gray-500">Checked</p>
          </div>
          <div class="bg-[#202028] rounded-lg p-3 text-center border border-green-500/20">
            <p class="text-2xl font-bold text-green-400">{{ gamesWithUpdates.length }}</p>
            <p class="text-xs text-gray-500">Updates</p>
          </div>
          <div class="bg-[#202028] rounded-lg p-3 text-center border border-red-500/20">
            <p class="text-2xl font-bold" :class="gamesWithErrors.length ? 'text-red-400' : 'text-gray-600'">{{ gamesWithErrors.length }}</p>
            <p class="text-xs text-gray-500">Errors</p>
          </div>
        </div>
        
        <div v-if="gamesWithErrors.length" class="space-y-1">
          <p class="text-xs text-gray-500 mb-1">Failed checks:</p>
          <div v-for="err in gamesWithErrors" :key="err.id" class="text-xs text-red-400/70 flex items-center gap-2">
            <span class="text-gray-500">{{ err.title }}:</span>
            <span class="truncate">{{ err.error }}</span>
          </div>
        </div>
      </section>

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
  transform: translateX(-20px);
}

.list-leave-active {
  position: absolute;
}
</style>
