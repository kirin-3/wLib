// SPDX-License-Identifier: GPL-3.0-or-later
import { createApp } from "vue";
import "./style.css";
import App from "./App.vue";
import router from "./router";
import { loadAccentPreference } from "./utils/accentPreference";
import { loadMotionPreference } from "./utils/motionPreference";
import { loadInterfaceScale } from "./utils/interfaceScalePreference";
import { loadTextSizePreference } from "./utils/textSizePreference";
import { loadThemePreference } from "./utils/themePreference";

loadMotionPreference();
loadThemePreference();
loadAccentPreference();
loadTextSizePreference();
loadInterfaceScale();

const app = createApp(App);
app.use(router);
app.mount("#app");
