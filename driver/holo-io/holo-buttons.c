// SPDX-License-Identifier: GPL-2.0
/*
 * holo-io buttons: one GPIO per child node of the device tree node. Every edge restarts an hrtimer; when the
 * line has been quiet for the debounce time the stable level is read and a press or release is queued.
 * A second hrtimer queues a repeat while a button that repeats stays pressed.
 */
#include <linux/device.h>
#include <linux/gpio/consumer.h>
#include <linux/interrupt.h>
#include <linux/ktime.h>
#include <linux/property.h>
#include <linux/string.h>
#include <linux/version.h>

#include "holo-io.h"

struct holo_label {
	const char *label;
	u16 code;
	bool repeats;
};

static const struct holo_label holo_labels[] = {
	{ "right", HOLO_CODE_BTN_RIGHT, true },
	{ "left", HOLO_CODE_BTN_LEFT, true },
	{ "zoom-in", HOLO_CODE_BTN_ZOOM_IN, true },
	{ "zoom-out", HOLO_CODE_BTN_ZOOM_OUT, true },
	{ "autorotate", HOLO_CODE_BTN_AUTOROTATE, false },
};

static void holo_timer_init(struct hrtimer *timer,
			    enum hrtimer_restart (*function)(struct hrtimer *))
{
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 13, 0)
	hrtimer_setup(timer, function, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
#else
	hrtimer_init(timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
	timer->function = function;
#endif
}

static irqreturn_t holo_button_irq(int irq, void *data)
{
	struct holo_button *button = data;
	struct holo_dev *hdev = button->hdev;

	atomic_inc(&hdev->stats.irqs);
	hrtimer_start(&button->debounce,
		      ms_to_ktime(READ_ONCE(hdev->debounce_ms)),
		      HRTIMER_MODE_REL);
	return IRQ_HANDLED;
}

static enum hrtimer_restart holo_debounce_expired(struct hrtimer *timer)
{
	struct holo_button *button =
		container_of(timer, struct holo_button, debounce);
	struct holo_dev *hdev = button->hdev;
	unsigned long flags;
	bool level;

	spin_lock_irqsave(&button->lock, flags);
	level = gpiod_get_value(button->gpiod) > 0;
	if (level != button->pressed) {
		button->pressed = level;
		holo_emit(hdev, button->code,
			  level ? HOLO_VALUE_PRESS : HOLO_VALUE_RELEASE);
		if (level && button->repeats)
			hrtimer_start(&button->repeat,
				      ms_to_ktime(READ_ONCE(hdev->repeat_ms)),
				      HRTIMER_MODE_REL);
	}
	spin_unlock_irqrestore(&button->lock, flags);
	return HRTIMER_NORESTART;
}

static enum hrtimer_restart holo_repeat_expired(struct hrtimer *timer)
{
	struct holo_button *button =
		container_of(timer, struct holo_button, repeat);
	struct holo_dev *hdev = button->hdev;
	enum hrtimer_restart restart = HRTIMER_NORESTART;
	unsigned long flags;

	/*
	 * The lock keeps a repeat from being queued after the release: the host treats a repeat as a
	 * button held down and would keep moving the camera.
	 */
	spin_lock_irqsave(&button->lock, flags);
	if (button->pressed) {
		holo_emit(hdev, button->code, HOLO_VALUE_REPEAT);
		hrtimer_forward_now(timer,
				    ms_to_ktime(READ_ONCE(hdev->repeat_ms)));
		restart = HRTIMER_RESTART;
	}
	spin_unlock_irqrestore(&button->lock, flags);
	return restart;
}

static const struct holo_label *holo_find_label(const char *label)
{
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(holo_labels); i++)
		if (!strcmp(holo_labels[i].label, label))
			return &holo_labels[i];
	return NULL;
}

static bool holo_code_in_use(struct holo_dev *hdev, u16 code)
{
	unsigned int i;

	for (i = 0; i < hdev->num_buttons; i++)
		if (hdev->buttons[i].code == code)
			return true;
	return false;
}

static int holo_button_setup(struct holo_dev *hdev,
			     struct fwnode_handle *node)
{
	struct device *dev = hdev->dev;
	const struct holo_label *entry;
	struct holo_button *button;
	const char *label;
	char *irq_name;
	int ret;

	ret = fwnode_property_read_string(node, "label", &label);
	if (ret)
		return dev_err_probe(dev, ret, "button node without label\n");

	entry = holo_find_label(label);
	if (!entry)
		return dev_err_probe(dev, -EINVAL, "unknown button label '%s'\n",
				     label);
	if (holo_code_in_use(hdev, entry->code))
		return dev_err_probe(dev, -EINVAL,
				     "button label '%s' is declared twice\n",
				     label);
	if (hdev->num_buttons >= HOLO_MAX_BUTTONS)
		return dev_err_probe(dev, -EINVAL, "too many buttons\n");

	button = &hdev->buttons[hdev->num_buttons];
	button->hdev = hdev;
	button->code = entry->code;
	button->repeats = entry->repeats;
	spin_lock_init(&button->lock);
	holo_timer_init(&button->debounce, holo_debounce_expired);
	holo_timer_init(&button->repeat, holo_repeat_expired);

	button->gpiod = devm_fwnode_gpiod_get(dev, node, NULL, GPIOD_IN, label);
	if (IS_ERR(button->gpiod))
		return dev_err_probe(dev, PTR_ERR(button->gpiod),
				     "cannot get the GPIO of '%s'\n", label);
	/* The level is read from timer callbacks, which cannot sleep. */
	if (gpiod_cansleep(button->gpiod))
		return dev_err_probe(dev, -EINVAL,
				     "GPIO of '%s' can sleep\n", label);

	button->irq = gpiod_to_irq(button->gpiod);
	if (button->irq < 0)
		return dev_err_probe(dev, button->irq,
				     "no interrupt for '%s'\n", label);

	irq_name = devm_kasprintf(dev, GFP_KERNEL, "holo-io-%s", label);
	if (!irq_name)
		return -ENOMEM;

	button->pressed = gpiod_get_value(button->gpiod) > 0;
	ret = devm_request_irq(dev, button->irq, holo_button_irq,
			       IRQF_TRIGGER_RISING | IRQF_TRIGGER_FALLING,
			       irq_name, button);
	if (ret)
		return dev_err_probe(dev, ret, "cannot request the interrupt of '%s'\n",
				     label);

	hdev->num_buttons++;
	return 0;
}

int holo_buttons_probe(struct holo_dev *hdev)
{
	struct fwnode_handle *child;
	int ret;

	device_for_each_child_node(hdev->dev, child) {
		ret = holo_button_setup(hdev, child);
		if (ret) {
			fwnode_handle_put(child);
			holo_buttons_remove(hdev);
			return ret;
		}
	}

	if (hdev->num_buttons == 0)
		return dev_err_probe(hdev->dev, -ENODEV,
				     "no button nodes in the device tree\n");
	return 0;
}

void holo_buttons_remove(struct holo_dev *hdev)
{
	unsigned int i;

	for (i = 0; i < hdev->num_buttons; i++)
		devm_free_irq(hdev->dev, hdev->buttons[i].irq,
			      &hdev->buttons[i]);
	/* The debounce callback starts the repeat timer, so it is cancelled first. */
	for (i = 0; i < hdev->num_buttons; i++)
		hrtimer_cancel(&hdev->buttons[i].debounce);
	for (i = 0; i < hdev->num_buttons; i++)
		hrtimer_cancel(&hdev->buttons[i].repeat);
	hdev->num_buttons = 0;
}
