/* Command-line options of the renderer. */
#ifndef RENDERER_OPTIONS_H
#define RENDERER_OPTIONS_H

#include <stdbool.h>

typedef enum { MODE_VIEWER, MODE_HOLOGRAM } RenderMode;
typedef enum { LOD_HIGH, LOD_LOW } LevelOfDetail;
typedef enum { MIRROR_NONE, MIRROR_HORIZONTAL, MIRROR_VERTICAL } MirrorAxis;
typedef enum { UP_AXIS_Y, UP_AXIS_Z } UpAxis;

typedef struct {
    RenderMode mode;
    LevelOfDetail lod;
    MirrorAxis mirror;
    UpAxis upAxis;
    float rotateZDeg;
    const char *modelPath;
    int width;
    int height;
    float autoRotateDegPerS;
    bool autoRotateOn;
    float benchmarkSeconds;
    const char *screenshotPath;
    const char *controlSocketPath;
    const char *modelsDir;
} RendererOptions;

/* Returns false and prints usage when the arguments are invalid or --help was given. */
bool ParseOptions(int argc, char **argv, RendererOptions *options);
const char *DefaultModelPath(LevelOfDetail lod);

#endif
