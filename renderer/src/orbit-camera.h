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
OrbitState ApplyLocalInput(OrbitState state, float deltaS, bool allowMouse);

#endif
