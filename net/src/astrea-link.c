#include "astrea-link.h"

#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <openssl/err.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/eventfd.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#include <fcntl.h>

#include "astrea-log.h"
#include "astrea-tls.h"

#define RECEIVE_BYTES (64 * 1024)
#define MAX_QUEUED_BYTES (256u * 1024u * 1024u)
#define NS_PER_S 1000000000ull
#define REASON_BYTES 192
#define POLL_CAP_MS 1000

typedef struct OutChunk {
    unsigned char *data;
    size_t length;
    size_t offset;
    struct OutChunk *next;
} OutChunk;

struct Link {
    SSL *ssl;
    int fd;
    int wakeFd;
    bool localIsHub;
    Role nodeRole;
    char peerNode[NODE_NAME_BYTES];
    Role peerRole;
    char session[96];
    NetworkSettings network;
    LinkCallbacks callbacks;
    void *user;
    pthread_mutex_t mutex;
    OutChunk *head;
    OutChunk *tail;
    size_t queued;
    FrameParser parser;
    bool closing;
    char closeReason[REASON_BYTES];
    uint64_t lastRxNs;
    uint64_t sequence;
};

uint64_t NowNs(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * NS_PER_S + (uint64_t)now.tv_nsec;
}

static void SetTimeouts(int fd, double seconds) {
    struct timeval timeout = {.tv_sec = (time_t)seconds, .tv_usec = (suseconds_t)((seconds - (time_t)seconds) * 1e6)};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
}

static void PrepareSocket(int fd) {
    int one = 1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
}

/* ----- handshake (blocking, with socket timeouts) ----- */

static int ReadFrameBlocking(SSL *ssl, FrameParser *parser, int *type, const unsigned char **payload, size_t *size, char *error, size_t capacity) {
    unsigned char buffer[4096];
    for (;;) {
        const char *problem = NULL;
        int status = ParserNext(parser, type, payload, size, &problem);
        if (status < 0) {
            snprintf(error, capacity, "protocol error: %s", problem);
            return -1;
        }
        if (status > 0) return 1;
        int received = SSL_read(ssl, buffer, sizeof(buffer));
        if (received <= 0) {
            int code = SSL_get_error(ssl, received);
            snprintf(error, capacity, code == SSL_ERROR_ZERO_RETURN ? "the peer closed the connection before HELLO" : "no HELLO received in time");
            ERR_clear_error();
            return -1;
        }
        if (!ParserFeed(parser, buffer, (size_t)received)) {
            snprintf(error, capacity, "out of memory");
            return -1;
        }
    }
}

static bool WriteAllBlocking(SSL *ssl, const unsigned char *data, size_t length) {
    size_t done = 0;
    while (done < length) {
        int written = SSL_write(ssl, data + done, (int)(length - done));
        if (written <= 0) return false;
        done += (size_t)written;
    }
    return true;
}

static bool SendHello(SSL *ssl, const char *node, Role role) {
    MpWriter payload, frame;
    MpWriterInit(&payload);
    bool ok = HelloEncode(&payload, node, role) && FrameEncode(&frame, MSG_HELLO, payload.data, payload.size);
    MpWriterFree(&payload);
    if (!ok) return false;
    ok = WriteAllBlocking(ssl, frame.data, frame.size);
    MpWriterFree(&frame);
    return ok;
}

static bool ReceiveHello(SSL *ssl, FrameParser *parser, const char *who, int64_t *version, char *node, size_t nodeCapacity, Role *role, char *error, size_t errorCapacity) {
    int type;
    const unsigned char *payload;
    size_t size;
    if (ReadFrameBlocking(ssl, parser, &type, &payload, &size, error, errorCapacity) < 0) return false;
    if (type != MSG_HELLO) {
        snprintf(error, errorCapacity, "%s sent message type %d instead of HELLO", who, type);
        return false;
    }
    if (!HelloDecode(payload, size, version, node, nodeCapacity, role)) {
        snprintf(error, errorCapacity, "%s sent an invalid HELLO", who);
        return false;
    }
    if (*version != PROTOCOL_VERSION) {
        snprintf(error, errorCapacity, "%s speaks protocol %lld, this node speaks %d", who, (long long)*version, PROTOCOL_VERSION);
        return false;
    }
    return true;
}

static Link *LinkCreate(SSL *ssl, int fd, bool localIsHub, Role nodeRole, const char *peerNode, Role peerRole, const NetworkSettings *network, FrameParser *parser, LinkCallbacks callbacks, void *user) {
    Link *link = calloc(1, sizeof(*link));
    if (link == NULL) return NULL;
    link->wakeFd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    if (link->wakeFd < 0) {
        free(link);
        return NULL;
    }
    link->ssl = ssl;
    link->fd = fd;
    link->localIsHub = localIsHub;
    link->nodeRole = nodeRole;
    snprintf(link->peerNode, sizeof(link->peerNode), "%s", peerNode);
    link->peerRole = peerRole;
    TlsDescribe(ssl, link->session, sizeof(link->session));
    link->network = *network;
    link->callbacks = callbacks;
    link->user = user;
    link->parser = *parser;
    ParserInit(parser);
    pthread_mutex_init(&link->mutex, NULL);
    return link;
}

static void AbandonHandshake(SSL *ssl, int fd, FrameParser *parser) {
    ParserFree(parser);
    SSL_free(ssl);
    close(fd);
}

Link *LinkAcceptPeer(SSL_CTX *context, int fd, const ClusterConfig *config, LinkCallbacks callbacks, void *user, char *error, size_t errorCapacity) {
    PrepareSocket(fd);
    SetTimeouts(fd, config->network.connectTimeoutS);
    FrameParser parser;
    ParserInit(&parser);
    SSL *ssl = SSL_new(context);
    if (ssl == NULL || SSL_set_fd(ssl, fd) != 1) {
        snprintf(error, errorCapacity, "cannot start TLS");
        if (ssl != NULL) SSL_free(ssl);
        close(fd);
        return NULL;
    }
    if (SSL_accept(ssl) != 1) {
        char detail[160];
        TlsLastError(detail, sizeof(detail));
        snprintf(error, errorCapacity, "TLS handshake failed: %s", detail);
        AbandonHandshake(ssl, fd, &parser);
        return NULL;
    }
    char certificateName[NODE_NAME_BYTES], helloNode[NODE_NAME_BYTES];
    int64_t version;
    Role helloRole;
    if (!TlsPeerNodeName(ssl, certificateName, sizeof(certificateName))) {
        snprintf(error, errorCapacity, "the certificate carries no node name");
        AbandonHandshake(ssl, fd, &parser);
        return NULL;
    }
    if (!ReceiveHello(ssl, &parser, certificateName, &version, helloNode, sizeof(helloNode), &helloRole, error, errorCapacity)) {
        AbandonHandshake(ssl, fd, &parser);
        return NULL;
    }
    const NodeConfig *known = ConfigNode(config, certificateName);
    if (strcmp(helloNode, certificateName) != 0) {
        snprintf(error, errorCapacity, "certificate says '%s' but HELLO says '%s'", certificateName, helloNode);
    } else if (helloRole == ROLE_COORDINATOR || known == NULL || !(known->roles & helloRole)) {
        snprintf(error, errorCapacity, "node '%s' is not allowed to connect as '%s'", certificateName, RoleName(helloRole));
    } else if (!SendHello(ssl, config->hubNode, ROLE_COORDINATOR)) {
        snprintf(error, errorCapacity, "could not answer HELLO");
    } else {
        SetTimeouts(fd, 0);
        Link *link = LinkCreate(ssl, fd, true, helloRole, certificateName, helloRole, &config->network, &parser, callbacks, user);
        if (link != NULL) return link;
        snprintf(error, errorCapacity, "out of memory");
    }
    AbandonHandshake(ssl, fd, &parser);
    return NULL;
}

static int ConnectTcp(const char *address, int port, double timeoutS, char *error, size_t errorCapacity) {
    char service[16];
    snprintf(service, sizeof(service), "%d", port);
    struct addrinfo hints = {.ai_family = AF_UNSPEC, .ai_socktype = SOCK_STREAM};
    struct addrinfo *found = NULL;
    int status = getaddrinfo(address, service, &hints, &found);
    if (status != 0) {
        snprintf(error, errorCapacity, "cannot resolve %s: %s", address, gai_strerror(status));
        return -1;
    }
    int fd = -1;
    for (struct addrinfo *item = found; item != NULL && fd < 0; item = item->ai_next) {
        int candidate = socket(item->ai_family, item->ai_socktype, item->ai_protocol);
        if (candidate < 0) continue;
        SetTimeouts(candidate, timeoutS);
        if (connect(candidate, item->ai_addr, item->ai_addrlen) == 0) fd = candidate;
        else {
            snprintf(error, errorCapacity, "cannot connect to %s:%d: %s", address, port, strerror(errno));
            close(candidate);
        }
    }
    freeaddrinfo(found);
    return fd;
}

Link *LinkDialHub(SSL_CTX *context, const ClusterConfig *config, const char *nodeName, Role role, int port, LinkCallbacks callbacks, void *user, char *error, size_t errorCapacity) {
    const NodeConfig *self = ConfigNode(config, nodeName);
    if (self == NULL || !(self->roles & role)) {
        snprintf(error, errorCapacity, "node '%s' is not configured with role '%s'", nodeName, RoleName(role));
        return NULL;
    }
    int fd = ConnectTcp(config->hubAddress, port, config->network.connectTimeoutS, error, errorCapacity);
    if (fd < 0) return NULL;
    PrepareSocket(fd);
    FrameParser parser;
    ParserInit(&parser);
    SSL *ssl = SSL_new(context);
    if (ssl == NULL || SSL_set_fd(ssl, fd) != 1 || SSL_set1_host(ssl, config->hubNode) != 1 || SSL_set_tlsext_host_name(ssl, config->hubNode) != 1) {
        snprintf(error, errorCapacity, "cannot start TLS");
        if (ssl != NULL) SSL_free(ssl);
        close(fd);
        return NULL;
    }
    if (SSL_connect(ssl) != 1) {
        char detail[160];
        TlsLastError(detail, sizeof(detail));
        snprintf(error, errorCapacity, "TLS handshake failed: %s", detail);
        AbandonHandshake(ssl, fd, &parser);
        return NULL;
    }
    char hubName[NODE_NAME_BYTES], helloNode[NODE_NAME_BYTES];
    int64_t version;
    Role helloRole;
    if (!TlsPeerNodeName(ssl, hubName, sizeof(hubName))) {
        snprintf(error, errorCapacity, "the hub certificate carries no node name");
    } else if (!SendHello(ssl, nodeName, role)) {
        snprintf(error, errorCapacity, "could not send HELLO");
    } else if (ReceiveHello(ssl, &parser, "the hub", &version, helloNode, sizeof(helloNode), &helloRole, error, errorCapacity)) {
        if (strcmp(helloNode, hubName) != 0 || strcmp(helloNode, config->hubNode) != 0 || helloRole != ROLE_COORDINATOR) {
            snprintf(error, errorCapacity, "unexpected hub identity '%s' as '%s'", helloNode, RoleName(helloRole));
        } else {
            SetTimeouts(fd, 0);
            Link *link = LinkCreate(ssl, fd, false, role, hubName, ROLE_COORDINATOR, &config->network, &parser, callbacks, user);
            if (link != NULL) return link;
            snprintf(error, errorCapacity, "out of memory");
        }
    }
    AbandonHandshake(ssl, fd, &parser);
    return NULL;
}

/* ----- running link ----- */

static void Wake(Link *link) {
    uint64_t one = 1;
    ssize_t ignored = write(link->wakeFd, &one, sizeof(one));
    (void)ignored;
}

void LinkClose(Link *link, const char *reason) {
    pthread_mutex_lock(&link->mutex);
    if (!link->closing) {
        link->closing = true;
        snprintf(link->closeReason, sizeof(link->closeReason), "%s", reason);
    }
    pthread_mutex_unlock(&link->mutex);
    Wake(link);
}

static bool IsClosing(Link *link) {
    pthread_mutex_lock(&link->mutex);
    bool closing = link->closing;
    pthread_mutex_unlock(&link->mutex);
    return closing;
}

static bool Enqueue(Link *link, int type, const void *payload, size_t size) {
    MpWriter frame;
    if (!FrameEncode(&frame, type, payload, size)) return false;
    OutChunk *chunk = calloc(1, sizeof(*chunk));
    if (chunk == NULL) {
        MpWriterFree(&frame);
        return false;
    }
    chunk->data = frame.data;
    chunk->length = frame.size;
    pthread_mutex_lock(&link->mutex);
    bool accepted = !link->closing && link->queued + chunk->length <= MAX_QUEUED_BYTES;
    if (accepted) {
        if (link->tail != NULL) link->tail->next = chunk;
        else link->head = chunk;
        link->tail = chunk;
        link->queued += chunk->length;
    }
    pthread_mutex_unlock(&link->mutex);
    if (!accepted) {
        free(chunk->data);
        free(chunk);
        return false;
    }
    Wake(link);
    return true;
}

bool LinkSend(Link *link, int type, const void *payload, size_t size) {
    if (type == MSG_HELLO || type == MSG_HEARTBEAT || !IsMessageAllowed(type, link->localIsHub, link->nodeRole)) return false;
    return Enqueue(link, type, payload, size);
}

static void Fail(Link *link, const char *format, const char *detail) {
    char reason[REASON_BYTES];
    snprintf(reason, sizeof(reason), format, detail);
    LinkClose(link, reason);
}

static bool FlushWrites(Link *link, bool *wantWrite) {
    for (;;) {
        pthread_mutex_lock(&link->mutex);
        OutChunk *chunk = link->head;
        pthread_mutex_unlock(&link->mutex);
        if (chunk == NULL) return true;
        int written = SSL_write(link->ssl, chunk->data + chunk->offset, (int)(chunk->length - chunk->offset));
        if (written > 0) {
            chunk->offset += (size_t)written;
            if (chunk->offset == chunk->length) {
                pthread_mutex_lock(&link->mutex);
                link->head = chunk->next;
                if (link->head == NULL) link->tail = NULL;
                link->queued -= chunk->length;
                pthread_mutex_unlock(&link->mutex);
                free(chunk->data);
                free(chunk);
            }
            continue;
        }
        int code = SSL_get_error(link->ssl, written);
        if (code == SSL_ERROR_WANT_WRITE) {
            *wantWrite = true;
            return true;
        }
        if (code == SSL_ERROR_WANT_READ) return true;
        Fail(link, "connection error: %s", code == SSL_ERROR_SYSCALL && errno != 0 ? strerror(errno) : "the peer closed the connection");
        ERR_clear_error();
        return false;
    }
}

static bool Dispatch(Link *link, int type, const unsigned char *payload, size_t size) {
    if (type == MSG_HELLO) {
        Fail(link, "protocol error: %s", "HELLO is only valid at the start of a connection");
        return false;
    }
    if (!MpIsValidMap(payload, size)) {
        Fail(link, "protocol error: %s", "the payload is not a valid map");
        return false;
    }
    if (!IsMessageAllowed(type, !link->localIsHub, link->nodeRole)) {
        char what[160];
        snprintf(what, sizeof(what), "message type %d is not allowed from %s (%s)", type, link->peerNode, RoleName(link->peerRole));
        Fail(link, "protocol error: %s", what);
        return false;
    }
    if (type != MSG_HEARTBEAT) {
        if (link->callbacks.onMessage != NULL) link->callbacks.onMessage(link, type, payload, size, link->user);
        return true;
    }
    uint64_t sequence, sentAtNs;
    bool echo;
    if (!HeartbeatDecode(payload, size, &sequence, &sentAtNs, &echo)) {
        Fail(link, "protocol error: %s", "invalid HEARTBEAT");
        return false;
    }
    if (!echo) {
        MpWriter body;
        MpWriterInit(&body);
        bool queued = HeartbeatEncode(&body, sequence, sentAtNs, true) && Enqueue(link, MSG_HEARTBEAT, body.data, body.size);
        MpWriterFree(&body);
        return queued || IsClosing(link);
    }
    uint64_t now = NowNs();
    if (now >= sentAtNs && link->callbacks.onRtt != NULL) link->callbacks.onRtt(link, (double)(now - sentAtNs) / 1e6, link->user);
    return true;
}

static bool DrainFrames(Link *link) {
    for (;;) {
        int type;
        const unsigned char *payload;
        size_t size;
        const char *problem = NULL;
        int status = ParserNext(&link->parser, &type, &payload, &size, &problem);
        if (status == 0) return true;
        if (status < 0) {
            Fail(link, "protocol error: %s", problem);
            return false;
        }
        if (!Dispatch(link, type, payload, size)) return false;
    }
}

static bool ReadAvailable(Link *link, unsigned char *buffer, size_t capacity, bool *wantWrite) {
    for (;;) {
        int received = SSL_read(link->ssl, buffer, (int)capacity);
        if (received > 0) {
            link->lastRxNs = NowNs();
            if (!ParserFeed(&link->parser, buffer, (size_t)received)) {
                Fail(link, "%s", "out of memory");
                return false;
            }
            if (!DrainFrames(link)) return false;
            continue;
        }
        int code = SSL_get_error(link->ssl, received);
        if (code == SSL_ERROR_WANT_READ) return true;
        if (code == SSL_ERROR_WANT_WRITE) {
            *wantWrite = true;
            return true;
        }
        if (code == SSL_ERROR_ZERO_RETURN) Fail(link, "%s", "peer closed the connection");
        else if (code == SSL_ERROR_SYSCALL) Fail(link, "connection error: %s", errno != 0 ? strerror(errno) : "unexpected end of the stream");
        else {
            char detail[120];
            TlsLastError(detail, sizeof(detail));
            Fail(link, "TLS error: %s", detail);
        }
        ERR_clear_error();
        return false;
    }
}

static void QueueHeartbeat(Link *link) {
    MpWriter body;
    MpWriterInit(&body);
    link->sequence++;
    if (HeartbeatEncode(&body, link->sequence, NowNs(), false)) Enqueue(link, MSG_HEARTBEAT, body.data, body.size);
    MpWriterFree(&body);
}

void LinkRun(Link *link) {
    int flags = fcntl(link->fd, F_GETFL, 0);
    fcntl(link->fd, F_SETFL, flags | O_NONBLOCK);
    uint64_t intervalNs = (uint64_t)(link->network.heartbeatIntervalS * 1e9);
    uint64_t timeoutNs = (uint64_t)(link->network.heartbeatTimeoutS * 1e9);
    link->lastRxNs = NowNs();
    uint64_t nextBeatNs = link->lastRxNs + intervalNs;
    bool wantWrite = false;
    unsigned char *buffer = malloc(RECEIVE_BYTES);
    if (buffer == NULL) LinkClose(link, "out of memory");
    while (buffer != NULL && !IsClosing(link)) {
        if (!DrainFrames(link)) break;
        struct pollfd fds[2] = {{link->fd, (short)(POLLIN | (wantWrite ? POLLOUT : 0)), 0}, {link->wakeFd, POLLIN, 0}};
        uint64_t now = NowNs();
        uint64_t deadline = nextBeatNs < link->lastRxNs + timeoutNs ? nextBeatNs : link->lastRxNs + timeoutNs;
        int waitMs = deadline > now ? (int)((deadline - now) / 1000000ull) + 1 : 0;
        if (waitMs > POLL_CAP_MS) waitMs = POLL_CAP_MS;
        poll(fds, 2, waitMs);
        if (fds[1].revents & POLLIN) {
            uint64_t counter;
            ssize_t ignored = read(link->wakeFd, &counter, sizeof(counter));
            (void)ignored;
        }
        wantWrite = false;
        if (IsClosing(link) || !FlushWrites(link, &wantWrite)) break;
        if ((fds[0].revents & (POLLIN | POLLERR | POLLHUP)) || SSL_pending(link->ssl) > 0)
            if (!ReadAvailable(link, buffer, RECEIVE_BYTES, &wantWrite)) break;
        now = NowNs();
        if (now >= nextBeatNs) {
            QueueHeartbeat(link);
            nextBeatNs = now + intervalNs;
        }
        if (now - link->lastRxNs > timeoutNs) LinkClose(link, "heartbeat timeout");
    }
    free(buffer);
    SSL_shutdown(link->ssl);
    close(link->fd);
    link->fd = -1;
}

void LinkFree(Link *link) {
    if (link == NULL) return;
    SSL_free(link->ssl);
    close(link->wakeFd);
    for (OutChunk *chunk = link->head; chunk != NULL;) {
        OutChunk *next = chunk->next;
        free(chunk->data);
        free(chunk);
        chunk = next;
    }
    ParserFree(&link->parser);
    pthread_mutex_destroy(&link->mutex);
    free(link);
}

const char *LinkPeerNode(const Link *link) { return link->peerNode; }
Role LinkPeerRole(const Link *link) { return link->peerRole; }
const char *LinkSession(const Link *link) { return link->session; }

void LinkCloseReason(Link *link, char *out, size_t capacity) {
    pthread_mutex_lock(&link->mutex);
    snprintf(out, capacity, "%s", link->closeReason[0] != '\0' ? link->closeReason : "closed");
    pthread_mutex_unlock(&link->mutex);
}

/* ----- hub ----- */

typedef struct {
    bool used;
    unsigned id;
    char node[NODE_NAME_BYTES];
    Role role;
    char session[96];
    Link *link;
} PeerSlot;

struct Hub {
    ClusterConfig config;
    int port;
    SSL_CTX *context;
    int listenFd;
    HubCallbacks callbacks;
    void *user;
    pthread_mutex_t mutex;
    PeerSlot peers[MAX_PEERS];
    atomic_bool stopping;
    atomic_uint nextId;
    atomic_int activeThreads;
};

typedef struct {
    Hub *hub;
    unsigned id;
} HubLinkUser;

typedef struct {
    Hub *hub;
    int fd;
} HubClient;

Hub *HubCreate(const ClusterConfig *config, int port, bool preferChacha, HubCallbacks callbacks, void *user, char *error, size_t errorCapacity) {
    Hub *hub = calloc(1, sizeof(*hub));
    if (hub == NULL) return NULL;
    hub->config = *config;
    hub->port = port;
    hub->callbacks = callbacks;
    hub->user = user;
    hub->listenFd = -1;
    pthread_mutex_init(&hub->mutex, NULL);
    hub->context = TlsServerContext(config, config->hubNode, preferChacha, error, errorCapacity);
    if (hub->context == NULL) {
        free(hub);
        return NULL;
    }
    return hub;
}

bool HubListen(Hub *hub, char *error, size_t errorCapacity) {
    char service[16];
    snprintf(service, sizeof(service), "%d", hub->port);
    struct addrinfo hints = {.ai_family = AF_UNSPEC, .ai_socktype = SOCK_STREAM, .ai_flags = AI_PASSIVE};
    struct addrinfo *found = NULL;
    int status = getaddrinfo(hub->config.hubBindAddress, service, &hints, &found);
    if (status != 0) {
        snprintf(error, errorCapacity, "cannot resolve %s: %s", hub->config.hubBindAddress, gai_strerror(status));
        return false;
    }
    for (struct addrinfo *item = found; item != NULL && hub->listenFd < 0; item = item->ai_next) {
        int fd = socket(item->ai_family, item->ai_socktype, item->ai_protocol);
        if (fd < 0) continue;
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
        if (bind(fd, item->ai_addr, item->ai_addrlen) == 0 && listen(fd, 16) == 0) hub->listenFd = fd;
        else {
            snprintf(error, errorCapacity, "cannot listen on %s:%d: %s", hub->config.hubBindAddress, hub->port, strerror(errno));
            close(fd);
        }
    }
    freeaddrinfo(found);
    return hub->listenFd >= 0;
}

static void HubOnMessage(Link *link, int type, const unsigned char *payload, size_t size, void *user) {
    (void)link;
    HubLinkUser *owner = user;
    if (owner->hub->callbacks.onMessage != NULL) owner->hub->callbacks.onMessage(owner->hub, owner->id, type, payload, size, owner->hub->user);
}

static void HubOnRtt(Link *link, double rttMs, void *user) {
    (void)link;
    HubLinkUser *owner = user;
    if (owner->hub->callbacks.onRtt != NULL) owner->hub->callbacks.onRtt(owner->hub, owner->id, rttMs, owner->hub->user);
}

static PeerSlot *ClaimSlot(Hub *hub, const char *node, Role role, Link **previous) {
    PeerSlot *free = NULL;
    for (int i = 0; i < MAX_PEERS; i++) {
        PeerSlot *slot = &hub->peers[i];
        if (slot->used && slot->role == role && strcmp(slot->node, node) == 0) {
            *previous = slot->link;
            return slot;
        }
        if (!slot->used && free == NULL) free = slot;
    }
    return free;
}

static void *HubClientThread(void *argument) {
    HubClient *client = argument;
    Hub *hub = client->hub;
    char reason[REASON_BYTES];
    HubLinkUser *owner = calloc(1, sizeof(*owner));
    LinkCallbacks callbacks = {HubOnMessage, HubOnRtt};
    Link *link = owner != NULL ? LinkAcceptPeer(hub->context, client->fd, &hub->config, callbacks, owner, reason, sizeof(reason)) : NULL;
    free(client);
    if (link == NULL) {
        if (owner == NULL) snprintf(reason, sizeof(reason), "out of memory");
        if (hub->callbacks.onReject != NULL) hub->callbacks.onReject(hub, reason, hub->user);
        free(owner);
        atomic_fetch_sub(&hub->activeThreads, 1);
        return NULL;
    }
    owner->hub = hub;
    owner->id = atomic_fetch_add(&hub->nextId, 1) + 1;
    Link *previous = NULL;
    pthread_mutex_lock(&hub->mutex);
    PeerSlot *slot = ClaimSlot(hub, LinkPeerNode(link), LinkPeerRole(link), &previous);
    if (slot != NULL) {
        slot->used = true;
        slot->id = owner->id;
        snprintf(slot->node, sizeof(slot->node), "%s", LinkPeerNode(link));
        slot->role = LinkPeerRole(link);
        snprintf(slot->session, sizeof(slot->session), "%s", LinkSession(link));
        slot->link = link;
    }
    pthread_mutex_unlock(&hub->mutex);
    if (slot == NULL) {
        if (hub->callbacks.onReject != NULL) hub->callbacks.onReject(hub, "too many connected peers", hub->user);
        LinkClose(link, "too many connected peers");
        LinkRun(link);
        LinkFree(link);
        free(owner);
        atomic_fetch_sub(&hub->activeThreads, 1);
        return NULL;
    }
    if (previous != NULL) LinkClose(previous, "replaced by a newer connection");
    if (hub->callbacks.onPeerUp != NULL) hub->callbacks.onPeerUp(hub, owner->id, LinkPeerNode(link), LinkPeerRole(link), LinkSession(link), hub->user);
    LinkRun(link);
    pthread_mutex_lock(&hub->mutex);
    for (int i = 0; i < MAX_PEERS; i++)
        if (hub->peers[i].used && hub->peers[i].link == link) hub->peers[i].used = false;
    pthread_mutex_unlock(&hub->mutex);
    LinkCloseReason(link, reason, sizeof(reason));
    if (hub->callbacks.onPeerDown != NULL) hub->callbacks.onPeerDown(hub, owner->id, LinkPeerNode(link), LinkPeerRole(link), reason, hub->user);
    LinkFree(link);
    free(owner);
    atomic_fetch_sub(&hub->activeThreads, 1);
    return NULL;
}

void HubServe(Hub *hub) {
    while (!atomic_load(&hub->stopping)) {
        int fd = accept(hub->listenFd, NULL, NULL);
        if (fd < 0) {
            if (errno == EINTR || errno == ECONNABORTED) continue;
            break;
        }
        HubClient *client = malloc(sizeof(*client));
        pthread_t thread;
        pthread_attr_t attributes;
        pthread_attr_init(&attributes);
        pthread_attr_setdetachstate(&attributes, PTHREAD_CREATE_DETACHED);
        if (client == NULL) {
            close(fd);
            continue;
        }
        client->hub = hub;
        client->fd = fd;
        atomic_fetch_add(&hub->activeThreads, 1);
        if (pthread_create(&thread, &attributes, HubClientThread, client) != 0) {
            atomic_fetch_sub(&hub->activeThreads, 1);
            close(fd);
            free(client);
        }
        pthread_attr_destroy(&attributes);
    }
}

void HubStop(Hub *hub) {
    atomic_store(&hub->stopping, true);
    if (hub->listenFd >= 0) shutdown(hub->listenFd, SHUT_RDWR);
    pthread_mutex_lock(&hub->mutex);
    for (int i = 0; i < MAX_PEERS; i++)
        if (hub->peers[i].used) LinkClose(hub->peers[i].link, "hub stopped");
    pthread_mutex_unlock(&hub->mutex);
    for (int waited = 0; waited < 100 && atomic_load(&hub->activeThreads) > 0; waited++) usleep(20000);
}

void HubDestroy(Hub *hub) {
    if (hub == NULL) return;
    if (hub->listenFd >= 0) close(hub->listenFd);
    SSL_CTX_free(hub->context);
    pthread_mutex_destroy(&hub->mutex);
    free(hub);
}

bool HubSend(Hub *hub, unsigned peerId, int type, const void *payload, size_t size) {
    bool sent = false;
    pthread_mutex_lock(&hub->mutex);
    for (int i = 0; i < MAX_PEERS && !sent; i++)
        if (hub->peers[i].used && hub->peers[i].id == peerId) sent = LinkSend(hub->peers[i].link, type, payload, size);
    pthread_mutex_unlock(&hub->mutex);
    return sent;
}

void HubClosePeer(Hub *hub, unsigned peerId, const char *reason) {
    pthread_mutex_lock(&hub->mutex);
    for (int i = 0; i < MAX_PEERS; i++)
        if (hub->peers[i].used && hub->peers[i].id == peerId) LinkClose(hub->peers[i].link, reason);
    pthread_mutex_unlock(&hub->mutex);
}

int HubSnapshot(Hub *hub, PeerInfo *out, int capacity) {
    int count = 0;
    pthread_mutex_lock(&hub->mutex);
    for (int i = 0; i < MAX_PEERS && count < capacity; i++) {
        if (!hub->peers[i].used) continue;
        out[count].id = hub->peers[i].id;
        snprintf(out[count].node, sizeof(out[count].node), "%s", hub->peers[i].node);
        out[count].role = hub->peers[i].role;
        snprintf(out[count].session, sizeof(out[count].session), "%s", hub->peers[i].session);
        count++;
    }
    pthread_mutex_unlock(&hub->mutex);
    return count;
}

/* ----- node ----- */

struct Node {
    ClusterConfig config;
    char name[NODE_NAME_BYTES];
    Role role;
    int port;
    SSL_CTX *context;
    NodeCallbacks callbacks;
    void *user;
    pthread_mutex_t mutex;
    Link *current;
    unsigned currentId;
    atomic_bool stopping;
    atomic_uint nextId;
    int stopFd;
};

typedef struct {
    Node *node;
    unsigned id;
} NodeLinkUser;

Node *NodeCreate(const ClusterConfig *config, const char *name, Role role, int port, bool preferChacha, NodeCallbacks callbacks, void *user, char *error, size_t errorCapacity) {
    Node *node = calloc(1, sizeof(*node));
    if (node == NULL) return NULL;
    node->config = *config;
    snprintf(node->name, sizeof(node->name), "%s", name);
    node->role = role;
    node->port = port;
    node->callbacks = callbacks;
    node->user = user;
    pthread_mutex_init(&node->mutex, NULL);
    node->stopFd = eventfd(0, EFD_NONBLOCK | EFD_CLOEXEC);
    node->context = TlsClientContext(config, name, preferChacha, error, errorCapacity);
    if (node->context == NULL || node->stopFd < 0) {
        if (node->context != NULL) SSL_CTX_free(node->context);
        if (node->stopFd >= 0) close(node->stopFd);
        free(node);
        return NULL;
    }
    return node;
}

static void NodeOnMessage(Link *link, int type, const unsigned char *payload, size_t size, void *user) {
    (void)link;
    NodeLinkUser *owner = user;
    if (owner->node->callbacks.onMessage != NULL) owner->node->callbacks.onMessage(owner->node, owner->id, type, payload, size, owner->node->user);
}

static void NodeOnRtt(Link *link, double rttMs, void *user) {
    (void)link;
    NodeLinkUser *owner = user;
    if (owner->node->callbacks.onRtt != NULL) owner->node->callbacks.onRtt(owner->node, owner->id, rttMs, owner->node->user);
}

static void SleepUnlessStopped(Node *node, double seconds) {
    struct pollfd wake = {node->stopFd, POLLIN, 0};
    poll(&wake, 1, (int)(seconds * 1000));
}

void NodeRun(Node *node) {
    const NetworkSettings *network = &node->config.network;
    double delay = network->reconnectInitialS;
    LinkCallbacks callbacks = {NodeOnMessage, NodeOnRtt};
    while (!atomic_load(&node->stopping)) {
        char error[REASON_BYTES] = "";
        NodeLinkUser owner = {node, 0};
        Link *link = LinkDialHub(node->context, &node->config, node->name, node->role, node->port, callbacks, &owner, error, sizeof(error));
        if (link == NULL) {
            if (node->callbacks.onError != NULL) node->callbacks.onError(node, error, node->user);
            SleepUnlessStopped(node, delay);
            delay = delay * 2 < network->reconnectMaxS ? delay * 2 : network->reconnectMaxS;
            continue;
        }
        delay = network->reconnectInitialS;
        owner.id = atomic_fetch_add(&node->nextId, 1) + 1;
        pthread_mutex_lock(&node->mutex);
        node->current = link;
        node->currentId = owner.id;
        pthread_mutex_unlock(&node->mutex);
        if (atomic_load(&node->stopping)) LinkClose(link, "node stopped");
        if (node->callbacks.onUp != NULL) node->callbacks.onUp(node, owner.id, LinkPeerNode(link), LinkSession(link), node->user);
        LinkRun(link);
        pthread_mutex_lock(&node->mutex);
        node->current = NULL;
        pthread_mutex_unlock(&node->mutex);
        char reason[REASON_BYTES];
        LinkCloseReason(link, reason, sizeof(reason));
        if (node->callbacks.onDown != NULL) node->callbacks.onDown(node, owner.id, reason, node->user);
        LinkFree(link);
        SleepUnlessStopped(node, delay);
    }
}

void NodeStop(Node *node) {
    atomic_store(&node->stopping, true);
    uint64_t one = 1;
    ssize_t ignored = write(node->stopFd, &one, sizeof(one));
    (void)ignored;
    NodeClose(node, "node stopped");
}

void NodeClose(Node *node, const char *reason) {
    pthread_mutex_lock(&node->mutex);
    if (node->current != NULL) LinkClose(node->current, reason);
    pthread_mutex_unlock(&node->mutex);
}

void NodeDestroy(Node *node) {
    if (node == NULL) return;
    SSL_CTX_free(node->context);
    close(node->stopFd);
    pthread_mutex_destroy(&node->mutex);
    free(node);
}

bool NodeSend(Node *node, int type, const void *payload, size_t size) {
    bool sent = false;
    pthread_mutex_lock(&node->mutex);
    if (node->current != NULL) sent = LinkSend(node->current, type, payload, size);
    pthread_mutex_unlock(&node->mutex);
    return sent;
}

bool NodeCurrent(Node *node, unsigned *id, char *hubNode, size_t hubCapacity, char *session, size_t sessionCapacity) {
    bool connected = false;
    pthread_mutex_lock(&node->mutex);
    if (node->current != NULL) {
        connected = true;
        *id = node->currentId;
        snprintf(hubNode, hubCapacity, "%s", LinkPeerNode(node->current));
        snprintf(session, sessionCapacity, "%s", LinkSession(node->current));
    }
    pthread_mutex_unlock(&node->mutex);
    return connected;
}
