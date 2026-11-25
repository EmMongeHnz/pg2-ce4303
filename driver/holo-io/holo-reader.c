// SPDX-License-Identifier: GPL-2.0
/*
 * holo-reader: prints the events of /dev/holo-input, and reads or sets the driver timings.
 *
 *   holo-reader                    print events until interrupted
 *   holo-reader -p 100             stop after 100 press events and print a summary line
 *   holo-reader -n 20              stop after 20 events
 *   holo-reader -g                 print the debounce and repeat times and exit
 *   holo-reader -D 8 -R 40         set debounce 8 ms and repeat 40 ms, then read
 *   holo-reader -d /dev/holo-input choose the device
 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#include "holo-ioctl.h"

struct Counters {
	unsigned long presses;
	unsigned long releases;
	unsigned long repeats;
	unsigned long total;
};

static const char *const CODE_NAMES[] = {
	[HOLO_CODE_BTN_RIGHT] = "right",
	[HOLO_CODE_BTN_LEFT] = "left",
	[HOLO_CODE_BTN_ZOOM_IN] = "zoom-in",
	[HOLO_CODE_BTN_ZOOM_OUT] = "zoom-out",
	[HOLO_CODE_BTN_AUTOROTATE] = "autorotate",
};

static const char *const VALUE_NAMES[] = {
	[HOLO_VALUE_RELEASE] = "release",
	[HOLO_VALUE_PRESS] = "press",
	[HOLO_VALUE_REPEAT] = "repeat",
};

static const char *NameOf(const char *const *names, size_t count, unsigned int index)
{
	return index < count && names[index] ? names[index] : "?";
}

static void CountEvent(struct Counters *counters, const struct HoloEvent *event)
{
	counters->total++;
	if (event->value == HOLO_VALUE_PRESS)
		counters->presses++;
	else if (event->value == HOLO_VALUE_RELEASE)
		counters->releases++;
	else if (event->value == HOLO_VALUE_REPEAT)
		counters->repeats++;
}

static void PrintEvent(const struct HoloEvent *event)
{
	printf("%llu %s %s\n", (unsigned long long)event->timestampNs,
	       NameOf(CODE_NAMES, sizeof(CODE_NAMES) / sizeof(CODE_NAMES[0]), event->code),
	       NameOf(VALUE_NAMES, sizeof(VALUE_NAMES) / sizeof(VALUE_NAMES[0]), (unsigned int)event->value));
	fflush(stdout);
}

static int SetTiming(int fd, unsigned long request, const char *name, const char *text)
{
	__u32 value = (__u32)strtoul(text, NULL, 10);

	if (ioctl(fd, request, &value) < 0) {
		fprintf(stderr, "holo-reader: cannot set %s to %s ms: %s\n", name, text, strerror(errno));
		return -1;
	}
	return 0;
}

static int PrintTimings(int fd)
{
	__u32 debounceMs;
	__u32 repeatMs;

	if (ioctl(fd, HOLO_IOC_GET_DEBOUNCE_MS, &debounceMs) < 0 || ioctl(fd, HOLO_IOC_GET_REPEAT_MS, &repeatMs) < 0) {
		fprintf(stderr, "holo-reader: cannot read the timings: %s\n", strerror(errno));
		return -1;
	}
	printf("debounce_ms=%u repeat_ms=%u\n", debounceMs, repeatMs);
	return 0;
}

static int ReadEvents(int fd, unsigned long maxEvents, unsigned long maxPresses)
{
	struct Counters counters = {0};
	struct HoloEvent batch[16];

	while ((!maxEvents || counters.total < maxEvents) && (!maxPresses || counters.presses < maxPresses)) {
		struct pollfd pfd = {.fd = fd, .events = POLLIN};
		ssize_t bytes;
		size_t count;
		size_t i;

		if (poll(&pfd, 1, -1) < 0) {
			if (errno == EINTR)
				continue;
			perror("holo-reader: poll");
			return -1;
		}
		bytes = read(fd, batch, sizeof(batch));
		if (bytes < 0) {
			if (errno == EAGAIN || errno == EINTR)
				continue;
			perror("holo-reader: read");
			return -1;
		}
		count = (size_t)bytes / sizeof(batch[0]);
		for (i = 0; i < count; i++) {
			CountEvent(&counters, &batch[i]);
			PrintEvent(&batch[i]);
		}
	}
	printf("summary presses=%lu releases=%lu repeats=%lu\n", counters.presses, counters.releases, counters.repeats);
	return 0;
}

int main(int argc, char **argv)
{
	const char *device = "/dev/holo-input";
	unsigned long maxEvents = 0;
	unsigned long maxPresses = 0;
	const char *debounce = NULL;
	const char *repeat = NULL;
	int printOnly = 0;
	int option;
	int fd;
	int status = 0;

	while ((option = getopt(argc, argv, "d:n:p:D:R:g")) != -1) {
		switch (option) {
		case 'd': device = optarg; break;
		case 'n': maxEvents = strtoul(optarg, NULL, 10); break;
		case 'p': maxPresses = strtoul(optarg, NULL, 10); break;
		case 'D': debounce = optarg; break;
		case 'R': repeat = optarg; break;
		case 'g': printOnly = 1; break;
		default:
			fprintf(stderr, "usage: %s [-d device] [-n events] [-p presses] [-D debounceMs] [-R repeatMs] [-g]\n", argv[0]);
			return 2;
		}
	}

	fd = open(device, O_RDONLY);
	if (fd < 0) {
		fprintf(stderr, "holo-reader: cannot open %s: %s\n", device, strerror(errno));
		return 1;
	}
	if ((debounce && SetTiming(fd, HOLO_IOC_SET_DEBOUNCE_MS, "debounce", debounce)) ||
	    (repeat && SetTiming(fd, HOLO_IOC_SET_REPEAT_MS, "repeat", repeat)))
		status = 1;
	else if (printOnly)
		status = PrintTimings(fd) ? 1 : 0;
	else
		status = ReadEvents(fd, maxEvents, maxPresses) ? 1 : 0;
	close(fd);
	return status;
}
