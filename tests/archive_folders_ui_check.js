// SPDX-License-Identifier: GPL-3.0-or-later
// Run in an isolated Vite browser: playwright-cli run-code --filename tests/archive_folders_ui_check.js
async (page) => {
  const check = (ok, message) => { if (!ok) throw new Error(message); };
  const previousUrl = page.url();
  const keys = ["wlib-mock-games", "wlib-mock-settings", "wlib-mock-platform", "wlib-mock-launch-targets"];
  const previous = await page.evaluate(keys => keys.map(key => [key, localStorage.getItem(key)]), keys);
  try {
    await page.evaluate(() => {
      delete window.pywebview;
      localStorage.removeItem("wlib-mock-launch-targets");
      localStorage.setItem("wlib-mock-platform", "linux");
      localStorage.setItem("wlib-mock-settings", JSON.stringify({library_folders: ["/one", "/two"], library_folder: "/one"}));
      localStorage.setItem("wlib-mock-games", JSON.stringify([{
        id: 1, title: "Multipart", version: "1.0", latest_version: "2.0",
        exe_path: "/one/Multipart/Ep1/Game.sh", developer: "", engine: "",
        launch_targets: [{id: 2, game_id: 1, label: "Ep2", exe_path: "/two/Multipart/Ep2/Game.sh", sort_order: 0}],
      }]));
    });
    const url = new URL(previousUrl); url.hash = "/";
    await page.goto(url.toString());
    await page.reload();
    await page.getByText("Multipart", {exact: true}).first().waitFor();
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("wlib-install-archive", {detail: {gameId: 1, archivePath: "/tmp/Multipart-2.0.zip"}})));
    const part = page.getByLabel("Part to update", {exact: true});
    const folder = page.getByLabel("Library folder", {exact: true});
    await part.waitFor();
    check(await part.locator("option:checked").textContent() === "Default", "Default part must be preselected");
    await page.getByText("/one/Multipart/Ep1", {exact: true}).waitFor();
    await part.selectOption("2");
    await page.getByText("/two/Multipart/Ep2", {exact: true}).waitFor();
    check(await folder.inputValue() === "/two", "Ep2 must preselect its own library folder");
    await folder.selectOption("/one");
    await part.selectOption({label: "Default"});
    await page.getByText("/one/Multipart/Ep1", {exact: true}).waitFor();
    await part.selectOption("2");
    await page.getByText("/two/Multipart/Ep2", {exact: true}).waitFor();
    check(await folder.inputValue() === "/one", "The user's explicit disk choice must survive part changes");
    return "PASS: default part selection, Ep2 old-folder preview and disk preselection, and preservation of a user-selected disk";
  } finally {
    const close = page.getByRole("button", {name: "Close", exact: true});
    if (await close.count()) await close.first().click();
    await page.evaluate(previous => {
      for (const [key, value] of previous) {
        if (value === null) localStorage.removeItem(key);
        else localStorage.setItem(key, value);
      }
    }, previous);
    await page.goto(previousUrl);
    await page.reload();
  }
}
