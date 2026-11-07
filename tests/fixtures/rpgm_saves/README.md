# RPG Maker save fixtures

Real save files from two unmodified games in the maintainer's library, used by
the save editor codec tests. Each `fileN` is a slot save, and `global.*` is the
slot index (a JSON array indexed by slot number, with `title`, `playtime` and
`timestamp` per slot).

- `mv/file1.rpgsave`, `mv/global.rpgsave`: RPG Maker MV. `LZString.compressToBase64`
  text holding JSON.
- `mz/file0.rmmzsave`, `mz/global.rmmzsave`: RPG Maker MZ.
- `lzstring_reference.json`: `[text, compressToBase64(text)]` pairs produced by
  the reference JavaScript lz-string 1.5.0.

## Findings (task 1.2)

- **MZ layout confirmed.** Read the file as bytes, decode as UTF-8, map each code
  point to one byte (latin-1), then `zlib.decompress` gives UTF-8 JSON. The file
  must be read in binary mode: text mode translates `\r\n` and corrupts the
  deflate stream.
- **MV JsonEx shape.** Objects carry `"@": "Game_Xxx"` class markers and
  `"@c": <n>` ids (also on plain dicts such as `party._items`, so item
  listings must skip `@` keys). Every array is wrapped as
  `{"@c": n, "@a": [...]}` (`actors._data`, `switches._data`,
  `variables._data`, `_paramPlus`, `party._actors`). The root has `"@c"`.
  No `@r` back-references occur in either fixture.
- **MZ JsonEx shape.** Class markers (`"@"`) are present, but arrays are plain
  lists and there are no `@a`/`@c` keys (the MZ fixture's data used none).
- Experience is `actor._exp` = `{"<classId>": exp}`; MZ keeps it plain, MV adds `@c`.
- `global` is a list: `null` at index 0, then one object per slot.
