/* Frame-time statistics for the --benchmark mode (spike 0c). */
#ifndef RENDERER_FRAME_STATS_H
#define RENDERER_FRAME_STATS_H

#define FRAME_STATS_CAPACITY 65536

typedef struct {
    float frameTimesS[FRAME_STATS_CAPACITY];
    int count;
    double totalS;
} FrameStats;

void RecordFrame(FrameStats *stats, float frameTimeS);
/* Prints average fps, 1 % low fps and the worst frame time. */
void PrintFrameStats(FrameStats *stats, int triangles, int width, int height);

#endif
