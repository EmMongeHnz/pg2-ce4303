/* Minimal logging shared by the C network components: warnings always, information only with --verbose. */
#ifndef ASTREA_LOG_H
#define ASTREA_LOG_H

#include <stdbool.h>

extern bool gLogVerbose;

void LogWrite(const char *level, const char *format, ...) __attribute__((format(printf, 2, 3)));

#define LOG_INFO(...) do { if (gLogVerbose) LogWrite("INFO", __VA_ARGS__); } while (0)
#define LOG_WARN(...) LogWrite("WARNING", __VA_ARGS__)

#endif
