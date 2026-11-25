/* Client side of the renderer's Unix socket: sends raw inputs and receives the camera state to draw. */
#ifndef RENDERER_CONTROL_LINK_H
#define RENDERER_CONTROL_LINK_H

#include <stdbool.h>
#include <stddef.h>

#include "control-protocol.h"

typedef struct {
    int fd;
    unsigned char pending[sizeof(ControlState)];
    size_t pendingBytes;
} ControlLink;

void ControlLinkInit(ControlLink *link);
bool ControlLinkIsConnected(const ControlLink *link);
bool ControlLinkTryConnect(ControlLink *link, const char *path);
void ControlLinkClose(ControlLink *link);

/* Never blocks: an input is dropped when the socket is full, and the link is closed if the peer is gone. */
bool ControlLinkSend(ControlLink *link, const ControlInput *input);

/* Reads every complete state that is available and keeps the newest in `latest`. Returns true when it did. */
bool ControlLinkReceive(ControlLink *link, ControlState *latest);

#endif
