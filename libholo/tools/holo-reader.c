// SPDX-License-Identifier: GPL-2.0
/*
 * holo-reader: prints the events of /dev/holo-input and reads or sets the driver timings, through libholo.
 *
 *   holo-reader                    print events until interrupted
 *   holo-reader -p 100             stop after 100 press events and print a summary line
 *   holo-reader -n 20              stop after 20 events
 *   holo-reader -g                 print the debounce and repeat times and exit
 *   holo-reader -s                 print the driver statistics (name=value) and exit
 *   holo-reader -D 8 -R 40         set debounce 8 ms and repeat 40 ms, then read
 *   holo-reader -d /dev/holo-input choose the device
 */
#include <errno.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "holo.h"

static volatile sig_atomic_t gStop = 0;

static void OnSignal(int signalNumber) {
    (void)signalNumber;
    gStop = 1;
}

static int PrintStats(void) {
    HoloStats stats;
    if (HoloReadStats(NULL, &stats) != HOLO_OK) {
        fprintf(stderr, "holo-reader: cannot read the statistics: %s\n", strerror(errno));
        return 1;
    }
    printf("irqs=%lu\nbounces_filtered=%lu\npresses=%lu\nreleases=%lu\nrepeats=%lu\nevents=%lu\ndropped=%lu\ndebounce_ms=%lu\nrepeat_ms=%lu\n", stats.irqs, stats.bouncesFiltered,
           stats.presses, stats.releases, stats.repeats, stats.events, stats.dropped, stats.debounceMs, stats.repeatMs);
    return 0;
}

static int PrintTimings(HoloDevice *device) {
    unsigned debounceMs, repeatMs;
    if (HoloGetDebounceMs(device, &debounceMs) != HOLO_OK || HoloGetRepeatMs(device, &repeatMs) != HOLO_OK) {
        fprintf(stderr, "holo-reader: cannot read the timings: %s\n", strerror(errno));
        return 1;
    }
    printf("debounce_ms=%u repeat_ms=%u\n", debounceMs, repeatMs);
    return 0;
}

static int ReadEvents(HoloDevice *device, unsigned long maxEvents, unsigned long maxPresses) {
    unsigned long total = 0, presses = 0, releases = 0, repeats = 0;
    while (!gStop && (!maxEvents || total < maxEvents) && (!maxPresses || presses < maxPresses)) {
        struct HoloEvent event;
        HoloStatus status = HoloReadEvent(device, 500, &event);
        if (status == HOLO_TIMEOUT || status == HOLO_INTERRUPTED) continue;
        if (status != HOLO_OK) {
            fprintf(stderr, "holo-reader: %s\n", status == HOLO_ERROR ? strerror(errno) : HoloStatusText(status));
            return 1;
        }
        total++;
        presses += event.value == HOLO_VALUE_PRESS;
        releases += event.value == HOLO_VALUE_RELEASE;
        repeats += event.value == HOLO_VALUE_REPEAT;
        printf("%llu %s %s\n", (unsigned long long)event.timestampNs, HoloButtonName(event.code), HoloValueName(event.value));
        fflush(stdout);
    }
    printf("summary presses=%lu releases=%lu repeats=%lu\n", presses, releases, repeats);
    return 0;
}

int main(int argc, char **argv) {
    const char *path = HOLO_DEFAULT_DEVICE, *debounce = NULL, *repeat = NULL;
    unsigned long maxEvents = 0, maxPresses = 0;
    bool printTimings = false, printStats = false;
    int option;
    while ((option = getopt(argc, argv, "d:n:p:D:R:gs")) != -1) {
        switch (option) {
            case 'd': path = optarg; break;
            case 'n': maxEvents = strtoul(optarg, NULL, 10); break;
            case 'p': maxPresses = strtoul(optarg, NULL, 10); break;
            case 'D': debounce = optarg; break;
            case 'R': repeat = optarg; break;
            case 'g': printTimings = true; break;
            case 's': printStats = true; break;
            default:
                fprintf(stderr, "usage: %s [-d device] [-n events] [-p presses] [-D debounceMs] [-R repeatMs] [-g] [-s]\n", argv[0]);
                return 2;
        }
    }
    if (printStats) return PrintStats();

    struct sigaction action = {.sa_handler = OnSignal};
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);
    HoloDevice *device;
    if (HoloOpen(path, &device) != HOLO_OK) {
        fprintf(stderr, "holo-reader: cannot open %s: %s\n", path, strerror(errno));
        return 1;
    }
    int status = 0;
    if (debounce != NULL && HoloSetDebounceMs(device, (unsigned)strtoul(debounce, NULL, 10)) != HOLO_OK) {
        fprintf(stderr, "holo-reader: cannot set the debounce to %s ms: %s\n", debounce, strerror(errno));
        status = 1;
    } else if (repeat != NULL && HoloSetRepeatMs(device, (unsigned)strtoul(repeat, NULL, 10)) != HOLO_OK) {
        fprintf(stderr, "holo-reader: cannot set the repeat to %s ms: %s\n", repeat, strerror(errno));
        status = 1;
    } else if (printTimings) {
        status = PrintTimings(device);
    } else {
        status = ReadEvents(device, maxEvents, maxPresses);
    }
    HoloClose(device);
    return status;
}
