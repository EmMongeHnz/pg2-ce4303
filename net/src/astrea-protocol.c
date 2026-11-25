#include "astrea-protocol.h"

#include <stdlib.h>
#include <string.h>

typedef enum { SENDER_HUB, SENDER_NODE, SENDER_EITHER } Sender;

typedef struct {
    int type;
    Sender sender;
    int roles;
    bool enabled;
} MessageRule;

static const MessageRule RULES[] = {
    {MSG_HELLO, SENDER_EITHER, 0, true},
    {MSG_JOB_ASSIGN, SENDER_HUB, ROLE_WORKER, true},
    {MSG_JOB_RESULT, SENDER_NODE, ROLE_WORKER, true},
    {MSG_HEARTBEAT, SENDER_EITHER, 0, true},
    {MSG_INPUT_EVENT, SENDER_NODE, ROLE_DEVICE_AGENT, true},
    {MSG_CAMERA_STATE, SENDER_HUB, ROLE_DEVICE_AGENT, true},
    {MSG_MODEL_PUSH, SENDER_HUB, ROLE_DEVICE_AGENT, true},
    {MSG_ROTATE, SENDER_HUB, ROLE_DEVICE_AGENT, false},
    {MSG_ROTATE_DONE, SENDER_NODE, ROLE_DEVICE_AGENT, false},
};

const char *RoleName(Role role) {
    switch (role) {
        case ROLE_COORDINATOR: return "coordinator";
        case ROLE_WORKER: return "worker";
        case ROLE_DEVICE_AGENT: return "device-agent";
        default: return "none";
    }
}

Role RoleFromName(const char *name) {
    if (strcmp(name, "coordinator") == 0) return ROLE_COORDINATOR;
    if (strcmp(name, "worker") == 0) return ROLE_WORKER;
    if (strcmp(name, "device-agent") == 0) return ROLE_DEVICE_AGENT;
    return ROLE_NONE;
}

bool IsMessageAllowed(int type, bool sentByHub, Role nodeRole) {
    for (size_t i = 0; i < sizeof(RULES) / sizeof(RULES[0]); i++) {
        const MessageRule *rule = &RULES[i];
        if (rule->type != type) continue;
        if (!rule->enabled) return false;
        if (rule->sender == SENDER_HUB && !sentByHub) return false;
        if (rule->sender == SENDER_NODE && sentByHub) return false;
        return rule->roles == 0 || (rule->roles & nodeRole) != 0;
    }
    return false;
}

void ParserInit(FrameParser *parser) {
    memset(parser, 0, sizeof(*parser));
}

void ParserFree(FrameParser *parser) {
    free(parser->data);
    memset(parser, 0, sizeof(*parser));
}

bool ParserFeed(FrameParser *parser, const unsigned char *data, size_t size) {
    if (parser->offset > 0 && parser->offset == parser->size) {
        parser->offset = 0;
        parser->size = 0;
    } else if (parser->offset > parser->capacity / 2) {
        memmove(parser->data, parser->data + parser->offset, parser->size - parser->offset);
        parser->size -= parser->offset;
        parser->offset = 0;
    }
    if (parser->size + size > parser->capacity) {
        size_t capacity = parser->capacity ? parser->capacity : 4096;
        while (capacity < parser->size + size) capacity *= 2;
        unsigned char *grown = realloc(parser->data, capacity);
        if (grown == NULL) return false;
        parser->data = grown;
        parser->capacity = capacity;
    }
    memcpy(parser->data + parser->size, data, size);
    parser->size += size;
    return true;
}

int ParserNext(FrameParser *parser, int *type, const unsigned char **payload, size_t *size, const char **error) {
    size_t available = parser->size - parser->offset;
    if (available < 4) return 0;
    const unsigned char *head = parser->data + parser->offset;
    uint32_t length = ((uint32_t)head[0] << 24) | ((uint32_t)head[1] << 16) | ((uint32_t)head[2] << 8) | head[3];
    if (length == 0) {
        *error = "empty frame";
        return -1;
    }
    if (length > MAX_FRAME_BYTES) {
        *error = "frame too large";
        return -1;
    }
    if (available < 4 + (size_t)length) return 0;
    int messageType = head[4];
    if (messageType < MSG_HELLO || messageType > MSG_ROTATE_DONE) {
        *error = "unknown message type";
        return -1;
    }
    *type = messageType;
    *payload = head + FRAME_HEADER_BYTES;
    *size = length - 1;
    parser->offset += 4 + (size_t)length;
    return 1;
}

bool FrameEncode(MpWriter *out, int type, const unsigned char *payload, size_t size) {
    if (size + 1 > MAX_FRAME_BYTES) return false;
    uint32_t length = (uint32_t)(size + 1);
    unsigned char header[FRAME_HEADER_BYTES] = {(unsigned char)(length >> 24), (unsigned char)(length >> 16), (unsigned char)(length >> 8), (unsigned char)length, (unsigned char)type};
    size_t total = FRAME_HEADER_BYTES + size;
    unsigned char *buffer = malloc(total);
    if (buffer == NULL) return false;
    memcpy(buffer, header, FRAME_HEADER_BYTES);
    if (size > 0) memcpy(buffer + FRAME_HEADER_BYTES, payload, size);
    out->data = buffer;
    out->size = total;
    out->capacity = total;
    out->failed = false;
    return true;
}

bool HelloEncode(MpWriter *out, const char *node, Role role) {
    MpMapHeader(out, 3);
    MpStr(out, "protocolVersion");
    MpUint(out, PROTOCOL_VERSION);
    MpStr(out, "node");
    MpStr(out, node);
    MpStr(out, "role");
    MpStr(out, RoleName(role));
    return !out->failed;
}

bool HelloDecode(const unsigned char *payload, size_t size, int64_t *version, char *node, size_t nodeCapacity, Role *role) {
    char roleName[32];
    if (!MpGetInt(payload, size, "protocolVersion", version)) return false;
    if (!MpGetString(payload, size, "node", node, nodeCapacity)) return false;
    if (!MpGetString(payload, size, "role", roleName, sizeof(roleName))) return false;
    *role = RoleFromName(roleName);
    return *role != ROLE_NONE;
}

bool HeartbeatEncode(MpWriter *out, uint64_t sequence, uint64_t sentAtNs, bool echo) {
    MpMapHeader(out, 3);
    MpStr(out, "sequence");
    MpUint(out, sequence);
    MpStr(out, "sentAtNs");
    MpUint(out, sentAtNs);
    MpStr(out, "echo");
    MpBool(out, echo);
    return !out->failed;
}

bool HeartbeatDecode(const unsigned char *payload, size_t size, uint64_t *sequence, uint64_t *sentAtNs, bool *echo) {
    int64_t sequenceValue, sentValue;
    if (!MpGetInt(payload, size, "sequence", &sequenceValue) || !MpGetInt(payload, size, "sentAtNs", &sentValue)) return false;
    if (sequenceValue < 0 || sentValue < 0 || !MpGetBool(payload, size, "echo", echo)) return false;
    *sequence = (uint64_t)sequenceValue;
    *sentAtNs = (uint64_t)sentValue;
    return true;
}
