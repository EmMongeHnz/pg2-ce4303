/* astrea-device-agent: runs on the Raspberry Pi next to the holo-io driver and the hologram renderer.
 *
 *   astrea-device-agent --config config.toml --name pi --models-dir out/models [--renderer-socket /tmp/astrea-hologram.sock]
 *
 * It reads the button events of /dev/holo-input and sends each one to the host as an INPUT_EVENT over the TLS 1.3
 * link, hands every CAMERA_STATE it receives to the renderer through a Unix socket, and installs the models that the
 * host pushes. The host decides what a button means; the agent forwards the events as they come. */
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "agent-models.h"
#include "agent-renderer.h"
#include "astrea-config.h"
#include "astrea-link.h"
#include "astrea-log.h"
#include "holo-event.h"

#define DEVICE_RETRY_S 2
#define READ_BATCH 16

typedef struct {
    Node *node;
    RendererLink *renderer;
    ModelStore *models;
    const char *devicePath;
    atomic_bool stopping;
    atomic_ulong forwarded;
    atomic_ulong dropped;
} Agent;

static Agent gAgent;

static void OnSignal(int signalNumber) {
    (void)signalNumber;
    atomic_store(&gAgent.stopping, true);
    if (gAgent.node != NULL) NodeStop(gAgent.node);
}

static const char *CodeName(unsigned code) {
    static const char *const NAMES[] = {"?", "right", "left", "zoom-in", "zoom-out", "autorotate"};
    return code < sizeof(NAMES) / sizeof(NAMES[0]) ? NAMES[code] : "?";
}

static const char *ValueName(int value) {
    return value == HOLO_VALUE_PRESS ? "press" : value == HOLO_VALUE_RELEASE ? "release" : value == HOLO_VALUE_REPEAT ? "repeat" : "?";
}

static void ForwardEvent(const struct HoloEvent *event) {
    MpWriter payload;
    MpWriterInit(&payload);
    MpMapHeader(&payload, 4);
    MpStr(&payload, "source");
    MpUint(&payload, event->source);
    MpStr(&payload, "code");
    MpUint(&payload, event->code);
    MpStr(&payload, "value");
    MpInt(&payload, event->value);
    MpStr(&payload, "timestampNs");
    MpUint(&payload, event->timestampNs);
    bool sent = !payload.failed && NodeSend(gAgent.node, MSG_INPUT_EVENT, payload.data, payload.size);
    MpWriterFree(&payload);
    if (sent) {
        atomic_fetch_add(&gAgent.forwarded, 1);
        if (event->value != HOLO_VALUE_REPEAT) LOG_INFO("sent to the host: %s %s", CodeName(event->code), ValueName(event->value));
        return;
    }
    unsigned long dropped = atomic_fetch_add(&gAgent.dropped, 1) + 1;
    if (dropped == 1 || dropped % 50 == 0) LOG_WARN("not connected to the host: %lu button events dropped so far", dropped);
}

static bool ReadDevice(int fd) {
    unsigned char buffer[READ_BATCH * sizeof(struct HoloEvent) * 2];
    size_t leftover = 0;
    while (!atomic_load(&gAgent.stopping)) {
        struct pollfd wait = {fd, POLLIN, 0};
        if (poll(&wait, 1, 200) <= 0) continue;
        ssize_t received = read(fd, buffer + leftover, READ_BATCH * sizeof(struct HoloEvent));
        if (received < 0) {
            if (errno == EAGAIN || errno == EINTR) continue;
            LOG_WARN("lost the input device: %s", strerror(errno));
            return false;
        }
        if (received == 0) {
            usleep(50000);
            continue;
        }
        size_t available = leftover + (size_t)received;
        size_t usable = available - available % sizeof(struct HoloEvent);
        for (size_t offset = 0; offset < usable; offset += sizeof(struct HoloEvent)) {
            struct HoloEvent event;
            memcpy(&event, buffer + offset, sizeof(event));
            ForwardEvent(&event);
        }
        leftover = available - usable;
        memmove(buffer, buffer + usable, leftover);
    }
    return true;
}

static void *DeviceMain(void *unused) {
    (void)unused;
    while (!atomic_load(&gAgent.stopping)) {
        int fd = open(gAgent.devicePath, O_RDONLY | O_NONBLOCK);
        if (fd < 0) {
            LOG_WARN("cannot open %s (%s), retrying; another process may be holding it (see: sudo fuser -v %s)", gAgent.devicePath, strerror(errno), gAgent.devicePath);
            for (int waited = 0; waited < DEVICE_RETRY_S * 10 && !atomic_load(&gAgent.stopping); waited++) usleep(100000);
            continue;
        }
        LOG_INFO("reading %s", gAgent.devicePath);
        ReadDevice(fd);
        close(fd);
        for (int waited = 0; waited < DEVICE_RETRY_S * 10 && !atomic_load(&gAgent.stopping); waited++) usleep(100000);
    }
    return NULL;
}

static void OnCameraState(const unsigned char *payload, size_t size) {
    double yaw, pitch, zoom;
    char modelId[CONTROL_MODEL_ID_BYTES];
    if (!MpGetDouble(payload, size, "yaw", &yaw) || !MpGetDouble(payload, size, "pitch", &pitch) || !MpGetDouble(payload, size, "zoom", &zoom) ||
        !MpGetString(payload, size, "modelId", modelId, sizeof(modelId))) {
        LOG_WARN("ignoring a CAMERA_STATE with unusable fields");
        return;
    }
    ControlState state;
    memset(&state, 0, sizeof(state));
    state.yawDeg = (float)yaw;
    state.pitchDeg = (float)pitch;
    state.zoom = (float)zoom;
    memcpy(state.modelId, modelId, strlen(modelId));
    RendererLinkSend(gAgent.renderer, &state);
}

static void OnModelPush(const unsigned char *payload, size_t size) {
    char installed[MODEL_ID_BYTES], error[160] = "";
    ModelResult result = ModelStoreAdd(gAgent.models, payload, size, installed, error, sizeof(error));
    if (result == MODEL_REJECTED) {
        LOG_WARN("model rejected: %s", error);
        return;
    }
    if (result != MODEL_INSTALLED) return;
    LOG_INFO("installed model '%s'", installed);
    ControlState latest;
    if (RendererLinkLatest(gAgent.renderer, &latest) && strncmp(latest.modelId, installed, CONTROL_MODEL_ID_BYTES) == 0) RendererLinkSend(gAgent.renderer, &latest);
}

static void NodeUp(Node *node, unsigned id, const char *hubNode, const char *session, void *user) {
    (void)node; (void)id; (void)user;
    LOG_INFO("connected to the host '%s' (%s)", hubNode, session);
}

static void NodeDown(Node *node, unsigned id, const char *reason, void *user) {
    (void)node; (void)id; (void)user;
    LOG_WARN("lost the host: %s", reason);
}

static void NodeMessage(Node *node, unsigned id, int type, const unsigned char *payload, size_t size, void *user) {
    (void)node; (void)id; (void)user;
    if (type == MSG_CAMERA_STATE) OnCameraState(payload, size);
    else if (type == MSG_MODEL_PUSH) OnModelPush(payload, size);
}

static void NodeError(Node *node, const char *text, void *user) {
    (void)node; (void)user;
    LOG_WARN("could not reach the hub: %s", text);
}

static void Usage(const char *program) {
    fprintf(stderr, "usage: %s --name NODE [--config FILE] [--models-dir DIR] [--renderer-socket PATH] [--device PATH | --no-device] [--aes-first] [--verbose]\n", program);
}

int main(int argc, char **argv) {
    const char *configPath = "config.toml", *name = NULL, *modelsDir = "out/models", *rendererSocket = "/tmp/astrea-hologram.sock";
    gAgent.devicePath = "/dev/holo-input";
    bool preferChacha = true, useDevice = true;
    static const struct option options[] = {{"config", 1, 0, 'c'}, {"name", 1, 0, 'n'}, {"models-dir", 1, 0, 'm'}, {"renderer-socket", 1, 0, 'r'}, {"device", 1, 0, 'd'},
                                            {"no-device", 0, 0, 'N'}, {"aes-first", 0, 0, 'a'}, {"verbose", 0, 0, 'v'}, {0, 0, 0, 0}};
    int option;
    while ((option = getopt_long(argc, argv, "", options, NULL)) != -1) {
        switch (option) {
            case 'c': configPath = optarg; break;
            case 'n': name = optarg; break;
            case 'm': modelsDir = optarg; break;
            case 'r': rendererSocket = optarg; break;
            case 'd': gAgent.devicePath = optarg; break;
            case 'N': useDevice = false; break;
            case 'a': preferChacha = false; break;
            case 'v': gLogVerbose = true; break;
            default: Usage(argv[0]); return 2;
        }
    }
    if (name == NULL) {
        Usage(argv[0]);
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, OnSignal);
    signal(SIGINT, OnSignal);

    ClusterConfig config;
    char error[256];
    if (!ConfigLoad(configPath, &config, error, sizeof(error))) {
        fprintf(stderr, "astrea-device-agent: %s\n", error);
        return 2;
    }
    gAgent.models = ModelStoreCreate(modelsDir);
    gAgent.renderer = RendererLinkStart(rendererSocket, error, sizeof(error));
    NodeCallbacks callbacks = {NodeUp, NodeDown, NodeMessage, NULL, NodeError};
    gAgent.node = NodeCreate(&config, name, ROLE_DEVICE_AGENT, config.hubDevicePort, preferChacha, callbacks, NULL, error, sizeof(error));
    if (gAgent.models == NULL || gAgent.renderer == NULL || gAgent.node == NULL) {
        fprintf(stderr, "astrea-device-agent: %s\n", error[0] != '\0' ? error : "out of memory");
        return 1;
    }
    printf("device agent '%s' dialing %s:%d, renderer socket %s\n", name, config.hubAddress, config.hubDevicePort, rendererSocket);
    fflush(stdout);
    pthread_t deviceThread;
    if (useDevice) pthread_create(&deviceThread, NULL, DeviceMain, NULL);
    NodeRun(gAgent.node);
    atomic_store(&gAgent.stopping, true);
    if (useDevice) pthread_join(deviceThread, NULL);
    RendererLinkStop(gAgent.renderer);
    ModelStoreDestroy(gAgent.models);
    NodeDestroy(gAgent.node);
    return 0;
}
