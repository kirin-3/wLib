# wLib Privacy Policy

wLib is a desktop app that runs on your computer. The wLib project does not run servers and does not collect, receive, or store any of your data.

**What wLib stores on your computer.** Your game library, settings, and app data stay in wLib's data folder on your machine (`~/.local/share/wLib` on Linux).

**Library sync (optional).** If you turn on sync, wLib writes one file, `wlib-library.json`, to the place you choose:

- **A folder:** wLib writes the file to that folder. Whatever program syncs that folder (for example Syncthing, MEGA, Dropbox, or Google Drive) handles it under that program's own privacy policy.
- **Dropbox:** wLib asks for access only to its own app folder, `/Apps/wLib`, and reads and writes only that file. wLib cannot see any other files in your Dropbox. Data goes directly between your computer and Dropbox, under [Dropbox's privacy policy](https://www.dropbox.com/privacy).

The sync file contains your game list (titles, developers, tags, F95Zone thread links, versions, cover links), your play status, favorites, ratings, notes, playtime, and general wLib settings. It also contains your computer's name, so you can see which machine synced last. It does not contain file paths, launch settings, login sessions, or passwords.

**Encryption.** You can protect the sync file with a passphrase. wLib then encrypts it on your computer before writing it, so the folder or Dropbox only holds encrypted data. wLib cannot recover a forgotten passphrase.

**Credentials.** The Dropbox access token and the encryption key are stored only in wLib's local database on your computer. They are never sent anywhere except to Dropbox, when the token is used to sign in.

**Disconnecting.** Use "Disconnect" in wLib's sync settings, or remove wLib under Dropbox > Settings > Connected apps. Disconnecting removes the token from your computer. The sync file stays where it is until you delete it.

**Other network requests.** wLib contacts F95Zone to check game versions and thread details. When you ask it to, it also downloads runtimes such as Proton, RPG Maker RTP, or Cheat Engine from their publishers. These requests send no library data.

**Contact.** Questions or concerns: open an issue at https://github.com/kirin-3/wLib/issues.
