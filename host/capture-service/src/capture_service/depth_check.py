"""Cross-check between the ArUco pose (from RGB) and the depth map.

If intrinsics, depth units and RGB/depth alignment are right, the depth measured
at each marker centre matches the camera-frame z predicted by the ring pose.
"""

from __future__ import annotations

import numpy as np

from astrea_common.aruco_ring import RingLayout
from astrea_common.rgbd_frame import RgbdFrame
from capture_service.ring_pose import RingPose

SAMPLE_RADIUS_PX = 2


def MarkerDepthResidualsMm(frame: RgbdFrame, pose: RingPose, layout: RingLayout) -> list[float]:
    """Return measured minus predicted depth (mm) at each marker centre used by the pose."""
    depthK = frame.depthIntrinsics
    height, width = frame.depthM.shape
    residuals = []
    for markerId in pose.markerIds:
        centerPlate = layout.MarkerCorners(markerId).mean(axis=0)
        centerCamera = pose.rotation @ centerPlate + pose.translationMm
        u = depthK.fx * centerCamera[0] / centerCamera[2] + depthK.cx
        v = depthK.fy * centerCamera[1] / centerCamera[2] + depthK.cy
        col, row = int(round(u)), int(round(v))
        if not (SAMPLE_RADIUS_PX <= col < width - SAMPLE_RADIUS_PX and SAMPLE_RADIUS_PX <= row < height - SAMPLE_RADIUS_PX):
            continue
        window = frame.depthM[row - SAMPLE_RADIUS_PX : row + SAMPLE_RADIUS_PX + 1, col - SAMPLE_RADIUS_PX : col + SAMPLE_RADIUS_PX + 1]
        valid = window[window > 0]
        if len(valid):
            residuals.append(float(np.median(valid)) * 1000.0 - float(centerCamera[2]))
    return residuals
