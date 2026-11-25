/*
 * libholo: the user-space library that talks to the holo-io driver. It is the only code that opens /dev/holo-input,
 * issues its ioctls or reads its sysfs statistics; every program (the device agent, the test reader) goes through it.
 *
 *   HoloDevice *device;
 *   HoloOpen("/dev/holo-input", &device);
 *   struct HoloEvent event;
 *   while (HoloReadEvent(device, 200, &event) != HOLO_ERROR) { ... }
 *   HoloClose(device);
 *
 * A HoloDevice is not thread safe: use it from one thread at a time.
 */
#ifndef LIBHOLO_HOLO_H
#define LIBHOLO_HOLO_H

#include <stdbool.h>

#include "holo-event.h"
#include "holo-ioctl.h"

#define HOLO_DEFAULT_DEVICE "/dev/holo-input"
#define HOLO_DEFAULT_STATS_DIRECTORY "/sys/class/misc/holo-input/device/stats"

typedef enum {
    HOLO_OK = 0,
    HOLO_TIMEOUT = 1,     /* nothing arrived within the timeout */
    HOLO_INTERRUPTED = 2, /* a signal arrived while waiting; the caller can check its stop flag and call again */
    HOLO_CLOSED = 3,      /* the device reported end of file (no writer, or it went away) */
    HOLO_ERROR = -1,      /* the system call failed; errno holds the reason */
} HoloStatus;

typedef struct HoloDevice HoloDevice;

/* Opens the device for reading. The driver allows one reader at a time, so a second open fails with EBUSY. */
HoloStatus HoloOpen(const char *path, HoloDevice **device);
void HoloClose(HoloDevice *device);
int HoloFileDescriptor(const HoloDevice *device);

/* Reads one event, waiting up to `timeoutMs` (negative waits forever). Records cut by the kernel are rebuilt. */
HoloStatus HoloReadEvent(HoloDevice *device, int timeoutMs, struct HoloEvent *event);

/* Debounce and repeat times of the driver, in milliseconds; the driver refuses values outside its range (EINVAL). */
HoloStatus HoloGetDebounceMs(const HoloDevice *device, unsigned *milliseconds);
HoloStatus HoloSetDebounceMs(const HoloDevice *device, unsigned milliseconds);
HoloStatus HoloGetRepeatMs(const HoloDevice *device, unsigned *milliseconds);
HoloStatus HoloSetRepeatMs(const HoloDevice *device, unsigned milliseconds);

typedef struct {
    unsigned long irqs;
    unsigned long bouncesFiltered;
    unsigned long presses;
    unsigned long releases;
    unsigned long repeats;
    unsigned long events;
    unsigned long dropped;
    unsigned long debounceMs;
    unsigned long repeatMs;
} HoloStats;

/* Reads the counters the driver exports in sysfs; a NULL directory means HOLO_DEFAULT_STATS_DIRECTORY. */
HoloStatus HoloReadStats(const char *directory, HoloStats *stats);

typedef enum {
    HOLO_MOVE_RIGHT,
    HOLO_MOVE_LEFT,
    HOLO_ZOOM_IN,
    HOLO_ZOOM_OUT,
    HOLO_TOGGLE_AUTO_ROTATE,
} HoloAction;

typedef enum { HOLO_PHASE_RELEASED, HOLO_PHASE_PRESSED, HOLO_PHASE_HELD } HoloPhase;

/* Translates a button event into what the button is wired to do. Returns false for events that are not from a button. */
bool HoloActionOf(const struct HoloEvent *event, HoloAction *action, HoloPhase *phase);

const char *HoloButtonName(unsigned code);
const char *HoloValueName(int value);
const char *HoloActionName(HoloAction action);
const char *HoloStatusText(HoloStatus status);

#endif
