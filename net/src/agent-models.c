#include "agent-models.h"

#include <errno.h>
#include <openssl/evp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "astrea-msgpack.h"

typedef struct {
    unsigned char *data;
    size_t length;
} Piece;

typedef struct {
    bool active;
    char id[MODEL_ID_BYTES];
    int64_t count;
    int64_t received;
    int64_t totalSize;
    char sha256[65];
    Piece *pieces;
} Assembly;

struct ModelStore {
    char directory[512];
    Assembly assembly;
};

bool ModelIdIsUsable(const char *id) {
    size_t length = strlen(id);
    if (length == 0 || length >= MODEL_ID_BYTES) return false;
    for (size_t i = 0; i < length; i++) {
        char c = id[i];
        bool letter = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9');
        bool punctuation = c == '-' || c == '_' || (c == '.' && i > 0);
        if (!letter && !punctuation) return false;
    }
    return true;
}

ModelStore *ModelStoreCreate(const char *directory) {
    ModelStore *store = calloc(1, sizeof(*store));
    if (store != NULL) snprintf(store->directory, sizeof(store->directory), "%s", directory);
    return store;
}

static void ClearAssembly(Assembly *assembly) {
    if (assembly->pieces != NULL)
        for (int64_t i = 0; i < assembly->count; i++) free(assembly->pieces[i].data);
    free(assembly->pieces);
    memset(assembly, 0, sizeof(*assembly));
}

void ModelStoreDestroy(ModelStore *store) {
    if (store == NULL) return;
    ClearAssembly(&store->assembly);
    free(store);
}

static void Fail(char *error, size_t capacity, const char *format, const char *detail) {
    snprintf(error, capacity, format, detail);
}

static bool MakeDirectory(const char *path) {
    char partial[512];
    snprintf(partial, sizeof(partial), "%s", path);
    for (char *cursor = partial + 1; *cursor != '\0'; cursor++) {
        if (*cursor != '/') continue;
        *cursor = '\0';
        if (mkdir(partial, 0755) != 0 && errno != EEXIST) return false;
        *cursor = '/';
    }
    return mkdir(partial, 0755) == 0 || errno == EEXIST;
}

static bool Sha256Hex(const unsigned char *data, size_t length, char *out) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digestLength = 0;
    if (EVP_Digest(data, length, digest, &digestLength, EVP_sha256(), NULL) != 1 || digestLength != 32) return false;
    for (unsigned int i = 0; i < digestLength; i++) snprintf(out + 2 * i, 3, "%02x", digest[i]);
    return true;
}

static bool WriteModel(const ModelStore *store, const Assembly *assembly, const unsigned char *data, char *error, size_t errorCapacity) {
    if (!MakeDirectory(store->directory)) {
        Fail(error, errorCapacity, "cannot create the models directory: %s", strerror(errno));
        return false;
    }
    char temporary[600], target[600];
    snprintf(temporary, sizeof(temporary), "%s/.%s-XXXXXX", store->directory, assembly->id);
    snprintf(target, sizeof(target), "%s/%s.glb", store->directory, assembly->id);
    int fd = mkstemp(temporary);
    if (fd < 0) {
        Fail(error, errorCapacity, "cannot write the model: %s", strerror(errno));
        return false;
    }
    size_t done = 0;
    while (done < (size_t)assembly->totalSize) {
        ssize_t written = write(fd, data + done, (size_t)assembly->totalSize - done);
        if (written < 0 && errno == EINTR) continue;
        if (written <= 0) break;
        done += (size_t)written;
    }
    bool ok = done == (size_t)assembly->totalSize && fchmod(fd, 0644) == 0;
    ok = close(fd) == 0 && ok;
    if (ok) ok = rename(temporary, target) == 0;
    if (!ok) {
        Fail(error, errorCapacity, "cannot install the model: %s", strerror(errno));
        unlink(temporary);
    }
    return ok;
}

static ModelResult Finish(ModelStore *store, char *installedId, char *error, size_t errorCapacity) {
    Assembly *assembly = &store->assembly;
    unsigned char *whole = malloc(assembly->totalSize > 0 ? (size_t)assembly->totalSize : 1);
    if (whole == NULL) {
        Fail(error, errorCapacity, "%s", "out of memory");
        return MODEL_REJECTED;
    }
    size_t filled = 0;
    bool fits = true;
    for (int64_t i = 0; i < assembly->count && fits; i++) {
        fits = filled + assembly->pieces[i].length <= (size_t)assembly->totalSize;
        if (fits) {
            memcpy(whole + filled, assembly->pieces[i].data, assembly->pieces[i].length);
            filled += assembly->pieces[i].length;
        }
    }
    char digest[65];
    ModelResult result = MODEL_REJECTED;
    if (!fits || filled != (size_t)assembly->totalSize) Fail(error, errorCapacity, "'%s' has the wrong size", assembly->id);
    else if (!Sha256Hex(whole, filled, digest) || strcmp(digest, assembly->sha256) != 0) Fail(error, errorCapacity, "'%s' failed the SHA-256 check", assembly->id);
    else if (WriteModel(store, assembly, whole, error, errorCapacity)) {
        snprintf(installedId, MODEL_ID_BYTES, "%s", assembly->id);
        result = MODEL_INSTALLED;
    }
    free(whole);
    ClearAssembly(assembly);
    return result;
}

ModelResult ModelStoreAdd(ModelStore *store, const unsigned char *payload, size_t size, char *installedId, char *error, size_t errorCapacity) {
    char id[MODEL_ID_BYTES], sha256[65], compression[16];
    int64_t index, count, totalSize;
    const unsigned char *data;
    size_t dataLength;
    if (!MpGetString(payload, size, "modelId", id, sizeof(id)) || !MpGetInt(payload, size, "index", &index) || !MpGetInt(payload, size, "count", &count) ||
        !MpGetInt(payload, size, "totalSize", &totalSize) || !MpGetString(payload, size, "sha256", sha256, sizeof(sha256)) ||
        !MpGetString(payload, size, "compression", compression, sizeof(compression)) || !MpGetBin(payload, size, "data", &data, &dataLength)) {
        Fail(error, errorCapacity, "%s", "invalid MODEL_PUSH payload");
        return MODEL_REJECTED;
    }
    if (!ModelIdIsUsable(id)) {
        Fail(error, errorCapacity, "unusable model id '%s'", id);
        return MODEL_REJECTED;
    }
    if (strcmp(compression, "none") != 0) {
        Fail(error, errorCapacity, "'%s' uses an unsupported compression", id);
        return MODEL_REJECTED;
    }
    if (count < 1 || count > MODEL_MAX_CHUNKS || index < 0 || index >= count || totalSize < 0 || totalSize > MODEL_MAX_BYTES || strlen(sha256) != 64) {
        Fail(error, errorCapacity, "'%s' announces an invalid transfer", id);
        return MODEL_REJECTED;
    }
    Assembly *assembly = &store->assembly;
    bool sameTransfer = assembly->active && strcmp(assembly->id, id) == 0 && assembly->count == count && assembly->totalSize == totalSize && strcmp(assembly->sha256, sha256) == 0;
    if (!sameTransfer) {
        ClearAssembly(assembly);
        assembly->pieces = calloc((size_t)count, sizeof(Piece));
        if (assembly->pieces == NULL) {
            Fail(error, errorCapacity, "%s", "out of memory");
            return MODEL_REJECTED;
        }
        assembly->active = true;
        snprintf(assembly->id, sizeof(assembly->id), "%s", id);
        assembly->count = count;
        assembly->totalSize = totalSize;
        snprintf(assembly->sha256, sizeof(assembly->sha256), "%s", sha256);
    }
    if (assembly->pieces[index].data != NULL || dataLength > (size_t)totalSize) {
        Fail(error, errorCapacity, "chunk %s of the model arrived twice or is too large", "");
        ClearAssembly(assembly);
        return MODEL_REJECTED;
    }
    assembly->pieces[index].data = malloc(dataLength > 0 ? dataLength : 1);
    if (assembly->pieces[index].data == NULL) {
        Fail(error, errorCapacity, "%s", "out of memory");
        ClearAssembly(assembly);
        return MODEL_REJECTED;
    }
    memcpy(assembly->pieces[index].data, data, dataLength);
    assembly->pieces[index].length = dataLength;
    if (++assembly->received < assembly->count) return MODEL_NEED_MORE;
    return Finish(store, installedId, error, errorCapacity);
}
