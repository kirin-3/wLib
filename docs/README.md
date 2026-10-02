# wLib Developer Documentation

Welcome to the wLib developer documentation! These guides cover the shared Linux/Windows codebase, host-specific paths and launch capabilities, and packaging for both platforms.

## Table of Contents
1. [Architecture Overview](architecture.md) - Tech stack, system communication, platform paths, and Linux/Windows capabilities.
2. [Backend Systems (Python)](backend.md) - Detailed breakdown of the Python core (API, Launcher, Scraper, etc.).
3. [Frontend (Vue 3 + TypeScript)](frontend.md) - Information on the UI, typed API bridge, and Vite/vue-tsc workflow.
4. [Database & Schema](database.md) - Explanation of the SQLite database schema and migrations.
5. [Browser Extension API](extension_api.md) - Details on how the companion browser extension communicates with wLib.
6. [Build & Packaging](build.md) - Linux release formats, Windows portable ZIP/MSI builds, signing, and platform CI verification.

For source setup, platform-specific development commands, and running tests, see [CONTRIBUTING.md](../CONTRIBUTING.md). The [build guide](build.md#release-automation) also covers release automation across Linux and Windows.
