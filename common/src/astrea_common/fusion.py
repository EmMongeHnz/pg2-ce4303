"""Per-view preprocessing and integration into one TSDF volume.

Shared by the single-node pipeline and the workers, so both fuse a view the same way. Preprocessing and
the per-pixel weights only look at one view, which is what lets a set of views be split across nodes and
merged exactly afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np

from astrea_common.tsdf import GridSpec, TsdfVolume
from astrea_common.view_package import ViewPackage
from astrea_common.view_preprocess import ComputeViewWeights, PreprocessParams, PreprocessView

DEFAULT_TRUNCATION_MM = 5.2


@dataclass(frozen=True)
class FusionSettings:
    truncationMm: float = DEFAULT_TRUNCATION_MM
    weights: bool = True
    bilinear: bool = True


def KeptPercent(view: ViewPackage, cleanedDepthMm: np.ndarray) -> float:
    valid = int((view.depthM > 0).sum())
    return 100.0 * int((cleanedDepthMm > 0).sum()) / valid if valid else 0.0


def FuseViews(views: Iterable[ViewPackage], params: PreprocessParams, grid: GridSpec, settings: FusionSettings = FusionSettings()) -> tuple[TsdfVolume, list[float]]:
    volume = TsdfVolume(grid, truncationMm=settings.truncationMm)
    keptPercent = []
    for view in views:
        cleanedDepthMm = PreprocessView(view, params)
        weights = ComputeViewWeights(cleanedDepthMm, view.intrinsics, params) if settings.weights else None
        volume.Integrate(cleanedDepthMm, view.intrinsics, view.rotation, view.translationMm, view.rgb, weights=weights, bilinear=settings.bilinear)
        keptPercent.append(KeptPercent(view, cleanedDepthMm))
    return volume, keptPercent
