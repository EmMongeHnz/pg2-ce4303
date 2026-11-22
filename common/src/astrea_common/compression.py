"""zstd helpers for payloads that cross the network (GLB models, TSDF blocks)."""

from __future__ import annotations

import zstandard

DEFAULT_LEVEL = 9


def ZstdCompress(data: bytes, level: int = DEFAULT_LEVEL) -> bytes:
    return zstandard.ZstdCompressor(level=level).compress(data)


def ZstdDecompress(data: bytes) -> bytes:
    return zstandard.ZstdDecompressor().decompress(data)
