"""Framing: round trips, frames cut at any offset, and every malformed stream the decoder must refuse."""

import asyncio
import struct

import msgpack
import pytest

from astrea_common import framing
from astrea_common.framing import (
    EmptyFrame,
    EncodeFrame,
    Frame,
    FrameDecoder,
    FrameTooLarge,
    MalformedPayload,
    ReadFrame,
    TruncatedFrame,
    UnknownMessageType,
    WriteFrame,
)

SAMPLE = {"seq": 7, "name": "pi", "blob": b"\x00\x01\xff"}


def RawFrame(messageType: int, body: bytes) -> bytes:
    return struct.pack(">IB", len(body) + 1, messageType) + body


def test_encoded_frame_has_length_type_and_msgpack_payload():
    data = EncodeFrame(4, SAMPLE)
    length, messageType = struct.unpack(">IB", data[:5])
    assert length == len(data) - 4
    assert messageType == 4
    assert msgpack.unpackb(data[5:], raw=False) == SAMPLE


def test_decoder_round_trips_one_frame():
    assert FrameDecoder().Feed(EncodeFrame(4, SAMPLE)) == [Frame(4, SAMPLE)]


def test_decoder_returns_several_frames_from_one_chunk():
    chunk = EncodeFrame(1, {"a": 1}) + EncodeFrame(2, {"b": 2}) + EncodeFrame(3, {"c": 3})
    assert [frame.messageType for frame in FrameDecoder().Feed(chunk)] == [1, 2, 3]


def test_decoder_handles_byte_by_byte_input():
    data = EncodeFrame(4, SAMPLE) + EncodeFrame(5, {"x": 1})
    decoder = FrameDecoder()
    frames = []
    for byte in data:
        frames += decoder.Feed(bytes([byte]))
    assert frames == [Frame(4, SAMPLE), Frame(5, {"x": 1})]
    decoder.Finish()


@pytest.mark.parametrize("cut", range(1, 40))
def test_decoder_handles_a_split_at_every_offset(cut: int):
    data = EncodeFrame(4, SAMPLE) + EncodeFrame(5, {"x": 1})
    cut = min(cut, len(data) - 1)
    decoder = FrameDecoder()
    frames = decoder.Feed(data[:cut]) + decoder.Feed(data[cut:])
    assert frames == [Frame(4, SAMPLE), Frame(5, {"x": 1})]


def test_decoder_waits_for_the_rest_of_a_partial_frame():
    data = EncodeFrame(4, SAMPLE)
    decoder = FrameDecoder()
    assert decoder.Feed(data[:-1]) == []
    assert decoder.Feed(data[-1:]) == [Frame(4, SAMPLE)]


def test_zero_length_frame_is_rejected():
    with pytest.raises(EmptyFrame):
        FrameDecoder().Feed(struct.pack(">I", 0))


def test_oversized_length_is_rejected_before_the_body_arrives():
    with pytest.raises(FrameTooLarge):
        FrameDecoder().Feed(struct.pack(">I", framing.MAX_FRAME_BYTES + 1))


def test_encoding_an_oversized_payload_is_rejected():
    with pytest.raises(FrameTooLarge):
        EncodeFrame(4, {"blob": b"\x00" * framing.MAX_FRAME_BYTES})


def test_corrupt_msgpack_is_rejected():
    with pytest.raises(MalformedPayload):
        FrameDecoder().Feed(RawFrame(4, b"\xc1\xc1\xc1"))


def test_empty_payload_is_rejected():
    with pytest.raises(MalformedPayload):
        FrameDecoder().Feed(RawFrame(4, b""))


def test_payload_with_trailing_bytes_is_rejected():
    with pytest.raises(MalformedPayload):
        FrameDecoder().Feed(RawFrame(4, msgpack.packb({"a": 1}) + b"\x00"))


def test_payload_that_is_not_a_map_is_rejected():
    with pytest.raises(MalformedPayload):
        FrameDecoder().Feed(RawFrame(4, msgpack.packb([1, 2, 3])))


def test_message_type_outside_one_byte_cannot_be_encoded():
    for invalid in (0, 256):
        with pytest.raises(UnknownMessageType):
            EncodeFrame(invalid, {})


def test_decoder_refuses_input_after_a_protocol_error():
    decoder = FrameDecoder()
    with pytest.raises(EmptyFrame):
        decoder.Feed(struct.pack(">I", 0))
    with pytest.raises(EmptyFrame):
        decoder.Feed(EncodeFrame(4, SAMPLE))


def test_finish_reports_a_frame_cut_in_half():
    decoder = FrameDecoder()
    decoder.Feed(EncodeFrame(4, SAMPLE)[:-2])
    with pytest.raises(TruncatedFrame):
        decoder.Finish()


def test_finish_accepts_a_stream_that_ends_between_frames():
    decoder = FrameDecoder()
    decoder.Feed(EncodeFrame(4, SAMPLE))
    decoder.Finish()


def ReaderOver(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def test_read_frame_returns_frames_then_none_at_clean_eof():
    async def scenario():
        reader = ReaderOver(EncodeFrame(4, SAMPLE) + EncodeFrame(5, {"x": 1}))
        return [await ReadFrame(reader), await ReadFrame(reader), await ReadFrame(reader)]

    assert asyncio.run(scenario()) == [Frame(4, SAMPLE), Frame(5, {"x": 1}), None]


@pytest.mark.parametrize("keep", [2, 5, 9])
def test_read_frame_rejects_a_stream_cut_inside_a_frame(keep: int):
    async def scenario():
        await ReadFrame(ReaderOver(EncodeFrame(4, SAMPLE)[:keep]))

    with pytest.raises(TruncatedFrame):
        asyncio.run(scenario())


def test_read_frame_rejects_an_oversized_length_without_reading_the_body():
    async def scenario():
        await ReadFrame(ReaderOver(struct.pack(">I", framing.MAX_FRAME_BYTES + 1)))

    with pytest.raises(FrameTooLarge):
        asyncio.run(scenario())


def test_write_frame_and_read_frame_agree_over_a_socket():
    async def scenario():
        received = asyncio.get_running_loop().create_future()

        async def handle(reader, writer):
            received.set_result(await ReadFrame(reader))
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        await WriteFrame(writer, 4, SAMPLE)
        result = await asyncio.wait_for(received, 5)
        writer.close()
        server.close()
        await server.wait_closed()
        return result

    assert asyncio.run(scenario()) == Frame(4, SAMPLE)
