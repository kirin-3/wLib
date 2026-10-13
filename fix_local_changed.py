with open("core/library_sync.py", "r") as f:
    content = f.read()

content = content.replace('''
    def local_changed(self) -> bool:
        return bool(self._setting("location")) and (
            timestamp(self._setting("local_modified_at"))
            > timestamp(self._setting("last_synced_at"))
            or bool(self._setting("encryption_change"))
        )
'''.strip(), '''
    def local_changed(self) -> bool:
        return bool(self._setting("location")) and (
            timestamp(self._setting("local_modified_at"))
            > timestamp(self._setting("last_synced_at"))
            or bool(self._setting("encryption_change"))
        )
'''.strip())

with open("core/library_sync.py", "w") as f:
    f.write(content)
