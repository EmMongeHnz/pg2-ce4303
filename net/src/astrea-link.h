/* Secure links between the hub (the host PC) and the nodes. A `Link` is one established TLS connection after the
 * HELLO exchange; it runs its own receive, send and heartbeat loop in the calling thread. `Hub` accepts nodes and
 * `Node` dials the hub and reconnects with exponential backoff. */
#ifndef ASTREA_LINK_H
#define ASTREA_LINK_H

#include <openssl/ssl.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "astrea-config.h"
#include "astrea-protocol.h"

typedef struct Link Link;

typedef struct {
    void (*onMessage)(Link *link, int type, const unsigned char *payload, size_t size, void *user);
    void (*onRtt)(Link *link, double rttMs, void *user);
} LinkCallbacks;

uint64_t NowNs(void);

/* The handshakes close `fd` on failure and fill `error` with the reason. */
Link *LinkAcceptPeer(SSL_CTX *context, int fd, const ClusterConfig *config, LinkCallbacks callbacks, void *user, char *error, size_t errorCapacity);
Link *LinkDialHub(SSL_CTX *context, const ClusterConfig *config, const char *nodeName, Role role, int port, LinkCallbacks callbacks, void *user, char *error, size_t errorCapacity);

void LinkRun(Link *link);
bool LinkSend(Link *link, int type, const void *payload, size_t size);
void LinkClose(Link *link, const char *reason);
void LinkFree(Link *link);
const char *LinkPeerNode(const Link *link);
Role LinkPeerRole(const Link *link);
const char *LinkSession(const Link *link);
void LinkCloseReason(Link *link, char *out, size_t capacity);

#define MAX_PEERS 32

typedef struct Hub Hub;

typedef struct {
    void (*onPeerUp)(Hub *hub, unsigned id, const char *node, Role role, const char *session, void *user);
    void (*onPeerDown)(Hub *hub, unsigned id, const char *node, Role role, const char *reason, void *user);
    void (*onMessage)(Hub *hub, unsigned id, int type, const unsigned char *payload, size_t size, void *user);
    void (*onRtt)(Hub *hub, unsigned id, double rttMs, void *user);
    void (*onReject)(Hub *hub, const char *reason, void *user);
} HubCallbacks;

typedef struct {
    unsigned id;
    char node[NODE_NAME_BYTES];
    Role role;
    char session[96];
} PeerInfo;

Hub *HubCreate(const ClusterConfig *config, int port, bool preferChacha, HubCallbacks callbacks, void *user, char *error, size_t errorCapacity);
bool HubListen(Hub *hub, char *error, size_t errorCapacity);
void HubServe(Hub *hub);
void HubStop(Hub *hub);
void HubDestroy(Hub *hub);
bool HubSend(Hub *hub, unsigned peerId, int type, const void *payload, size_t size);
void HubClosePeer(Hub *hub, unsigned peerId, const char *reason);
int HubSnapshot(Hub *hub, PeerInfo *out, int capacity);

typedef struct Node Node;

typedef struct {
    void (*onUp)(Node *node, unsigned id, const char *hubNode, const char *session, void *user);
    void (*onDown)(Node *node, unsigned id, const char *reason, void *user);
    void (*onMessage)(Node *node, unsigned id, int type, const unsigned char *payload, size_t size, void *user);
    void (*onRtt)(Node *node, unsigned id, double rttMs, void *user);
    void (*onError)(Node *node, const char *text, void *user);
} NodeCallbacks;

Node *NodeCreate(const ClusterConfig *config, const char *name, Role role, int port, bool preferChacha, NodeCallbacks callbacks, void *user, char *error, size_t errorCapacity);
void NodeRun(Node *node);
void NodeStop(Node *node);
void NodeDestroy(Node *node);
bool NodeSend(Node *node, int type, const void *payload, size_t size);
void NodeClose(Node *node, const char *reason);
bool NodeCurrent(Node *node, unsigned *id, char *hubNode, size_t hubCapacity, char *session, size_t sessionCapacity);

#endif
