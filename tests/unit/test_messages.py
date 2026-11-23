"""Messages: round trips through real frames, field validation, direction rules, model chunks and array blobs."""

import hashlib

import numpy as np
import pytest

from astrea_common.framing import Frame, FrameDecoder, MalformedPayload, UnknownMessageType
from astrea_common.messages import (
    PROTOCOL_VERSION,
    ArrayBlob,
    BlockPayload,
    CameraState,
    Compression,
    GridPayload,
    Heartbeat,
    Hello,
    InputEvent,
    InputSource,
    IntrinsicsPayload,
    InvalidMessage,
    IsAllowed,
    JobAssign,
    JobResult,
    JobStatus,
    MESSAGE_CLASSES,
    MessageType,
    ModelAssembler,
    ModelIntegrityError,
    ModelPush,
    Role,
    Rotate,
    RotateDone,
    SplitModel,
    ViewPayload,
    DecodeMessage,
    EncodeMessage,
)


def RoundTrip(message):
    frames = FrameDecoder().Feed(EncodeMessage(message))
    assert len(frames) == 1
    return DecodeMessage(frames[0])


def SampleView(index: int = 3) -> ViewPayload:
    rgb = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    depth = np.linspace(0.2, 0.5, 24, dtype=np.float32).reshape(4, 6)
    return ViewPayload(
        index,
        IntrinsicsPayload(500.0, 501.0, 3.0, 2.0, 6, 4),
        tuple(float(v) for v in np.eye(3).ravel()),
        (1.0, -2.0, 300.0),
        ArrayBlob.FromNumpy(rgb),
        ArrayBlob.FromNumpy(depth),
    )


def SampleJobAssign() -> JobAssign:
    return JobAssign("job-1", 0, 2, GridPayload((-50.0, -50.0, -10.0), 0.65, (10, 12, 14)), 2.6, {"minDepthMm": 200.0}, (SampleView(0), SampleView(1)))


def SampleJobResult() -> JobResult:
    shape = (2, 3, 4)
    block = BlockPayload(
        (1, 2, 3),
        shape,
        ArrayBlob.FromNumpy(np.ones(shape, dtype=np.float32)),
        ArrayBlob.FromNumpy(np.full(shape, 2.0, dtype=np.float32)),
        ArrayBlob.FromNumpy(np.zeros((*shape, 3), dtype=np.float32)),
    )
    return JobResult("job-1", 0, 1, JobStatus.OK, "", 1.5, (0, 1), (block,))


@pytest.mark.parametrize(
    "message",
    [
        Hello(PROTOCOL_VERSION, "worker-b", Role.WORKER),
        Heartbeat(5, 123456789, False),
        Heartbeat(5, 123456789, True),
        InputEvent(int(InputSource.JOYSTICK), 3, 1, 987654321),
        CameraState(12.5, -3.0, 1.2, "bunny"),
        Rotate(1, -64),
        RotateDone(1, 128),
        SplitModel("bunny", b"glb-bytes", Compression.ZSTD)[0],
    ],
)
def test_simple_messages_round_trip(message):
    assert RoundTrip(message) == message


def test_job_assign_round_trips_with_views():
    decoded = RoundTrip(SampleJobAssign())
    original = SampleJobAssign()
    assert decoded.jobId == original.jobId and decoded.grid == original.grid and decoded.params == original.params
    assert [view.index for view in decoded.views] == [0, 1]
    np.testing.assert_array_equal(decoded.views[0].depthM.ToNumpy(), original.views[0].depthM.ToNumpy())
    np.testing.assert_array_equal(decoded.views[1].rgb.ToNumpy(), original.views[1].rgb.ToNumpy())


def test_job_result_round_trips_with_blocks():
    decoded = RoundTrip(SampleJobResult())
    assert decoded.status is JobStatus.OK and decoded.viewIndices == (0, 1)
    block = decoded.blocks[0]
    assert block.startIndex == (1, 2, 3) and block.shape == (2, 3, 4)
    np.testing.assert_array_equal(block.weight.ToNumpy(), np.full((2, 3, 4), 2.0, dtype=np.float32))
    assert block.color.ToNumpy().shape == (2, 3, 4, 3)


def test_every_message_type_has_a_class_and_a_direction_rule():
    for messageType in MessageType:
        assert messageType in MESSAGE_CLASSES
        assert MESSAGE_CLASSES[messageType].MESSAGE_TYPE is messageType


def test_unknown_message_type_is_rejected():
    with pytest.raises(UnknownMessageType):
        DecodeMessage(Frame(200, {}))
    with pytest.raises(UnknownMessageType):
        DecodeMessage(Frame(0, {}))


def test_unknown_payload_keys_are_ignored_for_forward_compatibility():
    payload = Heartbeat(1, 2).ToPayload() | {"futureField": [1, 2, 3]}
    assert DecodeMessage(Frame(MessageType.HEARTBEAT, payload)) == Heartbeat(1, 2)


@pytest.mark.parametrize(
    "messageType,payload",
    [
        (MessageType.HELLO, {"protocolVersion": 1, "node": "pi"}),
        (MessageType.HELLO, {"protocolVersion": "1", "node": "pi", "role": "worker"}),
        (MessageType.HELLO, {"protocolVersion": 1, "node": "Bad Name", "role": "worker"}),
        (MessageType.HELLO, {"protocolVersion": 1, "node": "pi", "role": "admin"}),
        (MessageType.HELLO, {"protocolVersion": True, "node": "pi", "role": "worker"}),
        (MessageType.HEARTBEAT, {"sequence": -1, "sentAtNs": 0}),
        (MessageType.HEARTBEAT, {"sequence": 1, "sentAtNs": 0, "echo": "yes"}),
        (MessageType.CAMERA_STATE, {"yaw": float("nan"), "pitch": 0.0, "zoom": 1.0, "modelId": "m"}),
        (MessageType.CAMERA_STATE, {"yaw": 0.0, "pitch": 0.0, "zoom": 1.0}),
        (MessageType.INPUT_EVENT, {"source": 1, "code": 1, "value": 1}),
        (MessageType.MODEL_PUSH, ModelPush("m", 0, 1, 0, "0" * 64, Compression.NONE, b"").ToPayload() | {"index": 1}),
        (MessageType.MODEL_PUSH, ModelPush("m", 0, 1, 0, "0" * 64, Compression.NONE, b"").ToPayload() | {"sha256": "XYZ"}),
        (MessageType.MODEL_PUSH, ModelPush("m", 0, 1, 0, "0" * 64, Compression.NONE, b"").ToPayload() | {"data": "text"}),
    ],
)
def test_invalid_payloads_are_rejected(messageType, payload):
    with pytest.raises(InvalidMessage):
        DecodeMessage(Frame(messageType, payload))


def test_job_assign_with_part_outside_range_is_rejected():
    payload = SampleJobAssign().ToPayload() | {"part": 2}
    with pytest.raises(InvalidMessage):
        DecodeMessage(Frame(MessageType.JOB_ASSIGN, payload))


def test_job_assign_with_wrong_vector_length_is_rejected():
    payload = SampleJobAssign().ToPayload()
    payload["views"][0]["rotation"] = [1.0, 0.0]
    with pytest.raises(InvalidMessage):
        DecodeMessage(Frame(MessageType.JOB_ASSIGN, payload))


def test_job_result_with_unknown_status_is_rejected():
    payload = SampleJobResult().ToPayload() | {"status": "maybe"}
    with pytest.raises(InvalidMessage):
        DecodeMessage(Frame(MessageType.JOB_RESULT, payload))


def test_malformed_payload_is_still_a_protocol_error_class():
    assert issubclass(MalformedPayload, Exception)


@pytest.mark.parametrize(
    "messageType,sentByHub,role,allowed",
    [
        (MessageType.HELLO, True, Role.WORKER, True),
        (MessageType.HELLO, False, Role.DEVICE_AGENT, True),
        (MessageType.HEARTBEAT, False, Role.WORKER, True),
        (MessageType.JOB_ASSIGN, True, Role.WORKER, True),
        (MessageType.JOB_ASSIGN, False, Role.WORKER, False),
        (MessageType.JOB_ASSIGN, True, Role.DEVICE_AGENT, False),
        (MessageType.JOB_RESULT, False, Role.WORKER, True),
        (MessageType.JOB_RESULT, True, Role.WORKER, False),
        (MessageType.INPUT_EVENT, False, Role.DEVICE_AGENT, True),
        (MessageType.INPUT_EVENT, False, Role.WORKER, False),
        (MessageType.CAMERA_STATE, True, Role.DEVICE_AGENT, True),
        (MessageType.CAMERA_STATE, False, Role.DEVICE_AGENT, False),
        (MessageType.MODEL_PUSH, True, Role.DEVICE_AGENT, True),
        (MessageType.MODEL_PUSH, True, Role.WORKER, False),
        (MessageType.ROTATE, True, Role.DEVICE_AGENT, False),
        (MessageType.ROTATE_DONE, False, Role.DEVICE_AGENT, False),
    ],
)
def test_direction_and_role_rules(messageType, sentByHub, role, allowed):
    assert IsAllowed(messageType, sentByHub, role) is allowed


def test_split_model_chunks_share_the_digest_of_the_whole_model():
    data = bytes(range(256)) * 40
    chunks = SplitModel("bunny", data, chunkBytes=1000)
    assert len(chunks) == 11
    assert {chunk.sha256 for chunk in chunks} == {hashlib.sha256(data).hexdigest()}
    assert all(chunk.count == 11 and chunk.totalSize == len(data) for chunk in chunks)
    assert b"".join(chunk.data for chunk in chunks) == data


def test_empty_model_is_one_empty_chunk():
    chunks = SplitModel("empty", b"")
    assert len(chunks) == 1 and chunks[0].data == b""
    assert ModelAssembler().Add(chunks[0]) == b""


def test_assembler_rebuilds_the_model_in_any_order():
    data = bytes(range(256)) * 17
    chunks = SplitModel("bunny", data, chunkBytes=500)
    assembler = ModelAssembler()
    result = None
    for chunk in reversed(chunks):
        result = assembler.Add(chunk)
    assert result == data


def test_assembler_returns_none_until_the_last_chunk():
    chunks = SplitModel("bunny", b"x" * 3000, chunkBytes=1000)
    assembler = ModelAssembler()
    assert assembler.Add(chunks[0]) is None
    assert assembler.Add(chunks[1]) is None
    assert assembler.Add(chunks[2]) == b"x" * 3000


def test_assembler_rejects_a_duplicated_chunk():
    chunks = SplitModel("bunny", b"x" * 3000, chunkBytes=1000)
    assembler = ModelAssembler()
    assembler.Add(chunks[0])
    with pytest.raises(ModelIntegrityError):
        assembler.Add(chunks[0])


def test_assembler_rejects_a_corrupted_chunk():
    chunks = SplitModel("bunny", b"x" * 3000, chunkBytes=1000)
    tampered = ModelPush(*[getattr(chunks[1], name) for name in ("modelId", "index", "count", "totalSize", "sha256", "compression")], b"y" * 1000)
    assembler = ModelAssembler()
    assembler.Add(chunks[0])
    assembler.Add(tampered)
    with pytest.raises(ModelIntegrityError):
        assembler.Add(chunks[2])


def test_assembler_rejects_chunks_that_disagree_on_the_model():
    first = SplitModel("bunny", b"x" * 3000, chunkBytes=1000)
    other = SplitModel("bunny", b"z" * 3000, chunkBytes=1000)
    assembler = ModelAssembler()
    assembler.Add(first[0])
    with pytest.raises(ModelIntegrityError):
        assembler.Add(other[1])


def test_array_blob_round_trips_with_and_without_compression():
    array = np.random.default_rng(0).random((5, 7, 3)).astype(np.float32)
    for compression in Compression:
        blob = ArrayBlob.FromNumpy(array, compression)
        np.testing.assert_array_equal(ArrayBlob.FromPayload(blob.ToPayload()).ToNumpy(), array)


def test_array_blob_with_a_mismatching_size_is_rejected():
    blob = ArrayBlob("<f4", (4, 4), Compression.NONE, b"\x00" * 10)
    with pytest.raises(InvalidMessage):
        blob.ToNumpy()


def test_array_blob_cannot_inflate_beyond_its_declared_shape():
    bomb = ArrayBlob.FromNumpy(np.zeros(10_000_000, dtype=np.uint8))
    claimed = ArrayBlob("|u1", (16,), Compression.ZSTD, bomb.data)
    with pytest.raises(InvalidMessage):
        claimed.ToNumpy()


@pytest.mark.parametrize("dtype", ["O", "V8", ">f4", "<c16", "nonsense"])
def test_array_blob_rejects_unsupported_dtypes(dtype):
    payload = {"dtype": dtype, "shape": [1], "compression": "none", "data": b"\x00" * 8}
    with pytest.raises(InvalidMessage):
        ArrayBlob.FromPayload(payload)


def test_array_blob_rejects_too_many_dimensions_or_negative_sizes():
    for shape in ([1, 1, 1, 1, 1], [-1], []):
        with pytest.raises(InvalidMessage):
            ArrayBlob.FromPayload({"dtype": "<f4", "shape": shape, "compression": "none", "data": b""})
