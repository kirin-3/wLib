with open("core/library_sync.py", "r") as f:
    content = f.read()

content = content.replace('''
            row = cursor.fetchone()
            modified = str(row[0]) if row and row[0] is not None else ""
'''.strip(), '''
            row = cast(tuple[object, ...] | None, cursor.fetchone())
            modified = str(row[0]) if row and row[0] is not None else ""
'''.strip())

content = content.replace('''
                row = cursor.fetchone()
                return str(row[0]) if row and row[0] is not None else ""
'''.strip(), '''
                row = cast(tuple[object, ...] | None, cursor.fetchone())
                return str(row[0]) if row and row[0] is not None else ""
'''.strip())

with open("core/library_sync.py", "w") as f:
    f.write(content)
