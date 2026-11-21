"""Live viewer for the Record3D USB stream: RGB and colour depth side by side.

Usage:
    uv run astrea-stream-viewer              # runs until q in the window, the window is closed, or Ctrl+C
    uv run astrea-stream-viewer --list
    uv run astrea-stream-viewer --seconds 30

Meant to stay open during a whole scan. If the phone stops sending frames the viewer
keeps waiting and resumes by itself; it only exits when the stream is stopped on the phone.
"""

from __future__ import annotations

import argparse
import time
from collections import deque

import cv2

from capture_service.frame_dump import DescribeFrame, LivePreview
from capture_service.record3d_stream import ListDevices, Record3dStream, Record3dUnavailable

WINDOW_NAME = "astrea-stream-viewer"
FPS_WINDOW_FRAMES = 30
IDLE_POLL_MS = 100


def ParseArgs() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true", help="list connected devices and exit")
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--seconds", type=float, default=0.0, help="stop after this long; 0 means no limit")
    return parser.parse_args()


def QuitRequested(waitMs: int) -> bool:
    keyPressed = cv2.waitKey(waitMs) & 0xFF == ord("q")
    windowClosed = cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1
    return keyPressed or windowClosed


def MeasuredFps(timestamps: deque) -> float:
    if len(timestamps) < 2:
        return 0.0
    return (len(timestamps) - 1) / (timestamps[-1] - timestamps[0])


def Expired(start: float, seconds: float) -> bool:
    return seconds > 0 and time.monotonic() - start >= seconds


def Show(stream: Record3dStream, seconds: float) -> int:
    start = time.monotonic()
    arrivals: deque = deque(maxlen=FPS_WINDOW_FRAMES)
    totalFrames = 0
    waiting = False
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_AUTOSIZE)
    while not Expired(start, seconds) and not stream.stopped:
        try:
            frame = stream.Read()
        except TimeoutError:
            if not waiting:
                print("no frames received, is Record3D still streaming? waiting...")
                waiting = True
                arrivals.clear()
            if QuitRequested(IDLE_POLL_MS):
                break
            continue
        except Record3dUnavailable as error:
            print(error)
            break
        waiting = False
        if totalFrames == 0:
            print(DescribeFrame(frame))
        totalFrames += 1
        arrivals.append(time.monotonic())
        cv2.imshow(WINDOW_NAME, LivePreview(frame, MeasuredFps(arrivals)))
        if QuitRequested(1):
            break
    return totalFrames


def Main() -> None:
    args = ParseArgs()
    if args.list:
        for index, (productId, udid) in enumerate(ListDevices()):
            print(f"[{index}] product id {productId}, udid {udid}")
        return
    try:
        with Record3dStream(args.device) as stream:
            print(f"connected, device type: {'TrueDepth' if stream.isTrueDepth else 'LiDAR'}")
            totalFrames = Show(stream, args.seconds)
    except KeyboardInterrupt:
        return
    finally:
        cv2.destroyAllWindows()
    print(f"closed after {totalFrames} frames")


if __name__ == "__main__":
    Main()
