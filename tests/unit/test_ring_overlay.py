import numpy as np
import pytest

from astrea_common.rgbd_frame import Intrinsics, RgbdFrame
from capture_service.frame_dump import LivePreview, RotateCounterclockwise
from capture_service.ring_overlay import RingOverlay
from capture_service.synthetic_ring import SceneOptions, SyntheticRingRenderer, ViewPose

GREEN = (0, 220, 0)
RED = (0, 0, 255)
STATUS_BLOCK = (slice(40, 130), slice(0, 400))


@pytest.fixture(scope="module")
def renderer():
    return SyntheticRingRenderer()


def MakeFrame(renderer, gray):
    camera = renderer.camera
    intrinsics = Intrinsics(camera.fx, camera.fy, camera.cx, camera.cy, camera.width, camera.height)
    rgb = np.repeat(gray[:, :, None], 3, axis=2)
    depth = np.zeros(gray.shape, dtype=np.float32)
    return RgbdFrame(index=0, rgb=rgb, depthM=depth, rgbIntrinsics=intrinsics)


def CountPixels(image, bgr):
    return int(np.sum(np.all(image[STATUS_BLOCK] == bgr, axis=2)))


def test_overlay_reports_pose_in_green(renderer):
    gray = renderer.Render(ViewPose(60, 35, 350), SceneOptions(objectRadiusMm=0.0))
    annotation = RingOverlay(renderer.layout).Render(MakeFrame(renderer, gray))
    assert annotation.image.shape == (480, 640, 3)
    assert annotation.statusColor == GREEN
    assert annotation.statusLines[0].startswith("az ")


def test_overlay_reports_missing_pose_in_red(renderer):
    gray = np.full((480, 640), 128, np.uint8)
    annotation = RingOverlay(renderer.layout).Render(MakeFrame(renderer, gray))
    assert annotation.statusColor == RED
    assert annotation.statusLines[0].startswith("NO POSE")


@pytest.mark.parametrize("rotationDeg, expectedShape", [(0, (480, 1280, 3)), (90, (640, 960, 3)), (180, (480, 1280, 3)), (270, (640, 960, 3))])
def test_preview_rotates_panels_and_keeps_status_text_upright(renderer, rotationDeg, expectedShape):
    gray = renderer.Render(ViewPose(60, 35, 350), SceneOptions(objectRadiusMm=0.0))
    frame = MakeFrame(renderer, gray)
    annotation = RingOverlay(renderer.layout).Render(frame)
    preview = LivePreview(frame, 25.0, annotation, rotationDeg)
    assert preview.shape == expectedShape
    assert CountPixels(preview, GREEN) > 0


def test_counterclockwise_rotation_moves_the_top_right_corner_to_the_top_left():
    image = np.zeros((4, 6, 3), np.uint8)
    image[0, 5] = 255
    rotated = RotateCounterclockwise(image, 90)
    assert rotated.shape == (6, 4, 3)
    assert rotated[0, 0].tolist() == [255, 255, 255]
