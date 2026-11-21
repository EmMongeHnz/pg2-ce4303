import struct

import numpy as np

from astrea_common.glb import ReadGlbDocument, EncodeGlb, UNSIGNED_INT, UNSIGNED_SHORT


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


def test_large_meshes_use_32_bit_indices():
    vertices = np.random.default_rng(0).random((70000, 3)).astype(np.float32)
    faces = np.array([[0, 1, 69999]])
    document, _ = ReadGlbDocument(EncodeGlb(vertices, faces))
    assert document["accessors"][-1]["componentType"] == UNSIGNED_INT
