[B][SIZE=6]wLib —  The Open-Source, Linux-Native Game Manager for F95Zone[/SIZE][/B]

[ATTACH type="full" alt="wlib_library.png"]5757662[/ATTACH]

[ATTACH alt="wlib_grid.png"]5757666[/ATTACH][ATTACH alt="wlib_updates.png"]5757668[/ATTACH][ATTACH alt="wlib_extension.png"]5757670[/ATTACH][ATTACH alt="wlib_settings.png"]5757672[/ATTACH]


[B]TL;DR:[/B] A completely [B]open-source, Linux-native[/B] desktop app and browser extension to manage, track, and launch your F95 games. Built from the ground up for Linux users and the Steam Deck, featuring native Wine/Proton integration to seamlessly run Windows visual novels and games on your Linux machine. No trackers, no closed source, 100% yours.

[B][SIZE=5]Why wLib?[/SIZE][/B]
Windows users have long had great tools like xLibrary to manage their collections. However, as Linux gamers, we were left using workarounds, running managers through Wine, or dealing with closed-source software. wLib changes that. It is built specifically for Linux, completely open-source, and integrates directly with your system's Wine/Proton runners to make playing Windows games on Linux completely frictionless.

[SIZE=5][B]✨ Key Features[/B][/SIZE]
[LIST]
[*][B]Native Wine & Proton Integration[/B] — 99% of games on F95 are Windows [FONT=courier new].exe[/FONT] files. wLib natively understands this. Assign custom Wine prefixes or use Proton to launch Windows games with a single click right from the app.
[*][B]Playtime Tracking & Status [/B]— Automatically tracks precisely how long you play each game and allows you to organize titles by "Playing", "Completed", or "Dropped".
[*][B]100% Free & Open Source[/B] — The code is completely transparent. Anyone can audit it, contribute to it, or fork it. No hidden telemetry, no third-party servers.
[*][B]Smart Update Checker [/B]— Automatically compares your local library with F95Zone. Get flagged when a game has an update available and view detailed changelogs without leaving the app. Uses advanced Playwright scraping to intelligently bypass Cloudflare.
[*][B]Browser Extension Super-Powers [/B]— Seamlessly import games from F95 threads with one click. The extension talks directly to wLib to add games, fetch metadata (covers, tags, developer info), and keep your library synced.
[*][B]Cheat Engine & Japanese Locale Defaults [/B]— Need to run a game in Japanese or use Cheat Engine? Toggle `ja_JP.UTF-8` or enable auto-injection of Lunar Engine directly from the game's setting panel.
[*][B]Engine Auto-Config[/B] — Automatically applies environmental tweaks (like [FONT=courier new]winegstreamer=d[/FONT] for RPG Maker/NW.js) to resolve common Linux black-screen issues without manual tweaking.
[*][B]Beautiful, Modern UI[/B] — A highly responsive, fast, and sleek user interface built with Vue 3. Supports grid and list views, advanced filtering, and instant Dark/Light theme switching.
[*][B]Wayland Support[/B] — First-class support for forcing [FONT=courier new]SDL_VIDEODRIVER=wayland[/FONT] for native Linux binaries.
[/LIST]

[B][SIZE=5] Quick Start[/SIZE][/B]
[LIST=1]
[*]Download the latest [FONT=courier new]wLib-x86_64.AppImage[/FONT] (link below).
[*]Right-click the file → Properties → Permissions → Allow executing file as program.
[*]Double click to run wLib!
[*]Go to the [B]Extension[/B] tab in the app, follow the quick instructions to install the wLib browser extension (supports Chrome, Brave, Edge, Opera, Firefox).
[*]Visit any F95 game thread and click "Add to wLib" or "Open in wLib"!
[/LIST]

[SPOILER="Technical Details"]
[LIST]
[*][B]Stack[/B]: Python, PyWebView, Vue 3, Tailwind CSS, Vite.
[*][B]Extension[/B]: Manifest V3 (Chromium browsers & Firefox).
[*][B]Data[/B]: Local SQLite/JSON storage.
[*][B]Requirements[/B]: Any modern Linux Distribution (Ubuntu, Fedora, Arch, SteamOS, Mint, etc.) [I]Note: AppImages may require libfuse2 on newer distros.[/I]
[*][B]Source Code[/B]: [URL]https://github.com/kirin-3/wLib[/URL]
[/LIST]
[/SPOILER]

[SPOILER="Planned Features"]
[LIST]
[*][B]Steam Deck Gaming Mode UI[/B] — A controller-friendly interface specifically for SteamOS Gaming Mode.
[*][B]Cloud Sync [/B]— Open-source integrations for Nextcloud, Google Drive, and Dropbox to sync your library across multiple Linux machines.
[*][B]Auto-Extract & Organize[/B] — Automatically unzip downloaded game updates and move them to the correct folder.
[/LIST]
[/SPOILER]

[SPOILER="Known Issues"]
[LIST]
[*]Installing winetricks verbs (like DirectX/VCRedist) can take a long time to complete on slower drives.
[*]Linux platform only. No Windows or macOS support is planned.
[/LIST]
[/SPOILER]

[B][SIZE=5]Contribute & Support the Project[/SIZE][/B]
wLib is a passion project built for the Linux/Steam Deck F95 community. Because it is open-source, the best way to support the project is by starring the repository on GitHub, submitting bug reports, or contributing code!

[B][SIZE=4]⭐ [/SIZE][/B][URL='https://github.com/kirin-3/wLib'][B][SIZE=4]Star wLib on GitHub[/SIZE][/B][/URL]

[B][SIZE=4] ⏬ Download[/SIZE][/B]
[URL='https://github.com/kirin-3/wLib/releases/latest']Latest Release on GitHub[/URL]

[I]Disclaimer: wLib is an independent, open-source project and is not affiliated with F95Zone. All data is processed locally on your Linux machine.[/I]