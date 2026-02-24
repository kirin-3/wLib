<script setup>
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../../services/api'

const props = defineProps(['modelValue'])
const emit = defineEmits(['update:modelValue', 'save'])

const route = useRoute()
const router = useRouter()

const title = ref('')
const exePath = ref('')
const f95Url = ref('')
const coverImage = ref('')
const tags = ref('')
const rating = ref('')
const developer = ref('')
const engine = ref('')

// Auto-fill from query params when opened
watch(() => [props.modelValue, route.query], ([isOpen, query]) => {
    if (isOpen && query.action === 'import') {
        title.value = query.title || ''
        f95Url.value = query.f95url || ''
        coverImage.value = query.coverImage || ''
        try { tags.value = query.tags ? JSON.parse(query.tags) : [] } catch(e) { tags.value = [] }
        rating.value = query.rating || ''
        developer.value = query.developer || ''
        engine.value = query.engine || ''
    }
}, { immediate: true })

const close = () => {
    // Clear query params so normal opens don't auto-fill
    if (route.query.action === 'import') {
        router.replace({ query: {} })
    }
    title.value = ''
    exePath.value = ''
    f95Url.value = ''
    coverImage.value = ''
    tags.value = []
    rating.value = ''
    developer.value = ''
    engine.value = ''
    emit('update:modelValue', false)
}

const browseExe = async () => {
    const p = await api.browseFile()
    if (p) exePath.value = p
}

const save = () => {
    if (!title.value || !exePath.value) return;
    emit('save', {
        title: title.value,
        exe_path: exePath.value,
        f95_url: f95Url.value,
        cover_image: coverImage.value,
        tags: tags.value,
        rating: rating.value,
        developer: developer.value,
        engine: engine.value
    })
    close()
}
</script>

<template>
  <div v-if="modelValue" class="fixed inset-0 z-50 flex items-center justify-center p-4">
    <!-- Overlay -->
    <div class="absolute inset-0 bg-black/70 backdrop-blur-sm transition-opacity" @click="close"></div>
    
    <!-- Modal Content -->
    <div class="bg-[#15151a] w-full max-w-lg rounded-2xl border border-[#2d2d34] shadow-2xl relative overflow-hidden transform transition-all flex flex-col max-h-screen">
      
      <div class="px-6 py-5 border-b border-[#2d2d34] flex items-center justify-between bg-[#18181c]">
        <h3 class="text-xl font-bold text-white">Add New Game</h3>
        <button @click="close" class="text-gray-400 hover:text-white transition-colors">
          <svg class="w-6 h-6" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div class="p-6 overflow-y-auto space-y-5 flex-1">
        <div>
          <label class="block text-sm font-medium text-gray-300 mb-1.5 flex justify-between">
            Game Title <span class="text-red-400 text-xs">*</span>
          </label>
          <input v-model="title" type="text" placeholder="e.g. My Awesome RPG" 
            class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-4 py-3 text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 transition-all font-medium" />
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-300 mb-1.5 flex justify-between">
            Path to Executable <span class="text-red-400 text-xs">*</span>
          </label>
          <div class="flex gap-2">
            <input v-model="exePath" type="text" placeholder="/path/to/game.exe" 
              class="flex-1 bg-[#202028] border border-[#33333d] rounded-lg px-4 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 transition-all font-mono" />
            <button @click="browseExe" class="bg-[#2d2d34] hover:bg-[#33333d] text-white px-4 rounded-lg border border-[#3f3f4a] transition-colors text-sm font-medium">Browse</button>
          </div>
          <p class="text-xs text-gray-500 mt-2">The .exe or .sh file that launches the game.</p>
        </div>

        <div>
          <label class="block text-sm font-medium text-gray-300 mb-1.5">F95Zone Thread URL</label>
          <input v-model="f95Url" type="text" placeholder="https://f95zone.to/threads/..." 
            class="w-full bg-[#202028] border border-[#33333d] rounded-lg px-4 py-3 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 transition-all text-blue-400" />
          <p class="text-xs text-gray-500 mt-2">Required for checking game updates automatically.</p>
        </div>
      </div>

      <div class="px-6 py-5 border-t border-[#2d2d34] bg-[#101014] flex justify-end gap-3">
        <button @click="close" class="text-gray-400 hover:text-white px-5 py-2.5 rounded-lg text-sm font-medium transition-colors">Cancel</button>
        <button @click="save" :disabled="!title || !exePath"
          class="bg-blue-600 border border-blue-500 hover:bg-blue-500 text-white px-6 py-2.5 rounded-lg text-sm font-bold transition-all disabled:opacity-50 disabled:cursor-not-allowed shadow-lg shadow-blue-900/20">
          Save to Library
        </button>
      </div>

    </div>
  </div>
</template>
