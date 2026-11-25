/* Receives the MODEL_PUSH chunks of a model, checks the size and the SHA-256, and installs it as `<dir>/<id>.glb` with
 * an atomic rename so the renderer never reads a half-written file. Only uncompressed models are accepted. */
#ifndef AGENT_MODELS_H
#define AGENT_MODELS_H

#include <stdbool.h>
#include <stddef.h>

#define MODEL_ID_BYTES 32
#define MODEL_MAX_BYTES (128u * 1024u * 1024u)
#define MODEL_MAX_CHUNKS 4096

typedef enum { MODEL_NEED_MORE, MODEL_INSTALLED, MODEL_REJECTED } ModelResult;

typedef struct ModelStore ModelStore;

ModelStore *ModelStoreCreate(const char *directory);
void ModelStoreDestroy(ModelStore *store);

/* Adds one MODEL_PUSH payload. On MODEL_INSTALLED, `installedId` holds the id of the model that is now on disk. */
ModelResult ModelStoreAdd(ModelStore *store, const unsigned char *payload, size_t size, char *installedId, char *error, size_t errorCapacity);

bool ModelIdIsUsable(const char *id);

#endif
