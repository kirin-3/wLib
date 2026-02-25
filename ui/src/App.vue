<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, onWebviewReady } from './services/api'

const router = useRouter()
const hasAppUpdate = ref(false)
const isDark = ref(true)

const toggleTheme = () => {
    isDark.value = !isDark.value
    document.documentElement.classList.toggle('light', !isDark.value)
    localStorage.setItem('wlib-theme', isDark.value ? 'dark' : 'light')
}

const handleExtensionAdd = (event) => {
    const data = event.detail
    if (data && data.url) {
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
    // Load saved theme
    const savedTheme = localStorage.getItem('wlib-theme')
    if (savedTheme === 'light') {
        isDark.value = false
        document.documentElement.classList.add('light')
    }

    window.addEventListener('wlib-extension-add', handleExtensionAdd)
    window.addEventListener('wlib-extension-open', handleExtensionOpen)
    
    // Check for App Updates on Startup
    onWebviewReady(async () => {
        try {
            const release = await api.check_app_updates()
            if (release && release.success && release.version) {
                if (release.version !== 'v0.1') {
                    hasAppUpdate.value = true
                }
            }
        } catch (e) {
            console.error('Failed to check for app updates globally', e)
        }
    })
})

onUnmounted(() => {
    window.removeEventListener('wlib-extension-add', handleExtensionAdd)
    window.removeEventListener('wlib-extension-open', handleExtensionOpen)
})
</script>

<template>
  <div class="flex h-screen w-screen overflow-hidden" style="background: var(--bg-base); color: var(--text-primary)">
    
    <!-- Sidebar -->
    <aside class="w-64 flex flex-col justify-between shrink-0" style="background: var(--bg-surface); border-right: 1px solid var(--border)">
      <div>
        <div class="px-6 py-8">
          <div class="flex items-center gap-3">
            <img src="/icon.svg" alt="wLib Logo" class="w-8 h-8 drop-shadow-lg" />
            <h1 class="text-2xl font-extrabold bg-clip-text text-transparent bg-gradient-to-r from-[#d152d1] to-[#5a3968]">
              wLib
            </h1>
          </div>
          <p class="text-xs mt-1.5 uppercase tracking-widest font-semibold" style="color: var(--text-muted)">Game Manager</p>
        </div>

        <nav class="px-4 space-y-1 mt-2">
          <router-link to="/" 
            class="nav-link flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium"
            active-class="nav-active" 
            exact-active-class="nav-active">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>
            Library
          </router-link>
          
          <router-link to="/updates" 
            class="nav-link flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium"
            active-class="nav-active"
            exact-active-class="nav-active">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-10.45l4.8 4.8"/></svg>
            Updates
          </router-link>

          <router-link to="/extension" 
            class="nav-link flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium"
            active-class="nav-active"
            exact-active-class="nav-active">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
            Extension
          </router-link>

          <router-link to="/settings" 
            class="nav-link flex items-center gap-3 px-4 py-2.5 rounded-lg text-sm font-medium"
            active-class="nav-active"
            exact-active-class="nav-active">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
            Settings
          </router-link>
        </nav>
      </div>
      
      <!-- Footer: Version, Theme Toggle & Repo -->
      <div class="p-5 flex items-center justify-between" style="border-top: 1px solid var(--border)">
        <router-link to="/updates" v-if="hasAppUpdate" class="text-xs font-mono font-bold px-2 py-0.5 rounded-md animate-pulse" style="background: var(--brand-glow-strong); color: var(--brand); border: 1px solid var(--brand-deep)">v0.1.8 ✨</router-link>
        <span v-else class="text-xs font-mono font-semibold" style="color: var(--text-muted)">v0.2.2</span>
        
        <div class="flex items-center gap-2">
          <!-- Theme Toggle -->
          <button @click="toggleTheme" class="theme-toggle p-1.5 rounded-lg" :title="isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'">
            <!-- Sun icon (shown in dark mode → click to go light) -->
            <svg v-if="isDark" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>
            </svg>
            <!-- Moon icon (shown in light mode → click to go dark) -->
            <svg v-else class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>
            </svg>
          </button>
          
          <!-- GitHub -->
          <a href="https://github.com/kirin-3/wLib" target="_blank" rel="noopener noreferrer" 
             class="theme-toggle p-1.5 rounded-lg" title="View Source on GitHub">
            <svg class="w-4 h-4" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
              <path fill-rule="evenodd" clip-rule="evenodd" d="M12 2C6.477 2 2 6.477 2 12c0 4.42 2.865 8.166 6.839 9.489.5.092.682-.217.682-.482 0-.237-.008-.866-.013-1.7-2.782.603-3.369-1.34-3.369-1.34-.454-1.156-1.11-1.462-1.11-1.462-.908-.62.069-.608.069-.608 1.003.07 1.531 1.03 1.531 1.03.892 1.529 2.341 1.087 2.91.831.092-.646.35-1.086.636-1.336-2.22-.253-4.555-1.11-4.555-4.943 0-1.091.39-1.984 1.029-2.683-.103-.253-.446-1.27.098-2.647 0 0 .84-.269 2.75 1.025A9.578 9.578 0 0112 6.836c.85.004 1.705.114 2.504.336 1.909-1.294 2.747-1.025 2.747-1.025.546 1.379.203 2.394.1 2.647.64.699 1.028 1.592 1.028 2.683 0 3.842-2.339 4.687-4.566 4.935.359.309.678.919.678 1.852 0 1.336-.012 2.415-.012 2.743 0 .267.18.578.688.48C19.138 20.161 22 16.416 22 12c0-5.523-4.477-10-10-10z"/>
            </svg>
          </a>
        </div>
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

.nav-link {
  color: var(--text-secondary);
  transition: all 0.15s ease;
}
.nav-link:hover {
  background: var(--bg-raised);
  color: var(--text-primary);
}
.nav-active {
  background: var(--brand-glow) !important;
  color: var(--brand) !important;
  font-weight: 600;
}

.theme-toggle {
  color: var(--text-muted);
  transition: all 0.2s ease;
}
.theme-toggle:hover {
  color: var(--brand);
  background: var(--bg-raised);
}
</style>
