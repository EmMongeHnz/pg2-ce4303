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
MAX_PRIMITIVE_VERTICES = 65535


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


def SplitFaces(faces: np.ndarray, maxVertices: int):
    """Yield consecutive runs of faces that each touch at most `maxVertices` distinct vertices."""
    if len(faces) <= 1 or len(np.unique(faces)) <= maxVertices:
        yield faces
        return
    half = len(faces) // 2
    yield from SplitFaces(faces[:half], maxVertices)
    yield from SplitFaces(faces[half:], maxVertices)


class _GlbBuilder:
    def __init__(self) -> None:
        self.binary = b""
        self.bufferViews: list[dict] = []
        self.accessors: list[dict] = []

    def AddAccessor(self, data: np.ndarray, componentType: int, kind: str, target: int, **extra) -> int:
        blob = np.ascontiguousarray(data).tobytes()
        self.bufferViews.append({"buffer": 0, "byteOffset": len(self.binary), "byteLength": len(blob), "target": target})
        self.binary = _Pad(self.binary + blob, b"\x00")
        count = data.shape[0] if data.ndim > 1 else data.size
        self.accessors.append({"bufferView": len(self.bufferViews) - 1, "componentType": componentType, "count": count, "type": kind, **extra})
        return len(self.accessors) - 1

    def AddPrimitive(self, vertices: np.ndarray, normals: np.ndarray, colors: np.ndarray | None, indices: np.ndarray) -> dict:
        attributes = {
            "POSITION": self.AddAccessor(vertices, FLOAT, "VEC3", ARRAY_BUFFER, min=vertices.min(axis=0).tolist(), max=vertices.max(axis=0).tolist()),
            "NORMAL": self.AddAccessor(normals, FLOAT, "VEC3", ARRAY_BUFFER),
        }
        if colors is not None:
            attributes["COLOR_0"] = self.AddAccessor(colors, UNSIGNED_BYTE, "VEC4", ARRAY_BUFFER, normalized=True)
        indexType, indexDtype = (UNSIGNED_SHORT, np.uint16) if len(vertices) <= MAX_PRIMITIVE_VERTICES else (UNSIGNED_INT, np.uint32)
        index = self.AddAccessor(indices.reshape(-1).astype(indexDtype), indexType, "SCALAR", ELEMENT_ARRAY_BUFFER)
        return {"attributes": attributes, "indices": index, "mode": TRIANGLES}


def EncodeGlb(
    vertices: np.ndarray,
    faces: np.ndarray,
    colors: np.ndarray | None = None,
    normals: np.ndarray | None = None,
) -> bytes:
    """Encode a mesh. `colors` is Nx3 or Nx4 uint8; normals are computed when omitted.

    raylib stores mesh indices as 16-bit and silently truncates 32-bit ones, so a mesh with more than
    65535 vertices is written as several primitives of at most that many vertices each. Normals are
    computed on the whole mesh first, so shading stays smooth across the cuts.
    """
    vertices = np.ascontiguousarray(vertices, dtype=np.float32)
    faces = np.asarray(faces)
    if normals is None:
        normals = VertexNormals(vertices.astype(np.float64), faces).astype(np.float32)
    normals = np.ascontiguousarray(normals, dtype=np.float32)
    rgba = None
    if colors is not None:
        rgba = np.asarray(colors, dtype=np.uint8)
        if rgba.shape[1] == 3:
            rgba = np.concatenate([rgba, np.full((len(rgba), 1), 255, np.uint8)], axis=1)

    builder = _GlbBuilder()
    if len(vertices) <= MAX_PRIMITIVE_VERTICES:
        primitives = [builder.AddPrimitive(vertices, normals, rgba, faces)]
    else:
        primitives = []
        for part in SplitFaces(faces, MAX_PRIMITIVE_VERTICES):
            used = np.unique(part)
            primitives.append(builder.AddPrimitive(vertices[used], normals[used], None if rgba is None else rgba[used], np.searchsorted(used, part)))

    document = {
        "asset": {"version": "2.0", "generator": "astrea-view"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": primitives}],
        "accessors": builder.accessors,
        "bufferViews": builder.bufferViews,
        "buffers": [{"byteLength": len(builder.binary)}],
    }
    jsonChunk = _Pad(json.dumps(document, separators=(",", ":")).encode(), b" ")
    totalLength = 12 + 8 + len(jsonChunk) + 8 + len(builder.binary)
    return b"".join(
        [
            struct.pack("<III", GLB_MAGIC, GLB_VERSION, totalLength),
            struct.pack("<II", len(jsonChunk), CHUNK_JSON),
            jsonChunk,
            struct.pack("<II", len(builder.binary), CHUNK_BIN),
            builder.binary,
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
