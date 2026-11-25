/* astrea-netd: the secure network daemon of a node. It does all the TLS 1.3 mutual authentication, framing,
 * heartbeats and reconnection in C, and hands whole messages to the local program (Python) over a Unix socket, so no
 * byte of network traffic ever passes through that program.
 *
 *   astrea-netd --config config.toml --mode hub  --socket /tmp/hub.sock [--link reconstruction|device]
 *   astrea-netd --config config.toml --mode node --name pi --role worker --socket /tmp/pi.sock
 *
 * Local protocol, both directions: [u32 length][u8 kind][body], integers in big-endian.
 *   daemon to client: PEER_UP id node role session | PEER_DOWN id reason | MESSAGE id type payload |
 *                     RTT id microseconds | REJECT reason | FATAL reason
 *   client to daemon: SEND id type payload (id 0 means the only link of a node) | CLOSE id reason | SHUTDOWN
 */
#include <errno.h>
#include <getopt.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "astrea-config.h"
#include "astrea-link.h"
#include "astrea-log.h"

enum { EV_PEER_UP = 1, EV_PEER_DOWN = 2, EV_MESSAGE = 3, EV_RTT = 4, EV_REJECT = 5, EV_FATAL = 6 };
enum { CMD_SEND = 1, CMD_CLOSE = 2, CMD_SHUTDOWN = 3 };

#define LOCAL_MAX_FRAME (MAX_FRAME_BYTES + 64)

typedef struct {
    bool hubMode;
    bool exitOnDetach;
    Hub *hub;
    Node *node;
    int listenFd;
    pthread_mutex_t clientMutex;
    int clientFd;
    pthread_t networkThread;
} Daemon;

static Daemon gDaemon;
static volatile sig_atomic_t gTerminate = 0;

static void PutU32(unsigned char *out, uint32_t value) {
    out[0] = (unsigned char)(value >> 24);
    out[1] = (unsigned char)(value >> 16);
    out[2] = (unsigned char)(value >> 8);
    out[3] = (unsigned char)value;
}

static uint32_t GetU32(const unsigned char *in) {
    return ((uint32_t)in[0] << 24) | ((uint32_t)in[1] << 16) | ((uint32_t)in[2] << 8) | in[3];
}

static bool WriteAll(int fd, const unsigned char *data, size_t length) {
    while (length > 0) {
        ssize_t written = send(fd, data, length, MSG_NOSIGNAL);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) return false;
        data += written;
        length -= (size_t)written;
    }
    return true;
}

static bool ReadAll(int fd, unsigned char *data, size_t length) {
    while (length > 0) {
        ssize_t received = recv(fd, data, length, 0);
        if (received < 0 && errno == EINTR) {
            if (gTerminate) return false;
            continue;
        }
        if (received <= 0) return false;
        data += received;
        length -= (size_t)received;
    }
    return true;
}

/* Sends one event to the attached client; events are dropped while nobody is attached. */
static void Emit(unsigned char kind, const unsigned char *head, size_t headLength, const unsigned char *tail, size_t tailLength) {
    size_t bodyLength = 1 + headLength + tailLength;
    unsigned char *frame = malloc(4 + bodyLength);
    if (frame == NULL) return;
    PutU32(frame, (uint32_t)bodyLength);
    frame[4] = kind;
    if (headLength > 0) memcpy(frame + 5, head, headLength);
    if (tailLength > 0) memcpy(frame + 5 + headLength, tail, tailLength);
    pthread_mutex_lock(&gDaemon.clientMutex);
    if (gDaemon.clientFd >= 0 && !WriteAll(gDaemon.clientFd, frame, 4 + bodyLength)) {
        close(gDaemon.clientFd);
        gDaemon.clientFd = -1;
    }
    pthread_mutex_unlock(&gDaemon.clientMutex);
    free(frame);
}

static void EmitText(unsigned char kind, const char *text) {
    size_t length = strlen(text);
    Emit(kind, (const unsigned char *)text, length, NULL, 0);
}

static void EmitPeerUp(unsigned id, const char *node, Role role, const char *session) {
    unsigned char head[4 + 1 + NODE_NAME_BYTES + 1 + 16 + 2 + 96];
    size_t length = 0;
    PutU32(head, id);
    length += 4;
    const char *roleName = RoleName(role);
    head[length++] = (unsigned char)strlen(node);
    memcpy(head + length, node, strlen(node));
    length += strlen(node);
    head[length++] = (unsigned char)strlen(roleName);
    memcpy(head + length, roleName, strlen(roleName));
    length += strlen(roleName);
    head[length++] = (unsigned char)(strlen(session) >> 8);
    head[length++] = (unsigned char)strlen(session);
    memcpy(head + length, session, strlen(session));
    length += strlen(session);
    Emit(EV_PEER_UP, head, length, NULL, 0);
}

static void EmitPeerDown(unsigned id, const char *reason) {
    unsigned char head[4];
    PutU32(head, id);
    Emit(EV_PEER_DOWN, head, 4, (const unsigned char *)reason, strlen(reason));
}

static void EmitMessage(unsigned id, int type, const unsigned char *payload, size_t size) {
    unsigned char head[5];
    PutU32(head, id);
    head[4] = (unsigned char)type;
    Emit(EV_MESSAGE, head, 5, payload, size);
}

static void EmitRtt(unsigned id, double rttMs) {
    unsigned char head[8];
    PutU32(head, id);
    PutU32(head + 4, (uint32_t)(rttMs * 1000.0));
    Emit(EV_RTT, head, 8, NULL, 0);
}

static void HubPeerUp(Hub *hub, unsigned id, const char *node, Role role, const char *session, void *user) { (void)hub; (void)user; EmitPeerUp(id, node, role, session); }
static void HubPeerDown(Hub *hub, unsigned id, const char *node, Role role, const char *reason, void *user) { (void)hub; (void)node; (void)role; (void)user; EmitPeerDown(id, reason); }
static void HubMessage(Hub *hub, unsigned id, int type, const unsigned char *payload, size_t size, void *user) { (void)hub; (void)user; EmitMessage(id, type, payload, size); }
static void HubRtt(Hub *hub, unsigned id, double rttMs, void *user) { (void)hub; (void)user; EmitRtt(id, rttMs); }
static void HubReject(Hub *hub, const char *reason, void *user) {
    (void)hub;
    (void)user;
    LOG_WARN("rejected connection: %s", reason);
    EmitText(EV_REJECT, reason);
}

static void NodeUp(Node *node, unsigned id, const char *hubNode, const char *session, void *user) { (void)node; (void)user; EmitPeerUp(id, hubNode, ROLE_COORDINATOR, session); }
static void NodeDown(Node *node, unsigned id, const char *reason, void *user) { (void)node; (void)user; EmitPeerDown(id, reason); }
static void NodeMessage(Node *node, unsigned id, int type, const unsigned char *payload, size_t size, void *user) { (void)node; (void)user; EmitMessage(id, type, payload, size); }
static void NodeRttEvent(Node *node, unsigned id, double rttMs, void *user) { (void)node; (void)user; EmitRtt(id, rttMs); }
static void NodeError(Node *node, const char *text, void *user) {
    (void)node;
    (void)user;
    LOG_WARN("could not reach the hub: %s", text);
}

static void ReplayCurrentPeers(void) {
    if (gDaemon.hubMode) {
        PeerInfo peers[MAX_PEERS];
        int count = HubSnapshot(gDaemon.hub, peers, MAX_PEERS);
        for (int i = 0; i < count; i++) EmitPeerUp(peers[i].id, peers[i].node, peers[i].role, peers[i].session);
        return;
    }
    unsigned id;
    char hubNode[NODE_NAME_BYTES], session[96];
    if (NodeCurrent(gDaemon.node, &id, hubNode, sizeof(hubNode), session, sizeof(session))) EmitPeerUp(id, hubNode, ROLE_COORDINATOR, session);
}

static void Shutdown(void) {
    if (gDaemon.hubMode) HubStop(gDaemon.hub);
    else NodeStop(gDaemon.node);
}

static void HandleCommand(unsigned char kind, const unsigned char *body, size_t length) {
    if (kind == CMD_SHUTDOWN) {
        gTerminate = 1;
        Shutdown();
        return;
    }
    if (length < 4) return;
    unsigned id = GetU32(body);
    if (kind == CMD_SEND && length >= 5) {
        int type = body[4];
        bool sent = gDaemon.hubMode ? HubSend(gDaemon.hub, id, type, body + 5, length - 5) : NodeSend(gDaemon.node, type, body + 5, length - 5);
        if (!sent) LOG_INFO("message type %d for peer %u was not sent", type, id);
    } else if (kind == CMD_CLOSE) {
        char reason[160];
        size_t reasonLength = length - 4 < sizeof(reason) - 1 ? length - 4 : sizeof(reason) - 1;
        memcpy(reason, body + 4, reasonLength);
        reason[reasonLength] = '\0';
        if (gDaemon.hubMode) HubClosePeer(gDaemon.hub, id, reason);
        else NodeClose(gDaemon.node, reason);
    }
}

static void ServeClient(int fd) {
    for (;;) {
        unsigned char header[4];
        if (!ReadAll(fd, header, 4)) return;
        uint32_t length = GetU32(header);
        if (length == 0 || length > LOCAL_MAX_FRAME) return;
        unsigned char *body = malloc(length);
        if (body == NULL || !ReadAll(fd, body, length)) {
            free(body);
            return;
        }
        HandleCommand(body[0], body + 1, length - 1);
        free(body);
        if (gTerminate) return;
    }
}

static void *NetworkMain(void *unused) {
    (void)unused;
    if (gDaemon.hubMode) HubServe(gDaemon.hub);
    else NodeRun(gDaemon.node);
    return NULL;
}

static void OnSignal(int signalNumber) {
    (void)signalNumber;
    gTerminate = 1;
}

static int ListenLocal(const char *path) {
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof(address.sun_path)) return -1;
    strcpy(address.sun_path, path);
    unlink(path);
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    mode_t previous = umask(0177);
    int status = bind(fd, (struct sockaddr *)&address, sizeof(address));
    umask(previous);
    if (status != 0 || listen(fd, 1) != 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static void Usage(const char *program) {
    fprintf(stderr, "usage: %s --config FILE --socket PATH --mode hub|node [--name NODE --role worker|device-agent] [--link reconstruction|device] [--prefer-chacha20] [--exit-on-detach] [--verbose]\n", program);
}

int main(int argc, char **argv) {
    const char *configPath = "config.toml", *socketPath = NULL, *mode = NULL, *name = NULL, *roleText = NULL, *link = "reconstruction";
    bool preferChacha = false;
    static const struct option options[] = {{"config", 1, 0, 'c'}, {"socket", 1, 0, 's'}, {"mode", 1, 0, 'm'}, {"name", 1, 0, 'n'}, {"role", 1, 0, 'r'},
                                            {"link", 1, 0, 'l'}, {"prefer-chacha20", 0, 0, 'p'}, {"exit-on-detach", 0, 0, 'x'}, {"verbose", 0, 0, 'v'}, {0, 0, 0, 0}};
    int option;
    while ((option = getopt_long(argc, argv, "", options, NULL)) != -1) {
        switch (option) {
            case 'c': configPath = optarg; break;
            case 's': socketPath = optarg; break;
            case 'm': mode = optarg; break;
            case 'n': name = optarg; break;
            case 'r': roleText = optarg; break;
            case 'l': link = optarg; break;
            case 'p': preferChacha = true; break;
            case 'x': gDaemon.exitOnDetach = true; break;
            case 'v': gLogVerbose = true; break;
            default: Usage(argv[0]); return 2;
        }
    }
    if (socketPath == NULL || mode == NULL || (strcmp(mode, "hub") != 0 && strcmp(mode, "node") != 0)) {
        Usage(argv[0]);
        return 2;
    }
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, OnSignal);
    signal(SIGINT, OnSignal);
    prctl(PR_SET_PDEATHSIG, SIGTERM);

    ClusterConfig config;
    char error[256];
    if (!ConfigLoad(configPath, &config, error, sizeof(error))) {
        fprintf(stderr, "astrea-netd: %s\n", error);
        return 2;
    }
    pthread_mutex_init(&gDaemon.clientMutex, NULL);
    gDaemon.clientFd = -1;
    gDaemon.hubMode = strcmp(mode, "hub") == 0;
    if (gDaemon.hubMode) {
        HubCallbacks callbacks = {HubPeerUp, HubPeerDown, HubMessage, HubRtt, HubReject};
        int port = strcmp(link, "device") == 0 ? config.hubDevicePort : config.hubPort;
        gDaemon.hub = HubCreate(&config, port, preferChacha, callbacks, NULL, error, sizeof(error));
        if (gDaemon.hub == NULL || !HubListen(gDaemon.hub, error, sizeof(error))) {
            fprintf(stderr, "astrea-netd: %s\n", error);
            return 1;
        }
        LOG_INFO("hub listening on %s:%d", config.hubBindAddress, port);
    } else {
        Role role = roleText != NULL ? RoleFromName(roleText) : ROLE_NONE;
        if (name == NULL || (role != ROLE_WORKER && role != ROLE_DEVICE_AGENT)) {
            Usage(argv[0]);
            return 2;
        }
        NodeCallbacks callbacks = {NodeUp, NodeDown, NodeMessage, NodeRttEvent, NodeError};
        gDaemon.node = NodeCreate(&config, name, role, role == ROLE_DEVICE_AGENT ? config.hubDevicePort : config.hubPort, preferChacha, callbacks, NULL, error, sizeof(error));
        if (gDaemon.node == NULL) {
            fprintf(stderr, "astrea-netd: %s\n", error);
            return 1;
        }
    }
    gDaemon.listenFd = ListenLocal(socketPath);
    if (gDaemon.listenFd < 0) {
        fprintf(stderr, "astrea-netd: cannot listen on %s: %s\n", socketPath, strerror(errno));
        return 1;
    }
    pthread_create(&gDaemon.networkThread, NULL, NetworkMain, NULL);

    while (!gTerminate) {
        struct pollfd wait = {gDaemon.listenFd, POLLIN, 0};
        if (poll(&wait, 1, 500) <= 0) continue;
        int client = accept(gDaemon.listenFd, NULL, NULL);
        if (client < 0) continue;
        pthread_mutex_lock(&gDaemon.clientMutex);
        if (gDaemon.clientFd >= 0) close(gDaemon.clientFd);
        gDaemon.clientFd = client;
        pthread_mutex_unlock(&gDaemon.clientMutex);
        ReplayCurrentPeers();
        ServeClient(client);
        pthread_mutex_lock(&gDaemon.clientMutex);
        if (gDaemon.clientFd == client) {
            close(client);
            gDaemon.clientFd = -1;
        }
        pthread_mutex_unlock(&gDaemon.clientMutex);
        if (gDaemon.exitOnDetach) gTerminate = 1;
    }
    Shutdown();
    pthread_join(gDaemon.networkThread, NULL);
    unlink(socketPath);
    return 0;
}
