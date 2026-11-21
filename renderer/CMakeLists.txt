# AstreaView renderer: one binary for the PC viewer and the Pi hologram.
#   Desktop: cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j
#   GLES2 on a PC (checks the Pi shaders): add -DOPENGL_VERSION="ES 2.0"
#   Pi 3:    cmake -B build -DCMAKE_BUILD_TYPE=Release -DRENDERER_PLATFORM=DRM && cmake --build build -j
cmake_minimum_required(VERSION 3.16)
project(astrea-renderer C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

set(RENDERER_PLATFORM "Desktop" CACHE STRING "raylib platform: Desktop or DRM")
set_property(CACHE RENDERER_PLATFORM PROPERTY STRINGS Desktop DRM)
set(RAYLIB_VERSION 5.5)

include(FetchContent)
set(PLATFORM ${RENDERER_PLATFORM} CACHE STRING "" FORCE)
set(BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(BUILD_GAMES OFF CACHE BOOL "" FORCE)
set(CUSTOMIZE_BUILD OFF CACHE BOOL "" FORCE)
FetchContent_Declare(
  raylib
  GIT_REPOSITORY https://github.com/raysan5/raylib.git
  GIT_TAG ${RAYLIB_VERSION}
  GIT_SHALLOW TRUE
)
FetchContent_MakeAvailable(raylib)

add_executable(renderer
  src/main.c
  src/options.c
  src/orbit-camera.c
  src/shading.c
  src/frame-stats.c
)
target_link_libraries(renderer PRIVATE raylib m)
target_compile_options(renderer PRIVATE -Wall -Wextra)
if(RENDERER_PLATFORM STREQUAL "DRM" OR OPENGL_VERSION STREQUAL "ES 2.0")
  target_compile_definitions(renderer PRIVATE RENDERER_GLES2)
endif()
