# libholo: the static library that is the only user-space code talking to the holo-io driver, and the test reader.
#   make            # build/libholo.a and build/holo-reader
#   make SAN=1      # the same with AddressSanitizer and UndefinedBehaviorSanitizer (build-san/)
#   make clean
CC ?= gcc
BUILD := build
CFLAGS := -std=gnu11 -Wall -Wextra -O2 -g -Iinclude -I../driver/holo-io
ifdef SAN
CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
BUILD := build-san
endif

all: $(BUILD)/libholo.a $(BUILD)/holo-reader

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/holo.o: src/holo.c include/holo.h ../driver/holo-io/holo-event.h ../driver/holo-io/holo-ioctl.h | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD)/libholo.a: $(BUILD)/holo.o
	ar rcs $@ $^

$(BUILD)/holo-reader: tools/holo-reader.c $(BUILD)/libholo.a
	$(CC) $(CFLAGS) $< $(BUILD)/libholo.a -o $@

clean:
	rm -rf build build-san

.PHONY: all clean
