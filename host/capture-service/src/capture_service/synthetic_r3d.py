"""Writer for `.r3d` archives, used to build synthetic captures for tests and dry runs.

Mirrors the layout read by `r3d_reader` (see its module docstring).
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import cv2
import liblzfse
import numpy as np

from astrea_common.rgbd_frame import Intrinsics
from capture_service.synthetic_ring import PlaneDepthMapM, SceneOptions, SyntheticRingRenderer, ViewPose

JPEG_QUALITY = 95


@dataclass
class R3dFrameData:
    rgb: np.ndarray
    depthM: np.ndarray
    confidence: np.ndarray | None = None


def WriteR3d(path: str | Path, frames: list[R3dFrameData], rgbIntrinsics: Intrinsics, fps: float = 30.0,
             poses: list[list[float]] | None = None) -> None:
    depthHeight, depthWidth = frames[0].depthM.shape
    matrixColumnMajor = rgbIntrinsics.Matrix().T.reshape(-1).tolist()
    metadata = {
        "w": rgbIntrinsics.width,
        "h": rgbIntrinsics.height,
        "dw": depthWidth,
        "dh": depthHeight,
        "fps": fps,
        "K": matrixColumnMajor,
        "cameraType": 0,
        "poses": poses or [],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("metadata", json.dumps(metadata))
        for i, frame in enumerate(frames):
            ok, jpg = cv2.imencode(".jpg", cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
            if not ok:
                raise RuntimeError("JPEG encoding failed")
            archive.writestr(f"rgbd/{i}.jpg", jpg.tobytes())
            archive.writestr(f"rgbd/{i}.depth", liblzfse.compress(frame.depthM.astype(np.float32).tobytes()))
            if frame.confidence is not None:
                archive.writestr(f"rgbd/{i}.conf", liblzfse.compress(frame.confidence.astype(np.uint8).tobytes()))


def WriteSyntheticTurntableR3d(path: str | Path, poses: list[ViewPose], depthScale: int = 2,
                               options: SceneOptions = SceneOptions(objectRadiusMm=0.0)) -> Intrinsics:
    """Render the ring from each pose into an `.r3d` with depth at 1/`depthScale` of the RGB resolution."""
    renderer = SyntheticRingRenderer()
    camera = renderer.camera
    rgbIntrinsics = Intrinsics(camera.fx, camera.fy, camera.cx, camera.cy, camera.width, camera.height)
    depthIntrinsics = rgbIntrinsics.ScaledTo(camera.width // depthScale, camera.height // depthScale)
    frames = []
    for pose in poses:
        gray = renderer.Render(pose, options)
        depth = PlaneDepthMapM(pose, depthIntrinsics.Matrix(), depthIntrinsics.width, depthIntrinsics.height)
        frames.append(R3dFrameData(rgb=cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB), depthM=depth))
    WriteR3d(path, frames, rgbIntrinsics)
    return rgbIntrinsics
