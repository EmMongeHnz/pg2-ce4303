/*
 * Records exchanged with the local Python process over the renderer's Unix socket. They use the native byte
 * order because both ends run on the same machine. astrea_common.render_link mirrors this file and
 * tests/unit/test_render_link.py keeps both equal.
 */
#ifndef RENDERER_CONTROL_PROTOCOL_H
#define RENDERER_CONTROL_PROTOCOL_H

#include <stdint.h>

#define CONTROL_MODEL_ID_BYTES 32

#define CONTROL_KIND_ACTION_PRESS 1
#define CONTROL_KIND_ACTION_RELEASE 2
#define CONTROL_KIND_MOUSE_DELTA 3

#define CONTROL_ACTION_YAW_LEFT 1
#define CONTROL_ACTION_YAW_RIGHT 2
#define CONTROL_ACTION_ZOOM_IN 3
#define CONTROL_ACTION_ZOOM_OUT 4
#define CONTROL_ACTION_TOGGLE_AUTO_ROTATE 5
#define CONTROL_ACTION_NEXT_MODEL 6

/* Python to renderer: the camera state to draw. modelId is NUL padded. */
typedef struct {
    float yawDeg;
    float pitchDeg;
    float zoom;
    char modelId[CONTROL_MODEL_ID_BYTES];
} ControlState;

/* Renderer to Python: a raw input. value is the horizontal mouse movement in pixels for CONTROL_KIND_MOUSE_DELTA. */
typedef struct {
    uint8_t kind;
    uint8_t action;
    uint16_t reserved;
    float value;
} ControlInput;

_Static_assert(sizeof(ControlState) == 44, "ControlState must stay 44 bytes without padding");
_Static_assert(sizeof(ControlInput) == 8, "ControlInput must stay 8 bytes without padding");

#endif
