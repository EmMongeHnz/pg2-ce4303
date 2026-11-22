import struct

import numpy as np

from astrea_common.glb import ReadGlbDocument, EncodeGlb, UNSIGNED_SHORT, VertexNormals


def Tetrahedron():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=np.float32)
    faces = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
    return vertices, faces


def test_glb_layout_and_accessors():
    vertices, faces = Tetrahedron()
    colors = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255], [9, 9, 9]], dtype=np.uint8)
    data = EncodeGlb(vertices, faces, colors)
    assert len(data) % 4 == 0
    assert struct.unpack_from("<I", data, 8)[0] == len(data)
    document, binary = ReadGlbDocument(data)
    primitive = document["meshes"][0]["primitives"][0]
    assert set(primitive["attributes"]) == {"POSITION", "NORMAL", "COLOR_0"}
    accessors = document["accessors"]
    assert accessors[primitive["attributes"]["POSITION"]]["max"] == [1.0, 1.0, 1.0]
    indexAccessor = accessors[primitive["indices"]]
    assert indexAccessor["componentType"] == UNSIGNED_SHORT and indexAccessor["count"] == 12
    view = document["bufferViews"][indexAccessor["bufferView"]]
    indices = np.frombuffer(binary, np.uint16, count=12, offset=view["byteOffset"])
    assert np.array_equal(indices, faces.reshape(-1))
    for view in document["bufferViews"]:
        assert view["byteOffset"] % 4 == 0


def GridMesh(side: int):
    """Triangulated side x side grid with unique, known positions and a smooth normal field."""
    ys, xs = np.mgrid[0:side, 0:side]
    vertices = np.stack([xs.ravel(), ys.ravel(), np.sin(xs.ravel() / 50.0)], axis=1).astype(np.float32)
    index = np.arange(side * side).reshape(side, side)
    a, b, c, d = index[:-1, :-1].ravel(), index[:-1, 1:].ravel(), index[1:, :-1].ravel(), index[1:, 1:].ravel()
    return vertices, np.concatenate([np.stack([a, c, b], axis=1), np.stack([b, c, d], axis=1)])


def ReadPrimitive(document, binary, primitive, name, dtype, width):
    accessor = document["accessors"][primitive[name] if name == "indices" else primitive["attributes"][name]]
    view = document["bufferViews"][accessor["bufferView"]]
    data = np.frombuffer(binary, dtype, count=accessor["count"] * width, offset=view["byteOffset"])
    return data.reshape(-1, width) if width > 1 else data


def test_large_meshes_are_split_into_16_bit_primitives():
    vertices, faces = GridMesh(300)
    colors = (vertices[:, :2] % 256).astype(np.uint8)
    colors = np.concatenate([colors, np.zeros((len(colors), 1), np.uint8)], axis=1)
    document, binary = ReadGlbDocument(EncodeGlb(vertices, faces, colors))
    primitives = document["meshes"][0]["primitives"]
    assert len(vertices) > 65535 and len(primitives) >= 2
    triangles = []
    for primitive in primitives:
        assert document["accessors"][primitive["indices"]]["componentType"] == UNSIGNED_SHORT
        positions = ReadPrimitive(document, binary, primitive, "POSITION", np.float32, 3)
        indices = ReadPrimitive(document, binary, primitive, "indices", np.uint16, 1)
        assert len(positions) <= 65535 and indices.max() < len(positions)
        triangles.append(positions[indices.reshape(-1, 3)])
    expected = vertices[faces]
    got = np.concatenate(triangles)
    assert len(got) == len(expected)
    assert np.array_equal(np.sort(got.reshape(len(got), -1), axis=0), np.sort(expected.reshape(len(expected), -1), axis=0))


def test_split_primitives_keep_normals_and_colours_of_the_whole_mesh():
    vertices, faces = GridMesh(300)
    colors = np.stack([vertices[:, 0] % 256, vertices[:, 1] % 256, np.full(len(vertices), 7)], axis=1).astype(np.uint8)
    whole = VertexNormals(vertices.astype(np.float64), faces).astype(np.float32)
    byPosition = {tuple(v): i for i, v in enumerate(vertices)}
    document, binary = ReadGlbDocument(EncodeGlb(vertices, faces, colors))
    for primitive in document["meshes"][0]["primitives"]:
        positions = ReadPrimitive(document, binary, primitive, "POSITION", np.float32, 3)
        normals = ReadPrimitive(document, binary, primitive, "NORMAL", np.float32, 3)
        rgba = ReadPrimitive(document, binary, primitive, "COLOR_0", np.uint8, 4)
        original = np.array([byPosition[tuple(p)] for p in positions[::97]])
        assert np.allclose(normals[::97], whole[original])
        assert np.array_equal(rgba[::97, :3], colors[original])
