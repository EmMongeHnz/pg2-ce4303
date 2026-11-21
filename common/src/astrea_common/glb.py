"""Minimal glTF 2.0 binary (GLB) writer for vertex-coloured triangle meshes.

Kept dependency-free because the renderer (raylib/cgltf) only needs POSITION,
NORMAL, COLOR_0 and indices, and the coordinator exports the same layout.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963
FLOAT = 5126
UNSIGNED_BYTE = 5121
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
TRIANGLES = 4


def VertexNormals(vertices: np.ndarray, faces: np.ndarray) -> np.ndarray:
    a, b, c = (vertices[faces[:, i]] for i in range(3))
    faceNormals = np.cross(b - a, c - a)
    normals = np.zeros_like(vertices)
    for i in range(3):
        np.add.at(normals, faces[:, i], faceNormals)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.maximum(lengths, 1e-12)


def _Pad(data: bytes, fill: bytes) -> bytes:
    return data + fill * (-len(data) % 4)


def EncodeGlb(
    vertices: np.ndarray,
    faces: np.ndarray,
    colors: np.ndarray | None = None,
    normals: np.ndarray | None = None,
) -> bytes:
    """Encode a mesh. `colors` is Nx3 or Nx4 uint8; normals are computed when omitted."""
    vertices = np.ascontiguousarray(vertices, dtype=np.float32)
    if normals is None:
        normals = VertexNormals(vertices.astype(np.float64), np.asarray(faces)).astype(np.float32)
    normals = np.ascontiguousarray(normals, dtype=np.float32)
    indexType, indexDtype = (UNSIGNED_SHORT, np.uint16) if len(vertices) <= 0xFFFF else (UNSIGNED_INT, np.uint32)
    indices = np.ascontiguousarray(np.asarray(faces).reshape(-1), dtype=indexDtype)

    blobs = [vertices.tobytes(), normals.tobytes()]
    accessors = [
        {
            "bufferView": 0,
            "componentType": FLOAT,
            "count": len(vertices),
            "type": "VEC3",
            "min": vertices.min(axis=0).tolist(),
            "max": vertices.max(axis=0).tolist(),
        },
        {"bufferView": 1, "componentType": FLOAT, "count": len(vertices), "type": "VEC3"},
    ]
    attributes = {"POSITION": 0, "NORMAL": 1}
    if colors is not None:
        rgba = np.asarray(colors, dtype=np.uint8)
        if rgba.shape[1] == 3:
            rgba = np.concatenate([rgba, np.full((len(rgba), 1), 255, np.uint8)], axis=1)
        blobs.append(np.ascontiguousarray(rgba).tobytes())
        accessors.append(
            {
                "bufferView": 2,
                "componentType": UNSIGNED_BYTE,
                "normalized": True,
                "count": len(rgba),
                "type": "VEC4",
            }
        )
        attributes["COLOR_0"] = 2
    blobs.append(indices.tobytes())
    accessors.append(
        {"bufferView": len(blobs) - 1, "componentType": indexType, "count": len(indices), "type": "SCALAR"}
    )

    binary = b""
    bufferViews = []
    for i, blob in enumerate(blobs):
        isIndexBlob = i == len(blobs) - 1
        bufferViews.append(
            {
                "buffer": 0,
                "byteOffset": len(binary),
                "byteLength": len(blob),
                "target": ELEMENT_ARRAY_BUFFER if isIndexBlob else ARRAY_BUFFER,
            }
        )
        binary = _Pad(binary + blob, b"\x00")

    document = {
        "asset": {"version": "2.0", "generator": "astrea-view"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": attributes, "indices": len(accessors) - 1, "mode": TRIANGLES}]}],
        "accessors": accessors,
        "bufferViews": bufferViews,
        "buffers": [{"byteLength": len(binary)}],
    }
    jsonChunk = _Pad(json.dumps(document, separators=(",", ":")).encode(), b" ")
    totalLength = 12 + 8 + len(jsonChunk) + 8 + len(binary)
    return b"".join(
        [
            struct.pack("<III", GLB_MAGIC, GLB_VERSION, totalLength),
            struct.pack("<II", len(jsonChunk), CHUNK_JSON),
            jsonChunk,
            struct.pack("<II", len(binary), CHUNK_BIN),
            binary,
        ]
    )


def WriteGlb(path: str | Path, vertices, faces, colors=None, normals=None) -> None:
    Path(path).write_bytes(EncodeGlb(vertices, faces, colors, normals))


def ReadGlbDocument(data: bytes) -> tuple[dict, bytes]:
    """Split a GLB into its JSON document and binary chunk (used by tests and tools)."""
    magic, version, _ = struct.unpack_from("<III", data, 0)
    if magic != GLB_MAGIC or version != GLB_VERSION:
        raise ValueError("not a glTF 2.0 binary")
    jsonLength, _ = struct.unpack_from("<II", data, 12)
    document = json.loads(data[20 : 20 + jsonLength])
    binOffset = 20 + jsonLength
    binLength, _ = struct.unpack_from("<II", data, binOffset)
    return document, data[binOffset + 8 : binOffset + 8 + binLength]
