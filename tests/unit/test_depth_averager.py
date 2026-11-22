import numpy as np

from capture_service.depth_averager import AverageDepth


def Frame(value: float) -> np.ndarray:
    return np.full((4, 4), value, dtype=np.float32)


def test_mean_of_valid_samples():
    averaged = AverageDepth([Frame(0.300), Frame(0.302), Frame(0.304)])
    assert np.allclose(averaged, 0.302, atol=1e-6)


def test_pixel_with_a_single_valid_sample_is_dropped():
    frames = [Frame(0.3), Frame(0.3), Frame(0.3)]
    frames[0][1, 1] = 0.0
    frames[1][1, 1] = 0.0
    assert AverageDepth(frames)[1, 1] == 0.0


def test_missing_samples_do_not_bias_the_mean():
    frames = [Frame(0.3), Frame(0.3), Frame(0.3)]
    frames[2][0, 0] = 0.0
    assert np.isclose(AverageDepth(frames)[0, 0], 0.3)


def test_inconsistent_pixels_are_dropped():
    frames = [Frame(0.3), Frame(0.3), Frame(0.3)]
    frames[1][2, 2] = 0.4
    averaged = AverageDepth(frames)
    assert averaged[2, 2] == 0.0
    assert averaged[0, 0] > 0


def test_a_single_frame_is_returned_as_is():
    assert np.array_equal(AverageDepth([Frame(0.25)]), Frame(0.25))
