/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
/*
 * Record format of /dev/holo-input, shared by the holo-io driver and user space.
 * tests/unit/test_holo_event.py keeps astrea_common.holo_event equal to this file.
 */
#ifndef HOLO_EVENT_H
#define HOLO_EVENT_H

#include <linux/types.h>

#define HOLO_SOURCE_JOYSTICK 1
#define HOLO_SOURCE_ZX_GESTURE 2

#define HOLO_CODE_BTN_RIGHT 1
#define HOLO_CODE_BTN_LEFT 2
#define HOLO_CODE_BTN_ZOOM_IN 3
#define HOLO_CODE_BTN_ZOOM_OUT 4
#define HOLO_CODE_BTN_AUTOROTATE 5

#define HOLO_VALUE_RELEASE 0
#define HOLO_VALUE_PRESS 1
#define HOLO_VALUE_REPEAT 2

struct HoloEvent {
	__u64 timestampNs;
	__u16 source;
	__u16 code;
	__s32 value;
};

_Static_assert(sizeof(struct HoloEvent) == 16, "HoloEvent must stay 16 bytes without padding");

#endif /* HOLO_EVENT_H */
