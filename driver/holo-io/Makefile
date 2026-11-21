# Out-of-tree build of the holo-io kernel module.
#   make                      # against the running kernel (on the Pi)
#   make KDIR=/path/to/kernel # against another kernel tree or headers
#   make clean
obj-m := holo-io.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean
