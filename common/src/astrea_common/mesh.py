"""Triangle meshes: ASCII PLY reading and area-weighted surface sampling (NumPy only)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class TriangleMesh:
    """`vertices` is Nx3 float, `faces` is Mx3 int indices into `vertices`."""

    vertices: np.ndarray
    faces: np.ndarray

    def FaceAreas(self) -> np.ndarray:
        a, b, c = (self.vertices[self.faces[:, i]] for i in range(3))
        return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)

    def FaceNormals(self) -> np.ndarray:
        a, b, c = (self.vertices[self.faces[:, i]] for i in range(3))
        normals = np.cross(b - a, c - a)
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        return normals / np.maximum(lengths, 1e-12)

    def Transformed(self, rotation: np.ndarray, translation: np.ndarray, scale: float = 1.0) -> "TriangleMesh":
        return TriangleMesh(scale * self.vertices @ np.asarray(rotation).T + translation, self.faces)


def ReadAsciiPly(path: str | Path) -> TriangleMesh:
    """Read the x, y, z vertices and triangle faces of an ASCII PLY, ignoring other vertex properties."""
    with open(path, "r") as file:
        if file.readline().strip() != "ply":
            raise ValueError(f"{path}: not a PLY file")
        vertexCount = faceCount = 0
        vertexProperties: list[str] = []
        currentElement = ""
        for line in iter(file.readline, ""):
            words = line.split()
            if not words:
                continue
            if words[0] == "format" and words[1] != "ascii":
                raise ValueError(f"{path}: only ASCII PLY is supported, got {words[1]}")
            if words[0] == "element":
                currentElement = words[1]
                if currentElement == "vertex":
                    vertexCount = int(words[2])
                elif currentElement == "face":
                    faceCount = int(words[2])
            elif words[0] == "property" and currentElement == "vertex":
                vertexProperties.append(words[-1])
            elif words[0] == "end_header":
                break
        columns = [vertexProperties.index(name) for name in ("x", "y", "z")]
        vertexRows = [file.readline().split() for _ in range(vertexCount)]
        vertices = np.array(vertexRows, dtype=np.float64)[:, columns]
        faceRows = [file.readline().split() for _ in range(faceCount)]
    if any(row[0] != "3" for row in faceRows):
        raise ValueError(f"{path}: only triangle faces are supported")
    faces = np.array([row[1:4] for row in faceRows], dtype=np.int64)
    return TriangleMesh(vertices, faces)


def SampleSurface(mesh: TriangleMesh, count: int, rng: np.random.Generator, faceIds: np.ndarray | None = None):
    """Return (points, faceIndexOfEachPoint) drawn uniformly by area, optionally restricted to `faceIds`."""
    candidates = np.arange(len(mesh.faces)) if faceIds is None else np.asarray(faceIds)
    areas = mesh.FaceAreas()[candidates]
    chosen = candidates[rng.choice(len(candidates), size=count, p=areas / areas.sum())]
    a, b, c = (mesh.vertices[mesh.faces[chosen, i]] for i in range(3))
    r1 = np.sqrt(rng.random((count, 1)))
    r2 = rng.random((count, 1))
    return (1.0 - r1) * a + r1 * (1.0 - r2) * b + r1 * r2 * c, chosen
