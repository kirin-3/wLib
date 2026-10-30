# Playnite fixture generator

Requires the .NET 10 SDK. From the repository root:

```sh
dotnet run --project scripts/fixtures/playnite/PlayniteFixtures.csproj
```

The generator uses LiteDB 4.1.4 and a seeded skip-list RNG to regenerate
`tests/fixtures/playnite/library/` byte for byte. All IDs and timestamps are fixed.
The database files and images are committed. CI and packaging never run this
generator, and .NET is not an application dependency.

There are 147 live games, one deleted game, multiple index pages, and a 13,000
character notes field spanning extend pages. The named games cover F95 plugin
metadata, all action types, other-OS paths, local and remote covers, an invalid
image, user and community scores, a DLsite ID, and a Steam-only entry.

Internal collection names match Playnite's `GetCollection<TItem>()`: `Game`,
`CompletionStatus`, `Company`, `Category`, `Tag`, `GameFeature`, and `Genre`.
The completion-status database also has an unrelated `CompletionStatusSettings`
collection to check that the reader returns only the requested collection.

## Small library and AVIF regression fixtures

```sh
dotnet run --project scripts/fixtures/playnite/PlayniteFixtures.csproj -- --small
```

This generates the separate `tests/fixtures/playnite/small-library/` (two
synthetic F95-linked games) and `empty-games.db` (a present, empty Game
collection). All six optional lookups are absent: five valid 8 KiB databases
have no collections; completionstatuses contains only the unrelated
CompletionStatusSettings collection. Game records deliberately retain
unresolved metadata IDs. The original 147-game fixture is untouched.
Before the optional-collection fix, reading the small library raises
“The file is not a Playnite library.”

Both small-library covers copy `tests/fixtures/playnite/avif/cover.png`, whose
bytes are AVIF despite the extension, reproducing Playnite's misleading names.
This is our generated 2×2 lossless image (red, green, blue, white pixels), not
third-party artwork. Its complete PNG input is `avif/source.png`. Regenerate
the AVIF with libavif 1.4.2 / aom 3.15.2, then regenerate the small library:

```sh
avifenc --codec aom --speed 6 --lossless tests/fixtures/playnite/avif/source.png tests/fixtures/playnite/avif/cover.png
```

Encoder versions may change the compressed bytes. The committed complete AVIF
is the rendering fixture; brand/size edge cases are built in Python tests.
Normal tests need neither .NET nor an image encoder, network access, or the VM
library. Fixtures are synthetic and distributed under the project license.
