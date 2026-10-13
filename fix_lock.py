with open("core/library_sync.py", "r") as f:
    content = f.read()

content = content.replace('''
        local_modified = self._setting("local_modified_at")
        local_changed = self.local_changed()
'''.strip(), '''
        with closing(database.get_connection()) as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            local_modified = self._setting("local_modified_at")
            local_changed = self.local_changed()
            connection.commit()
'''.strip())

with open("core/library_sync.py", "w") as f:
    f.write(content)
