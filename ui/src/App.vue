<script setup>
import { onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()

const handleExtensionAdd = (event) => {
    const data = event.detail
    if (data && data.url) {
        // Navigate to the library view and set the importer state
        router.push({
            path: '/',
            query: {
                action: 'import',
                f95url: data.url,
                title: data.title,
                coverImage: data.coverImage,
                tags: JSON.stringify(data.tags || []),
                rating: data.rating,
                developer: data.developer,
                engine: data.engine
            }
        })
    }
}

const handleExtensionOpen = (event) => {
    const data = event.detail
    if (data && data.url) {
        // Navigate to library view and tell it to open this game's detail modal
        router.push({
            path: '/',
            query: {
                action: 'open',
                f95url: data.url
            }
        })
    }
}

onMounted(() => {
    window.addEventListener('wlib-extension-add', handleExtensionAdd)
    window.addEventListener('wlib-extension-open', handleExtensionOpen)
})

onUnmounted(() => {
    window.removeEventListener('wlib-extension-add', handleExtensionAdd)
    window.removeEventListener('wlib-extension-open', handleExtensionOpen)
})
</script>

<template>
  <div class="flex h-screen w-screen overflow-hidden bg-[#101014] text-[#e2e8f0]">
    
    <!-- Sidebar -->
    <aside class="w-64 bg-[#18181c] border-r border-[#2d2d34] flex flex-col justify-between">
      <div>
        <div class="px-6 py-8">
          <h1 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-indigo-500">
            wLib
          </h1>
          <p class="text-xs text-gray-500 mt-1 uppercase tracking-wider font-semibold">Game Manager</p>
        </div>

        <nav class="px-4 space-y-2 mt-4">
          <router-link to="/" 
            class="flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors"
            active-class="bg-[#2d2d34] text-white" 
            exact-active-class="bg-blue-600/10 text-blue-400">
            <svg xmlns="http://www.w3.org/-2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>
            Library
          </router-link>
          
          <router-link to="/updates" 
            class="flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors hover:bg-[#2d2d34]"
            active-class="bg-[#2d2d34] text-white"
            exact-active-class="bg-blue-600/10 text-blue-400">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.45l4.8 4.8"/></svg>
            Updates
          </router-link>

          <router-link to="/extension" 
            class="flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors hover:bg-[#2d2d34]"
            active-class="bg-[#2d2d34] text-white"
            exact-active-class="bg-blue-600/10 text-blue-400">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
            Extension
          </router-link>

          <router-link to="/settings" 
            class="flex items-center gap-3 px-4 py-3 rounded-lg text-sm font-medium transition-colors hover:bg-[#2d2d34]"
            active-class="bg-[#2d2d34] text-white"
            exact-active-class="bg-blue-600/10 text-blue-400">
            <svg xmlns="http://www.w3.org/-2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
            Settings
          </router-link>
        </nav>
      </div>
    </aside>

    <!-- Main Content Area -->
    <main class="flex-1 overflow-y-auto relative">
      <router-view v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </main>
    
  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
