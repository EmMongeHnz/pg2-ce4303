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
#define KEY_PITCH_DEG_PER_S 60.0f
#define KEY_ZOOM_PER_S 1.0f
#define MOUSE_DEG_PER_PX 0.3f
#define WHEEL_ZOOM_STEP 0.1f

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

OrbitState ApplyLocalInput(OrbitState state, float deltaS, bool allowMouse) {
    if (IsKeyDown(KEY_LEFT)) state.yawDeg -= KEY_YAW_DEG_PER_S * deltaS;
    if (IsKeyDown(KEY_RIGHT)) state.yawDeg += KEY_YAW_DEG_PER_S * deltaS;
    if (IsKeyDown(KEY_UP)) state.pitchDeg += KEY_PITCH_DEG_PER_S * deltaS;
    if (IsKeyDown(KEY_DOWN)) state.pitchDeg -= KEY_PITCH_DEG_PER_S * deltaS;
    if (IsKeyDown(KEY_EQUAL) || IsKeyDown(KEY_KP_ADD)) state.zoom *= 1.0f + KEY_ZOOM_PER_S * deltaS;
    if (IsKeyDown(KEY_MINUS) || IsKeyDown(KEY_KP_SUBTRACT)) state.zoom /= 1.0f + KEY_ZOOM_PER_S * deltaS;
    if (IsKeyPressed(KEY_R)) state = DefaultOrbitState();
    if (allowMouse) {
        if (IsMouseButtonDown(MOUSE_BUTTON_LEFT)) {
            Vector2 delta = GetMouseDelta();
            state.yawDeg -= delta.x * MOUSE_DEG_PER_PX;
            state.pitchDeg += delta.y * MOUSE_DEG_PER_PX;
        }
        state.zoom *= 1.0f + GetMouseWheelMove() * WHEEL_ZOOM_STEP;
    }
    return ClampOrbitState(state);
}
