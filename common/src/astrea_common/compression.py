"""zstd helpers for payloads that cross the network (GLB models, TSDF blocks)."""

from __future__ import annotations

import zstandard

DEFAULT_LEVEL = 9


def ZstdCompress(data: bytes, level: int = DEFAULT_LEVEL) -> bytes:
    return zstandard.ZstdCompressor(level=level).compress(data)


def ZstdDecompress(data: bytes, maxOutputBytes: int | None = None) -> bytes:
    """`maxOutputBytes` caps the output so a payload from the network cannot inflate without bound."""
    decompressor = zstandard.ZstdDecompressor()
    if maxOutputBytes:
        return decompressor.decompress(data, max_output_size=maxOutputBytes)
    return decompressor.decompress(data)
