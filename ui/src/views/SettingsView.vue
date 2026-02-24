<script setup>
import { ref, onMounted } from 'vue'
import { api, onWebviewReady } from '../services/api.js'

const protonPath = ref('')
const prefixPath = ref('')
const enableLogging = ref(false)
const saving = ref(false)
const installingDeps = ref(false)
const installSuccess = ref(false)
const installError = ref('')
const downloadingProton = ref(false)
const protonError = ref('')
const installingRtps = ref(false)
const rtpSuccess = ref(false)
const rtpError = ref('')

const loadSettings = async () => {
    try {
        const data = await api.getSettings()
        if (data) {
           protonPath.value = data.proton_path || ''
           prefixPath.value = data.wine_prefix_path || ''
           enableLogging.value = !!data.enable_logging
        }
    } catch (e) {
        console.error("Failed to load settings", e)
    }
}

const browseProton = async () => {
    const p = await api.browseFile()
    if (p) protonPath.value = p
}

const browsePrefix = async () => {
    const p = await api.browseDirectory()
    if (p) prefixPath.value = p
}

const downloadProton = async () => {
    downloadingProton.value = true
    protonError.value = ''
    try {
        const result = await api.downloadProtonGe()
        if (result && result.success && result.path) {
            protonPath.value = result.path
            await saveSettings() // auto save settings since proton changed
        } else {
            protonError.value = result?.error || "Failed to download Proton."
        }
    } catch (e) {
        protonError.value = e.toString()
    } finally {
        downloadingProton.value = false
    }
}

const installRtps = async () => {
    installingRtps.value = true
    rtpSuccess.value = false
    rtpError.value = ''
    try {
        const result = await api.installRpgmakerRtp()
        if (result && result.success) {
            rtpSuccess.value = true
            setTimeout(() => rtpSuccess.value = false, 5000)
        } else {
            rtpError.value = result?.error || "Unknown error occurred"
        }
    } catch (e) {
        rtpError.value = e.toString()
    } finally {
        installingRtps.value = false
    }
}

const installDeps = async () => {
    installingDeps.value = true
    installSuccess.value = false
    installError.value = ''
    try {
        const result = await api.installRpgmakerDependencies()
        if (result && result.success) {
            installSuccess.value = true
            setTimeout(() => installSuccess.value = false, 5000)
        } else {
            installError.value = result?.error || "Unknown error occurred"
        }
    } catch (e) {
        installError.value = e.toString()
    } finally {
        installingDeps.value = false
    }
}

onMounted(() => {
    onWebviewReady(() => {
        loadSettings()
    })
})

const saveSettings = async () => {
    saving.value = true
    try {
        await api.saveSettings({
            proton_path: protonPath.value,
            wine_prefix_path: prefixPath.value,
            enable_logging: enableLogging.value
        })
    } catch(e) {
        console.error(e)
    } finally {
        saving.value = false
    }
}
</script>

<template>
  <div class="p-8 max-w-4xl pb-12">
    
    <header class="mb-10">
      <h2 class="text-3xl font-bold text-white mb-2 tracking-tight">Settings</h2>
      <p class="text-gray-400 text-sm border-l-2 border-blue-500 pl-3">Configure launch paths and application behavior.</p>
    </header>

    <div class="bg-[#1a1a20] rounded-xl border border-[#2d2d34] shadow-lg overflow-hidden">
      <div class="p-8 space-y-8">
        
        <!-- Environment Settings -->
        <section>
          <h3 class="text-lg font-semibold text-white mb-4 flex items-center gap-2">
            <svg class="w-5 h-5 text-blue-400" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" x2="12" y1="22.08" y2="12"/></svg>
            Proton & Wine Environment
          </h3>
          
          <div class="space-y-5">
            <div>
              <label class="block text-sm font-medium text-gray-300 mb-1.5 flex justify-between items-center">
                <span>Proton / Wine Executable Path</span>
                <button @click="downloadProton" :disabled="downloadingProton" class="text-xs text-blue-400 hover:text-blue-300 font-medium flex items-center gap-1 disabled:opacity-50">
                  <svg v-if="downloadingProton" class="w-3 h-3 animate-spin" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
                  <svg v-else class="w-3 h-3" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/></svg>
                  {{ downloadingProton ? 'Downloading (Check terminal)...' : 'Auto Download Latest GE-Proton' }}
                </button>
              </label>
              <div class="flex gap-3">
                <input v-model="protonPath" type="text" placeholder="/usr/bin/wine or /path/to/GE-Proton/proton" 
                  class="flex-1 bg-[#25252e] border border-[#33333d] rounded-lg px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 transition-shadow" />
                <button @click="browseProton" class="bg-[#2d2d34] hover:bg-[#33333d] text-white px-4 rounded-lg border border-[#3f3f4a] transition-colors text-sm font-medium">Browse</button>
              </div>
              <p class="text-xs text-gray-500 mt-2">Leave empty to use the system default `wine` command.</p>
              <p v-if="protonError" class="text-xs text-red-400 mt-1">{{ protonError }}</p>
            </div>

            <div>
              <label class="block text-sm font-medium text-gray-300 mb-1.5">Default Wine Prefix Path (WINEPREFIX)</label>
              <div class="flex gap-3">
                <input v-model="prefixPath" type="text" placeholder="Auto-managed (~/.local/share/wLib/prefix) if left empty" 
                  class="flex-1 bg-[#25252e] border border-[#33333d] rounded-lg px-4 py-2.5 text-sm text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 transition-shadow" />
                <button @click="browsePrefix" class="bg-[#2d2d34] hover:bg-[#33333d] text-white px-4 rounded-lg border border-[#3f3f4a] transition-colors text-sm font-medium">Browse</button>
              </div>
              <p class="text-xs text-gray-500 mt-2">The location where game dependencies and save files will be isolated.</p>
            </div>
            
            <div class="flex items-center justify-between mt-6 bg-[#1a1a20] p-4 rounded-lg border border-[#2d2d34]">
              <div>
                <h4 class="text-sm font-medium text-gray-200">Enable Debug Logging</h4>
                <p class="text-xs text-gray-500 mt-1">Saves Wine/Proton launch output to a .log file next to the game executable to help troubleshoot black screens.</p>
              </div>
              <label class="relative inline-flex items-center cursor-pointer">
                <input type="checkbox" v-model="enableLogging" class="sr-only peer">
                <div class="w-11 h-6 bg-[#2d2d34] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-gray-300 after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-blue-600"></div>
              </label>
            </div>
            
            <hr class="border-[#2d2d34] my-4" />
            
            <div>
              <h4 class="text-sm font-bold text-gray-200 mb-2">Advanced Tools</h4>
              <p class="text-xs text-gray-400 mb-4">Run these tools to fix common issues with certain game engines.</p>
              
              <div class="flex flex-col gap-3">
                <div class="flex items-center justify-between bg-[#1a1a20] p-4 rounded-lg border border-[#2d2d34]">
                  <div>
                    <h5 class="text-sm font-medium text-gray-200">RPGMaker / Unity Fix (Winetricks)</h5>
                    <p class="text-xs text-gray-500 mt-1">Installs corefonts, d3d, quartz, wmp9, directshow. Fixes video decoding black screens.</p>
                  </div>
                  <button @click="installDeps" :disabled="installingDeps"
                    class="bg-[#25252e] hover:bg-[#2d2d34] text-white px-4 py-2 rounded border border-[#33333d] transition-colors text-xs font-semibold disabled:opacity-50 flex items-center gap-2">
                    <svg v-if="installingDeps" class="w-3 h-3 animate-spin" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
                    {{ installingDeps ? 'Installing...' : 'Install DLLs' }}
                  </button>
                </div>
                
                <div class="flex items-center justify-between bg-[#1a1a20] p-4 rounded-lg border border-[#2d2d34]">
                  <div>
                    <h5 class="text-sm font-medium text-gray-200">RPGMaker XP / VX Ace RTP</h5>
                    <p class="text-xs text-gray-500 mt-1">Downloads official RGSS-RTP missing files (rgss104e, rgss3a) into your prefix.</p>
                  </div>
                  <button @click="installRtps" :disabled="installingRtps"
                    class="bg-[#25252e] hover:bg-[#2d2d34] text-white px-4 py-2 rounded border border-[#33333d] transition-colors text-xs font-semibold disabled:opacity-50 flex items-center gap-2">
                    <svg v-if="installingRtps" class="w-3 h-3 animate-spin" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a10 10 0 0 1 10 10"/></svg>
                    {{ installingRtps ? 'Processing...' : 'Install RTPs' }}
                  </button>
                </div>
              </div>

              <div class="mt-2 space-y-1">
                <p v-if="installError" class="text-xs text-red-400">{{ installError }}</p>
                <p v-if="installSuccess" class="text-xs text-green-400">DLL Installation started in the terminal!</p>
                <p v-if="rtpError" class="text-xs text-red-400">{{ rtpError }}</p>
                <p v-if="rtpSuccess" class="text-xs text-green-400">RTP Installation started in the background! (This may take a minute)</p>
              </div>
            </div>
          </div>
        </section>

      </div>
      
      <div class="bg-[#15151a] px-8 py-5 flex justify-end gap-3 border-t border-[#2d2d34]">
        <button class="text-gray-400 hover:text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">Reset</button>
        <button @click="saveSettings" class="bg-blue-600 hover:bg-blue-500 text-white px-6 py-2 rounded-lg text-sm font-medium transition-colors shadow-lg shadow-blue-900/20">Save Changes</button>
      </div>
    </div>
    
  </div>
</template>
