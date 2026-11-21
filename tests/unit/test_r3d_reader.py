import json
import zipfile

import liblzfse
import numpy as np
import pytest

from astrea_common.rgbd_frame import Intrinsics
from capture_service.depth_check import MarkerDepthResidualsMm
from capture_service.r3d_reader import R3dFormatError, R3dReader
from capture_service.ring_pose import AngleDifferenceDeg, RingPoseEstimator
from capture_service.synthetic_r3d import R3dFrameData, WriteR3d, WriteSyntheticTurntableR3d
from astrea_common.aruco_ring import RingLayout
from capture_service.synthetic_ring import ViewPose

RGB_INTRINSICS = Intrinsics(fx=500.0, fy=510.0, cx=300.0, cy=200.0, width=640, height=480)


def MakeFrames(count=3, confidence=False):
    rng = np.random.default_rng(1)
    frames = []
    for _ in range(count):
        depth = rng.uniform(0.2, 0.6, (240, 320)).astype(np.float32)
        depth[0, 0] = np.nan
        frames.append(
            R3dFrameData(
                rgb=rng.integers(0, 255, (480, 640, 3), dtype=np.uint8),
                depthM=depth,
                confidence=np.full((240, 320), 2, np.uint8) if confidence else None,
            )
        )
    return frames


def test_round_trip_depth_intrinsics_and_confidence(tmp_path):
    frames = MakeFrames(confidence=True)
    path = tmp_path / "capture.r3d"
    WriteR3d(path, frames, RGB_INTRINSICS, fps=60.0)
    with R3dReader(path) as reader:
        assert len(reader) == 3
        assert reader.rgbIntrinsics == RGB_INTRINSICS
        frame = reader.ReadFrame(1)
        assert frame.depthM[0, 0] == 0.0
        assert np.array_equal(frame.depthM[1:], frames[1].depthM[1:])
        assert frame.rgb.shape == (480, 640, 3)
        assert frame.confidence is not None and frame.confidence.shape == (240, 320)
        assert frame.timestampS == pytest.approx(1 / 60)
        depthK = frame.depthIntrinsics
        assert (depthK.width, depthK.height, depthK.fx, depthK.cx) == (320, 240, 250.0, 150.0)


def test_column_major_k_and_per_frame_intrinsics(tmp_path):
    path = tmp_path / "capture.r3d"
    WriteR3d(path, MakeFrames(count=2), RGB_INTRINSICS)
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    metadata = json.loads(entries["metadata"])
    assert metadata["K"][6:8] == [300.0, 200.0]
    metadata["perFrameIntrinsicCoeffs"] = [[500, 510, 300, 200], [520, 530, 310, 210]]
    entries["metadata"] = json.dumps(metadata).encode()
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    with R3dReader(path) as reader:
        assert reader.ReadFrame(1).rgbIntrinsics.fx == 520


def test_wrong_depth_size_is_reported(tmp_path):
    path = tmp_path / "broken.r3d"
    WriteR3d(path, MakeFrames(count=1), RGB_INTRINSICS)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("rgbd/1.depth", liblzfse.compress(b"\x00" * 16))
    with R3dReader(path) as reader, pytest.raises(R3dFormatError):
        reader.ReadDepth(1)


def test_missing_metadata_is_reported(tmp_path):
    path = tmp_path / "empty.r3d"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("rgbd/0.jpg", b"")
    with pytest.raises(R3dFormatError):
        R3dReader(path)


def test_synthetic_capture_pose_and_depth_agree(tmp_path):
    path = tmp_path / "turntable.r3d"
    poses = [ViewPose(azimuth, 35, 350) for azimuth in (0, 90, 200)]
    WriteSyntheticTurntableR3d(path, poses)
    layout = RingLayout()
    with R3dReader(path) as reader:
        estimator = RingPoseEstimator(layout, reader.rgbIntrinsics.Matrix())
        for truth, frame in zip(poses, reader):
            estimate = estimator.Estimate(frame.Bgr())
            assert abs(AngleDifferenceDeg(estimate.azimuthDeg, truth.azimuthDeg)) < 0.5
            residuals = MarkerDepthResidualsMm(frame, estimate, layout)
            assert len(residuals) >= 4
            assert np.median(np.abs(residuals)) < 2.0
