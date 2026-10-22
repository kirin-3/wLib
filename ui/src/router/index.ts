// SPDX-License-Identifier: GPL-3.0-or-later
import { createRouter, createWebHashHistory } from "vue-router";
import type { RouteRecordRaw } from "vue-router";
import LibraryView from "../views/LibraryView.vue";
import SettingsView from "../views/SettingsView.vue";
import ExtensionView from "../views/ExtensionView.vue";
import UpdatesView from "../views/UpdatesView.vue";
import LibraryBackupView from "../views/LibraryBackupView.vue";
import SyncView from "../views/SyncView.vue";
import DiagnosticsView from "../views/DiagnosticsView.vue";

const routes: RouteRecordRaw[] = [
  { path: "/diagnostics", name: "diagnostics", component: DiagnosticsView },
  {
    path: "/",
    name: "library",
    component: LibraryView,
  },
  {
    path: "/updates",
    name: "updates",
    component: UpdatesView,
  },
  {
    path: "/extension",
    name: "extension",
    component: ExtensionView,
  },
  {
    path: "/import-export",
    name: "import-export",
    component: LibraryBackupView,
  },
  {
    path: "/sync",
    name: "sync",
    component: SyncView,
  },
  {
    path: "/settings",
    name: "settings",
    component: SettingsView,
  },
  {
    path: "/:pathMatch(.*)*",
    redirect: "/",
  },
];

// We use HashHistory here for PyWebView local file loading compatibility
const router = createRouter({
  history: createWebHashHistory(),
  routes,
});

export default router;
