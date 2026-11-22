/* Parsing of `renderer --mode viewer|hologram --lod high|low ...`. */
#include "options.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VIEWER_WIDTH 1280
#define VIEWER_HEIGHT 720
#define HOLOGRAM_AUTO_ROTATE_DEG_PER_S 20.0f

static void PrintUsage(const char *program) {
    fprintf(stderr,
            "usage: %s [--mode viewer|hologram] [--lod high|low] [--model file.glb]\n"
            "          [--mirror none|horizontal|vertical] [--width px] [--height px]\n"
            "          [--auto-rotate deg-per-s] [--benchmark seconds] [--screenshot file.png]\n"
            "          [--up-axis y|z] [--rotate-z degrees]\n"
            "\n"
            "  --mode        viewer: PC window with mouse orbit; hologram: black background,\n"
            "                bright lighting, mirrored for the Pepper's Ghost panel\n"
            "  --lod         picks models/lod-high.glb or models/lod-low.glb when --model is omitted\n"
            "  --width/--height  0 = native display size (default in hologram mode)\n"
            "  --benchmark   render for N seconds without vsync limits, print fps stats and exit\n"
            "  --screenshot  render a few frames, save a PNG and exit\n"
            "  --up-axis     vertical axis of the model: y (glTF standard, default) or z (astrea-reconstruct\n"
            "                output, plate frame)\n"
            "  --rotate-z    turn the model about its own z axis by this many degrees (the vertical one\n"
            "                with --up-axis z)\n",
            program);
}

static bool ParseFloat(const char *value, float *result) {
    char *end = NULL;
    *result = strtof(value, &end);
    return end != value && *end == '\0' && isfinite(*result);
}

static bool ParseChoice(const char *value, const char *const *names, int count, int *result) {
    for (int i = 0; i < count; i++) {
        if (strcmp(value, names[i]) == 0) {
            *result = i;
            return true;
        }
    }
    return false;
}

const char *DefaultModelPath(LevelOfDetail lod) {
    return lod == LOD_HIGH ? "models/lod-high.glb" : "models/lod-low.glb";
}

bool ParseOptions(int argc, char **argv, RendererOptions *options) {
    static const char *const MODES[] = {"viewer", "hologram"};
    static const char *const LODS[] = {"high", "low"};
    static const char *const MIRRORS[] = {"none", "horizontal", "vertical"};
    static const char *const UP_AXES[] = {"y", "z"};
    int mode = MODE_VIEWER, lod = LOD_HIGH, mirror = -1, upAxis = UP_AXIS_Y;
    float autoRotate = -1.0f, rotateZ = 0.0f;
    int width = -1, height = -1;

    *options = (RendererOptions){0};
    for (int i = 1; i < argc; i++) {
        const char *flag = argv[i];
        const char *value = i + 1 < argc ? argv[i + 1] : NULL;
        bool ok = value != NULL;
        if (strcmp(flag, "--help") == 0 || strcmp(flag, "-h") == 0) {
            ok = false;
        } else if (strcmp(flag, "--mode") == 0) {
            ok = ok && ParseChoice(value, MODES, 2, &mode);
        } else if (strcmp(flag, "--lod") == 0) {
            ok = ok && ParseChoice(value, LODS, 2, &lod);
        } else if (strcmp(flag, "--mirror") == 0) {
            ok = ok && ParseChoice(value, MIRRORS, 3, &mirror);
        } else if (strcmp(flag, "--up-axis") == 0) {
            ok = ok && ParseChoice(value, UP_AXES, 2, &upAxis);
        } else if (strcmp(flag, "--rotate-z") == 0) {
            ok = ok && ParseFloat(value, &rotateZ);
        } else if (strcmp(flag, "--model") == 0) {
            options->modelPath = value;
        } else if (strcmp(flag, "--width") == 0) {
            width = ok ? atoi(value) : 0;
        } else if (strcmp(flag, "--height") == 0) {
            height = ok ? atoi(value) : 0;
        } else if (strcmp(flag, "--auto-rotate") == 0) {
            autoRotate = ok ? (float)atof(value) : 0.0f;
        } else if (strcmp(flag, "--benchmark") == 0) {
            options->benchmarkSeconds = ok ? (float)atof(value) : 0.0f;
            ok = ok && options->benchmarkSeconds > 0.0f;
        } else if (strcmp(flag, "--screenshot") == 0) {
            options->screenshotPath = value;
        } else {
            fprintf(stderr, "unknown option: %s\n", flag);
            ok = false;
        }
        if (!ok) {
            PrintUsage(argv[0]);
            return false;
        }
        i++;
    }

    bool isHologram = mode == MODE_HOLOGRAM;
    options->mode = (RenderMode)mode;
    options->lod = (LevelOfDetail)lod;
    options->upAxis = (UpAxis)upAxis;
    options->rotateZDeg = rotateZ;
    options->mirror = mirror >= 0 ? (MirrorAxis)mirror : (isHologram ? MIRROR_HORIZONTAL : MIRROR_NONE);
    options->width = width >= 0 ? width : (isHologram ? 0 : VIEWER_WIDTH);
    options->height = height >= 0 ? height : (isHologram ? 0 : VIEWER_HEIGHT);
    options->autoRotateDegPerS = autoRotate >= 0.0f ? autoRotate : (isHologram ? HOLOGRAM_AUTO_ROTATE_DEG_PER_S : 0.0f);
    if (options->modelPath == NULL) options->modelPath = DefaultModelPath(options->lod);
    return true;
}
