"""Reader for Record3D `.r3d` exports (spike 0a fallback path).

An `.r3d` is a zip archive with:
- `metadata`: JSON with `w`/`h` (RGB size), `dw`/`dh` (depth size), `fps`, `K`
  (RGB intrinsics, 9 values in column-major order), optional `poses`
  (`[qx, qy, qz, qw, tx, ty, tz]` per frame) and `perFrameIntrinsicCoeffs`;
- `rgbd/<i>.jpg`: RGB frame;
- `rgbd/<i>.depth`: LZFSE-compressed float32 depth in metres, `dh` x `dw`;
- `rgbd/<i>.conf`: LZFSE-compressed uint8 confidence (LiDAR devices only).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import cv2
import liblzfse
import numpy as np

from astrea_common.rgbd_frame import Intrinsics, RgbdFrame, SanitizeDepth

FRAME_PATTERN = re.compile(r"^rgbd/(\d+)\.depth$")


class R3dFormatError(ValueError):
    pass


def IntrinsicsFromMetadata(metadata: dict, frameIndex: int | None = None) -> Intrinsics:
    width, height = int(metadata["w"]), int(metadata["h"])
    perFrame = metadata.get("perFrameIntrinsicCoeffs")
    if frameIndex is not None and perFrame and frameIndex < len(perFrame):
        fx, fy, cx, cy = perFrame[frameIndex][:4]
        return Intrinsics(fx, fy, cx, cy, width, height)
    matrix = np.asarray(metadata["K"], dtype=np.float64).reshape(3, 3).T
    return Intrinsics(matrix[0, 0], matrix[1, 1], matrix[0, 2], matrix[1, 2], width, height)


class R3dReader:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.archive = zipfile.ZipFile(self.path)
        names = set(self.archive.namelist())
        if "metadata" not in names:
            raise R3dFormatError(f"{self.path} has no metadata entry")
        self.metadata = json.loads(self.archive.read("metadata"))
        for key in ("w", "h", "dw", "dh", "K"):
            if key not in self.metadata:
                raise R3dFormatError(f"metadata is missing '{key}'")
        self.frameIndices = sorted(
            int(match.group(1)) for name in names if (match := FRAME_PATTERN.match(name))
        )
        self._names = names

    def __enter__(self) -> "R3dReader":
        return self

    def __exit__(self, *exc) -> None:
        self.Close()

    def Close(self) -> None:
        self.archive.close()

    def __len__(self) -> int:
        return len(self.frameIndices)

    @property
    def fps(self) -> float | None:
        return self.metadata.get("fps")

    @property
    def depthSize(self) -> tuple[int, int]:
        return int(self.metadata["dw"]), int(self.metadata["dh"])

    @property
    def rgbIntrinsics(self) -> Intrinsics:
        return IntrinsicsFromMetadata(self.metadata)

    def ReadDepth(self, frameIndex: int) -> np.ndarray:
        width, height = self.depthSize
        raw = liblzfse.decompress(self.archive.read(f"rgbd/{frameIndex}.depth"))
        expected = width * height * 4
        if len(raw) != expected:
            raise R3dFormatError(f"frame {frameIndex}: depth has {len(raw)} bytes, expected {expected}")
        return SanitizeDepth(np.frombuffer(raw, dtype=np.float32).reshape(height, width))

    def ReadConfidence(self, frameIndex: int) -> np.ndarray | None:
        name = f"rgbd/{frameIndex}.conf"
        if name not in self._names:
            return None
        width, height = self.depthSize
        raw = liblzfse.decompress(self.archive.read(name))
        return np.frombuffer(raw, dtype=np.uint8).reshape(height, width)

    def ReadRgb(self, frameIndex: int) -> np.ndarray:
        encoded = np.frombuffer(self.archive.read(f"rgbd/{frameIndex}.jpg"), dtype=np.uint8)
        bgr = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if bgr is None:
            raise R3dFormatError(f"frame {frameIndex}: cannot decode the RGB image")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    def ReadFrame(self, frameIndex: int) -> RgbdFrame:
        poses = self.metadata.get("poses") or []
        pose = np.asarray(poses[frameIndex], dtype=np.float64) if frameIndex < len(poses) else None
        fps = self.fps
        return RgbdFrame(
            index=frameIndex,
            rgb=self.ReadRgb(frameIndex),
            depthM=self.ReadDepth(frameIndex),
            rgbIntrinsics=IntrinsicsFromMetadata(self.metadata, frameIndex),
            timestampS=frameIndex / fps if fps else None,
            confidence=self.ReadConfidence(frameIndex),
            poseQuatPos=pose,
        )

    def __iter__(self):
        for frameIndex in self.frameIndices:
            yield self.ReadFrame(frameIndex)
