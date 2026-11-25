#include "agent-renderer.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include "astrea-log.h"

struct RendererLink {
    char path[108];
    int listenFd;
    int clientFd;
    pthread_mutex_t mutex;
    ControlState latest;
    bool haveLatest;
    atomic_bool stopping;
    pthread_t thread;
};

static void DropClient(RendererLink *link) {
    if (link->clientFd >= 0) close(link->clientFd);
    link->clientFd = -1;
}

static void SendLocked(RendererLink *link, const ControlState *state) {
    if (link->clientFd < 0) return;
    ssize_t sent = send(link->clientFd, state, sizeof(*state), MSG_NOSIGNAL | MSG_DONTWAIT);
    if (sent == (ssize_t)sizeof(*state)) return;
    /* A renderer that cannot keep up simply misses a state (the newest wins); a cut record would break the stream. */
    if (sent < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR)) return;
    DropClient(link);
}

static void *Serve(void *argument) {
    RendererLink *link = argument;
    while (!atomic_load(&link->stopping)) {
        pthread_mutex_lock(&link->mutex);
        int clientFd = link->clientFd;
        pthread_mutex_unlock(&link->mutex);
        struct pollfd fds[2] = {{link->listenFd, POLLIN, 0}, {clientFd, POLLIN, 0}};
        if (poll(fds, clientFd >= 0 ? 2 : 1, 200) <= 0) continue;
        if (fds[0].revents & POLLIN) {
            int accepted = accept(link->listenFd, NULL, NULL);
            if (accepted >= 0) {
                fcntl(accepted, F_SETFL, O_NONBLOCK);
                pthread_mutex_lock(&link->mutex);
                DropClient(link);
                link->clientFd = accepted;
                if (link->haveLatest) SendLocked(link, &link->latest);
                pthread_mutex_unlock(&link->mutex);
                LOG_INFO("the renderer connected");
                clientFd = -1;
            }
        }
        if (clientFd >= 0 && (fds[1].revents & (POLLIN | POLLHUP | POLLERR))) {
            unsigned char discard[256];
            ssize_t received = recv(clientFd, discard, sizeof(discard), MSG_DONTWAIT);
            if (received == 0 || (received < 0 && errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)) {
                pthread_mutex_lock(&link->mutex);
                if (link->clientFd == clientFd) DropClient(link);
                pthread_mutex_unlock(&link->mutex);
                LOG_INFO("the renderer disconnected");
            }
        }
    }
    return NULL;
}

RendererLink *RendererLinkStart(const char *socketPath, char *error, size_t errorCapacity) {
    RendererLink *link = calloc(1, sizeof(*link));
    if (link == NULL) return NULL;
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (strlen(socketPath) >= sizeof(address.sun_path)) {
        snprintf(error, errorCapacity, "the socket path is too long");
        free(link);
        return NULL;
    }
    strcpy(address.sun_path, socketPath);
    snprintf(link->path, sizeof(link->path), "%s", socketPath);
    link->clientFd = -1;
    pthread_mutex_init(&link->mutex, NULL);
    unlink(socketPath);
    link->listenFd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (link->listenFd < 0 || bind(link->listenFd, (struct sockaddr *)&address, sizeof(address)) != 0 || listen(link->listenFd, 1) != 0) {
        snprintf(error, errorCapacity, "cannot listen on %s: %s", socketPath, strerror(errno));
        if (link->listenFd >= 0) close(link->listenFd);
        free(link);
        return NULL;
    }
    pthread_create(&link->thread, NULL, Serve, link);
    return link;
}

void RendererLinkSend(RendererLink *link, const ControlState *state) {
    pthread_mutex_lock(&link->mutex);
    link->latest = *state;
    link->haveLatest = true;
    SendLocked(link, state);
    pthread_mutex_unlock(&link->mutex);
}

bool RendererLinkLatest(RendererLink *link, ControlState *state) {
    pthread_mutex_lock(&link->mutex);
    bool have = link->haveLatest;
    if (have) *state = link->latest;
    pthread_mutex_unlock(&link->mutex);
    return have;
}

void RendererLinkStop(RendererLink *link) {
    if (link == NULL) return;
    atomic_store(&link->stopping, true);
    pthread_join(link->thread, NULL);
    pthread_mutex_lock(&link->mutex);
    DropClient(link);
    pthread_mutex_unlock(&link->mutex);
    close(link->listenFd);
    unlink(link->path);
    pthread_mutex_destroy(&link->mutex);
    free(link);
}
