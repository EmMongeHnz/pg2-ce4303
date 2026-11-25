/* The device agent's Unix socket for the hologram renderer (`renderer --control-socket`): the renderer connects as a
 * client, receives every camera state as a fixed-size `ControlState` record and is sent the latest one on connecting. */
#ifndef AGENT_RENDERER_H
#define AGENT_RENDERER_H

#include <stdbool.h>
#include <stddef.h>

#include "control-protocol.h"

typedef struct RendererLink RendererLink;

RendererLink *RendererLinkStart(const char *socketPath, char *error, size_t errorCapacity);
void RendererLinkSend(RendererLink *link, const ControlState *state);
bool RendererLinkLatest(RendererLink *link, ControlState *state);
void RendererLinkStop(RendererLink *link);

#endif
