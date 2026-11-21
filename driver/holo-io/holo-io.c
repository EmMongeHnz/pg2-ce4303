// SPDX-License-Identifier: GPL-2.0
/*
 * holo-io: AstreaView input driver for the Raspberry Pi 3.
 *
 * M0 skeleton: it only loads and unloads, so the out-of-tree build can be
 * checked on the Pi. M1 adds /dev/holo-input (GPIO joystick, debounce,
 * autorepeat, kfifo, poll, ioctl, sysfs) on top of this file.
 */
#include <linux/init.h>
#include <linux/kernel.h>
#include <linux/module.h>

#define HOLO_IO_VERSION "0.0.1"

static int __init holo_io_init(void)
{
	pr_info("holo-io: loaded (skeleton %s)\n", HOLO_IO_VERSION);
	return 0;
}

static void __exit holo_io_exit(void)
{
	pr_info("holo-io: unloaded\n");
}

module_init(holo_io_init);
module_exit(holo_io_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("AstreaView team");
MODULE_DESCRIPTION("AstreaView holographic display input driver");
MODULE_VERSION(HOLO_IO_VERSION);
