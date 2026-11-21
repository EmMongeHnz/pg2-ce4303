/*
 * AstreaView renderer (spike 0c prototype).
 *
 *   renderer --mode viewer   --model scan.glb            # PC window, mouse orbit
 *   renderer --mode hologram --lod low                   # Pi: black, mirrored, auto-rotate
 *   renderer --mode hologram --model test.glb --benchmark 20
 *
 * Keys: arrows = yaw/pitch, +/- = zoom, R = reset, Esc = quit.
 */
#include <stdio.h>

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

typedef struct {
    Model model;
    int triangles;
} LoadedModel;

static int CountTriangles(Model model) {
    int triangles = 0;
    for (int i = 0; i < model.meshCount; i++) triangles += model.meshes[i].triangleCount;
    return triangles;
}

/* Centres the model and scales it to a unit bounding sphere so the camera works for any scan. */
static void NormaliseModel(Model *model) {
    BoundingBox box = GetModelBoundingBox(*model);
    Vector3 center = Vector3Scale(Vector3Add(box.min, box.max), 0.5f);
    float radius = Vector3Length(Vector3Subtract(box.max, center));
    float scale = radius > 0.0f ? 1.0f / radius : 1.0f;
    model->transform = MatrixMultiply(MatrixTranslate(-center.x, -center.y, -center.z), MatrixScale(scale, scale, scale));
}

static bool LoadScene(const char *path, Shader shader, LoadedModel *loaded) {
    if (!FileExists(path)) {
        TraceLog(LOG_ERROR, "RENDERER: model not found: %s", path);
        return false;
    }
    loaded->model = LoadModel(path);
    loaded->triangles = CountTriangles(loaded->model);
    if (loaded->model.meshCount == 0 || loaded->triangles == 0) {
        TraceLog(LOG_ERROR, "RENDERER: model has no triangles: %s", path);
        return false;
    }
    for (int i = 0; i < loaded->model.materialCount; i++) loaded->model.materials[i].shader = shader;
    NormaliseModel(&loaded->model);
    TraceLog(LOG_INFO, "RENDERER: loaded %s (%d meshes, %d triangles)", path, loaded->model.meshCount, loaded->triangles);
    return true;
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
    if (!LoadScene(options.modelPath, shading.shader, &loaded)) {
        UnloadShading(shading);
        CloseWindow();
        return 1;
    }

    RenderTexture2D target = LoadRenderTexture(GetScreenWidth(), GetScreenHeight());
    static FrameStats stats;
    OrbitState orbit = DefaultOrbitState();
    int frame = 0;
    while (!WindowShouldClose()) {
        float deltaS = GetFrameTime();
        if (IsWindowResized()) {
            UnloadRenderTexture(target);
            target = LoadRenderTexture(GetScreenWidth(), GetScreenHeight());
        }
        orbit.yawDeg += options.autoRotateDegPerS * deltaS;
        orbit = ApplyLocalInput(orbit, deltaS, options.mode == MODE_VIEWER);
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

    UnloadRenderTexture(target);
    UnloadModel(loaded.model);
    UnloadShading(shading);
    CloseWindow();
    return 0;
}
