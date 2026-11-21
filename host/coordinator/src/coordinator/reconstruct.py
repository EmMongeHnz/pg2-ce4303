"""Single-node reconstruction of saved capture sessions into GLB models (the coordinator working alone).

Usage:
    uv run astrea-reconstruct out/scans/cup
    uv run astrea-reconstruct out/scans/cup out/scans/cup-high-pass --out out/scans/cup/model
    uv run astrea-reconstruct out/scans/cup --max-radius-mm 90 --min-depth-mm 220

Each saved view goes through the shared per-view preprocessing, is fused into one TSDF on the common
grid, meshed, cleaned and decimated into LOD-high and LOD-low. The exit code is 1 when a LOD exceeds
its triangle budget or is not a single connected component (the M3 gate for real meshes).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from astrea_common.tsdf import GridSpec, TsdfVolume
from astrea_common.view_package import ListViewPaths, ReadView, ViewPackage
from astrea_common.view_preprocess import PreprocessParams, PreprocessView
from coordinator.meshing import ComponentCount
from coordinator.postprocess import LOD_HIGH_TRIANGLES, LOD_LOW_TRIANGLES, BuildLods

LOD_BUDGETS = {"high": LOD_HIGH_TRIANGLES, "low": LOD_LOW_TRIANGLES}
REPORT_NAME = "report.json"
MIN_KEPT_PERCENT_WARNING = 2.0


@dataclass(frozen=True)
class LodReport:
    faces: int
    budget: int
    components: int
    glbBytes: int
    compressedGlbBytes: int

    @property
    def passesGate(self) -> bool:
        return self.components == 1 and self.faces <= self.budget


@dataclass(frozen=True)
class ReconstructionReport:
    viewCount: int
    keptPercentPerView: list[float]
    preprocess: dict
    fusionSeconds: float
    lods: dict[str, LodReport]

    @property
    def passesGate(self) -> bool:
        return all(lod.passesGate for lod in self.lods.values())


def IterateViews(sessionDirs: list[Path]):
    for sessionDir in sessionDirs:
        for path in ListViewPaths(sessionDir):
            yield ReadView(path)


def KeptPercent(view: ViewPackage, cleanedDepthMm: np.ndarray) -> float:
    valid = int((view.depthM > 0).sum())
    return 100.0 * int((cleanedDepthMm > 0).sum()) / valid if valid else 0.0


def FuseViews(views, params: PreprocessParams, grid: GridSpec) -> tuple[TsdfVolume, list[float]]:
    volume = TsdfVolume(grid)
    keptPercent = []
    for view in views:
        cleanedDepthMm = PreprocessView(view, params)
        volume.Integrate(cleanedDepthMm, view.intrinsics, view.rotation, view.translationMm, view.rgb)
        keptPercent.append(KeptPercent(view, cleanedDepthMm))
    return volume, keptPercent


def Reconstruct(sessionDirs: list[Path], outDir: Path, params: PreprocessParams = PreprocessParams(), grid: GridSpec | None = None) -> ReconstructionReport:
    start = time.monotonic()
    volume, keptPercent = FuseViews(IterateViews(sessionDirs), params, grid or GridSpec.Default())
    fusionSeconds = time.monotonic() - start
    if not keptPercent:
        raise ValueError("the sessions contain no views")
    lods = BuildLods(volume)
    outDir.mkdir(parents=True, exist_ok=True)
    lodReports = {}
    for name, lod in lods.items():
        glb = lod.ToGlb()
        (outDir / f"lod-{name}.glb").write_bytes(glb)
        lodReports[name] = LodReport(len(lod.mesh.faces), LOD_BUDGETS[name], ComponentCount(lod.mesh), len(glb), len(lod.ToCompressedGlb()))
    report = ReconstructionReport(len(keptPercent), keptPercent, asdict(params), fusionSeconds, lodReports)
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


def PrintReport(report: ReconstructionReport, outDir: Path) -> None:
    lowest = min(report.keptPercentPerView)
    print(f"{report.viewCount} views fused in {report.fusionSeconds:.1f} s; depth kept per view: min {lowest:.0f} %, median {np.median(report.keptPercentPerView):.0f} %")
    if lowest < MIN_KEPT_PERCENT_WARNING:
        print(f"warning: some views keep under {MIN_KEPT_PERCENT_WARNING:.0f} % of their depth; check the crop radius and depth range")
    for name, lod in report.lods.items():
        status = "OK" if lod.passesGate else "FAIL"
        print(f"lod-{name}: {lod.faces} faces (budget {lod.budget}), {lod.components} component(s), GLB {lod.glbBytes / 1024:.0f} KiB, zstd {lod.compressedGlbBytes / 1024:.0f} KiB  {status}")
    print(f"written to {outDir}")


def Main() -> None:
    args = ParseArgs()
    outDir = args.out or args.sessions[0] / "model"
    try:
        report = Reconstruct(args.sessions, outDir, ParamsFromArgs(args))
    except ValueError as error:
        sys.exit(f"reconstruction failed: {error}")
    PrintReport(report, outDir)
    sys.exit(0 if report.passesGate else 1)


if __name__ == "__main__":
    Main()
