// SPDX-License-Identifier: GPL-3.0-or-later
import { ref } from "vue";

// Whether an update check runs. App.vue's status poll keeps it current, so
// views read it here instead of polling the same status themselves.
export const updateCheckRunning = ref(false);
