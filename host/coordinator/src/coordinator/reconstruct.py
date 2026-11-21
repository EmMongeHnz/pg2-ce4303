"""Single-node reconstruction of saved capture sessions into GLB models (the coordinator working alone).

Usage:
    uv run astrea-reconstruct out/scans/cup
    uv run astrea-reconstruct out/scans/cup out/scans/cup-high-pass --out out/scans/cup/model
    uv run astrea-reconstruct out/scans/cup --voxel-mm 0.5 --max-radius-mm 90 --min-depth-mm 220

Each saved view goes through the shared per-view preprocessing, is fused into one TSDF whose grid is fitted
to the object (or a fixed cube with --extent-mm), meshed, cleaned and exported as three models: lod-full
(no triangle cap), lod-high (30k) and lod-low (8k). The exit code is 1 when LOD-high or LOD-low exceeds its
budget or is not a single connected component (the M3 gate for real meshes).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from astrea_common.tsdf import BYTES_PER_VOXEL, GridSpec, TsdfVolume
from astrea_common.view_package import ListViewPaths, ReadView, ViewPackage
from astrea_common.view_preprocess import ComputeViewWeights, PlatePoints, PreprocessParams, PreprocessView
from coordinator.meshing import ComponentCount
from coordinator.postprocess import LOD_HIGH_TRIANGLES, LOD_LOW_TRIANGLES, TAUBIN_ITERATIONS, BuildLods

LOD_BUDGETS = {"full": None, "high": LOD_HIGH_TRIANGLES, "low": LOD_LOW_TRIANGLES}
REPORT_NAME = "report.json"
MIN_KEPT_PERCENT_WARNING = 2.0
DEFAULT_VOXEL_MM = 0.65
DEFAULT_TRUNCATION_MM = 5.2
DEFAULT_MAX_VOXELS = 100_000_000
GRID_MARGIN_TRUNCATIONS = 1.5
BYTES_PER_GIB = 1024**3


@dataclass(frozen=True)
class ReconstructSettings:
    voxelMm: float = DEFAULT_VOXEL_MM
    extentMm: float | None = None
    maxVoxels: int = DEFAULT_MAX_VOXELS
    truncationMm: float = DEFAULT_TRUNCATION_MM
    weights: bool = True
    bilinear: bool = True
    taubinIterations: int = TAUBIN_ITERATIONS


@dataclass(frozen=True)
class LodReport:
    faces: int
    budget: int | None
    components: int
    glbBytes: int
    compressedGlbBytes: int

    @property
    def passesGate(self) -> bool:
        return self.components == 1 and (self.budget is None or self.faces <= self.budget)


@dataclass(frozen=True)
class ReconstructionReport:
    viewCount: int
    keptPercentPerView: list[float]
    preprocess: dict
    settings: dict
    grid: dict
    fusionSeconds: float
    lods: dict[str, LodReport]

    @property
    def passesGate(self) -> bool:
        return all(lod.passesGate for lod in self.lods.values())


def CollectViewPaths(sessionDirs: list[Path]) -> list[Path]:
    return [path for sessionDir in sessionDirs for path in ListViewPaths(sessionDir)]


def IterateViews(viewPaths: list[Path]):
    for path in viewPaths:
        yield ReadView(path)


def KeptPercent(view: ViewPackage, cleanedDepthMm: np.ndarray) -> float:
    valid = int((view.depthM > 0).sum())
    return 100.0 * int((cleanedDepthMm > 0).sum()) / valid if valid else 0.0


def MeasureObjectBounds(views, params: PreprocessParams) -> tuple[np.ndarray, np.ndarray]:
    """Bounding box (plate frame, mm) of the points that survive preprocessing in any view."""
    lows, highs = [], []
    for view in views:
        points = PlatePoints(view, PreprocessView(view, params))
        if len(points):
            lows.append(points.min(axis=0))
            highs.append(points.max(axis=0))
    if not lows:
        raise ValueError("no surface points remain after preprocessing; check the depth range and crop parameters")
    return np.min(lows, axis=0), np.max(highs, axis=0)


def ChooseGrid(viewPaths: list[Path], params: PreprocessParams, settings: ReconstructSettings) -> GridSpec:
    if settings.extentMm is not None:
        grid = GridSpec.Cube(settings.extentMm, settings.voxelMm)
    else:
        low, high = MeasureObjectBounds(IterateViews(viewPaths), params)
        margin = GRID_MARGIN_TRUNCATIONS * settings.truncationMm + settings.voxelMm
        grid = GridSpec.FitToBounds(low, high, settings.voxelMm, margin)
    EnsureGridFits(grid, settings.maxVoxels)
    return grid


def EnsureGridFits(grid: GridSpec, maxVoxels: int) -> None:
    if grid.voxelCount > maxVoxels:
        raise ValueError(
            f"the grid {grid.shape} has {grid.voxelCount / 1e6:.0f} M voxels (~{grid.memoryBytes / BYTES_PER_GIB:.1f} GiB, "
            f"{BYTES_PER_VOXEL} bytes each), above --max-voxels {maxVoxels}; raise --voxel-mm or --max-voxels"
        )


def FuseViews(views, params: PreprocessParams, grid: GridSpec, settings: ReconstructSettings = ReconstructSettings()) -> tuple[TsdfVolume, list[float]]:
    volume = TsdfVolume(grid, truncationMm=settings.truncationMm)
    keptPercent = []
    for view in views:
        cleanedDepthMm = PreprocessView(view, params)
        weights = ComputeViewWeights(cleanedDepthMm, view.intrinsics, params) if settings.weights else None
        volume.Integrate(cleanedDepthMm, view.intrinsics, view.rotation, view.translationMm, view.rgb, weights=weights, bilinear=settings.bilinear)
        keptPercent.append(KeptPercent(view, cleanedDepthMm))
    return volume, keptPercent


def Reconstruct(
    sessionDirs: list[Path],
    outDir: Path,
    params: PreprocessParams = PreprocessParams(),
    grid: GridSpec | None = None,
    settings: ReconstructSettings = ReconstructSettings(),
) -> ReconstructionReport:
    viewPaths = CollectViewPaths(sessionDirs)
    if not viewPaths:
        raise ValueError("the sessions contain no views")
    grid = grid or ChooseGrid(viewPaths, params, settings)
    start = time.monotonic()
    volume, keptPercent = FuseViews(IterateViews(viewPaths), params, grid, settings)
    fusionSeconds = time.monotonic() - start
    lods = BuildLods(volume, taubinIterations=settings.taubinIterations)
    outDir.mkdir(parents=True, exist_ok=True)
    lodReports = {}
    for name, lod in lods.items():
        glb = lod.ToGlb()
        (outDir / f"lod-{name}.glb").write_bytes(glb)
        lodReports[name] = LodReport(len(lod.mesh.faces), LOD_BUDGETS[name], ComponentCount(lod.mesh), len(glb), len(lod.ToCompressedGlb()))
    report = ReconstructionReport(len(keptPercent), keptPercent, asdict(params), asdict(settings), asdict(grid), fusionSeconds, lodReports)
    (outDir / REPORT_NAME).write_text(json.dumps(asdict(report), indent=2))
    return report


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sessions", type=Path, nargs="+", help="session directories written by astrea-capture")
    parser.add_argument("--out", type=Path, default=None, help="output directory (default: <first session>/model)")
    defaults = PreprocessParams()
    parser.add_argument("--min-depth-mm", type=float, default=defaults.minDepthMm)
    parser.add_argument("--max-depth-mm", type=float, default=defaults.maxDepthMm)
    parser.add_argument("--plate-margin-mm", type=float, default=defaults.plateMarginMm)
    parser.add_argument("--max-height-mm", type=float, default=defaults.maxHeightMm)
    parser.add_argument("--max-radius-mm", type=float, default=defaults.cylinderRadiusMm)
    parser.add_argument("--support-depth-mm", type=float, default=defaults.supportDepthMm)
    parser.add_argument("--min-support-pixels", type=int, default=defaults.minSupportPixels)
    parser.add_argument("--min-component-pixels", type=int, default=defaults.minComponentPixels)
    settings = ReconstructSettings()
    parser.add_argument("--voxel-mm", type=float, default=settings.voxelMm, help="voxel size; about one pixel footprint (0.65 mm at 30 cm) is the useful limit")
    parser.add_argument("--extent-mm", type=float, default=None, help="use a fixed cube of this side instead of fitting the grid to the object")
    parser.add_argument("--max-voxels", type=int, default=settings.maxVoxels, help="refuse grids larger than this (20 bytes per voxel)")
    parser.add_argument("--truncation-mm", type=float, default=settings.truncationMm)
    parser.add_argument("--taubin-iterations", type=int, default=settings.taubinIterations, help="0 disables smoothing")
    parser.add_argument("--no-weights", action="store_true", help="give every observation the same weight")
    parser.add_argument("--no-bilinear", action="store_true", help="sample depth and colour at the nearest pixel")
    return parser.parse_args()


def ParamsFromArgs(args: argparse.Namespace) -> PreprocessParams:
    return PreprocessParams(
        minDepthMm=args.min_depth_mm,
        maxDepthMm=args.max_depth_mm,
        plateMarginMm=args.plate_margin_mm,
        maxHeightMm=args.max_height_mm,
        cylinderRadiusMm=args.max_radius_mm,
        supportDepthMm=args.support_depth_mm,
        minSupportPixels=args.min_support_pixels,
        minComponentPixels=args.min_component_pixels,
    )


def SettingsFromArgs(args: argparse.Namespace) -> ReconstructSettings:
    return ReconstructSettings(
        voxelMm=args.voxel_mm,
        extentMm=args.extent_mm,
        maxVoxels=args.max_voxels,
        truncationMm=args.truncation_mm,
        weights=not args.no_weights,
        bilinear=not args.no_bilinear,
        taubinIterations=args.taubin_iterations,
    )


def PrintReport(report: ReconstructionReport, outDir: Path) -> None:
    lowest = min(report.keptPercentPerView)
    shape = report.grid["shape"]
    print(f"grid {shape[0]}x{shape[1]}x{shape[2]} at {report.grid['voxelMm']:.2f} mm")
    print(f"{report.viewCount} views fused in {report.fusionSeconds:.1f} s; depth kept per view: min {lowest:.0f} %, median {np.median(report.keptPercentPerView):.0f} %")
    if lowest < MIN_KEPT_PERCENT_WARNING:
        print(f"warning: some views keep under {MIN_KEPT_PERCENT_WARNING:.0f} % of their depth; check the crop radius and depth range")
    for name, lod in report.lods.items():
        budget = "no cap" if lod.budget is None else f"budget {lod.budget}"
        status = "OK" if lod.passesGate else "FAIL"
        print(f"lod-{name}: {lod.faces} faces ({budget}), {lod.components} component(s), GLB {lod.glbBytes / 1024:.0f} KiB, zstd {lod.compressedGlbBytes / 1024:.0f} KiB  {status}")
    print(f"written to {outDir}")


def Main() -> None:
    args = ParseArgs()
    outDir = args.out or args.sessions[0] / "model"
    try:
        report = Reconstruct(args.sessions, outDir, ParamsFromArgs(args), settings=SettingsFromArgs(args))
    except ValueError as error:
        sys.exit(f"reconstruction failed: {error}")
    PrintReport(report, outDir)
    sys.exit(0 if report.passesGate else 1)


if __name__ == "__main__":
    Main()
