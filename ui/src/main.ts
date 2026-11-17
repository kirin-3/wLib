// SPDX-License-Identifier: GPL-3.0-or-later
import { createApp } from "vue";
import "@fontsource-variable/inter";
import "./style.css";
import App from "./App.vue";
import router from "./router";
import { api, onWebviewReady } from "./services/api";
import { loadTvMode } from "./utils/tvMode";
import { loadAccentPreference } from "./utils/accentPreference";
import { loadMotionPreference } from "./utils/motionPreference";
import { startCoverAnimation } from "./utils/coverAnimation";
import { startRunningGamesTracking, refreshRunningGames } from "./composables/useRunningGames";
import { loadInterfaceScale } from "./utils/interfaceScalePreference";
import { loadTextSizePreference } from "./utils/textSizePreference";
import { loadThemePreference } from "./utils/themePreference";
import { createUiErrorReporter } from "./utils/uiErrorReporter";

const reporter = createUiErrorReporter();
window.addEventListener("error", (event) => {
  reporter.reportWindow(event, router.currentRoute.value.path);
});
window.addEventListener("unhandledrejection", (event) => {
  reporter.reportRejection(event, router.currentRoute.value.path);
});
onWebviewReady(() => reporter.connect((report) => api.logUiError(report)));

loadMotionPreference();
startRunningGamesTracking();
startCoverAnimation();
// Before the bridge is ready the call would fall back to mock data.
onWebviewReady(() => void refreshRunningGames(() => api.getRunningGames()));
loadThemePreference();
loadAccentPreference();
loadTextSizePreference();
loadInterfaceScale();
loadTvMode();

const app = createApp(App);
app.config.errorHandler = (error, instance, info) => {
  console.error(error);
  const component = instance?.$options;
  const name = component?.__name || component?.name || "Vue";
  reporter.reportVue(error, `${name} / ${info}`, router.currentRoute.value.path);
};
app.use(router);
app.mount("#app");
