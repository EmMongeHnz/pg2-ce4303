# Secure network components in C: the static library, the daemon used by the Python nodes and the device agent
# (the agent reads /dev/holo-input through the record format of driver/holo-io/holo-event.h).
#   make            # build into net/build
#   make SAN=1      # the same with AddressSanitizer and UndefinedBehaviorSanitizer (used to look for memory errors)
#   make clean
# Needs a C compiler, make, libssl-dev (sudo apt install build-essential libssl-dev).
CC ?= gcc
BUILD := build
CFLAGS := -std=gnu11 -Wall -Wextra -O2 -g -pthread -Isrc -I../renderer/src -I../driver/holo-io
ifdef SAN
CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
BUILD := build-san
endif
LDLIBS := -lssl -lcrypto -pthread

LIB_SOURCES := src/astrea-log.c src/astrea-msgpack.c src/astrea-protocol.c src/astrea-config.c src/astrea-tls.c src/astrea-link.c
LIB_OBJECTS := $(patsubst src/%.c,$(BUILD)/%.o,$(LIB_SOURCES))

AGENT_OBJECTS := $(BUILD)/astrea-agent.o $(BUILD)/agent-models.o $(BUILD)/agent-renderer.o

all: $(BUILD)/libastrea-net.a $(BUILD)/astrea-netd $(BUILD)/astrea-device-agent

$(BUILD)/%.o: src/%.c $(wildcard src/*.h) | $(BUILD)
	$(CC) $(CFLAGS) -c $< -o $@

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/libastrea-net.a: $(LIB_OBJECTS)
	ar rcs $@ $^

$(BUILD)/astrea-netd: $(BUILD)/astrea-netd.o $(BUILD)/libastrea-net.a
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

$(BUILD)/astrea-device-agent: $(AGENT_OBJECTS) $(BUILD)/libastrea-net.a
	$(CC) $(CFLAGS) $^ -o $@ $(LDLIBS)

clean:
	rm -rf build build-san

.PHONY: all clean
