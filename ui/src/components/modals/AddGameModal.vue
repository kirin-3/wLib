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
    <div class="modal-content w-full max-w-lg rounded-2xl shadow-2xl relative overflow-hidden transform transition-all flex flex-col max-h-screen">
      
      <div class="px-6 py-5 flex items-center justify-between" style="border-bottom: 1px solid var(--border); background: var(--bg-surface)">
        <h3 class="text-xl font-bold" style="color: var(--text-primary)">Add New Game</h3>
        <button @click="close" class="transition-colors" style="color: var(--text-muted)" onmouseover="this.style.color='var(--text-primary)'" onmouseout="this.style.color='var(--text-muted)'">
          <svg class="w-6 h-6" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      <div class="p-6 overflow-y-auto space-y-5 flex-1">
        <div>
          <label class="block text-sm font-medium mb-1.5 flex justify-between" style="color: var(--text-secondary)">
            Game Title <span class="text-red-400 text-xs">*</span>
          </label>
          <input v-model="title" type="text" placeholder="e.g. My Awesome RPG" 
            class="modal-input w-full !py-3 font-medium" />
        </div>

        <div>
          <label class="block text-sm font-medium mb-1.5 flex justify-between" style="color: var(--text-secondary)">
            Path to Executable <span class="text-red-400 text-xs">*</span>
          </label>
          <div class="flex gap-2">
            <input v-model="exePath" type="text" placeholder="/path/to/game.exe" 
              class="modal-input flex-1 !py-3 text-sm font-mono" />
            <button @click="browseExe" class="modal-btn !py-3">Browse</button>
          </div>
          <p class="text-xs mt-2" style="color: var(--text-muted)">The .exe or .sh file that launches the game.</p>
        </div>

        <div>
          <label class="block text-sm font-medium mb-1.5" style="color: var(--text-secondary)">F95Zone Thread URL</label>
          <input v-model="f95Url" type="text" placeholder="https://f95zone.to/threads/..." 
            class="modal-input w-full !py-3 text-sm" style="color: var(--brand)" />
          <p class="text-xs mt-2" style="color: var(--text-muted)">Required for checking game updates automatically.</p>
        </div>
      </div>

      <div class="px-6 py-5 flex justify-end gap-3" style="border-top: 1px solid var(--border); background: var(--bg-inset)">
        <button @click="close" class="px-5 py-2.5 rounded-lg text-sm font-medium transition-colors" style="color: var(--text-secondary)">Cancel</button>
        <button @click="save" :disabled="!title || !exePath"
          class="text-white px-6 py-2.5 rounded-lg text-sm font-bold transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          style="background: var(--brand); box-shadow: var(--shadow-brand)">
          Save to Library
        </button>
      </div>

    </div>
  </div>
</template>

<style scoped>
.modal-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-modal);
}

.modal-input {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-radius: 0.5rem;
  padding: 0.5rem 1rem;
  color: var(--text-primary);
  transition: all 0.15s ease;
}
.modal-input::placeholder {
  color: var(--text-muted);
}
.modal-input:focus {
  outline: none;
  border-color: var(--brand);
  box-shadow: 0 0 0 3px var(--brand-glow);
}

.modal-btn {
  background: var(--bg-overlay);
  border: 1px solid var(--border-hover);
  color: var(--text-primary);
  padding: 0.5rem 1rem;
  border-radius: 0.5rem;
  font-size: 0.875rem;
  font-weight: 500;
  transition: all 0.15s ease;
}
.modal-btn:hover {
  background: var(--border-hover);
}
</style>
