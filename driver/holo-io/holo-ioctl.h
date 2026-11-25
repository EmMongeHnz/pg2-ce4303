/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
/*
 * ioctl interface of /dev/holo-input. Timings are in milliseconds.
 * tests/unit/test_holo_io_sources.py checks these ranges against the host hold timeout.
 */
#ifndef HOLO_IOCTL_H
#define HOLO_IOCTL_H

#include <linux/ioctl.h>
#include <linux/types.h>

#include "holo-event.h"

#define HOLO_DEBOUNCE_MIN_MS 1
#define HOLO_DEBOUNCE_MAX_MS 100
#define HOLO_DEBOUNCE_DEFAULT_MS 10

#define HOLO_REPEAT_MIN_MS 10
#define HOLO_REPEAT_MAX_MS 100
#define HOLO_REPEAT_DEFAULT_MS 50

#define HOLO_IOC_MAGIC 'H'
#define HOLO_IOC_GET_DEBOUNCE_MS _IOR(HOLO_IOC_MAGIC, 1, __u32)
#define HOLO_IOC_SET_DEBOUNCE_MS _IOW(HOLO_IOC_MAGIC, 2, __u32)
#define HOLO_IOC_GET_REPEAT_MS _IOR(HOLO_IOC_MAGIC, 3, __u32)
#define HOLO_IOC_SET_REPEAT_MS _IOW(HOLO_IOC_MAGIC, 4, __u32)

#endif /* HOLO_IOCTL_H */
