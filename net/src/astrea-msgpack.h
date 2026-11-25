/* The small subset of MessagePack the protocol needs: maps with string keys holding integers, floats, booleans,
 * strings and binary blobs. The reader checks every length against the buffer, so a hostile payload cannot make it
 * read out of bounds, and it limits the nesting depth it will skip. */
#ifndef ASTREA_MSGPACK_H
#define ASTREA_MSGPACK_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MP_MAX_DEPTH 32
#define MP_MAX_BYTES (64u * 1024u * 1024u)

typedef struct {
    unsigned char *data;
    size_t size;
    size_t capacity;
    bool failed;
} MpWriter;

void MpWriterInit(MpWriter *writer);
void MpWriterFree(MpWriter *writer);
void MpMapHeader(MpWriter *writer, uint32_t count);
void MpStr(MpWriter *writer, const char *text);
void MpStrN(MpWriter *writer, const char *text, size_t length);
void MpUint(MpWriter *writer, uint64_t value);
void MpInt(MpWriter *writer, int64_t value);
void MpBool(MpWriter *writer, bool value);
void MpFloat64(MpWriter *writer, double value);
void MpBin(MpWriter *writer, const void *data, size_t length);

typedef enum { MP_NIL, MP_BOOL, MP_INT, MP_UINT, MP_FLOAT, MP_STR, MP_BIN, MP_ARRAY, MP_MAP } MpType;

typedef struct {
    MpType type;
    bool boolean;
    int64_t integer;
    uint64_t unsignedInteger;
    double real;
    const unsigned char *bytes;
    size_t length;
} MpValue;

typedef struct {
    const unsigned char *cursor;
    const unsigned char *end;
} MpReader;

/* For arrays and maps, `length` is the element count and the elements are left in the reader. */
bool MpReadValue(MpReader *reader, MpValue *value);
bool MpSkip(MpReader *reader, int depth);

bool MpIsValidMap(const unsigned char *payload, size_t size);
bool MpFindKey(const unsigned char *payload, size_t size, const char *key, MpValue *out);
bool MpGetInt(const unsigned char *payload, size_t size, const char *key, int64_t *out);
bool MpGetDouble(const unsigned char *payload, size_t size, const char *key, double *out);
bool MpGetBool(const unsigned char *payload, size_t size, const char *key, bool *out);
bool MpGetString(const unsigned char *payload, size_t size, const char *key, char *out, size_t capacity);
bool MpGetBin(const unsigned char *payload, size_t size, const char *key, const unsigned char **data, size_t *length);

#endif
