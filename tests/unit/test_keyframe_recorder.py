import numpy as np

from astrea_common.rgbd_frame import Intrinsics, RgbdFrame
from astrea_common.view_package import ListViewPaths, ReadView
from capture_service.coverage import SectorOf
from capture_service.keyframe_recorder import KeyframeRecorder
from capture_service.keyframe_selector import SelectorParams
from capture_service.ring_pose import RingPose
from capture_service.synthetic_ring import ViewPose

INTRINSICS = Intrinsics(fx=100.0, fy=100.0, cx=8.0, cy=6.0, width=16, height=12)
FRAME_S = 1 / 30


def Pose(azimuthDeg: float, error: float = 0.3) -> RingPose:
    rotation, translation = ViewPose(azimuthDeg, 35.0, 300.0).PlateToCamera()
    return RingPose(rotation, translation, (0, 1), error)


def Frame(index: int, depthM: float) -> RgbdFrame:
    return RgbdFrame(index, np.full((12, 16, 3), index % 256, np.uint8), np.full((12, 16), depthM, np.float32), INTRINSICS, timestampS=index * FRAME_S)


def ScanSteps(recorder: KeyframeRecorder, azimuths: list[float]) -> list:
    saved, index = [], 0
    for azimuth in azimuths:
        for _ in range(20):
            view = recorder.Feed(Frame(index, 0.3), Pose(azimuth))
            index += 1
            if view is not None:
                saved.append(view)
    return saved


def test_one_keyframe_per_step_of_a_full_turn(tmp_path):
    recorder = KeyframeRecorder(tmp_path, SelectorParams(stepDeg=9.9))
    saved = ScanSteps(recorder, [float(a) for a in range(5, 360, 10)])
    assert len(saved) == 36
    assert [view.index for view in saved] == list(range(36))
    assert recorder.coverage.coveredCount == 36
    assert len(ListViewPaths(tmp_path)) == 36


def test_saved_view_matches_the_tracked_pose_and_averaged_depth(tmp_path):
    recorder = KeyframeRecorder(tmp_path, averageFrames=3)
    saved = ScanSteps(recorder, [45.0])
    view = ReadView(ListViewPaths(tmp_path)[0])
    assert len(saved) == 1
    assert np.isclose(view.azimuthDeg, Pose(45.0).azimuthDeg)
    assert np.allclose(view.rotation, Pose(45.0).rotation)
    assert np.allclose(view.depthM, 0.3)
    assert view.rgb.shape == (12, 16, 3)
    assert SectorOf(view.azimuthDeg) == 4


def test_frame_with_the_lowest_reprojection_error_provides_the_pose(tmp_path):
    recorder = KeyframeRecorder(tmp_path, averageFrames=5)
    saved = None
    for index in range(20):
        error = 0.1 if index == 14 else 0.5
        saved = recorder.Feed(Frame(index, 0.3), Pose(40.0, error)) or saved
    assert saved is not None
    assert saved.reprojectionErrorPx == 0.1
    assert saved.rgb[0, 0, 0] == 14
