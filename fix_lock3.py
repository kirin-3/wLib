with open("core/library_sync.py", "r") as f:
    content = f.read()

content = content.replace('''
    def _push(self, location: SyncLocation, revision: str | None) -> None:
        # Capture payload and its timestamp under one SQLite write lock.
        with closing(database.get_connection()) as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            modified = self._setting("local_modified_at") or utc_now()
            device = socket.gethostname()
            backup = build_sync_backup(modified, device)
            connection.commit()
'''.strip(), '''
    def _push(self, location: SyncLocation, revision: str | None) -> None:
        # Capture payload and its timestamp under one SQLite write lock.
        with closing(database.get_connection()) as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            cursor = connection.cursor()
            _ = cursor.execute("SELECT value FROM settings WHERE key = ?", ("sync_local_modified_at",))
            row = cursor.fetchone()
            modified = str(row[0]) if row and row[0] is not None else ""
            if not modified:
                modified = utc_now()
            device = socket.gethostname()

            # build_sync_backup uses its own connection inside, which is allowed in WAL mode, but since we hold BEGIN IMMEDIATE,
            # it might be blocked if it tries to write, but it only reads. Wait, reads might be blocked? No, SQLite allows concurrent reads.
            backup = build_sync_backup(modified, device)
            connection.commit()
'''.strip())

with open("core/library_sync.py", "w") as f:
    f.write(content)
