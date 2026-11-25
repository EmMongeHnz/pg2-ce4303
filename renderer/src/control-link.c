#include "control-link.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

void ControlLinkInit(ControlLink *link) {
    memset(link, 0, sizeof(*link));
    link->fd = -1;
}

bool ControlLinkIsConnected(const ControlLink *link) {
    return link->fd >= 0;
}

void ControlLinkClose(ControlLink *link) {
    if (link->fd >= 0) close(link->fd);
    ControlLinkInit(link);
}

bool ControlLinkTryConnect(ControlLink *link, const char *path) {
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(path) >= sizeof(address.sun_path)) return false;
    strcpy(address.sun_path, path);

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return false;
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) < 0 || fcntl(fd, F_SETFL, O_NONBLOCK) < 0) {
        close(fd);
        return false;
    }
    ControlLinkInit(link);
    link->fd = fd;
    return true;
}

bool ControlLinkSend(ControlLink *link, const ControlInput *input) {
    if (link->fd < 0) return false;
    ssize_t sent = send(link->fd, input, sizeof(*input), MSG_NOSIGNAL);
    if (sent == (ssize_t)sizeof(*input)) return true;
    if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return false;
    /* A partial record would desynchronise the stream, so the link is rebuilt. */
    ControlLinkClose(link);
    return false;
}

bool ControlLinkReceive(ControlLink *link, ControlState *latest) {
    bool updated = false;
    while (link->fd >= 0) {
        ssize_t received = recv(link->fd, link->pending + link->pendingBytes, sizeof(link->pending) - link->pendingBytes, 0);
        if (received == 0) {
            ControlLinkClose(link);
            break;
        }
        if (received < 0) {
            if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR) ControlLinkClose(link);
            break;
        }
        link->pendingBytes += (size_t)received;
        if (link->pendingBytes == sizeof(link->pending)) {
            memcpy(latest, link->pending, sizeof(*latest));
            latest->modelId[CONTROL_MODEL_ID_BYTES - 1] = '\0';
            link->pendingBytes = 0;
            updated = true;
        }
    }
    return updated;
}
