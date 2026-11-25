/*
 * AstreaView renderer (spike 0c prototype).
 *
 *   renderer --mode viewer   --model scan.glb            # PC window, mouse orbit
 *   renderer --mode viewer   --model lod-full.glb --up-axis z --rotate-z 90   # astrea-reconstruct output
 *   renderer --mode hologram --lod low                   # Pi: black, mirrored, auto-rotate
 *   renderer --mode hologram --model test.glb --benchmark 20
 *   renderer --mode viewer   --control-socket /run/astrea/viewer.sock --models-dir models   # driven by the host
 *
 * Keys: A/D = yaw, W/S = zoom, R = auto-rotation on/off, N = next model, mouse drag = yaw, Esc = quit.
 */
#include <stdio.h>
#include <string.h>

#include "control-link.h"
#include "frame-stats.h"
#include "options.h"
#include "orbit-camera.h"
#include "raylib.h"
#include "raymath.h"
#include "shading.h"

#define VIEWER_AMBIENT 0.35f
#define HOLOGRAM_AMBIENT 0.55f
#define SCREENSHOT_WARMUP_FRAMES 5
#define VIEWER_BACKGROUND (Color){30, 32, 38, 255}
#define CONNECT_RETRY_S 0.5

typedef struct {
    Model model;
    int triangles;
} LoadedModel;

static int CountTriangles(Model model) {
    int triangles = 0;
    for (int i = 0; i < model.meshCount; i++) triangles += model.meshes[i].triangleCount;
    return triangles;
}

/* Spin about the model's own z axis first, then stand a z-up model on the viewer's y axis. */
static Matrix ModelOrientation(const RendererOptions *options) {
    Matrix orientation = MatrixRotateZ(options->rotateZDeg * DEG2RAD);
    if (options->upAxis == UP_AXIS_Z) orientation = MatrixMultiply(orientation, MatrixRotateX(-90.0f * DEG2RAD));
    return orientation;
}

/* Centres the model and scales it to a unit bounding sphere so the camera works for any scan. */
static void NormaliseModel(Model *model, const RendererOptions *options) {
    BoundingBox box = GetModelBoundingBox(*model);
    Vector3 center = Vector3Scale(Vector3Add(box.min, box.max), 0.5f);
    float radius = Vector3Length(Vector3Subtract(box.max, center));
    float scale = radius > 0.0f ? 1.0f / radius : 1.0f;
    Matrix centred = MatrixMultiply(MatrixTranslate(-center.x, -center.y, -center.z), ModelOrientation(options));
    model->transform = MatrixMultiply(centred, MatrixScale(scale, scale, scale));
}

static bool LoadModelFile(const char *path, const RendererOptions *options, Shader shader, LoadedModel *loaded) {
    if (!FileExists(path)) {
        TraceLog(LOG_ERROR, "RENDERER: model not found: %s", path);
        return false;
    }
    loaded->model = LoadModel(path);
    loaded->triangles = CountTriangles(loaded->model);
    if (loaded->model.meshCount == 0 || loaded->triangles == 0) {
        TraceLog(LOG_ERROR, "RENDERER: model has no triangles: %s", path);
        UnloadModel(loaded->model);
        return false;
    }
    for (int i = 0; i < loaded->model.materialCount; i++) loaded->model.materials[i].shader = shader;
    NormaliseModel(&loaded->model, options);
    TraceLog(LOG_INFO, "RENDERER: loaded %s (%d meshes, %d triangles)", path, loaded->model.meshCount, loaded->triangles);
    return true;
}

static bool LoadScene(const RendererOptions *options, Shader shader, LoadedModel *loaded) {
    return LoadModelFile(options->modelPath, options, shader, loaded);
}

/* The id comes from the host, so it can only name a file inside the models directory. */
static bool ModelIdIsSafe(const char *modelId) {
    if (modelId[0] == '\0' || modelId[0] == '.') return false;
    for (const char *c = modelId; *c != '\0'; c++) {
        bool allowed = (*c >= 'a' && *c <= 'z') || (*c >= 'A' && *c <= 'Z') || (*c >= '0' && *c <= '9') || *c == '-' || *c == '_' || *c == '.';
        if (!allowed) return false;
    }
    return true;
}

typedef struct {
    char initial[CONTROL_MODEL_ID_BYTES];
    char current[CONTROL_MODEL_ID_BYTES];
    char rejected[CONTROL_MODEL_ID_BYTES];
} ModelSelection;

static void SwitchModel(const RendererOptions *options, Shader shader, ModelSelection *selection, const char *modelId, LoadedModel *loaded) {
    if (modelId[0] == '\0' || strcmp(modelId, selection->current) == 0) return;
    char path[512];
    /* The model given with --model may live outside the models directory, so it is reloaded from where it came. */
    bool isInitial = strcmp(modelId, selection->initial) == 0;
    bool loadable = isInitial || (options->modelsDir != NULL && ModelIdIsSafe(modelId));
    if (isInitial) snprintf(path, sizeof(path), "%s", options->modelPath);
    else if (loadable) snprintf(path, sizeof(path), "%s/%s.glb", options->modelsDir, modelId);
    LoadedModel next = {0};
    if (loadable && FileExists(path) && LoadModelFile(path, options, shader, &next)) {
        UnloadModel(loaded->model);
        *loaded = next;
        strcpy(selection->current, modelId);
        selection->rejected[0] = '\0';
    } else if (strcmp(modelId, selection->rejected) != 0) {
        TraceLog(LOG_WARNING, "RENDERER: cannot show model '%s', keeping the current one", modelId);
        strcpy(selection->rejected, modelId);
    }
}

static void SendInput(ControlLink *link, uint8_t kind, uint8_t action, float value) {
    ControlInput input = {.kind = kind, .action = action, .value = value};
    ControlLinkSend(link, &input);
}

static void SendHeldChange(ControlLink *link, bool now, bool before, uint8_t action) {
    if (now != before) SendInput(link, now ? CONTROL_KIND_ACTION_PRESS : CONTROL_KIND_ACTION_RELEASE, action, 0.0f);
}

static void ReportInput(ControlLink *link, const CameraInput *now, const CameraInput *before) {
    SendHeldChange(link, now->yawLeft, before->yawLeft, CONTROL_ACTION_YAW_LEFT);
    SendHeldChange(link, now->yawRight, before->yawRight, CONTROL_ACTION_YAW_RIGHT);
    SendHeldChange(link, now->zoomIn, before->zoomIn, CONTROL_ACTION_ZOOM_IN);
    SendHeldChange(link, now->zoomOut, before->zoomOut, CONTROL_ACTION_ZOOM_OUT);
    if (now->toggleAutoRotate) SendInput(link, CONTROL_KIND_ACTION_PRESS, CONTROL_ACTION_TOGGLE_AUTO_ROTATE, 0.0f);
    if (now->nextModel) SendInput(link, CONTROL_KIND_ACTION_PRESS, CONTROL_ACTION_NEXT_MODEL, 0.0f);
    if (now->mouseDeltaX != 0.0f) SendInput(link, CONTROL_KIND_MOUSE_DELTA, 0, now->mouseDeltaX);
}

static Rectangle MirroredSource(RenderTexture2D target, MirrorAxis mirror) {
    float width = (float)target.texture.width;
    float height = (float)target.texture.height;
    // Render textures are stored bottom-up, so "no mirror" already needs a negative height.
    Rectangle source = {0.0f, 0.0f, width, -height};
    if (mirror == MIRROR_HORIZONTAL) source.width = -width;
    if (mirror == MIRROR_VERTICAL) source.height = height;
    return source;
}

static void DrawFrame(const RendererOptions *options, LoadedModel *loaded, Camera3D camera, RenderTexture2D target) {
    Color background = options->mode == MODE_HOLOGRAM ? BLACK : VIEWER_BACKGROUND;
    BeginTextureMode(target);
    ClearBackground(background);
    BeginMode3D(camera);
    DrawModel(loaded->model, Vector3Zero(), 1.0f, WHITE);
    EndMode3D();
    EndTextureMode();

    BeginDrawing();
    ClearBackground(BLACK);
    Rectangle destination = {0.0f, 0.0f, (float)GetScreenWidth(), (float)GetScreenHeight()};
    DrawTexturePro(target.texture, MirroredSource(target, options->mirror), destination, Vector2Zero(), 0.0f, WHITE);
    if (options->mode == MODE_VIEWER) {
        DrawText(TextFormat("%d tris  %d fps", loaded->triangles, GetFPS()), 10, 10, 20, RAYWHITE);
    }
    EndDrawing();
}

static void SaveScreenshot(const char *path) {
    Image image = LoadImageFromScreen();
    ExportImage(image, path);
    UnloadImage(image);
}

int main(int argc, char **argv) {
    RendererOptions options;
    if (!ParseOptions(argc, argv, &options)) return 2;

    unsigned int flags = 0;
    if (options.benchmarkSeconds <= 0.0f) flags |= FLAG_VSYNC_HINT;
    if (options.mode == MODE_VIEWER) flags |= FLAG_WINDOW_RESIZABLE;
    SetConfigFlags(flags);
    InitWindow(options.width, options.height, "AstreaView");
    SetTargetFPS(options.benchmarkSeconds > 0.0f ? 0 : 60);
    if (options.mode == MODE_VIEWER) SetExitKey(KEY_ESCAPE);

    Shading shading = LoadShading(options.mode == MODE_HOLOGRAM ? HOLOGRAM_AMBIENT : VIEWER_AMBIENT);
    LoadedModel loaded = {0};
    if (!LoadScene(&options, shading.shader, &loaded)) {
        UnloadShading(shading);
        CloseWindow();
        return 1;
    }

    RenderTexture2D target = LoadRenderTexture(GetScreenWidth(), GetScreenHeight());
    static FrameStats stats;
    OrbitState orbit = DefaultOrbitState();
    bool controlled = options.controlSocketPath != NULL;
    bool autoRotating = options.autoRotateOn;
    ControlLink link;
    ControlLinkInit(&link);
    ModelSelection selection = {0};
    strncpy(selection.initial, GetFileNameWithoutExt(options.modelPath), CONTROL_MODEL_ID_BYTES - 1);
    strcpy(selection.current, selection.initial);
    CameraInput previousInput = {0};
    double nextConnectS = 0.0;
    int frame = 0;
    while (!WindowShouldClose()) {
        float deltaS = GetFrameTime();
        if (IsWindowResized()) {
            UnloadRenderTexture(target);
            target = LoadRenderTexture(GetScreenWidth(), GetScreenHeight());
        }
        if (controlled) {
            if (!ControlLinkIsConnected(&link) && GetTime() >= nextConnectS) {
                if (ControlLinkTryConnect(&link, options.controlSocketPath)) previousInput = (CameraInput){0};
                nextConnectS = GetTime() + CONNECT_RETRY_S;
            }
            ControlState state;
            if (ControlLinkReceive(&link, &state)) {
                orbit = ClampOrbitState((OrbitState){.yawDeg = state.yawDeg, .pitchDeg = state.pitchDeg, .zoom = state.zoom});
                SwitchModel(&options, shading.shader, &selection, state.modelId, &loaded);
            }
            if (options.mode == MODE_VIEWER) {
                CameraInput input = PollCameraInput(true);
                ReportInput(&link, &input, &previousInput);
                previousInput = input;
            }
        } else {
            CameraInput input = PollCameraInput(options.mode == MODE_VIEWER);
            if (input.toggleAutoRotate) autoRotating = !autoRotating;
            if (autoRotating) orbit.yawDeg += options.autoRotateDegPerS * deltaS;
            orbit = ApplyCameraInput(orbit, &input, deltaS);
        }
        Camera3D camera = OrbitCamera(orbit);
        UpdateHeadlight(&shading, camera);
        DrawFrame(&options, &loaded, camera, target);
        frame++;

        if (options.benchmarkSeconds > 0.0f && frame > 1) {
            RecordFrame(&stats, deltaS);
            if (stats.totalS >= options.benchmarkSeconds) break;
        }
        if (options.screenshotPath != NULL && frame == SCREENSHOT_WARMUP_FRAMES) {
            SaveScreenshot(options.screenshotPath);
            break;
        }
    }
    if (options.benchmarkSeconds > 0.0f) {
        PrintFrameStats(&stats, loaded.triangles, GetScreenWidth(), GetScreenHeight());
    }

    ControlLinkClose(&link);
    UnloadRenderTexture(target);
    UnloadModel(loaded.model);
    UnloadShading(shading);
    CloseWindow();
    return 0;
}
