# SPDX-License-Identifier: GPL-3.0-or-later
"""What the catalog builder and the app must agree on.

Imported by both `catalog_server` and `core.catalog`, so it must not import
anything else from the app.
"""

SCHEMA_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS: frozenset[int] = frozenset({1})

# Object keys in the bucket. A breaking schema change publishes under v2/.
MANIFEST_KEY = "v1/manifest.json"
SNAPSHOT_PREFIX = "v1/snapshots/"

SOURCES: tuple[str, ...] = ("f95", "dlsite", "ryuugames", "vndb")

# Manifest fields.
MANIFEST_SCHEMA_VERSION = "schema_version"
MANIFEST_GENERATED_AT = "generated_at"
MANIFEST_SNAPSHOT = "snapshot"
MANIFEST_SOURCES = "sources"
SNAPSHOT_PATH = "path"
SNAPSHOT_SIZE = "size"
SNAPSHOT_SHA256 = "sha256"
SNAPSHOT_UNCOMPRESSED_SIZE = "uncompressed_size"
SOURCE_COUNT = "count"
SOURCE_UPDATED_AT = "updated_at"

# Keys of the snapshot's `meta(key, value)` table.
META_SCHEMA_VERSION = "schema_version"
META_BUILT_AT = "built_at"
