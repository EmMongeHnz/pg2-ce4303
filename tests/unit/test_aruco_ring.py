import cv2
import numpy as np
import pytest

from astrea_common.aruco_ring import PixelToPlateMatrix, PlateMmToPixel, RenderPlate, RingLayout

PX_PER_MM = 6.0


def test_default_layout_is_valid():
    RingLayout().Validate()


@pytest.mark.parametrize(
    "layout",
    [
        RingLayout(markerCenterRadiusMm=85.0),
        RingLayout(markerCount=40),
        RingLayout(firstMarkerId=45),
        RingLayout(markerCount=2),
    ],
)
def test_invalid_layouts_are_rejected(layout):
    with pytest.raises(ValueError):
        layout.Validate()


def test_layout_json_round_trip():
    layout = RingLayout(plateDiameterMm=200.0, markerSizeMm=26.0)
    assert RingLayout.FromJson(layout.ToJson()) == layout


def test_marker_corners_are_square_and_centered_on_ring():
    layout = RingLayout()
    for markerId in layout.markerIds:
        corners = layout.MarkerCorners(markerId)
        sides = np.linalg.norm(np.roll(corners, -1, axis=0) - corners, axis=1)
        assert np.allclose(sides, layout.markerSizeMm)
        assert np.isclose(np.linalg.norm(corners.mean(axis=0)), layout.markerCenterRadiusMm)
        assert np.allclose(corners[:, 2], 0.0)


def test_pixel_mapping_is_inverse_of_plate_mapping():
    points = np.array([[0.0, 0.0], [50.0, -20.0], [-89.0, 30.0]])
    pixels = PlateMmToPixel(points, PX_PER_MM, 90.0)
    homogeneous = np.column_stack([pixels, np.ones(len(pixels))]) @ PixelToPlateMatrix(PX_PER_MM, 90.0).T
    assert np.allclose(homogeneous[:, :2], points)


def test_rendered_plate_detects_every_marker_with_layout_corners():
    layout = RingLayout()
    plate = RenderPlate(layout, PX_PER_MM)
    detector = cv2.aruco.ArucoDetector(layout.Dictionary(), cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(plate)
    assert sorted(ids.ravel().tolist()) == layout.markerIds
    for markerId, detected in zip(ids.ravel(), corners):
        expected = PlateMmToPixel(layout.MarkerCorners(int(markerId))[:, :2], PX_PER_MM, layout.plateRadiusMm)
        assert np.abs(detected.reshape(4, 2) - expected).max() < 1.5
