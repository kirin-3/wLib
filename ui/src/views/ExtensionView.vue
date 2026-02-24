<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { api, onWebviewReady } from '../services/api'

const connectionStatus = ref('checking') // 'checking' | 'connected' | 'disconnected'
const lastCheck = ref('')
let pollInterval = null

const checkConnection = async () => {
    try {
        const resp = await fetch('http://localhost:8183/api/check?url=__ping__', { signal: AbortSignal.timeout(2000) })
        if (resp.ok) {
            connectionStatus.value = 'connected'
        } else {
            connectionStatus.value = 'disconnected'
        }
    } catch {
        connectionStatus.value = 'disconnected'
    }
    lastCheck.value = new Date().toLocaleTimeString()
}

const openExtensionFolder = async () => {
    try {
        await api.openExtensionFolder()
    } catch {
        // fallback: try xdg-open
        console.log('Could not open extension folder via API')
    }
}

onMounted(() => {
    onWebviewReady(() => {
        checkConnection()
        pollInterval = setInterval(checkConnection, 10000) // check every 10s
    })
})

onUnmounted(() => {
    if (pollInterval) clearInterval(pollInterval)
})
</script>

<template>
  <div class="p-8 max-w-4xl h-full flex flex-col overflow-y-auto">
    <header class="mb-8">
      <h2 class="text-3xl font-bold text-white mb-2 tracking-tight">Browser Extension</h2>
      <p class="text-gray-400 text-sm border-l-2 border-purple-500 pl-3">Connect your browser to add games directly from F95Zone.</p>
    </header>

    <div class="space-y-8 pb-12">
      
      <!-- Connection Status -->
      <section class="bg-[#1a1a20] rounded-xl border border-[#2d2d34] p-6">
        <h3 class="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg class="w-5 h-5 text-purple-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M2 12h5l2-9 4 18 2-9h5"/><circle cx="19" cy="12" r="2"/></svg>
          Connection Status
        </h3>
        
        <div class="flex items-center gap-4">
          <div class="flex items-center gap-3">
            <!-- Status Indicator -->
            <div :class="[
              'w-3 h-3 rounded-full',
              connectionStatus === 'connected' ? 'bg-green-500 shadow-lg shadow-green-500/40' :
              connectionStatus === 'checking' ? 'bg-yellow-500 animate-pulse' :
              'bg-red-500 shadow-lg shadow-red-500/40'
            ]"></div>
            
            <div>
              <p :class="[
                'text-sm font-semibold',
                connectionStatus === 'connected' ? 'text-green-400' :
                connectionStatus === 'checking' ? 'text-yellow-400' :
                'text-red-400'
              ]">
                {{ connectionStatus === 'connected' ? 'Connected' : connectionStatus === 'checking' ? 'Checking...' : 'Disconnected' }}
              </p>
              <p class="text-xs text-gray-500">HTTP server on port 8183 · Last checked: {{ lastCheck || '—' }}</p>
            </div>
          </div>
          
          <button @click="checkConnection" class="ml-auto bg-[#2d2d34] hover:bg-[#33333d] text-white px-3 py-1.5 rounded-lg text-xs font-medium border border-[#3f3f4a] transition-colors">
            Refresh
          </button>
        </div>
      </section>

      <!-- Extension Folder -->
      <section class="bg-[#1a1a20] rounded-xl border border-[#2d2d34] p-6">
        <h3 class="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg class="w-5 h-5 text-purple-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
          Extension Files
        </h3>
        
        <p class="text-sm text-gray-400 mb-4">The browser extension is bundled with wLib in the <code class="bg-[#25252e] px-1.5 py-0.5 rounded text-blue-400 text-xs">extension/</code> directory.</p>
        
        <button @click="openExtensionFolder"
          class="bg-purple-600 hover:bg-purple-500 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition-all shadow-lg shadow-purple-900/20 flex items-center gap-2">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" x2="12" y1="11" y2="17"/><polyline points="9 14 12 11 15 14"/></svg>
          Open Extension Folder
        </button>
      </section>

      <!-- Installation Instructions -->
      <section class="bg-[#1a1a20] rounded-xl border border-[#2d2d34] p-6">
        <h3 class="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg class="w-5 h-5 text-purple-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>
          How to Install
        </h3>
        
        <!-- Chrome / Chromium -->
        <div class="mb-6">
          <h4 class="text-sm font-bold text-gray-200 mb-3 flex items-center gap-2">
            <span class="bg-blue-600/20 text-blue-400 px-2 py-0.5 rounded text-xs font-bold">Chrome / Chromium / Edge</span>
          </h4>
          <ol class="space-y-2 text-sm text-gray-400 list-decimal list-inside">
            <li>Open <code class="bg-[#25252e] px-1.5 py-0.5 rounded text-blue-400 text-xs">chrome://extensions</code> in your browser</li>
            <li>Enable <span class="text-white font-medium">Developer mode</span> (toggle in the top-right corner)</li>
            <li>Click <span class="text-white font-medium">Load unpacked</span></li>
            <li>Select the <code class="bg-[#25252e] px-1.5 py-0.5 rounded text-blue-400 text-xs">extension/</code> folder from the wLib directory</li>
            <li>The wLib extension icon should appear in your toolbar</li>
          </ol>
        </div>
        
        <!-- Firefox -->
        <div>
          <h4 class="text-sm font-bold text-gray-200 mb-3 flex items-center gap-2">
            <span class="bg-orange-600/20 text-orange-400 px-2 py-0.5 rounded text-xs font-bold">Firefox</span>
          </h4>
          <ol class="space-y-2 text-sm text-gray-400 list-decimal list-inside">
            <li>Open <code class="bg-[#25252e] px-1.5 py-0.5 rounded text-orange-400 text-xs">about:debugging#/runtime/this-firefox</code> in Firefox</li>
            <li>Click <span class="text-white font-medium">Load Temporary Add-on</span></li>
            <li>Navigate to the <code class="bg-[#25252e] px-1.5 py-0.5 rounded text-orange-400 text-xs">extension/</code> folder and select <code class="bg-[#25252e] px-1.5 py-0.5 rounded text-orange-400 text-xs">manifest.json</code></li>
            <li>The extension will remain active until Firefox is restarted</li>
          </ol>
          <p class="text-xs text-gray-600 mt-3 italic">Note: Firefox requires the extension to be reloaded each time the browser restarts (temporary add-on limitation).</p>
        </div>
      </section>

      <!-- How it works -->
      <section class="bg-[#1a1a20] rounded-xl border border-[#2d2d34] p-6">
        <h3 class="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <svg class="w-5 h-5 text-purple-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg>
          How It Works
        </h3>
        <div class="space-y-3 text-sm text-gray-400">
          <p>When you visit an F95Zone game thread, the extension automatically:</p>
          <ul class="space-y-1.5 list-disc list-inside ml-2">
            <li>Extracts the game title, version, developer, engine, cover image, tags, and rating</li>
            <li>Shows an <span class="text-green-400 font-medium">"Add to wLib"</span> button on the page</li>
            <li>If the game is already in your library, shows <span class="text-blue-400 font-medium">"Open in wLib"</span> instead</li>
          </ul>
          <p class="text-xs text-gray-500 mt-2">Communication happens via a local HTTP server on <code class="bg-[#25252e] px-1.5 py-0.5 rounded text-purple-400 text-xs">localhost:8183</code>. wLib must be running for the extension to work.</p>
        </div>
      </section>

    </div>
  </div>
</template>
