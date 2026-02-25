<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { api, onWebviewReady } from '../services/api'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const games = ref([])
const status = ref({ running: false, total: 0, checked: 0, current: '', results: [] })
const lastCheckTime = ref('')
const autoCheckFreq = ref('weekly')
const lastAutoCheck = ref('')
let pollInterval = null

// App Update State
const appUpdate = ref(null)
const appUpdateLoading = ref(true)

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
        // Fetch App Update (GitHub)
        try {
            appUpdateLoading.value = true
            const release = await api.check_app_updates()
            if (release && release.success && release.version) {
                // Determine if newer (simple string compare for now since we use v0.1 format)
                const currentVersion = 'v0.1' // In a real app, this should be fetched from backend or package.json
                if (release.version !== currentVersion) {
                    appUpdate.value = {
                        version: release.version,
                        changelogHtml: DOMPurify.sanitize(marked.parse(release.changelog || 'No changelog provided.')),
                        url: release.url,
                        assets: release.assets || []
                    }
                }
            }
        } catch (e) {
            console.error('Failed to check app updates', e)
        } finally {
            appUpdateLoading.value = false
        }

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

      <!-- App Update Available (GitHub) -->
      <section v-if="appUpdate" class="bg-gradient-to-br from-[#1c1c24] to-[#15151a] rounded-xl border border-indigo-500/30 p-6 shadow-lg shadow-indigo-900/10 relative overflow-hidden">
        <div class="absolute top-0 right-0 w-64 h-64 bg-indigo-500/5 rounded-full blur-3xl -mr-20 -mt-20 pointer-events-none"></div>
        
        <div class="flex items-start justify-between mb-4 relative z-10">
          <div>
            <h3 class="text-xl font-bold text-white flex items-center gap-2 mb-1">
              <svg class="w-6 h-6 text-indigo-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>
              wLib Update Available
            </h3>
            <div class="flex items-center gap-3">
              <span class="text-xs font-mono bg-black/40 text-gray-400 px-2 py-0.5 rounded-md border border-gray-700">Current: v0.1</span>
              <span class="text-xs text-gray-500">→</span>
              <span class="text-xs font-mono bg-indigo-500/20 text-indigo-300 px-2 py-0.5 rounded-md border border-indigo-500/30 font-bold animate-pulse">New: {{ appUpdate.version }}</span>
            </div>
          </div>
          
          <div class="flex gap-2 shrink-0">
             <button @click="openInBrowser(appUpdate.url)" class="bg-[#25252e] hover:bg-[#2d2d34] text-white px-4 py-2 rounded-lg text-xs font-medium border border-[#33333d] transition-colors flex items-center gap-1.5 shadow-sm">
                <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg>
                Release Notes
             </button>
             <!-- Provide direct download links if assets exist -->
             <button v-for="asset in appUpdate.assets.filter(a => a.name.endsWith('.AppImage') || a.name.endsWith('.tar.gz'))" :key="asset.name"
                     @click="openInBrowser(asset.url)" 
                     class="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-lg text-xs font-bold transition-all shadow-lg shadow-indigo-900/20 flex items-center gap-1.5 shrink-0">
                <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                {{ asset.name.endsWith('.AppImage') ? '.AppImage' : '.tar.gz' }}
             </button>
          </div>
        </div>
        
        <!-- Markdown Changelog Rendered Output -->
        <div class="bg-black/40 rounded-lg p-5 border border-[#2d2d34]/60 text-sm text-gray-300 relative z-10 max-h-64 overflow-y-auto wlib-changelog" v-html="appUpdate.changelogHtml"></div>
      </section>

      <!-- F95Zone Game Auto-Check Setting -->
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

/* Markdown styling for the changelog injector */
:deep(.wlib-changelog h1), :deep(.wlib-changelog h2), :deep(.wlib-changelog h3) {
  color: #fff;
  font-weight: 600;
  margin-top: 1rem;
  margin-bottom: 0.5rem;
}
:deep(.wlib-changelog h2) { font-size: 1.1rem; border-bottom: 1px solid #333; padding-bottom: 0.25rem; }
:deep(.wlib-changelog p) { margin-bottom: 0.75rem; line-height: 1.5; }
:deep(.wlib-changelog ul) { list-style-type: disc; margin-left: 1.5rem; margin-bottom: 1rem; }
:deep(.wlib-changelog li) { margin-bottom: 0.25rem; }
:deep(.wlib-changelog a) { color: #818cf8; text-decoration: underline; text-underline-offset: 2px; }
:deep(.wlib-changelog a:hover) { color: #a5b4fc; }
:deep(.wlib-changelog code) { background: #1a1a20; padding: 0.1rem 0.3rem; border-radius: 4px; font-family: monospace; font-size: 0.85em; }
:deep(.wlib-changelog pre) { background: #111; padding: 1rem; border-radius: 8px; overflow-x: auto; margin-bottom: 1rem; border: 1px solid #333; }
:deep(.wlib-changelog pre code) { background: transparent; padding: 0; }
</style>
