#include "frame-stats.h"

#include <stdio.h>
#include <stdlib.h>

static int CompareFloats(const void *a, const void *b) {
    float left = *(const float *)a, right = *(const float *)b;
    return (left > right) - (left < right);
}

void RecordFrame(FrameStats *stats, float frameTimeS) {
    if (stats->count < FRAME_STATS_CAPACITY) stats->frameTimesS[stats->count++] = frameTimeS;
    stats->totalS += frameTimeS;
}

void PrintFrameStats(FrameStats *stats, int triangles, int width, int height) {
    if (stats->count == 0) {
        printf("benchmark: no frames rendered\n");
        return;
    }
    qsort(stats->frameTimesS, stats->count, sizeof(float), CompareFloats);
    int percentile99 = (int)(0.99 * (stats->count - 1));
    float slowest = stats->frameTimesS[stats->count - 1];
    float low1 = stats->frameTimesS[percentile99];
    printf("benchmark: %d frames in %.2f s, %dx%d, %d triangles\n", stats->count, stats->totalS, width, height,
           triangles);
    printf("benchmark: average %.1f fps, 1%% low %.1f fps, worst frame %.1f ms\n", stats->count / stats->totalS,
           low1 > 0.0f ? 1.0f / low1 : 0.0f, slowest * 1000.0f);
}
