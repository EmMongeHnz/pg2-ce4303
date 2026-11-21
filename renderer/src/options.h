/* Command-line options of the renderer. */
#ifndef RENDERER_OPTIONS_H
#define RENDERER_OPTIONS_H

#include <stdbool.h>

typedef enum { MODE_VIEWER, MODE_HOLOGRAM } RenderMode;
typedef enum { LOD_HIGH, LOD_LOW } LevelOfDetail;
typedef enum { MIRROR_NONE, MIRROR_HORIZONTAL, MIRROR_VERTICAL } MirrorAxis;

typedef struct {
    RenderMode mode;
    LevelOfDetail lod;
    MirrorAxis mirror;
    const char *modelPath;
    int width;
    int height;
    float autoRotateDegPerS;
    float benchmarkSeconds;
    const char *screenshotPath;
} RendererOptions;

/* Returns false and prints usage when the arguments are invalid or --help was given. */
bool ParseOptions(int argc, char **argv, RendererOptions *options);
const char *DefaultModelPath(LevelOfDetail lod);

#endif
