// SPDX-License-Identifier: GPL-3.0-or-later
// Run against an isolated Vite browser session with playwright-cli run-code --filename.
async (page) => {
  const check = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  const verifyContrast = async () => {
    const failures = await page.locator(".sync-panel, .sync-dialog, .backup-passphrase-fields").evaluateAll(roots => {
      const rgb = value => (value.match(/[\d.]+/g) || []).map(Number);
      const luminance = color => rgb(color).slice(0, 3).map(c => c / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4).reduce((sum, c, index) => sum + c * [.2126, .7152, .0722][index], 0);
      const failed = [];
      for (const root of roots) for (const el of root.querySelectorAll("h3, p, label, strong, span, dt, dd, button, select, input[type=password]")) {
        if (!el.getClientRects().length || el.disabled) continue;
        let ancestor = el;
        while (ancestor.parentElement && rgb(getComputedStyle(ancestor).backgroundColor).length === 4 && rgb(getComputedStyle(ancestor).backgroundColor)[3] === 0) ancestor = ancestor.parentElement;
        const color = luminance(getComputedStyle(el).color);
        const background = luminance(getComputedStyle(ancestor).backgroundColor);
        const ratio = (Math.max(color, background) + .05) / (Math.min(color, background) + .05);
        if (ratio < 4.5) failed.push({ text: el.textContent?.slice(0, 40), ratio });
      }
      return failed;
    });
    check(failures.length === 0, "Text contrast below 4.5:1: " + JSON.stringify(failures));
  };
  page.on("pageerror", error => errors.push(error.message));
  await page.keyboard.press("Escape");
  const url = "http://127.0.0.1:5173/#/sync";
  const setState = async (state, extra = {}) => {
    await page.evaluate(({ state, extra }) => localStorage.setItem("wlib-mock-sync", JSON.stringify({ state, location: state === "off" ? "" : "folder", folder_path: "/browser/mock/sync", last_synced_at: state === "off" ? "" : "2026-10-09T12:00:00Z", last_device: "Browser mock device", ...extra })), { state, extra });
    await page.goto(url);
    await page.reload();
    await page.getByRole("heading", { name: "Library sync", exact: true }).waitFor();
  };
  for (const theme of ["light", "dark"]) {
    await page.evaluate(theme => localStorage.setItem("wlib-theme", theme), theme);
    await setState("off");
    await page.evaluate(theme => document.documentElement.classList.toggle("light", theme === "light"), theme);
    await page.getByLabel("Sync location").selectOption("dropbox");
    await page.getByRole("button", { name: "Connect Dropbox", exact: true }).click();
    await page.getByRole("button", { name: "Sync now", exact: true }).click();
    await verifyContrast();
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await page.getByText("Sync is off", { exact: true }).waitFor();
    await page.getByLabel("Sync location").selectOption("gdrive");
    await page.getByRole("button", { name: "Connect Google Drive", exact: true }).click();
    await page.getByText("Connected account: mock@example.com", { exact: true }).waitFor();
    await verifyContrast();
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await page.getByText("Sync is off", { exact: true }).waitFor();
    await page.getByLabel("Sync location").selectOption("dropbox");
    await page.getByRole("button", { name: "Connect Dropbox", exact: true }).click();
    await page.getByRole("button", { name: "Sync now", exact: true }).click();
    await page.getByRole("checkbox", { name: "Encrypt the sync file with a passphrase", exact: true }).click();
    await page.getByRole("dialog").waitFor();
    await verifyContrast();
    await page.getByLabel("Passphrase", { exact: true }).fill("mock-passphrase");
    await page.getByLabel("Confirm passphrase", { exact: true }).fill("wrong");
    await page.getByRole("button", { name: "Enable encryption", exact: true }).click();
    await page.getByText("The passphrases do not match.", { exact: true }).waitFor();
    await page.getByLabel("Confirm passphrase", { exact: true }).fill("mock-passphrase");
    await page.getByRole("button", { name: "Enable encryption", exact: true }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    check(await page.getByRole("checkbox", { name: "Encrypt the sync file with a passphrase", exact: true }).isChecked(), "Encryption must enable after confirmation");
    await page.getByRole("checkbox", { name: "Encrypt the sync file with a passphrase", exact: true }).click();
    check(!await page.getByRole("checkbox", { name: "Encrypt the sync file with a passphrase", exact: true }).isChecked(), "Encryption must disable");
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await page.getByText("Sync is off", { exact: true }).waitFor();
    await page.evaluate(() => {
      window.syncCheckCalls = [];
      window.pywebview = { api: {
        browse_directory: () => "/browser/mock/chosen-folder",
        open_in_browser: url => { window.syncCheckCalls.push(url); return { success: true }; },
        resolve_sync_choice: choice => {
          window.syncCheckCalls.push(choice);
          const status = { ...JSON.parse(localStorage.getItem("wlib-mock-sync")), state: "idle" };
          localStorage.setItem("wlib-mock-sync", JSON.stringify(status));
          return { success: true, ...status };
        },
      } };
    });
    await page.getByLabel("Sync location").selectOption("folder");
    await page.getByRole("button", { name: "Choose folder", exact: true }).click();
    await page.getByText("/browser/mock/chosen-folder", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Privacy policy", exact: true }).click();
    check(await page.evaluate(() => window.syncCheckCalls.includes("https://github.com/kirin-3/wLib/blob/master/PRIVACY.md")), "Privacy policy must call the external browser with the repository policy URL");
    await page.evaluate(() => { delete window.pywebview; delete window.syncCheckCalls; });

    for (const choice of ["Use sync file", "Use this machine", "Merge once"]) {
      await setState("needs_choice");
      await page.getByRole("dialog").waitFor();
      await page.evaluate(() => {
        window.syncChoiceCall = null;
        window.pywebview = { api: { resolve_sync_choice: choice => {
          window.syncChoiceCall = choice;
          const status = { ...JSON.parse(localStorage.getItem("wlib-mock-sync")), state: "idle" };
          localStorage.setItem("wlib-mock-sync", JSON.stringify(status));
          return { success: true, ...status };
        } } };
      });
      await page.getByRole("button", { name: new RegExp("^" + choice) }).click();
      await page.getByRole("dialog").waitFor({ state: "hidden" });
      await page.getByText("Ready to sync", { exact: true }).waitFor();
      check(await page.evaluate(() => window.syncChoiceCall) === ({ "Use sync file": "use-remote", "Use this machine": "use-local", "Merge once": "merge-once" })[choice], "Choice must call the correct bridge action");
      await page.evaluate(() => { delete window.pywebview; delete window.syncChoiceCall; });
    }
    await setState("needs_passphrase");
    await page.getByRole("dialog").waitFor();
    await page.keyboard.press("Tab");
    check(await page.getByLabel("Passphrase", { exact: true }).evaluate(el => el === document.activeElement), "Dialog must accept keyboard focus");
    await page.getByLabel("Passphrase", { exact: true }).fill("wrong");
    await page.getByRole("button", { name: "Unlock and sync", exact: true }).click();
    await page.getByText("The file could not be decrypted.", { exact: true }).waitFor();
    await page.getByLabel("Passphrase", { exact: true }).fill("mock-passphrase");
    await page.getByRole("button", { name: "Unlock and sync", exact: true }).click();
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    await setState("needs_choice");
    await page.getByRole("dialog").waitFor();
    await page.keyboard.press("Escape");
    await page.getByRole("dialog").waitFor({ state: "hidden" });
    await page.getByRole("button", { name: "Choose library", exact: true }).click();
    await page.getByRole("dialog").waitFor();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    check(await page.getByRole("button", { name: "Choose library", exact: true }).evaluate(el => el === document.activeElement), "Dialog must return focus to its opener");
    await setState("error", { last_error: "Browser mock folder is unavailable." });
    await page.getByText("Browser mock folder is unavailable.", { exact: true }).waitFor();
    await setState("syncing");
    check(await page.getByRole("button", { name: "Syncing...", exact: true }).isDisabled(), "Running sync must disable actions");
    await setState("idle");
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      if (width < 640 && await page.getByRole("button", { name: "Collapse navigation", exact: true }).count()) await page.getByRole("button", { name: "Collapse navigation", exact: true }).click();
      await page.evaluate(theme => document.documentElement.classList.toggle("light", theme === "light"), theme);
      const sizes = await page.locator(".sync-panel").evaluate(el => ({ client: el.clientWidth, scroll: el.scrollWidth }));
      check(sizes.scroll <= sizes.client + 1, `Sync panel overflows at ${width}px`);
      await page.getByRole("checkbox", { name: "Encrypt the sync file with a passphrase", exact: true }).click();
      await page.getByRole("dialog").waitFor();
      const dialogSizes = await page.getByRole("dialog").evaluate(el => ({ client: el.clientWidth, scroll: el.scrollWidth }));
      check(dialogSizes.scroll <= dialogSizes.client + 1, `Dialog overflows at ${width}px`);
      await page.keyboard.press("Escape");
    }
    await page.setViewportSize({ width: 1280, height: 900 });
    if (await page.getByRole("button", { name: "Expand navigation", exact: true }).count()) await page.getByRole("button", { name: "Expand navigation", exact: true }).click();
    await page.getByRole("button", { name: "Export", exact: true }).click();
    await page.getByPlaceholder("Choose a folder or enter /path/to/wlib-export.json").fill("/browser/mock/export.json");
    await page.getByRole("checkbox", { name: "Encrypt with passphrase", exact: true }).check();
    await verifyContrast();
    await page.getByLabel("Export passphrase", { exact: true }).fill("mock-passphrase");
    await page.getByLabel("Confirm export passphrase", { exact: true }).fill("wrong");
    await page.getByRole("button", { name: "Create Export", exact: true }).click();
    await page.getByText("Enter a passphrase and a matching confirmation.", { exact: true }).waitFor();
    await page.getByLabel("Confirm export passphrase", { exact: true }).fill("mock-passphrase");
    await page.getByRole("button", { name: "Create Export", exact: true }).click();
    await page.getByText(/^Exported 0 games/).waitFor();
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await page.getByPlaceholder("/path/to/wlib-export.json", { exact: true }).fill("/browser/mock/encrypted.json");
    await page.getByRole("button", { name: "Inspect Backup", exact: true }).click();
    await page.getByLabel("Backup passphrase", { exact: true }).fill("wrong");
    await verifyContrast();
    await page.getByRole("button", { name: "Inspect Backup", exact: true }).click();
    await page.getByText("The file could not be decrypted.", { exact: true }).waitFor();
    await page.getByLabel("Backup passphrase", { exact: true }).fill("mock-passphrase");
    await page.getByRole("button", { name: "Inspect Backup", exact: true }).click();
    await page.getByRole("button", { name: "Import Selected", exact: true }).click();
    await page.getByText(/^Imported backup/).waitFor();
  }
  check(errors.length === 0, errors.join("\n"));
  return "PASS: light/dark; connect/disconnect; sync; all choices; passphrase mismatch/unlock; Escape/focus; 1280/390/320px; encrypted export/import; no page errors";
}
