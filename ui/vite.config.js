// SPDX-License-Identifier: GPL-3.0-or-later
import { cpSync, existsSync, mkdirSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

const root = dirname(fileURLToPath(import.meta.url))

// The built-in web game player serves Ruffle from ui/dist/ruffle/. It is copied
// as plain files and never imported, so the app bundle does not grow.
function copyRuffle() {
  return {
    name: 'wlib-copy-ruffle',
    apply: 'build',
    closeBundle() {
      const source = resolve(root, 'node_modules/@ruffle-rs/ruffle')
      if (!existsSync(join(source, 'ruffle.js'))) {
        throw new Error('@ruffle-rs/ruffle is missing; run npm ci')
      }
      const target = resolve(root, 'dist/ruffle')
      mkdirSync(target, { recursive: true })
      for (const name of readdirSync(source)) {
        if (name === 'ruffle.js' || name.endsWith('.wasm') || name.startsWith('LICENSE')) {
          cpSync(join(source, name), join(target, name))
        }
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    tailwindcss(),
    copyRuffle(),
  ],
})
