// SPDX-License-Identifier: GPL-2.0
/*
 * holo-io core: platform driver bound to the "astreaview,holo-io" device tree node, the /dev/holo-input
 * character device (kfifo + wait queue, read, poll, ioctl) and the sysfs statistics.
 * The GPIO buttons that feed the queue live in holo-buttons.c.
 */
#include <linux/atomic.h>
#include <linux/device.h>
#include <linux/ktime.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/poll.h>
#include <linux/sysfs.h>
#include <linux/uaccess.h>
#include <linux/version.h>

#include "holo-io.h"

#define HOLO_IO_VERSION "0.1.0"
#define HOLO_READ_BATCH 16

void holo_emit(struct holo_dev *hdev, u16 code, s32 value)
{
	struct HoloEvent event = {
		.timestampNs = ktime_get_ns(),
		.source = HOLO_SOURCE_JOYSTICK,
		.code = code,
		.value = value,
	};

	switch (value) {
	case HOLO_VALUE_PRESS:
		atomic_inc(&hdev->stats.presses);
		break;
	case HOLO_VALUE_RELEASE:
		atomic_inc(&hdev->stats.releases);
		break;
	case HOLO_VALUE_REPEAT:
		atomic_inc(&hdev->stats.repeats);
		break;
	}

	if (kfifo_in_spinlocked(&hdev->events, &event, 1, &hdev->fifo_lock) == 0) {
		atomic_inc(&hdev->stats.dropped);
		return;
	}
	wake_up_interruptible(&hdev->readq);
}

static struct holo_dev *holo_from_file(struct file *file)
{
	return container_of(file->private_data, struct holo_dev, misc);
}

static int holo_open(struct inode *inode, struct file *file)
{
	struct holo_dev *hdev = holo_from_file(file);

	if (atomic_cmpxchg(&hdev->opened, 0, 1) != 0)
		return -EBUSY;

	/* Events produced while nobody was reading are stale. */
	kfifo_reset_out(&hdev->events);
	return nonseekable_open(inode, file);
}

static int holo_release(struct inode *inode, struct file *file)
{
	atomic_set(&holo_from_file(file)->opened, 0);
	return 0;
}

static ssize_t holo_read(struct file *file, char __user *buf, size_t count,
			 loff_t *ppos)
{
	struct holo_dev *hdev = holo_from_file(file);
	struct HoloEvent batch[HOLO_READ_BATCH];
	size_t wanted = count / sizeof(struct HoloEvent);
	unsigned int got;

	if (wanted == 0)
		return -EINVAL;
	wanted = min_t(size_t, wanted, HOLO_READ_BATCH);

	for (;;) {
		got = kfifo_out_spinlocked(&hdev->events, batch, wanted,
					   &hdev->fifo_lock);
		if (got)
			break;
		if (READ_ONCE(hdev->dying))
			return -ENODEV;
		if (file->f_flags & O_NONBLOCK)
			return -EAGAIN;
		if (wait_event_interruptible(hdev->readq,
					     !kfifo_is_empty(&hdev->events) ||
					     READ_ONCE(hdev->dying)))
			return -ERESTARTSYS;
	}

	if (copy_to_user(buf, batch, got * sizeof(batch[0])))
		return -EFAULT;
	return got * sizeof(batch[0]);
}

static __poll_t holo_poll(struct file *file, poll_table *wait)
{
	struct holo_dev *hdev = holo_from_file(file);

	poll_wait(file, &hdev->readq, wait);
	if (READ_ONCE(hdev->dying))
		return EPOLLHUP;
	if (!kfifo_is_empty(&hdev->events))
		return EPOLLIN | EPOLLRDNORM;
	return 0;
}

static long holo_set_timing(u32 __user *argp, u32 *target, u32 min, u32 max)
{
	u32 value;

	if (get_user(value, argp))
		return -EFAULT;
	if (value < min || value > max)
		return -EINVAL;
	WRITE_ONCE(*target, value);
	return 0;
}

static long holo_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	struct holo_dev *hdev = holo_from_file(file);
	u32 __user *argp = (u32 __user *)arg;

	switch (cmd) {
	case HOLO_IOC_GET_DEBOUNCE_MS:
		return put_user(READ_ONCE(hdev->debounce_ms), argp);
	case HOLO_IOC_SET_DEBOUNCE_MS:
		return holo_set_timing(argp, &hdev->debounce_ms,
				       HOLO_DEBOUNCE_MIN_MS,
				       HOLO_DEBOUNCE_MAX_MS);
	case HOLO_IOC_GET_REPEAT_MS:
		return put_user(READ_ONCE(hdev->repeat_ms), argp);
	case HOLO_IOC_SET_REPEAT_MS:
		return holo_set_timing(argp, &hdev->repeat_ms,
				       HOLO_REPEAT_MIN_MS, HOLO_REPEAT_MAX_MS);
	default:
		return -ENOTTY;
	}
}

static const struct file_operations holo_fops = {
	.owner = THIS_MODULE,
	.open = holo_open,
	.release = holo_release,
	.read = holo_read,
	.poll = holo_poll,
	.unlocked_ioctl = holo_ioctl,
	.compat_ioctl = compat_ptr_ioctl,
};

#define HOLO_STAT_ATTR(name, expr)                                            \
	static ssize_t name##_show(struct device *dev,                        \
				   struct device_attribute *attr, char *buf)  \
	{                                                                     \
		struct holo_dev *hdev = dev_get_drvdata(dev);                 \
		return sysfs_emit(buf, "%d\n", (int)(expr));                  \
	}                                                                     \
	static DEVICE_ATTR_RO(name)

#define HOLO_EVENT_COUNT(hdev)                                                \
	(atomic_read(&(hdev)->stats.presses) +                                \
	 atomic_read(&(hdev)->stats.releases) +                               \
	 atomic_read(&(hdev)->stats.repeats))

/* Edges that did not end as a press or a release were absorbed by the debounce window. */
#define HOLO_FILTERED_COUNT(hdev)                                             \
	max(0, atomic_read(&(hdev)->stats.irqs) -                             \
		       atomic_read(&(hdev)->stats.presses) -                  \
		       atomic_read(&(hdev)->stats.releases))

HOLO_STAT_ATTR(irqs, atomic_read(&hdev->stats.irqs));
HOLO_STAT_ATTR(bounces_filtered, HOLO_FILTERED_COUNT(hdev));
HOLO_STAT_ATTR(presses, atomic_read(&hdev->stats.presses));
HOLO_STAT_ATTR(releases, atomic_read(&hdev->stats.releases));
HOLO_STAT_ATTR(repeats, atomic_read(&hdev->stats.repeats));
HOLO_STAT_ATTR(events, HOLO_EVENT_COUNT(hdev));
HOLO_STAT_ATTR(dropped, atomic_read(&hdev->stats.dropped));
HOLO_STAT_ATTR(debounce_ms, READ_ONCE(hdev->debounce_ms));
HOLO_STAT_ATTR(repeat_ms, READ_ONCE(hdev->repeat_ms));

static struct attribute *holo_stats_attrs[] = {
	&dev_attr_irqs.attr,
	&dev_attr_bounces_filtered.attr,
	&dev_attr_presses.attr,
	&dev_attr_releases.attr,
	&dev_attr_repeats.attr,
	&dev_attr_events.attr,
	&dev_attr_dropped.attr,
	&dev_attr_debounce_ms.attr,
	&dev_attr_repeat_ms.attr,
	NULL,
};

static const struct attribute_group holo_stats_group = {
	.name = "stats",
	.attrs = holo_stats_attrs,
};

static void holo_teardown(struct platform_device *pdev)
{
	struct holo_dev *hdev = platform_get_drvdata(pdev);

	/* Interrupts first: a late edge must not re-arm a timer that was just cancelled. */
	holo_buttons_remove(hdev);
	WRITE_ONCE(hdev->dying, true);
	wake_up_interruptible_all(&hdev->readq);
	misc_deregister(&hdev->misc);
}

static int holo_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct holo_dev *hdev;
	int ret;

	hdev = devm_kzalloc(dev, sizeof(*hdev), GFP_KERNEL);
	if (!hdev)
		return -ENOMEM;

	hdev->dev = dev;
	hdev->debounce_ms = HOLO_DEBOUNCE_DEFAULT_MS;
	hdev->repeat_ms = HOLO_REPEAT_DEFAULT_MS;
	INIT_KFIFO(hdev->events);
	spin_lock_init(&hdev->fifo_lock);
	init_waitqueue_head(&hdev->readq);
	atomic_set(&hdev->opened, 0);
	platform_set_drvdata(pdev, hdev);

	hdev->misc.minor = MISC_DYNAMIC_MINOR;
	hdev->misc.name = "holo-input";
	hdev->misc.fops = &holo_fops;
	hdev->misc.parent = dev;
	hdev->misc.mode = 0660;
	ret = misc_register(&hdev->misc);
	if (ret)
		return dev_err_probe(dev, ret, "cannot register /dev/holo-input\n");

	ret = devm_device_add_group(dev, &holo_stats_group);
	if (ret)
		goto err_misc;

	ret = holo_buttons_probe(hdev);
	if (ret)
		goto err_misc;

	dev_info(dev, "loaded %s: %u buttons, debounce %u ms, repeat %u ms\n",
		 HOLO_IO_VERSION, hdev->num_buttons, hdev->debounce_ms,
		 hdev->repeat_ms);
	return 0;

err_misc:
	misc_deregister(&hdev->misc);
	return ret;
}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 11, 0)
static void holo_remove(struct platform_device *pdev)
{
	holo_teardown(pdev);
}
#else
static int holo_remove(struct platform_device *pdev)
{
	holo_teardown(pdev);
	return 0;
}
#endif

static const struct of_device_id holo_of_match[] = {
	{ .compatible = "astreaview,holo-io" },
	{ }
};
MODULE_DEVICE_TABLE(of, holo_of_match);

/*
 * Unbinding through sysfs would free the device while a file descriptor may still be open on it.
 */
static struct platform_driver holo_driver = {
	.probe = holo_probe,
	.remove = holo_remove,
	.driver = {
		.name = "holo-io",
		.of_match_table = holo_of_match,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(holo_driver);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("AstreaView team");
MODULE_DESCRIPTION("AstreaView holographic display input driver");
MODULE_VERSION(HOLO_IO_VERSION);
