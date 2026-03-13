import { createApp } from "vue";
import "./style.css";
import App from "./App.vue";
import router from "./router";
import { loadMotionPreference } from "./utils/motionPreference";

loadMotionPreference();

const app = createApp(App);
app.use(router);
app.mount("#app");
