import re

with open("tests/test_library_backup.py", "r") as f:
    data = f.read()

data = data.replace(
    'api.import_library_backup(str(backup_path), {"sections": [SECTION_SETTINGS_GENERAL]}, options={"merge_mode": "fill_missing"})',
    'api.import_library_backup(str(backup_path), options={"sections": [SECTION_SETTINGS_GENERAL], "merge_mode": "fill_missing"})'
)

with open("tests/test_library_backup.py", "w") as f:
    f.write(data)
