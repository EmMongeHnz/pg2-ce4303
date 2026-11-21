/* Vertex-colour Lambert shading with a headlight, for GLSL 330 (PC) and GLSL ES 100 (Pi). */
#ifndef RENDERER_SHADING_H
#define RENDERER_SHADING_H

#include "raylib.h"

typedef struct {
    Shader shader;
    int lightDirLoc;
    int ambientLoc;
} Shading;

Shading LoadShading(float ambient);
void UpdateHeadlight(Shading *shading, Camera3D camera);
void UnloadShading(Shading shading);

#endif
