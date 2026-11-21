/* Shader sources and uniforms; raylib binds mvp, matNormal and colDiffuse by name. */
#include "shading.h"

#include "raymath.h"

#ifdef RENDERER_GLES2
#define GLSL_HEADER "#version 100\nprecision mediump float;\n#define IN_VERTEX attribute\n#define VARYING_OUT varying\n#define VARYING_IN varying\n#define FRAG_COLOR gl_FragColor\n"
#define FRAG_OUTPUT_DECLARATION ""
#else
#define GLSL_HEADER "#version 330\n#define IN_VERTEX in\n#define VARYING_OUT out\n#define VARYING_IN in\n#define FRAG_COLOR finalColor\n"
#define FRAG_OUTPUT_DECLARATION "out vec4 finalColor;\n"
#endif

static const char *VERTEX_SHADER = GLSL_HEADER
    "IN_VERTEX vec3 vertexPosition;\n"
    "IN_VERTEX vec3 vertexNormal;\n"
    "IN_VERTEX vec4 vertexColor;\n"
    "uniform mat4 mvp;\n"
    "uniform mat4 matNormal;\n"
    "VARYING_OUT vec3 fragNormal;\n"
    "VARYING_OUT vec4 fragColor;\n"
    "void main() {\n"
    "    fragNormal = (matNormal * vec4(vertexNormal, 0.0)).xyz;\n"
    "    fragColor = vertexColor;\n"
    "    gl_Position = mvp * vec4(vertexPosition, 1.0);\n"
    "}\n";

static const char *FRAGMENT_SHADER = GLSL_HEADER FRAG_OUTPUT_DECLARATION
    "VARYING_IN vec3 fragNormal;\n"
    "VARYING_IN vec4 fragColor;\n"
    "uniform vec4 colDiffuse;\n"
    "uniform vec3 lightDir;\n"
    "uniform float ambient;\n"
    "void main() {\n"
    "    float diffuse = max(dot(normalize(fragNormal), -lightDir), 0.0);\n"
    "    vec3 color = fragColor.rgb * colDiffuse.rgb * (ambient + (1.0 - ambient) * diffuse);\n"
    "    FRAG_COLOR = vec4(color, 1.0);\n"
    "}\n";

Shading LoadShading(float ambient) {
    Shading shading = {.shader = LoadShaderFromMemory(VERTEX_SHADER, FRAGMENT_SHADER)};
    shading.lightDirLoc = GetShaderLocation(shading.shader, "lightDir");
    shading.ambientLoc = GetShaderLocation(shading.shader, "ambient");
    SetShaderValue(shading.shader, shading.ambientLoc, &ambient, SHADER_UNIFORM_FLOAT);
    return shading;
}

void UpdateHeadlight(Shading *shading, Camera3D camera) {
    Vector3 forward = Vector3Normalize(Vector3Subtract(camera.target, camera.position));
    Vector3 right = Vector3Normalize(Vector3CrossProduct(forward, camera.up));
    Vector3 up = Vector3CrossProduct(right, forward);
    // The light sits above-left of the viewer so silhouettes keep some relief.
    Vector3 lightDir = Vector3Normalize(Vector3Add(forward, Vector3Add(Vector3Scale(right, 0.4f), Vector3Scale(up, -0.5f))));
    SetShaderValue(shading->shader, shading->lightDirLoc, &lightDir, SHADER_UNIFORM_VEC3);
}

void UnloadShading(Shading shading) {
    UnloadShader(shading.shader);
}
