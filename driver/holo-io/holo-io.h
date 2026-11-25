/* SPDX-License-Identifier: GPL-2.0 */
/* Internal declarations shared by the holo-io core and its button source. */
#ifndef HOLO_IO_H
#define HOLO_IO_H

#include <linux/atomic.h>
#include <linux/gpio/consumer.h>
#include <linux/hrtimer.h>
#include <linux/kfifo.h>
#include <linux/miscdevice.h>
#include <linux/spinlock.h>
#include <linux/wait.h>

#include "holo-event.h"
#include "holo-ioctl.h"

#define HOLO_FIFO_EVENTS 256
#define HOLO_MAX_BUTTONS 8

struct holo_stats {
	atomic_t irqs;
	atomic_t presses;
	atomic_t releases;
	atomic_t repeats;
	atomic_t dropped;
};

struct holo_dev;

struct holo_button {
	struct holo_dev *hdev;
	struct gpio_desc *gpiod;
	struct hrtimer debounce;
	struct hrtimer repeat;
	spinlock_t lock;
	int irq;
	u16 code;
	bool repeats;
	bool pressed;
};

struct holo_dev {
	struct device *dev;
	struct miscdevice misc;
	DECLARE_KFIFO(events, struct HoloEvent, HOLO_FIFO_EVENTS);
	spinlock_t fifo_lock;
	wait_queue_head_t readq;
	atomic_t opened;
	bool dying;
	u32 debounce_ms;
	u32 repeat_ms;
	struct holo_stats stats;
	struct holo_button buttons[HOLO_MAX_BUTTONS];
	unsigned int num_buttons;
};

void holo_emit(struct holo_dev *hdev, u16 code, s32 value);
int holo_buttons_probe(struct holo_dev *hdev);
void holo_buttons_remove(struct holo_dev *hdev);

#endif /* HOLO_IO_H */
