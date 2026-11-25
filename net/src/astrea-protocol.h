/* Wire protocol shared by every node: framing `[u32 length][u8 type][msgpack map]`, the message types, who may send
 * what, and the HELLO and HEARTBEAT payloads that the network layer itself handles. */
#ifndef ASTREA_PROTOCOL_H
#define ASTREA_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "astrea-msgpack.h"

#define PROTOCOL_VERSION 1
#define MAX_FRAME_BYTES (4 * 1024 * 1024)
#define FRAME_HEADER_BYTES 5
#define NODE_NAME_BYTES 64

typedef enum {
    MSG_HELLO = 1,
    MSG_JOB_ASSIGN = 2,
    MSG_JOB_RESULT = 3,
    MSG_HEARTBEAT = 4,
    MSG_INPUT_EVENT = 5,
    MSG_CAMERA_STATE = 6,
    MSG_MODEL_PUSH = 7,
    MSG_ROTATE = 8,
    MSG_ROTATE_DONE = 9,
} MessageType;

typedef enum {
    ROLE_NONE = 0,
    ROLE_COORDINATOR = 1,
    ROLE_WORKER = 2,
    ROLE_DEVICE_AGENT = 4,
} Role;

const char *RoleName(Role role);
Role RoleFromName(const char *name);

/* `nodeRole` is the role of the node at the other end of the link from the hub's point of view. */
bool IsMessageAllowed(int type, bool sentByHub, Role nodeRole);

typedef struct {
    unsigned char *data;
    size_t size;
    size_t capacity;
    size_t offset;
} FrameParser;

void ParserInit(FrameParser *parser);
void ParserFree(FrameParser *parser);
bool ParserFeed(FrameParser *parser, const unsigned char *data, size_t size);

/* Returns 1 with a frame, 0 when more bytes are needed, -1 on a protocol error (see `error`). The payload points into
 * the parser and stays valid until the next call to ParserFeed. */
int ParserNext(FrameParser *parser, int *type, const unsigned char **payload, size_t *size, const char **error);

bool FrameEncode(MpWriter *out, int type, const unsigned char *payload, size_t size);

bool HelloEncode(MpWriter *out, const char *node, Role role);
bool HelloDecode(const unsigned char *payload, size_t size, int64_t *version, char *node, size_t nodeCapacity, Role *role);
bool HeartbeatEncode(MpWriter *out, uint64_t sequence, uint64_t sentAtNs, bool echo);
bool HeartbeatDecode(const unsigned char *payload, size_t size, uint64_t *sequence, uint64_t *sentAtNs, bool *echo);

#endif
