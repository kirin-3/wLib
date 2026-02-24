import { createRouter, createWebHashHistory } from 'vue-router'
import LibraryView from '../views/LibraryView.vue'
import SettingsView from '../views/SettingsView.vue'
import ExtensionView from '../views/ExtensionView.vue'
import UpdatesView from '../views/UpdatesView.vue'

const routes = [
    {
        path: '/',
        name: 'library',
        component: LibraryView
    },
    {
        path: '/updates',
        name: 'updates',
        component: UpdatesView
    },
    {
        path: '/extension',
        name: 'extension',
        component: ExtensionView
    },
    {
        path: '/settings',
        name: 'settings',
        component: SettingsView
    }
]

// We use HashHistory here for PyWebView local file loading compatibility
const router = createRouter({
    history: createWebHashHistory(),
    routes
})

export default router
