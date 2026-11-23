"""Protocol messages carried by the frames of `astrea_common.framing`, with validation of every field.

Topology: the host is the hub and every other node holds one connection to it. Each message type has a
fixed sender side (hub or node) and, when it only makes sense for one kind of node, a node role; a frame
that breaks those rules closes the link (`IsAllowed`). Decoders ignore unknown payload keys, so new
optional fields can be added without breaking `PROTOCOL_VERSION`; anything else needs a version bump.

Link tags of the block diagram: N1 `JOB_ASSIGN`/`JOB_RESULT`, N2 `MODEL_PUSH`, N3 `INPUT_EVENT`/
`CAMERA_STATE`, N4 `ROTATE`/`ROTATE_DONE` (defined, disabled until phase 8). `HELLO` and `HEARTBEAT` run on
every link.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from typing import Any, ClassVar, Protocol

import numpy as np

from astrea_common.framing import Frame, ProtocolError, UnknownMessageType, EncodeFrame

PROTOCOL_VERSION = 1
DEFAULT_MODEL_CHUNK_BYTES = 1024 * 1024
SHA256_HEX_LENGTH = 64
NODE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
ALLOWED_DTYPE_KINDS = "uif"
MAX_DTYPE_BYTES = 8
MAX_ARRAY_DIMENSIONS = 4


class InvalidMessage(ProtocolError):
    pass


class ModelIntegrityError(ProtocolError):
    pass


class MessageType(IntEnum):
    HELLO = 1
    JOB_ASSIGN = 2
    JOB_RESULT = 3
    HEARTBEAT = 4
    INPUT_EVENT = 5
    CAMERA_STATE = 6
    MODEL_PUSH = 7
    ROTATE = 8
    ROTATE_DONE = 9


class Role(StrEnum):
    COORDINATOR = "coordinator"
    WORKER = "worker"
    DEVICE_AGENT = "device-agent"


class Compression(StrEnum):
    NONE = "none"
    ZSTD = "zstd"


class JobStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


class InputSource(IntEnum):
    JOYSTICK = 1
    ZX_GESTURE = 2


class Sender(StrEnum):
    HUB = "hub"
    NODE = "node"
    EITHER = "either"


@dataclass(frozen=True)
class MessageRule:
    sender: Sender
    nodeRole: Role | None
    enabled: bool = True


MESSAGE_RULES: dict[MessageType, MessageRule] = {
    MessageType.HELLO: MessageRule(Sender.EITHER, None),
    MessageType.HEARTBEAT: MessageRule(Sender.EITHER, None),
    MessageType.JOB_ASSIGN: MessageRule(Sender.HUB, Role.WORKER),
    MessageType.JOB_RESULT: MessageRule(Sender.NODE, Role.WORKER),
    MessageType.INPUT_EVENT: MessageRule(Sender.NODE, Role.DEVICE_AGENT),
    MessageType.CAMERA_STATE: MessageRule(Sender.HUB, Role.DEVICE_AGENT),
    MessageType.MODEL_PUSH: MessageRule(Sender.HUB, Role.DEVICE_AGENT),
    MessageType.ROTATE: MessageRule(Sender.HUB, Role.DEVICE_AGENT, enabled=False),
    MessageType.ROTATE_DONE: MessageRule(Sender.NODE, Role.DEVICE_AGENT, enabled=False),
}


def IsAllowed(messageType: MessageType, sentByHub: bool, nodeRole: Role) -> bool:
    """`nodeRole` is the role of the node at the other end of the link from the hub's point of view."""
    rule = MESSAGE_RULES[messageType]
    if not rule.enabled:
        return False
    if rule.sender is Sender.HUB and not sentByHub:
        return False
    if rule.sender is Sender.NODE and sentByHub:
        return False
    return rule.nodeRole is None or rule.nodeRole == nodeRole


def _RequireKey(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise InvalidMessage(f"missing field '{key}'")
    return payload[key]


def _RequireStr(payload: dict[str, Any], key: str) -> str:
    value = _RequireKey(payload, key)
    if not isinstance(value, str):
        raise InvalidMessage(f"field '{key}' must be a string")
    return value


def _RequireInt(payload: dict[str, Any], key: str, minimum: int | None = None) -> int:
    value = _RequireKey(payload, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidMessage(f"field '{key}' must be an integer")
    if minimum is not None and value < minimum:
        raise InvalidMessage(f"field '{key}' must be at least {minimum}")
    return value


def _RequireNumber(payload: dict[str, Any], key: str) -> float:
    value = _RequireKey(payload, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidMessage(f"field '{key}' must be a finite number")
    return float(value)


def _RequireBytes(payload: dict[str, Any], key: str) -> bytes:
    value = _RequireKey(payload, key)
    if not isinstance(value, bytes):
        raise InvalidMessage(f"field '{key}' must be binary")
    return value


def _RequireMap(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = _RequireKey(payload, key)
    if not isinstance(value, dict):
        raise InvalidMessage(f"field '{key}' must be a map")
    return value


def _RequireList(payload: dict[str, Any], key: str) -> list[Any]:
    value = _RequireKey(payload, key)
    if not isinstance(value, list):
        raise InvalidMessage(f"field '{key}' must be a list")
    return value


def _RequireEnum(payload: dict[str, Any], key: str, enumType: type[StrEnum]) -> Any:
    text = _RequireStr(payload, key)
    try:
        return enumType(text)
    except ValueError as error:
        raise InvalidMessage(f"field '{key}' has unsupported value '{text}'") from error


def _RequireNumbers(payload: dict[str, Any], key: str, length: int) -> tuple[float, ...]:
    values = _RequireList(payload, key)
    if len(values) != length:
        raise InvalidMessage(f"field '{key}' must hold {length} numbers")
    return tuple(_RequireNumber({"value": item}, "value") for item in values)


def _RequireInts(payload: dict[str, Any], key: str, length: int, minimum: int | None = None) -> tuple[int, ...]:
    values = _RequireList(payload, key)
    if len(values) != length:
        raise InvalidMessage(f"field '{key}' must hold {length} integers")
    return tuple(_RequireInt({"value": item}, "value", minimum) for item in values)


def _OptionalNumbers(payload: dict[str, Any], key: str) -> tuple[float, ...]:
    if key not in payload:
        return ()
    return _RequireNumbers(payload, key, len(_RequireList(payload, key)))


def _RequireNodeName(payload: dict[str, Any], key: str) -> str:
    name = _RequireStr(payload, key)
    if not NODE_NAME_PATTERN.match(name):
        raise InvalidMessage(f"field '{key}' is not a valid node name")
    return name


def _RequireSha256(payload: dict[str, Any], key: str) -> str:
    digest = _RequireStr(payload, key)
    if len(digest) != SHA256_HEX_LENGTH or any(char not in "0123456789abcdef" for char in digest):
        raise InvalidMessage(f"field '{key}' must be a lowercase hex SHA-256")
    return digest


class Message(Protocol):
    MESSAGE_TYPE: ClassVar[MessageType]

    def ToPayload(self) -> dict[str, Any]: ...

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "Message": ...


@dataclass(frozen=True)
class Hello:
    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.HELLO
    protocolVersion: int
    node: str
    role: Role

    def ToPayload(self) -> dict[str, Any]:
        return {"protocolVersion": self.protocolVersion, "node": self.node, "role": self.role.value}

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "Hello":
        return cls(_RequireInt(payload, "protocolVersion", 1), _RequireNodeName(payload, "node"), _RequireEnum(payload, "role", Role))


@dataclass(frozen=True)
class Heartbeat:
    """`echo` marks the reply to a heartbeat; the sender of the original uses `sentAtNs` to compute the RTT."""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.HEARTBEAT
    sequence: int
    sentAtNs: int
    echo: bool = False

    def ToPayload(self) -> dict[str, Any]:
        return {"sequence": self.sequence, "sentAtNs": self.sentAtNs, "echo": self.echo}

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "Heartbeat":
        echo = payload.get("echo", False)
        if not isinstance(echo, bool):
            raise InvalidMessage("field 'echo' must be a boolean")
        return cls(_RequireInt(payload, "sequence", 0), _RequireInt(payload, "sentAtNs", 0), echo)


@dataclass(frozen=True)
class InputEvent:
    """Mirrors `struct HoloEvent{source, code, value, timestampNs}` of the `holo-io` driver."""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.INPUT_EVENT
    source: int
    code: int
    value: int
    timestampNs: int

    def ToPayload(self) -> dict[str, Any]:
        return {"source": self.source, "code": self.code, "value": self.value, "timestampNs": self.timestampNs}

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "InputEvent":
        return cls(_RequireInt(payload, "source", 0), _RequireInt(payload, "code", 0), _RequireInt(payload, "value"), _RequireInt(payload, "timestampNs", 0))


@dataclass(frozen=True)
class CameraState:
    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.CAMERA_STATE
    yaw: float
    pitch: float
    zoom: float
    modelId: str

    def ToPayload(self) -> dict[str, Any]:
        return {"yaw": self.yaw, "pitch": self.pitch, "zoom": self.zoom, "modelId": self.modelId}

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "CameraState":
        return cls(_RequireNumber(payload, "yaw"), _RequireNumber(payload, "pitch"), _RequireNumber(payload, "zoom"), _RequireStr(payload, "modelId"))


@dataclass(frozen=True)
class Rotate:
    """Motorized turntable request (phase 8); the hub rejects it while `MESSAGE_RULES` keeps it disabled."""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.ROTATE
    requestId: int
    steps: int

    def ToPayload(self) -> dict[str, Any]:
        return {"requestId": self.requestId, "steps": self.steps}

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "Rotate":
        return cls(_RequireInt(payload, "requestId", 0), _RequireInt(payload, "steps"))


@dataclass(frozen=True)
class RotateDone:
    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.ROTATE_DONE
    requestId: int
    positionSteps: int

    def ToPayload(self) -> dict[str, Any]:
        return {"requestId": self.requestId, "positionSteps": self.positionSteps}

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "RotateDone":
        return cls(_RequireInt(payload, "requestId", 0), _RequireInt(payload, "positionSteps"))


@dataclass(frozen=True)
class ModelPush:
    """One chunk of a model. `sha256` is the digest of the whole transmitted byte string (after compression)."""

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.MODEL_PUSH
    modelId: str
    index: int
    count: int
    totalSize: int
    sha256: str
    compression: Compression
    data: bytes

    def ToPayload(self) -> dict[str, Any]:
        return {
            "modelId": self.modelId,
            "index": self.index,
            "count": self.count,
            "totalSize": self.totalSize,
            "sha256": self.sha256,
            "compression": self.compression.value,
            "data": self.data,
        }

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "ModelPush":
        chunk = cls(
            _RequireStr(payload, "modelId"),
            _RequireInt(payload, "index", 0),
            _RequireInt(payload, "count", 1),
            _RequireInt(payload, "totalSize", 0),
            _RequireSha256(payload, "sha256"),
            _RequireEnum(payload, "compression", Compression),
            _RequireBytes(payload, "data"),
        )
        if chunk.index >= chunk.count:
            raise InvalidMessage(f"chunk index {chunk.index} is outside 0..{chunk.count - 1}")
        return chunk


def SplitModel(modelId: str, data: bytes, compression: Compression = Compression.NONE, chunkBytes: int = DEFAULT_MODEL_CHUNK_BYTES) -> list[ModelPush]:
    if chunkBytes < 1:
        raise ValueError("chunkBytes must be positive")
    digest = hashlib.sha256(data).hexdigest()
    pieces = [data[start : start + chunkBytes] for start in range(0, len(data), chunkBytes)] or [b""]
    return [ModelPush(modelId, index, len(pieces), len(data), digest, compression, piece) for index, piece in enumerate(pieces)]


class ModelAssembler:
    """Collects the chunks of one model, in any order, and returns the bytes once the SHA-256 matches."""

    def __init__(self) -> None:
        self._first: ModelPush | None = None
        self._pieces: dict[int, bytes] = {}

    def Add(self, chunk: ModelPush) -> bytes | None:
        if self._first is None:
            self._first = chunk
        self._CheckConsistent(chunk)
        if chunk.index in self._pieces:
            raise ModelIntegrityError(f"chunk {chunk.index} of '{chunk.modelId}' arrived twice")
        self._pieces[chunk.index] = chunk.data
        if len(self._pieces) < chunk.count:
            return None
        return self._Finish(chunk)

    def _CheckConsistent(self, chunk: ModelPush) -> None:
        first = self._first
        assert first is not None
        same = (chunk.modelId, chunk.count, chunk.totalSize, chunk.sha256, chunk.compression) == (first.modelId, first.count, first.totalSize, first.sha256, first.compression)
        if not same:
            raise ModelIntegrityError(f"chunk {chunk.index} of '{chunk.modelId}' disagrees with the first chunk")

    def _Finish(self, chunk: ModelPush) -> bytes:
        data = b"".join(self._pieces[index] for index in range(chunk.count))
        if len(data) != chunk.totalSize:
            raise ModelIntegrityError(f"'{chunk.modelId}' has {len(data)} bytes, expected {chunk.totalSize}")
        if hashlib.sha256(data).hexdigest() != chunk.sha256:
            raise ModelIntegrityError(f"'{chunk.modelId}' failed the SHA-256 check")
        return data


@dataclass(frozen=True)
class ArrayBlob:
    """A NumPy array as bytes with its dtype and shape, optionally zstd-compressed (C order, little endian)."""

    dtype: str
    shape: tuple[int, ...]
    compression: Compression
    data: bytes

    @staticmethod
    def FromNumpy(array: np.ndarray, compression: Compression = Compression.ZSTD, level: int | None = None) -> "ArrayBlob":
        contiguous = np.ascontiguousarray(array)
        littleEndian = contiguous.astype(contiguous.dtype.newbyteorder("<"), copy=False)
        raw = littleEndian.tobytes()
        if compression is Compression.ZSTD:
            from astrea_common.compression import DEFAULT_LEVEL, ZstdCompress

            raw = ZstdCompress(raw, DEFAULT_LEVEL if level is None else level)
        return ArrayBlob(littleEndian.dtype.str, tuple(contiguous.shape), compression, raw)

    def ToNumpy(self) -> np.ndarray:
        dtype = np.dtype(self.dtype)
        expectedBytes = int(np.prod(self.shape, dtype=np.int64)) * dtype.itemsize
        raw = self.data
        if self.compression is Compression.ZSTD:
            from astrea_common.compression import ZstdDecompress

            try:
                raw = ZstdDecompress(raw, maxOutputBytes=expectedBytes)
            except Exception as error:
                raise InvalidMessage(f"array blob does not decompress to {expectedBytes} bytes: {error}") from error
        if len(raw) != expectedBytes:
            raise InvalidMessage(f"array blob holds {len(raw)} bytes, shape {self.shape} needs {expectedBytes}")
        return np.frombuffer(raw, dtype=dtype).reshape(self.shape)

    def ToPayload(self) -> dict[str, Any]:
        return {"dtype": self.dtype, "shape": list(self.shape), "compression": self.compression.value, "data": self.data}

    @staticmethod
    def FromPayload(payload: dict[str, Any]) -> "ArrayBlob":
        dtypeText = _RequireStr(payload, "dtype")
        try:
            dtype = np.dtype(dtypeText)
        except TypeError as error:
            raise InvalidMessage(f"unknown dtype '{dtypeText}'") from error
        if dtype.kind not in ALLOWED_DTYPE_KINDS or dtype.itemsize > MAX_DTYPE_BYTES or dtype.byteorder == ">":
            raise InvalidMessage(f"dtype '{dtypeText}' is not allowed")
        shapeList = _RequireList(payload, "shape")
        if not 1 <= len(shapeList) <= MAX_ARRAY_DIMENSIONS:
            raise InvalidMessage(f"array shape must have 1 to {MAX_ARRAY_DIMENSIONS} dimensions")
        shape = _RequireInts(payload, "shape", len(shapeList), minimum=0)
        return ArrayBlob(dtypeText, shape, _RequireEnum(payload, "compression", Compression), _RequireBytes(payload, "data"))


def _BlobField(payload: dict[str, Any], key: str) -> ArrayBlob:
    return ArrayBlob.FromPayload(_RequireMap(payload, key))


@dataclass(frozen=True)
class GridPayload:
    """The common voxel grid every worker integrates into (`astrea_common.tsdf.GridSpec`)."""

    originMm: tuple[float, float, float]
    voxelMm: float
    shape: tuple[int, int, int]

    def ToPayload(self) -> dict[str, Any]:
        return {"originMm": list(self.originMm), "voxelMm": self.voxelMm, "shape": list(self.shape)}

    @staticmethod
    def FromPayload(payload: dict[str, Any]) -> "GridPayload":
        voxelMm = _RequireNumber(payload, "voxelMm")
        if voxelMm <= 0:
            raise InvalidMessage("field 'voxelMm' must be positive")
        return GridPayload(_RequireNumbers(payload, "originMm", 3), voxelMm, _RequireInts(payload, "shape", 3, minimum=1))  # type: ignore[arg-type]


@dataclass(frozen=True)
class IntrinsicsPayload:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    def ToPayload(self) -> dict[str, Any]:
        return {"fx": self.fx, "fy": self.fy, "cx": self.cx, "cy": self.cy, "width": self.width, "height": self.height}

    @staticmethod
    def FromPayload(payload: dict[str, Any]) -> "IntrinsicsPayload":
        return IntrinsicsPayload(
            _RequireNumber(payload, "fx"),
            _RequireNumber(payload, "fy"),
            _RequireNumber(payload, "cx"),
            _RequireNumber(payload, "cy"),
            _RequireInt(payload, "width", 1),
            _RequireInt(payload, "height", 1),
        )


@dataclass(frozen=True)
class ViewPayload:
    """One keyframe: `xCamera = rotation @ xPlate + translationMm`, rotation row-major."""

    index: int
    intrinsics: IntrinsicsPayload
    rotation: tuple[float, ...]
    translationMm: tuple[float, ...]
    rgb: ArrayBlob
    depthM: ArrayBlob

    def ToPayload(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "intrinsics": self.intrinsics.ToPayload(),
            "rotation": list(self.rotation),
            "translationMm": list(self.translationMm),
            "rgb": self.rgb.ToPayload(),
            "depthM": self.depthM.ToPayload(),
        }

    @staticmethod
    def FromPayload(payload: dict[str, Any]) -> "ViewPayload":
        return ViewPayload(
            _RequireInt(payload, "index", 0),
            IntrinsicsPayload.FromPayload(_RequireMap(payload, "intrinsics")),
            _RequireNumbers(payload, "rotation", 9),
            _RequireNumbers(payload, "translationMm", 3),
            _BlobField(payload, "rgb"),
            _BlobField(payload, "depthM"),
        )


@dataclass(frozen=True)
class JobAssign:
    """Views a worker must preprocess and integrate into `grid`.

    A job larger than one frame is sent as `parts` messages with the same `jobId`; the worker starts when
    all of them arrived. `params` carries the `PreprocessParams` overrides and the TSDF options by name.
    """

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.JOB_ASSIGN
    jobId: str
    part: int
    parts: int
    grid: GridPayload
    truncationMm: float
    params: dict[str, Any]
    views: tuple[ViewPayload, ...]

    def ToPayload(self) -> dict[str, Any]:
        return {
            "jobId": self.jobId,
            "part": self.part,
            "parts": self.parts,
            "grid": self.grid.ToPayload(),
            "truncationMm": self.truncationMm,
            "params": self.params,
            "views": [view.ToPayload() for view in self.views],
        }

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "JobAssign":
        part, parts = _RequireInt(payload, "part", 0), _RequireInt(payload, "parts", 1)
        if part >= parts:
            raise InvalidMessage(f"part {part} is outside 0..{parts - 1}")
        views = tuple(ViewPayload.FromPayload(item) for item in _RequireViewMaps(payload))
        return cls(
            _RequireStr(payload, "jobId"),
            part,
            parts,
            GridPayload.FromPayload(_RequireMap(payload, "grid")),
            _RequireNumber(payload, "truncationMm"),
            _RequireMap(payload, "params"),
            views,
        )


def _RequireViewMaps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = _RequireList(payload, "views")
    if not all(isinstance(item, dict) for item in items):
        raise InvalidMessage("every entry of 'views' must be a map")
    return items


@dataclass(frozen=True)
class BlockPayload:
    """A box of voxels with weight > 0: `startIndex` is its corner in the grid and the arrays share `shape`."""

    startIndex: tuple[int, int, int]
    shape: tuple[int, int, int]
    tsdf: ArrayBlob
    weight: ArrayBlob
    color: ArrayBlob

    def ToPayload(self) -> dict[str, Any]:
        return {
            "startIndex": list(self.startIndex),
            "shape": list(self.shape),
            "tsdf": self.tsdf.ToPayload(),
            "weight": self.weight.ToPayload(),
            "color": self.color.ToPayload(),
        }

    @staticmethod
    def FromPayload(payload: dict[str, Any]) -> "BlockPayload":
        return BlockPayload(
            _RequireInts(payload, "startIndex", 3, minimum=0),  # type: ignore[arg-type]
            _RequireInts(payload, "shape", 3, minimum=1),  # type: ignore[arg-type]
            _BlobField(payload, "tsdf"),
            _BlobField(payload, "weight"),
            _BlobField(payload, "color"),
        )


@dataclass(frozen=True)
class JobResult:
    """Partial volume of one job as non-empty blocks; multi-part like `JobAssign`.

    `viewIndices` and `keptPercent` (percentage of depth kept by preprocessing, one per view, optional)
    travel in part 0.
    """

    MESSAGE_TYPE: ClassVar[MessageType] = MessageType.JOB_RESULT
    jobId: str
    part: int
    parts: int
    status: JobStatus
    error: str
    elapsedS: float
    viewIndices: tuple[int, ...]
    blocks: tuple[BlockPayload, ...]
    keptPercent: tuple[float, ...] = ()

    def ToPayload(self) -> dict[str, Any]:
        return {
            "jobId": self.jobId,
            "part": self.part,
            "parts": self.parts,
            "status": self.status.value,
            "error": self.error,
            "elapsedS": self.elapsedS,
            "viewIndices": list(self.viewIndices),
            "blocks": [block.ToPayload() for block in self.blocks],
            "keptPercent": list(self.keptPercent),
        }

    @classmethod
    def FromPayload(cls, payload: dict[str, Any]) -> "JobResult":
        part, parts = _RequireInt(payload, "part", 0), _RequireInt(payload, "parts", 1)
        if part >= parts:
            raise InvalidMessage(f"part {part} is outside 0..{parts - 1}")
        blockMaps = _RequireList(payload, "blocks")
        if not all(isinstance(item, dict) for item in blockMaps):
            raise InvalidMessage("every entry of 'blocks' must be a map")
        viewIndices = _RequireInts(payload, "viewIndices", len(_RequireList(payload, "viewIndices")), minimum=0)
        return cls(
            _RequireStr(payload, "jobId"),
            part,
            parts,
            _RequireEnum(payload, "status", JobStatus),
            _RequireStr(payload, "error"),
            _RequireNumber(payload, "elapsedS"),
            viewIndices,
            tuple(BlockPayload.FromPayload(item) for item in blockMaps),
            _OptionalNumbers(payload, "keptPercent"),
        )


MESSAGE_CLASSES: dict[MessageType, type] = {
    MessageType.HELLO: Hello,
    MessageType.JOB_ASSIGN: JobAssign,
    MessageType.JOB_RESULT: JobResult,
    MessageType.HEARTBEAT: Heartbeat,
    MessageType.INPUT_EVENT: InputEvent,
    MessageType.CAMERA_STATE: CameraState,
    MessageType.MODEL_PUSH: ModelPush,
    MessageType.ROTATE: Rotate,
    MessageType.ROTATE_DONE: RotateDone,
}


def EncodeMessage(message: Message) -> bytes:
    return EncodeFrame(int(message.MESSAGE_TYPE), message.ToPayload())


def DecodeMessage(frame: Frame) -> Message:
    try:
        messageType = MessageType(frame.messageType)
    except ValueError as error:
        raise UnknownMessageType(f"unknown message type {frame.messageType}") from error
    return MESSAGE_CLASSES[messageType].FromPayload(frame.payload)
