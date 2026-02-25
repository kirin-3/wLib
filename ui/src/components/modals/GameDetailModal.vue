<script setup>
import { ref, watch, computed } from 'vue'
import { api } from '../../services/api'

const props = defineProps({
    modelValue: Boolean,
    game: Object
})
const emit = defineEmits(['update:modelValue', 'updated', 'deleted', 'launch'])

// Editable fields
const title = ref('')
const exePath = ref('')
const f95Url = ref('')
const version = ref('')
const developer = ref('')
const commandLineArgs = ref('')
const coverImage = ref('')
const status = ref('')
const tags = ref([])
const engine = ref('')
const newTag = ref('')
const latestVersion = ref('')
const runJapaneseLocale = ref(false)
const runWayland = ref(false)
const autoInjectCe = ref(false)
const ceInstalled = ref(false)

// F95Zone rating (read-only, from scraper)
const f95Rating = ref('')

// Personal ratings (0-5 scale)
const ratingGraphics = ref(0)
const ratingStory = ref(0)
const ratingFappability = ref(0)
const ratingGameplay = ref(0)

const saving = ref(false)
const deleting = ref(false)

const statuses = [
    { value: '', label: 'Not Started' },
    { value: 'completed', label: '✅ Completed' },
    { value: 'in_progress', label: '🎮 In Progress' },
    { value: 'replaying', label: '🔄 Replaying' },
    { value: 'waiting_update', label: '⏳ Waiting for Update' },
    { value: 'abandoned', label: '🚫 Abandoned' },
]

const averagePersonalRating = computed(() => {
    const sum = ratingGraphics.value + ratingStory.value + ratingFappability.value + ratingGameplay.value
    const avg = sum / 4
    return avg > 0 ? avg.toFixed(1) : '—'
})

const hasUpdate = computed(() => {
    return latestVersion.value && version.value && latestVersion.value.trim() !== version.value.trim()
})

// Sync props → local refs when game changes
watch(() => props.game, (g) => {
    if (g) {
        title.value = g.title || ''
        exePath.value = g.exe_path || ''
        f95Url.value = g.f95_url || ''
        version.value = g.version || ''
        developer.value = g.developer || ''
        commandLineArgs.value = g.command_line_args || ''
        coverImage.value = g.cover_image_path || ''
        status.value = g.status || ''
        engine.value = g.engine || ''
        runJapaneseLocale.value = g.run_japanese_locale ? true : false
        runWayland.value = g.run_wayland ? true : false
        autoInjectCe.value = g.auto_inject_ce ? true : false
        // Parse tags: could be comma-separated string or already an array
        if (typeof g.tags === 'string' && g.tags) {
            tags.value = g.tags.split(',').map(t => t.trim()).filter(Boolean)
        } else if (Array.isArray(g.tags)) {
            tags.value = [...g.tags]
        } else {
            tags.value = []
        }
        f95Rating.value = g.rating || ''
        latestVersion.value = g.latest_version || ''
        ratingGraphics.value = g.rating_graphics || 0
        ratingStory.value = g.rating_story || 0
        ratingFappability.value = g.rating_fappability || 0
        ratingGameplay.value = g.rating_gameplay || 0
    }
}, { immediate: true })

import { onMounted } from 'vue'
import { onWebviewReady } from '../../services/api'
onMounted(() => {
    onWebviewReady(async () => {
        try {
            const ceCheck = await api.isCheatEngineInstalled()
            ceInstalled.value = !!ceCheck?.installed
        } catch (e) {
            console.error("Failed to check cheat engine status", e)
        }
    })
})

const close = () => {
    emit('update:modelValue', false)
}

const browseExe = async () => {
    const p = await api.browseFile()
    if (p) exePath.value = p
}

const save = async () => {
    if (!props.game) return
    saving.value = true
    try {
        await api.updateGame(props.game.id, {
            title: title.value,
            exe_path: exePath.value,
            f95_url: f95Url.value,
            version: version.value,
            developer: developer.value,
            command_line_args: commandLineArgs.value,
            cover_image_path: coverImage.value,
            status: status.value,
            tags: tags.value.join(', '),
            engine: engine.value,
            run_japanese_locale: runJapaneseLocale.value,
            run_wayland: runWayland.value,
            auto_inject_ce: autoInjectCe.value,
            latest_version: latestVersion.value,
            rating_graphics: ratingGraphics.value,
            rating_story: ratingStory.value,
            rating_fappability: ratingFappability.value,
            rating_gameplay: ratingGameplay.value,
        })
        emit('updated')
        close()
    } catch (e) {
        console.error("Failed to save game", e)
    } finally {
        saving.value = false
    }
}

const deleteGame = async () => {
    if (!props.game || !confirm('Are you sure you want to remove this game from your library?')) return
    deleting.value = true
    try {
        await api.deleteGame(props.game.id)
        emit('deleted')
        close()
    } catch(e) {
        console.error("Failed to delete", e)
    } finally {
        deleting.value = false
    }
}

const launchGame = () => {
    if (props.game) {
        emit('launch', props.game.exe_path, commandLineArgs.value, runJapaneseLocale.value, runWayland.value, autoInjectCe.value)
    }
}

const addTag = () => {
    const t = newTag.value.trim()
    if (t && !tags.value.includes(t)) {
        tags.value.push(t)
    }
    newTag.value = ''
}

const removeTag = (tag) => {
    tags.value = tags.value.filter(t => t !== tag)
}

const openInBrowser = async () => {
    if (f95Url.value) {
        await api.openInBrowser(f95Url.value)
    }
}
</script>

<template>
  <div v-if="modelValue && game" class="fixed inset-0 z-50 flex items-center justify-center p-4">
    <!-- Overlay -->
    <div class="absolute inset-0 bg-black/70 backdrop-blur-sm" @click="close"></div>
    
    <!-- Modal Content -->
    <div class="bg-[#15151a] w-full max-w-2xl rounded-2xl border border-[#2d2d34] shadow-2xl relative overflow-hidden transform transition-all flex flex-col max-h-[90vh]">
      
      <!-- Header with Cover Image Background -->
      <div class="relative h-40 overflow-hidden bg-gradient-to-br from-[#202028] to-[#15151a]">
        <img v-if="coverImage" :src="coverImage" class="absolute inset-0 w-full h-full object-cover opacity-40 blur-sm" />
        <div class="absolute inset-0 bg-gradient-to-t from-[#15151a] via-transparent to-transparent"></div>
        
        <div class="absolute bottom-4 left-6 right-6 flex items-end justify-between">
          <div>
            <h3 class="text-xl font-bold text-white drop-shadow-lg">{{ title || 'Untitled Game' }}</h3>
            <div class="flex items-center gap-2 mt-0.5">
              <p v-if="developer" class="text-sm text-gray-400">by {{ developer }}</p>
              <span v-if="engine" class="bg-purple-600/30 text-purple-300 text-xs font-bold px-2 py-0.5 rounded-md border border-purple-500/30">{{ engine }}</span>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <!-- F95Zone Rating Badge -->
            <div v-if="f95Rating" class="bg-black/60 backdrop-blur-md text-yellow-400 text-xs font-bold px-2.5 py-1 rounded-lg border border-white/10 flex items-center gap-1">
              <svg class="w-3.5 h-3.5 fill-yellow-400" viewBox="0 0 20 20"><path d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"/></svg>
              F95: {{ f95Rating }}
            </div>
            <!-- Personal Average -->
            <div class="bg-black/60 backdrop-blur-md text-blue-400 text-xs font-bold px-2.5 py-1 rounded-lg border border-white/10">
              You: {{ averagePersonalRating }}
            </div>
          </div>
        </div>
        
        <button @click="close" class="absolute top-3 right-3 text-gray-400 hover:text-white transition-colors bg-black/40 rounded-lg p-1.5 backdrop-blur-md">
          <svg class="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" /></svg>
        </button>
      </div>

      <!-- Scrollable Body -->
      <div class="p-6 overflow-y-auto space-y-6 flex-1">
        
        <!-- Status Selector -->
        <div class="flex flex-wrap gap-2">
          <button v-for="s in statuses" :key="s.value" @click="status = s.value"
            :class="[
              'px-3 py-1.5 rounded-full text-xs font-semibold border transition-all',
              status === s.value 
                ? 'bg-blue-600/20 border-blue-500 text-blue-400' 
                : 'bg-[#202028] border-[#33333d] text-gray-400 hover:border-gray-500'
            ]">
            {{ s.label }}
          </button>
        </div>
        
        <!-- Game Info Fields -->
        <div class="grid grid-cols-2 gap-4">
          <div class="col-span-2">
            <label class="block text-xs font-medium text-gray-400 mb-1">Title</label>
            <input v-model="title" type="text" class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500/50 focus:border-blue-500 transition-all" />
          </div>
          
          <div class="col-span-2">
            <label class="block text-xs font-medium text-gray-400 mb-1">Executable Path</label>
            <div class="flex gap-2">
              <input v-model="exePath" type="text" class="flex-1 bg-[#202028] border border-[#33333d] rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all" />
              <button @click="browseExe" class="bg-[#2d2d34] hover:bg-[#33333d] text-white px-3 rounded-lg border border-[#3f3f4a] transition-colors text-xs font-medium">Browse</button>
            </div>
          </div>
          
          <div>
            <label class="block text-xs font-medium text-gray-400 mb-1">Version</label>
            <input v-model="version" type="text" placeholder="v1.0" class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-3 py-2 text-sm text-blue-400 font-mono focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all" />
          </div>
          
          <div>
            <label class="block text-xs font-medium text-gray-400 mb-1">Developer</label>
            <input v-model="developer" type="text" class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all" />
          </div>
          
          <div class="col-span-2">
            <label class="block text-xs font-medium text-gray-400 mb-1">F95Zone URL</label>
            <input v-model="f95Url" type="text" placeholder="https://f95zone.to/threads/..." class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-3 py-2 text-sm text-blue-400 focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all" />
          </div>
          
          <div class="col-span-2">
            <label class="block text-xs font-medium text-gray-400 mb-1">Command Line Arguments</label>
            <input v-model="commandLineArgs" type="text" placeholder="--fullscreen --no-intro" class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all" />
          </div>

          <div class="col-span-2 flex items-center justify-between bg-[#202028] p-3 rounded-lg border border-[#33333d]">
            <div>
              <p class="text-sm font-medium text-white">Run with Japanese Locale</p>
              <p class="text-xs text-gray-500">Enable this if the game has unreadable text (Mojibake) or crashes due to missing Japanese fonts (Wolf RPG, older engines).</p>
            </div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" v-model="runJapaneseLocale" class="sr-only peer">
              <div class="w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
            </label>
          </div>

          <div class="col-span-2 flex items-center justify-between bg-[#202028] p-3 rounded-lg border border-[#33333d]">
            <div>
              <p class="text-sm font-medium text-white">Run in Wayland Compatibility Mode</p>
              <p class="text-xs text-gray-500">Enable this if your system uses Wayland and the game crashes or freezes on launch (sets specific SDL and Vulkan flags).</p>
            </div>
            <label class="relative inline-flex items-center cursor-pointer">
              <input type="checkbox" v-model="runWayland" class="sr-only peer">
              <div class="w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
            </label>
          </div>

          <div class="col-span-2 flex items-center justify-between p-3 rounded-lg border border-[#33333d] transition-colors"
               :class="ceInstalled ? 'bg-[#202028]' : 'bg-[#15151a] opacity-60'">
            <div>
              <p class="text-sm font-medium text-white flex items-center gap-2">
                Auto-Launch & Inject Cheat Engine
                <span v-if="!ceInstalled" class="bg-gray-800 text-gray-400 text-[10px] uppercase px-2 py-0.5 rounded font-bold border border-gray-700">Not Installed</span>
              </p>
              <p class="text-xs text-gray-500 mt-1">Spawns Cheat Engine inside the same virtual Wine sandbox immediately after the game starts.</p>
            </div>
            <label class="relative inline-flex items-center" :class="ceInstalled ? 'cursor-pointer' : 'cursor-not-allowed'">
              <input type="checkbox" v-model="autoInjectCe" :disabled="!ceInstalled" class="sr-only peer">
              <div class="w-11 h-6 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-green-600"></div>
            </label>
          </div>
          
          <div class="col-span-2">
            <label class="block text-xs font-medium text-gray-400 mb-1">Cover Image URL</label>
            <input v-model="coverImage" type="text" placeholder="https://..." class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all" />
          </div>
          
          <div>
            <label class="block text-xs font-medium text-gray-400 mb-1">Engine</label>
            <input v-model="engine" type="text" placeholder="Unity, RPGM, Ren'Py..." class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-3 py-2 text-sm text-purple-400 focus:outline-none focus:ring-1 focus:ring-purple-500/50 transition-all" />
          </div>
        </div>
        
        <!-- Personal Ratings Section -->
        <div>
          <h4 class="text-sm font-bold text-gray-300 mb-3 flex items-center gap-2">
            <svg class="w-4 h-4 text-blue-400" viewBox="0 0 20 20" fill="currentColor"><path d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"/></svg>
            Your Ratings
          </h4>
          <div class="space-y-3">
            <div v-for="cat in [
              { label: '🎨 Graphics', model: 'ratingGraphics' },
              { label: '📖 Story', model: 'ratingStory' },
              { label: '🔥 Fappability', model: 'ratingFappability' },
              { label: '🎮 Gameplay', model: 'ratingGameplay' },
            ]" :key="cat.model" class="flex items-center gap-3">
              <span class="text-xs text-gray-400 w-28 shrink-0">{{ cat.label }}</span>
              <div class="flex gap-1">
                <button v-for="star in 5" :key="star" @click="
                  cat.model === 'ratingGraphics' ? ratingGraphics = star :
                  cat.model === 'ratingStory' ? ratingStory = star :
                  cat.model === 'ratingFappability' ? ratingFappability = star :
                  ratingGameplay = star
                " class="transition-all hover:scale-110">
                  <svg class="w-5 h-5" :class="[
                    star <= (cat.model === 'ratingGraphics' ? ratingGraphics :
                             cat.model === 'ratingStory' ? ratingStory :
                             cat.model === 'ratingFappability' ? ratingFappability :
                             ratingGameplay)
                      ? 'text-yellow-400 fill-yellow-400' 
                      : 'text-[#33333d] fill-[#33333d]'
                  ]" viewBox="0 0 20 20">
                    <path d="M10 15l-5.878 3.09 1.123-6.545L.489 6.91l6.572-.955L10 0l2.939 5.955 6.572.955-4.756 4.635 1.123 6.545z"/>
                  </svg>
                </button>
              </div>
              <span class="text-xs text-gray-500 font-mono ml-auto">{{ 
                cat.model === 'ratingGraphics' ? ratingGraphics :
                cat.model === 'ratingStory' ? ratingStory :
                cat.model === 'ratingFappability' ? ratingFappability :
                ratingGameplay
              }}/5</span>
            </div>
          </div>
        </div>
        
        <!-- Tags (editable) -->
        <div>
          <h4 class="text-xs font-medium text-gray-400 mb-2">Tags</h4>
          <div class="flex flex-wrap gap-1.5 mb-2">
            <span v-for="tag in tags" :key="tag"
              class="bg-[#202028] border border-[#33333d] text-gray-400 text-xs px-2 py-0.5 rounded-full flex items-center gap-1 group/tag">
              {{ tag }}
              <button @click="removeTag(tag)" class="text-gray-600 hover:text-red-400 transition-colors ml-0.5">&times;</button>
            </span>
          </div>
          <div class="flex gap-2">
            <input v-model="newTag" type="text" placeholder="Add a tag..." @keydown.enter.prevent="addTag"
              class="flex-1 bg-[#202028] border border-[#33333d] rounded-lg px-3 py-1.5 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500/50 transition-all" />
            <button @click="addTag" class="bg-[#2d2d34] hover:bg-[#33333d] text-white px-3 rounded-lg border border-[#3f3f4a] transition-colors text-xs font-medium">Add</button>
          </div>
        </div>
      </div>
      
      <!-- Footer Actions -->
      <div class="px-6 py-4 border-t border-[#2d2d34] bg-[#101014] flex items-center justify-between">
        <div class="flex gap-2">
          <button @click="deleteGame" :disabled="deleting"
            class="text-red-400 hover:text-red-300 hover:bg-red-500/10 px-3 py-2 rounded-lg text-xs font-medium transition-all">
            {{ deleting ? 'Removing...' : '🗑 Remove' }}
          </button>
          <button v-if="f95Url" @click="openInBrowser"
            class="text-purple-400 hover:text-purple-300 hover:bg-purple-500/10 px-3 py-2 rounded-lg text-xs font-medium transition-all flex items-center gap-1">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3"/></svg>
            Open in Browser
          </button>
        </div>
        <div class="flex gap-3">
          <button @click="launchGame"
            class="bg-green-600 hover:bg-green-500 text-white px-5 py-2 rounded-lg text-sm font-bold transition-all shadow-lg shadow-green-900/20 flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><polygon points="5 3 19 12 5 21 5 3"/></svg>
            Play
          </button>
          <button @click="save" :disabled="saving"
            class="bg-blue-600 hover:bg-blue-500 text-white px-5 py-2 rounded-lg text-sm font-bold transition-all shadow-lg shadow-blue-900/20 disabled:opacity-50">
            {{ saving ? 'Saving...' : 'Save Changes' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
