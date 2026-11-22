"""Wire framing shared by every network link: `[u32 length][u8 type][msgpack payload]`.

`length` is big-endian and counts the type byte plus the payload, so a frame is never shorter than 1.
The payload is a msgpack map. Frames above `MAX_FRAME_BYTES` are rejected before they are buffered, so a
peer cannot make a node allocate memory by announcing a huge length.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Any

import msgpack

MAX_FRAME_BYTES = 16 * 1024 * 1024
LENGTH_BYTES = 4
TYPE_BYTES = 1
HEADER_BYTES = LENGTH_BYTES + TYPE_BYTES
MIN_TYPE = 1
MAX_TYPE = 255

_LENGTH = struct.Struct(">I")


class ProtocolError(Exception):
    """A peer sent something the protocol does not allow; the link must be closed."""


class FrameTooLarge(ProtocolError):
    pass


class EmptyFrame(ProtocolError):
    pass


class TruncatedFrame(ProtocolError):
    pass


class UnknownMessageType(ProtocolError):
    pass


class MalformedPayload(ProtocolError):
    pass


@dataclass(frozen=True)
class Frame:
    messageType: int
    payload: dict[str, Any]


def EncodeFrame(messageType: int, payload: dict[str, Any]) -> bytes:
    if not MIN_TYPE <= messageType <= MAX_TYPE:
        raise UnknownMessageType(f"message type {messageType} does not fit in one byte")
    body = msgpack.packb(payload, use_bin_type=True)
    length = TYPE_BYTES + len(body)
    if length > MAX_FRAME_BYTES:
        raise FrameTooLarge(f"frame of {length} bytes exceeds {MAX_FRAME_BYTES}")
    return _LENGTH.pack(length) + bytes([messageType]) + body


def DecodePayload(body: bytes) -> dict[str, Any]:
    try:
        payload = msgpack.unpackb(body, raw=False)
    except (msgpack.ExtraData, msgpack.UnpackException, ValueError) as error:
        raise MalformedPayload(f"payload is not valid msgpack: {error}") from error
    if not isinstance(payload, dict):
        raise MalformedPayload(f"payload must be a map, got {type(payload).__name__}")
    return payload


def ParseLength(headerLength: bytes) -> int:
    (length,) = _LENGTH.unpack(headerLength)
    if length < TYPE_BYTES:
        raise EmptyFrame("frame length 0 leaves no room for the type byte")
    if length > MAX_FRAME_BYTES:
        raise FrameTooLarge(f"frame announces {length} bytes, above {MAX_FRAME_BYTES}")
    return length


class FrameDecoder:
    """Incremental decoder: `Feed` accepts bytes cut at any offset and returns the frames completed so far.

    After a protocol error the decoder refuses further input, because the stream is no longer in sync.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._failed: ProtocolError | None = None

    def Feed(self, data: bytes) -> list[Frame]:
        if self._failed is not None:
            raise self._failed
        self._buffer.extend(data)
        try:
            return self._ExtractFrames()
        except ProtocolError as error:
            self._failed = error
            raise

    def Finish(self) -> None:
        """Call at end of stream: leftover bytes mean the peer cut a frame in half."""
        if self._buffer:
            raise TruncatedFrame(f"stream ended with {len(self._buffer)} bytes of an incomplete frame")

    def _ExtractFrames(self) -> list[Frame]:
        frames: list[Frame] = []
        offset = 0
        while len(self._buffer) - offset >= LENGTH_BYTES:
            length = ParseLength(bytes(self._buffer[offset : offset + LENGTH_BYTES]))
            end = offset + LENGTH_BYTES + length
            if len(self._buffer) < end:
                break
            messageType = self._buffer[offset + LENGTH_BYTES]
            body = bytes(self._buffer[offset + HEADER_BYTES : end])
            frames.append(Frame(messageType, DecodePayload(body)))
            offset = end
        del self._buffer[:offset]
        return frames


async def ReadFrame(reader: asyncio.StreamReader) -> Frame | None:
    """Reads one frame; returns None when the peer closed the stream cleanly between frames."""
    try:
        headerLength = await reader.readexactly(LENGTH_BYTES)
    except asyncio.IncompleteReadError as error:
        if not error.partial:
            return None
        raise TruncatedFrame("stream ended inside a frame header") from error
    length = ParseLength(headerLength)
    try:
        rest = await reader.readexactly(length)
    except asyncio.IncompleteReadError as error:
        raise TruncatedFrame(f"stream ended after {len(error.partial)} of {length} frame bytes") from error
    return Frame(rest[0], DecodePayload(rest[TYPE_BYTES:]))


async def WriteFrame(writer: asyncio.StreamWriter, messageType: int, payload: dict[str, Any]) -> None:
    writer.write(EncodeFrame(messageType, payload))
    await writer.drain()
