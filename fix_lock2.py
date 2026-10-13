with open("core/library_sync.py", "r") as f:
    content = f.read()

content = content.replace('''
        with closing(database.get_connection()) as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            local_modified = self._setting("local_modified_at")
            local_changed = self.local_changed()
            connection.commit()
'''.strip(), '''
        with closing(database.get_connection()) as connection:
            _ = connection.execute("BEGIN IMMEDIATE")

            def _get_setting_locked(key: str) -> str:
                cursor = connection.cursor()
                _ = cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
                row = cursor.fetchone()
                return str(row[0]) if row and row[0] is not None else ""

            local_modified = _get_setting_locked("sync_local_modified_at")
            last_synced = _get_setting_locked("sync_last_synced_at")
            loc = _get_setting_locked("sync_location")
            enc = _get_setting_locked("sync_encryption_change")

            local_changed = bool(loc) and (
                timestamp(local_modified) > timestamp(last_synced) or bool(enc)
            )
            connection.commit()
'''.strip())

with open("core/library_sync.py", "w") as f:
    f.write(content)
