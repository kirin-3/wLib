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
