# wLib Privacy Policy

wLib is a desktop app that runs on your computer. The wLib project does not collect, receive, or store any of your data. Its only server builds a public game catalog, described below, and receives nothing from the app.

**What wLib stores on your computer.** Your game library, settings, and app data stay in wLib's data folder on your machine (`~/.local/share/wLib` on Linux, `%LOCALAPPDATA%\wLib` on Windows). That folder also holds local-only save backups, attached PDFs, diagnostics logs, and the app log. Those files stay on your computer and are never included in JSON exports or sync.

**Library sync (optional).** If you turn on sync, wLib writes one file, `wlib-library.json`, to the place you choose:

- **A folder:** wLib writes the file to that folder. Whatever program syncs that folder (for example Syncthing, MEGA, Dropbox, or Google Drive) handles it under that program's own privacy policy.
- **Dropbox:** wLib asks for access only to its own app folder, `/Apps/wLib`, and reads and writes only that file. wLib cannot see any other files in your Dropbox. Data goes directly between your computer and Dropbox, under [Dropbox's privacy policy](https://www.dropbox.com/privacy).
- **Google Drive:** wLib asks for one Google permission, access to its own hidden app data folder in your Drive (the `drive.appdata` scope), and reads and writes only that file there. wLib cannot see or change any other file in your Drive, and the file does not appear in My Drive. Data goes directly between your computer and Google, under [Google's privacy policy](https://policies.google.com/privacy). wLib's use of information received from Google APIs adheres to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements. wLib uses that access only to sync your library, does not transfer it to anyone, and does not use it for advertising.

The sync file contains your game list (titles, developers, tags, F95Zone thread links, versions, cover and screenshot links), your play status, favorites, ratings, notes, playtime, and general wLib settings. It also contains your computer's name, so you can see which machine synced last. It does not contain file paths, launch settings, login sessions, passwords, save backups, or attached PDFs.

**Encryption.** You can protect the sync file with a passphrase. wLib then encrypts it on your computer before writing it, so the folder, Dropbox, or Google Drive only holds encrypted data. wLib cannot recover a forgotten passphrase.

**Credentials.** The Dropbox or Google refresh token and the encryption key are stored only in wLib's local database on your computer. A token is never sent anywhere except to Dropbox or Google, to sign in and to keep the connection alive. Signing in to Google also shows wLib your Google account email address, which it displays in the sync settings and does not send anywhere.

**Disconnecting.** Use "Disconnect" in wLib's sync settings, or remove wLib under Dropbox > Settings > Connected apps, or under Google Account > Security > Third-party apps & services. Disconnecting removes the token from your computer and asks Dropbox or Google to revoke it. The sync file stays where it is until you delete it.

**Game catalog (on by default).** The wLib project builds a catalog of games from public pages and APIs of F95Zone, DLsite, Ryuugames and VNDB on its own server, and publishes it as a file on Cloudflare R2 at `wlib-db.thekirin.net`. At startup, and at most every 12 hours or when you choose "Check now", wLib downloads a small manifest from that address and, when it changed, the catalog file. These are plain downloads: they carry no query parameters, cookies, account data or library data, only a user agent with wLib's version. Cloudflare sees your IP address, as with any download, under [Cloudflare's privacy policy](https://www.cloudflare.com/privacypolicy/). The app never contacts the project's server. The catalog is stored as `catalog.db` in wLib's data folder and is never exported, synced or backed up. You can turn it off in Settings → Game catalog, which also deletes the local copy. VNDB data is available under the Open Database License.

**Other network requests.** wLib contacts F95Zone to check game versions and thread details. When you ask it to, it also downloads runtimes such as Proton, RPG Maker RTP, or Cheat Engine from their publishers. These requests send no library data.

**Diagnostics.** wLib writes logs locally. The **Diagnostics** page can build a report on request and copy it for a bug report; it replaces your home folder and login name in paths and includes app/OS versions, paths, database size, and game count. Nothing is sent automatically.

**Contact.** Questions or concerns: open an issue at https://github.com/kirin-3/wLib/issues.
