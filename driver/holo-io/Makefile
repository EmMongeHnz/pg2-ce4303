# Out-of-tree build of the holo-io kernel module, its overlay and the test reader.
#   make                      # module, overlay and reader against the running kernel (on the Pi)
#   make KDIR=/path/to/kernel # against another kernel tree or headers
#   sudo make install         # module, udev rule and overlay (then add dtoverlay=holo-io to config.txt)
#   make clean
obj-m := holo-io.o
holo-io-y := holo-core.o holo-buttons.o

KDIR ?= /lib/modules/$(shell uname -r)/build
OVERLAY_DIR ?= /boot/firmware/overlays

all: modules overlay holo-reader

modules:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

overlay: holo-io.dtbo

holo-io.dtbo: holo-io-overlay.dts
	dtc -@ -I dts -O dtb -o $@ $<

holo-reader: holo-reader.c holo-event.h holo-ioctl.h
	$(CC) -Wall -Wextra -O2 -o $@ $<

install: all
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules_install
	install -D -m 0644 99-holo-io.rules /etc/udev/rules.d/99-holo-io.rules
	install -D -m 0644 holo-io.dtbo $(OVERLAY_DIR)/holo-io.dtbo
	depmod -a
	udevadm control --reload-rules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
	rm -f holo-io.dtbo holo-reader
