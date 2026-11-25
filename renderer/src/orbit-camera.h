/* Orbital camera driven by yaw, pitch and zoom: the same fields as CameraState on the wire. */
#ifndef RENDERER_ORBIT_CAMERA_H
#define RENDERER_ORBIT_CAMERA_H

#include "raylib.h"

typedef struct {
    float yawDeg;
    float pitchDeg;
    float zoom;
} OrbitState;

OrbitState DefaultOrbitState(void);
OrbitState ClampOrbitState(OrbitState state);
Camera3D OrbitCamera(OrbitState state);

/* Raw input of one frame: A/D yaw, W/S zoom, R toggles the automatic rotation, N picks the next model. */
typedef struct {
    bool yawLeft;
    bool yawRight;
    bool zoomIn;
    bool zoomOut;
    bool toggleAutoRotate;
    bool nextModel;
    float mouseDeltaX;
} CameraInput;

CameraInput PollCameraInput(bool allowMouse);
OrbitState ApplyCameraInput(OrbitState state, const CameraInput *input, float deltaS);

#endif
