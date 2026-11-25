#include "holo.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define PENDING_RECORDS 16

struct HoloDevice {
    int fd;
    size_t pendingBytes;
    unsigned char pending[PENDING_RECORDS * sizeof(struct HoloEvent)];
};

HoloStatus HoloOpen(const char *path, HoloDevice **device) {
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return HOLO_ERROR;
    HoloDevice *opened = calloc(1, sizeof(*opened));
    if (opened == NULL) {
        close(fd);
        errno = ENOMEM;
        return HOLO_ERROR;
    }
    opened->fd = fd;
    *device = opened;
    return HOLO_OK;
}

void HoloClose(HoloDevice *device) {
    if (device == NULL) return;
    close(device->fd);
    free(device);
}

int HoloFileDescriptor(const HoloDevice *device) {
    return device->fd;
}

static long long NowMs(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (long long)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static bool TakePendingEvent(HoloDevice *device, struct HoloEvent *event) {
    if (device->pendingBytes < sizeof(*event)) return false;
    memcpy(event, device->pending, sizeof(*event));
    device->pendingBytes -= sizeof(*event);
    memmove(device->pending, device->pending + sizeof(*event), device->pendingBytes);
    return true;
}

HoloStatus HoloReadEvent(HoloDevice *device, int timeoutMs, struct HoloEvent *event) {
    long long deadline = timeoutMs < 0 ? 0 : NowMs() + timeoutMs;
    for (;;) {
        if (TakePendingEvent(device, event)) return HOLO_OK;
        int waitMs = -1;
        if (timeoutMs >= 0) {
            long long remaining = deadline - NowMs();
            waitMs = remaining > 0 ? (int)remaining : 0;
        }
        struct pollfd wait = {device->fd, POLLIN, 0};
        int ready = poll(&wait, 1, waitMs);
        if (ready < 0) return errno == EINTR ? HOLO_INTERRUPTED : HOLO_ERROR;
        if (ready == 0) return HOLO_TIMEOUT;
        ssize_t received = read(device->fd, device->pending + device->pendingBytes, sizeof(device->pending) - device->pendingBytes);
        if (received > 0) {
            device->pendingBytes += (size_t)received;
            continue;
        }
        if (received == 0) return HOLO_CLOSED;
        if (errno == EAGAIN || errno == EWOULDBLOCK) continue;
        return errno == EINTR ? HOLO_INTERRUPTED : HOLO_ERROR;
    }
}

static HoloStatus GetTiming(const HoloDevice *device, unsigned long request, unsigned *milliseconds) {
    __u32 value = 0;
    if (ioctl(device->fd, request, &value) < 0) return HOLO_ERROR;
    *milliseconds = value;
    return HOLO_OK;
}

static HoloStatus SetTiming(const HoloDevice *device, unsigned long request, unsigned milliseconds) {
    __u32 value = milliseconds;
    return ioctl(device->fd, request, &value) < 0 ? HOLO_ERROR : HOLO_OK;
}

HoloStatus HoloGetDebounceMs(const HoloDevice *device, unsigned *milliseconds) { return GetTiming(device, HOLO_IOC_GET_DEBOUNCE_MS, milliseconds); }
HoloStatus HoloSetDebounceMs(const HoloDevice *device, unsigned milliseconds) { return SetTiming(device, HOLO_IOC_SET_DEBOUNCE_MS, milliseconds); }
HoloStatus HoloGetRepeatMs(const HoloDevice *device, unsigned *milliseconds) { return GetTiming(device, HOLO_IOC_GET_REPEAT_MS, milliseconds); }
HoloStatus HoloSetRepeatMs(const HoloDevice *device, unsigned milliseconds) { return SetTiming(device, HOLO_IOC_SET_REPEAT_MS, milliseconds); }

static HoloStatus ReadCounter(const char *directory, const char *name, unsigned long *value) {
    char path[512], text[64];
    snprintf(path, sizeof(path), "%s/%s", directory, name);
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return HOLO_ERROR;
    ssize_t length = read(fd, text, sizeof(text) - 1);
    int savedErrno = errno;
    close(fd);
    if (length <= 0) {
        errno = length < 0 ? savedErrno : EIO;
        return HOLO_ERROR;
    }
    text[length] = '\0';
    char *end;
    errno = 0;
    unsigned long parsed = strtoul(text, &end, 10);
    if (end == text || errno != 0) {
        errno = EINVAL;
        return HOLO_ERROR;
    }
    *value = parsed;
    return HOLO_OK;
}

HoloStatus HoloReadStats(const char *directory, HoloStats *stats) {
    if (directory == NULL) directory = HOLO_DEFAULT_STATS_DIRECTORY;
    const struct {
        const char *name;
        unsigned long *target;
    } counters[] = {
        {"irqs", &stats->irqs},           {"bounces_filtered", &stats->bouncesFiltered}, {"presses", &stats->presses},
        {"releases", &stats->releases},   {"repeats", &stats->repeats},                  {"events", &stats->events},
        {"dropped", &stats->dropped},     {"debounce_ms", &stats->debounceMs},           {"repeat_ms", &stats->repeatMs},
    };
    memset(stats, 0, sizeof(*stats));
    for (size_t i = 0; i < sizeof(counters) / sizeof(counters[0]); i++)
        if (ReadCounter(directory, counters[i].name, counters[i].target) != HOLO_OK) return HOLO_ERROR;
    return HOLO_OK;
}

bool HoloActionOf(const struct HoloEvent *event, HoloAction *action, HoloPhase *phase) {
    if (event->source != HOLO_SOURCE_JOYSTICK) return false;
    switch (event->code) {
        case HOLO_CODE_BTN_RIGHT: *action = HOLO_MOVE_RIGHT; break;
        case HOLO_CODE_BTN_LEFT: *action = HOLO_MOVE_LEFT; break;
        case HOLO_CODE_BTN_ZOOM_IN: *action = HOLO_ZOOM_IN; break;
        case HOLO_CODE_BTN_ZOOM_OUT: *action = HOLO_ZOOM_OUT; break;
        case HOLO_CODE_BTN_AUTOROTATE: *action = HOLO_TOGGLE_AUTO_ROTATE; break;
        default: return false;
    }
    switch (event->value) {
        case HOLO_VALUE_RELEASE: *phase = HOLO_PHASE_RELEASED; return true;
        case HOLO_VALUE_PRESS: *phase = HOLO_PHASE_PRESSED; return true;
        case HOLO_VALUE_REPEAT: *phase = HOLO_PHASE_HELD; return true;
        default: return false;
    }
}

const char *HoloButtonName(unsigned code) {
    switch (code) {
        case HOLO_CODE_BTN_RIGHT: return "right";
        case HOLO_CODE_BTN_LEFT: return "left";
        case HOLO_CODE_BTN_ZOOM_IN: return "zoom-in";
        case HOLO_CODE_BTN_ZOOM_OUT: return "zoom-out";
        case HOLO_CODE_BTN_AUTOROTATE: return "autorotate";
        default: return "?";
    }
}

const char *HoloValueName(int value) {
    switch (value) {
        case HOLO_VALUE_RELEASE: return "release";
        case HOLO_VALUE_PRESS: return "press";
        case HOLO_VALUE_REPEAT: return "repeat";
        default: return "?";
    }
}

const char *HoloActionName(HoloAction action) {
    switch (action) {
        case HOLO_MOVE_RIGHT: return "MoveRight";
        case HOLO_MOVE_LEFT: return "MoveLeft";
        case HOLO_ZOOM_IN: return "ZoomIn";
        case HOLO_ZOOM_OUT: return "ZoomOut";
        case HOLO_TOGGLE_AUTO_ROTATE: return "ToggleAutoRotate";
        default: return "?";
    }
}

const char *HoloStatusText(HoloStatus status) {
    switch (status) {
        case HOLO_OK: return "ok";
        case HOLO_TIMEOUT: return "timed out";
        case HOLO_INTERRUPTED: return "interrupted by a signal";
        case HOLO_CLOSED: return "the device was closed";
        default: return "error";
    }
}
