"""Distance statistics between a reconstructed mesh and its ground truth (synthetic test, spike 3).

Distances are point to nearest sample, so they carry a bias of about half the reference sample spacing
(0.1 mm or less with the defaults on a 12 cm object).

Accuracy is reconstruction -> reference. Completeness is reference -> reconstruction and is meant to be
restricted to the reference faces some camera could see, since the underside of an object resting on the
plate is never observed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from astrea_common.mesh import SampleSurface, TriangleMesh

DEFAULT_SAMPLES = 300_000
TARGET_SAMPLE_FACTOR = 4
SEED = 0


@dataclass(frozen=True)
class DistanceStats:
    meanMm: float
    rmsMm: float
    p95Mm: float
    maxMm: float


def SummarizeDistances(distances: np.ndarray) -> DistanceStats:
    return DistanceStats(
        float(distances.mean()),
        float(np.sqrt(np.mean(distances**2))),
        float(np.percentile(distances, 95)),
        float(distances.max()),
    )


def SurfaceDistances(source: TriangleMesh, target: TriangleMesh, samples: int = DEFAULT_SAMPLES, sourceFaceIds=None) -> np.ndarray:
    """Distance from points sampled on `source` to points sampled densely on `target`."""
    rng = np.random.default_rng(SEED)
    sourcePoints, _ = SampleSurface(source, samples, rng, sourceFaceIds)
    targetPoints, _ = SampleSurface(target, TARGET_SAMPLE_FACTOR * samples, rng)
    return cKDTree(targetPoints).query(sourcePoints)[0]


def AccuracyStats(reconstruction: TriangleMesh, reference: TriangleMesh, samples: int = DEFAULT_SAMPLES) -> DistanceStats:
    return SummarizeDistances(SurfaceDistances(reconstruction, reference, samples))


def CompletenessStats(reconstruction: TriangleMesh, reference: TriangleMesh, visibleFaceIds: np.ndarray, samples: int = DEFAULT_SAMPLES) -> DistanceStats:
    return SummarizeDistances(SurfaceDistances(reference, reconstruction, samples, visibleFaceIds))
