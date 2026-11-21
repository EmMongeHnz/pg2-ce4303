"""Live RGB-D from the Record3D app over USB (spike 0a primary path).

Needs the `record3d` Python package (`uv sync --extra record3d`), the `usbmuxd`
daemon on Linux, and the USB streaming in-app purchase. The library is imported
lazily so the rest of the capture service works without it.
"""

from __future__ import annotations

import threading
import time

import numpy as np

from astrea_common.rgbd_frame import Intrinsics, RgbdFrame, SanitizeDepth

DEVICE_TYPE_TRUEDEPTH = 0
DEVICE_TYPE_LIDAR = 1


class Record3dUnavailable(RuntimeError):
    pass


def _ImportRecord3d():
    try:
        import record3d
    except ImportError as error:
        raise Record3dUnavailable("install it with `uv sync --extra record3d`") from error
    return record3d


def ListDevices() -> list[tuple[int, str]]:
    record3d = _ImportRecord3d()
    return [(device.product_id, device.udid) for device in record3d.Record3DStream.get_connected_devices()]


class Record3dStream:
    """Callback-driven session wrapped into a blocking `Read()` that returns the newest frame."""

    def __init__(self, deviceIndex: int = 0) -> None:
        record3d = _ImportRecord3d()
        devices = record3d.Record3DStream.get_connected_devices()
        if deviceIndex >= len(devices):
            raise Record3dUnavailable(
                f"device {deviceIndex} not found ({len(devices)} connected). "
                "Is the iPhone plugged in, trusted, and is Record3D in USB streaming mode?"
            )
        self._frameReady = threading.Event()
        self._stopped = threading.Event()
        self._frameCount = 0
        self._session = record3d.Record3DStream()
        self._session.on_new_frame = self._frameReady.set
        self._session.on_stream_stopped = self._stopped.set
        self._session.connect(devices[deviceIndex])

    @property
    def deviceType(self) -> int:
        return self._session.get_device_type()

    @property
    def isTrueDepth(self) -> bool:
        return self.deviceType == DEVICE_TYPE_TRUEDEPTH

    def Read(self, timeoutS: float = 2.0) -> RgbdFrame:
        if not self._frameReady.wait(timeoutS):
            if self._stopped.is_set():
                raise Record3dUnavailable("the stream was stopped on the phone")
            raise TimeoutError(f"no frame within {timeoutS} s")
        self._frameReady.clear()
        rgb = np.ascontiguousarray(self._session.get_rgb_frame())
        depth = SanitizeDepth(self._session.get_depth_frame())
        coefficients = self._session.get_intrinsic_mat()
        height, width = rgb.shape[:2]
        intrinsics = Intrinsics(coefficients.fx, coefficients.fy, coefficients.tx, coefficients.ty, width, height)
        pose = self._session.get_camera_pose()
        frame = RgbdFrame(
            index=self._frameCount,
            rgb=rgb,
            depthM=depth,
            rgbIntrinsics=intrinsics,
            timestampS=time.monotonic(),
            confidence=None if self.isTrueDepth else np.ascontiguousarray(self._session.get_confidence_frame()),
            poseQuatPos=np.array([pose.qx, pose.qy, pose.qz, pose.qw, pose.tx, pose.ty, pose.tz]),
        )
        self._frameCount += 1
        return frame

    @property
    def stopped(self) -> bool:
        return self._stopped.is_set()
