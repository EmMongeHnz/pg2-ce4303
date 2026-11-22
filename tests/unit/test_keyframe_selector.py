import numpy as np

from capture_service.keyframe_selector import KeyframeSelector, SelectorParams
from capture_service.ring_pose import RingPose
from capture_service.synthetic_ring import ViewPose

FRAME_S = 1 / 30
FAST = SelectorParams(stableSeconds=0.5)


def PoseAt(azimuthDeg: float, elevationDeg: float = 35.0, distanceMm: float = 300.0) -> RingPose:
    rotation, translation = ViewPose(azimuthDeg, elevationDeg, distanceMm).PlateToCamera()
    return RingPose(rotation, translation, (0, 1), 0.3)


def Hold(selector: KeyframeSelector, azimuthDeg: float, startS: float, seconds: float = 1.0) -> tuple[list[float | None], float]:
    results = []
    time = startS
    while time < startS + seconds:
        results.append(selector.Update(PoseAt(azimuthDeg), time))
        time += FRAME_S
    return results, time


def test_first_stable_pose_is_a_keyframe_after_the_stability_window():
    selector = KeyframeSelector(FAST)
    results, _ = Hold(selector, 40.0, 0.0)
    accepted = [r for r in results if r is not None]
    assert len(accepted) == 1
    assert accepted[0] == 0.0
    firstIndex = next(i for i, r in enumerate(results) if r is not None)
    assert firstIndex * FRAME_S >= FAST.stableSeconds


def test_no_second_keyframe_without_rotation():
    selector = KeyframeSelector(FAST)
    _, time = Hold(selector, 40.0, 0.0)
    results, _ = Hold(selector, 40.0, time, seconds=2.0)
    assert all(r is None for r in results)


def test_advance_below_the_step_is_ignored_and_above_it_is_accepted():
    selector = KeyframeSelector(FAST)
    _, time = Hold(selector, 40.0, 0.0)
    results, time = Hold(selector, 47.0, time)
    assert all(r is None for r in results)
    results, _ = Hold(selector, 51.0, time)
    assert any(r is not None for r in results)


def test_rotation_in_the_negative_direction_also_counts():
    selector = KeyframeSelector(FAST)
    _, time = Hold(selector, 40.0, 0.0)
    results, _ = Hold(selector, 29.0, time)
    assert any(r is not None for r in results)


def test_advance_wraps_around_zero():
    selector = KeyframeSelector(FAST)
    _, time = Hold(selector, 355.0, 0.0)
    results, _ = Hold(selector, 6.0, time)
    assert any(r is not None for r in results)


def test_moving_pose_never_becomes_a_keyframe():
    selector = KeyframeSelector(FAST)
    results = [selector.Update(PoseAt(i * 1.0), i * FRAME_S) for i in range(120)]
    assert all(r is None for r in results)


def test_losing_the_pose_restarts_the_stability_window():
    selector = KeyframeSelector(FAST)
    for i in range(10):
        selector.Update(PoseAt(40.0), i * FRAME_S)
    selector.Update(None, 10 * FRAME_S)
    assert selector.Update(PoseAt(40.0), 11 * FRAME_S) is None
    assert selector.Update(PoseAt(40.0), 11 * FRAME_S + 0.3) is None
    assert selector.Update(PoseAt(40.0), 11 * FRAME_S + 0.6) is not None


def test_distance_drift_restarts_the_window():
    selector = KeyframeSelector(FAST)
    selector.Update(PoseAt(40.0), 0.0)
    selector.Update(PoseAt(40.0, distanceMm=310.0), 0.3)
    assert selector.Update(PoseAt(40.0, distanceMm=310.0), 0.6) is None
    assert selector.Update(PoseAt(40.0, distanceMm=310.0), 0.9) is not None
