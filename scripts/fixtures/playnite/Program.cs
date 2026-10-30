using LiteDB;
using System.Reflection;

var small = args.Contains("--small");
var root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "../../../../../../tests/fixtures/playnite/", small ? "small-library" : "library"));
Directory.CreateDirectory(root);
Guid Id(int n) => Guid.Parse($"00112233-4455-6677-8899-{n:000000000000}");
BsonArray Ids(params int[] ns) => new BsonArray(ns.Select(n => new BsonValue(Id(n))));
BsonArray Arr(params BsonValue[] values) => new BsonArray(values);
BsonDocument Link(string url) => new BsonDocument { ["Name"] = "Link", ["Url"] = url };
BsonDocument Action(string name, string path, int type = 0) => new BsonDocument {
    ["Name"] = name, ["Path"] = path, ["Type"] = type, ["IsPlayAction"] = true
};
void Write(string name, IEnumerable<BsonDocument> rows, bool delete = false) {
    var file = Path.Combine(root, name + ".db");
    if (File.Exists(file)) File.Delete(file);
    using var db = new LiteDatabase(file);
    // Seed skip-list levels so the committed binary fixtures reproduce exactly.
    var indexer = db.Engine.GetType().GetField("_indexer", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(db.Engine)!;
    indexer.GetType().GetField("_rand", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(indexer, new Random(42));
    var model = new Dictionary<string, string> {
        ["games"] = "Game", ["completionstatuses"] = "CompletionStatus", ["companies"] = "Company",
        ["categories"] = "Category", ["tags"] = "Tag", ["features"] = "GameFeature", ["genres"] = "Genre"
    }[name];
    var col = db.GetCollection(model);
    foreach (var row in rows) col.Insert(row);
    if (name == "completionstatuses") db.GetCollection("CompletionStatusSettings").Insert(new BsonDocument { ["_id"] = 0, ["DefaultStatus"] = Id(2002) });
    if (delete) { col.Insert(new BsonDocument { ["_id"] = Id(9999), ["Name"] = "Deleted game" }); col.Delete(Id(9999)); }
}
if (small) {
    Write("games", Enumerable.Range(1, 2).Select(i => new BsonDocument {
        ["_id"] = Id(i), ["Name"] = $"Small fixture {i}",
        ["Links"] = Arr(Link($"https://f95zone.to/threads/{12345 * i}/")),
        ["CoverImage"] = Id(i) + "/cover.png", ["DeveloperIds"] = Ids(1001),
        ["CategoryIds"] = Ids(3001), ["GenreIds"] = Ids(6001),
        ["FeatureIds"] = Ids(4001), ["TagIds"] = Ids(5001), ["CompletionStatusId"] = Id(2001)
    }));
    foreach (var name in new[] { "companies", "completionstatuses", "categories", "features", "tags", "genres" })
        Write(name, Array.Empty<BsonDocument>());
    File.WriteAllText(Path.Combine(root, "database.json"), "{\"Version\":4}\n");
    foreach (var i in Enumerable.Range(1, 2)) {
        var dir = Path.Combine(root, "files", Id(i).ToString());
        Directory.CreateDirectory(dir);
        File.Copy(Path.Combine(root, "../avif/cover.png"), Path.Combine(dir, "cover.png"), true);
    }
    // Keep an explicitly created but empty Game collection as a strict-reader case.
    var empty = Path.Combine(root, "../empty-games.db");
    if (File.Exists(empty)) File.Delete(empty);
    using (var db = new LiteDatabase(empty)) {
        var indexer = db.Engine.GetType().GetField("_indexer", BindingFlags.NonPublic | BindingFlags.Instance)!.GetValue(db.Engine)!;
        indexer.GetType().GetField("_rand", BindingFlags.NonPublic | BindingFlags.Instance)!.SetValue(indexer, new Random(42));
        var col = db.GetCollection("Game");
        col.Insert(new BsonDocument { ["_id"] = Id(1) });
        col.Delete(Id(1));
    }
    Console.WriteLine(root);
    return;
}
var games = new List<BsonDocument> {
    new BsonDocument {
        ["_id"] = Id(1), ["Name"] = "Fixture novel", ["DeveloperIds"] = Ids(1001),
        ["Links"] = Arr(Link("https://f95zone.to/threads/12345"), Link("https://vndb.org/v1234")),
        ["Version"] = "0.9", ["FeatureIds"] = Ids(4001, 4002), ["TagIds"] = Ids(5001),
        ["GenreIds"] = Ids(6001), ["CategoryIds"] = Ids(3001, 3002), ["CompletionStatusId"] = Id(2001),
        ["Playtime"] = 7200L, ["LastActivity"] = new DateTime(2025, 1, 2, 3, 4, 5, DateTimeKind.Utc),
        ["Added"] = new DateTime(2024, 1, 2, 0, 0, 0, DateTimeKind.Utc), ["Favorite"] = true,
        ["Notes"] = "Route A done", ["UserScore"] = 80, ["CommunityScore"] = 99,
        ["InstallDirectory"] = "/opt/games/novel", ["CoverImage"] = Id(1) + "/cover.png",
        ["GameActions"] = Arr(Action("Play", "{InstallDir}/game.sh"), Action("Play (32-bit)", "game32.sh"))
    },
    new BsonDocument { ["_id"] = Id(2), ["Name"] = "F95-23456", ["Notes"] = new string('x', 13000), ["CoverImage"] = Id(2) + "/cover.jpg", ["CompletionStatusId"] = Id(2002) },
    new BsonDocument { ["_id"] = Id(3), ["Name"] = "DLsite only", ["GameId"] = "RJ123456", ["CoverImage"] = Id(3) + "/fake.png" },
    new BsonDocument { ["_id"] = Id(4), ["Name"] = "Steam only", ["Links"] = Arr(Link("https://store.steampowered.com/app/1234/")) },
    new BsonDocument { ["_id"] = Id(5), ["Name"] = "https://f95zone.to/threads/34567", ["CommunityScore"] = 90, ["CoverImage"] = "https://example.com/cover.jpg", ["GameActions"] = Arr(Action("Script", "echo hello", 3), Action("Link", "https://example.com", 1), Action("Emulator", "emulator", 2)) },
    new BsonDocument { ["_id"] = Id(6), ["Name"] = "Windows action", ["Links"] = Arr(Link("https://f95zone.to/threads/45678")), ["InstallDirectory"] = @"D:\Games\Novel", ["GameActions"] = Arr(Action("Play", @"{InstallDir}\game.exe")), ["CompletionStatusId"] = Id(2003) },
    new BsonDocument { ["_id"] = Id(7), ["Name"] = "Local only", ["Hidden"] = true, ["GameActions"] = Arr(Action("Play", "/opt/games/local/run.sh")), ["UserScore"] = 0 },
};
// Several index pages and skip-list levels, with stable IDs and local actions.
for (int i = 10; i < 150; i++) games.Add(new BsonDocument { ["_id"] = Id(i), ["Name"] = $"Index game {i}", ["GameActions"] = Arr(Action("Play", $"/opt/games/{i}/run.sh")) });
Write("games", games, true);
foreach (var (name, rows) in new[] {
    ("companies", new[] { (1001, "Fixture studio") }),
    ("completionstatuses", new[] { (2001, "Beaten"), (2002, "Not Started"), (2003, "My custom status") }),
    ("categories", new[] { (3001, "Favorites VN"), (3002, "Backlog") }),
    ("features", new[] { (4001, "Ren'Py"), (4002, "Completed") }),
    ("tags", new[] { (5001, "Romance") }),
    ("genres", new[] { (6001, "Visual Novel") })
}) Write(name, rows.Select(r => new BsonDocument { ["_id"] = Id(r.Item1), ["Name"] = r.Item2 }));
File.WriteAllText(Path.Combine(root, "database.json"), "{\"Version\":4}\n");
void Image(int id, string name, byte[] data) { var dir = Path.Combine(root, "files", Id(id).ToString()); Directory.CreateDirectory(dir); File.WriteAllBytes(Path.Combine(dir, name), data); }
Image(1, "cover.png", Convert.FromBase64String("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="));
Image(2, "cover.jpg", Convert.FromBase64String("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAABAAEDAREAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAv/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCwAB//2Q=="));
Image(3, "fake.png", System.Text.Encoding.UTF8.GetBytes("This is not an image.\n"));
Console.WriteLine(root);
