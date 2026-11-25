#include "astrea-log.h"

#include <stdarg.h>
#include <stdio.h>
#include <time.h>

bool gLogVerbose = false;

void LogWrite(const char *level, const char *format, ...) {
    char stamp[32];
    time_t now = time(NULL);
    struct tm local;
    localtime_r(&now, &local);
    strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &local);
    char line[1024];
    va_list args;
    va_start(args, format);
    vsnprintf(line, sizeof(line), format, args);
    va_end(args);
    fprintf(stderr, "%s %s astrea.net %s\n", stamp, level, line);
    fflush(stderr);
}
