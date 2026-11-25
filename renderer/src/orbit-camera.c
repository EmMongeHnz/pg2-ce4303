/* Orbital camera around a model normalised to a unit bounding sphere at the origin. */
#include "orbit-camera.h"

#include <math.h>

#define DEFAULT_PITCH_DEG 20.0f
#define MIN_PITCH_DEG -80.0f
#define MAX_PITCH_DEG 80.0f
#define MIN_ZOOM 0.4f
#define MAX_ZOOM 3.0f
#define BASE_DISTANCE 3.0f
#define FOV_Y_DEG 35.0f
#define KEY_YAW_DEG_PER_S 90.0f
#define KEY_ZOOM_PER_S 1.0f
#define MOUSE_DEG_PER_PX 0.3f

OrbitState DefaultOrbitState(void) {
    return (OrbitState){.yawDeg = 0.0f, .pitchDeg = DEFAULT_PITCH_DEG, .zoom = 1.0f};
}

OrbitState ClampOrbitState(OrbitState state) {
    state.yawDeg = fmodf(state.yawDeg, 360.0f);
    if (state.yawDeg < 0.0f) state.yawDeg += 360.0f;
    state.pitchDeg = fminf(fmaxf(state.pitchDeg, MIN_PITCH_DEG), MAX_PITCH_DEG);
    state.zoom = fminf(fmaxf(state.zoom, MIN_ZOOM), MAX_ZOOM);
    return state;
}

Camera3D OrbitCamera(OrbitState state) {
    float yaw = state.yawDeg * DEG2RAD;
    float pitch = state.pitchDeg * DEG2RAD;
    float distance = BASE_DISTANCE / state.zoom;
    Vector3 position = {
        distance * cosf(pitch) * sinf(yaw),
        distance * sinf(pitch),
        distance * cosf(pitch) * cosf(yaw),
    };
    return (Camera3D){
        .position = position,
        .target = {0.0f, 0.0f, 0.0f},
        .up = {0.0f, 1.0f, 0.0f},
        .fovy = FOV_Y_DEG,
        .projection = CAMERA_PERSPECTIVE,
    };
}

CameraInput PollCameraInput(bool allowMouse) {
    CameraInput input = {
        .yawLeft = IsKeyDown(KEY_A),
        .yawRight = IsKeyDown(KEY_D),
        .zoomIn = IsKeyDown(KEY_W),
        .zoomOut = IsKeyDown(KEY_S),
        .toggleAutoRotate = IsKeyPressed(KEY_R),
        .nextModel = IsKeyPressed(KEY_N),
    };
    if (allowMouse && IsMouseButtonDown(MOUSE_BUTTON_LEFT)) input.mouseDeltaX = GetMouseDelta().x;
    return input;
}

OrbitState ApplyCameraInput(OrbitState state, const CameraInput *input, float deltaS) {
    int yaw = (int)input->yawRight - (int)input->yawLeft;
    int zoom = (int)input->zoomIn - (int)input->zoomOut;
    state.yawDeg += yaw * KEY_YAW_DEG_PER_S * deltaS - input->mouseDeltaX * MOUSE_DEG_PER_PX;
    state.zoom *= powf(1.0f + KEY_ZOOM_PER_S * deltaS, (float)zoom);
    return ClampOrbitState(state);
}
