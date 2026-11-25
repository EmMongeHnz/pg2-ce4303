#include "astrea-msgpack.h"

#include <stdlib.h>
#include <string.h>

static bool Reserve(MpWriter *writer, size_t extra) {
    if (writer->failed) return false;
    size_t needed = writer->size + extra;
    if (needed > MP_MAX_BYTES) {
        writer->failed = true;
        return false;
    }
    if (needed <= writer->capacity) return true;
    size_t capacity = writer->capacity ? writer->capacity : 256;
    while (capacity < needed) capacity *= 2;
    unsigned char *grown = realloc(writer->data, capacity);
    if (grown == NULL) {
        writer->failed = true;
        return false;
    }
    writer->data = grown;
    writer->capacity = capacity;
    return true;
}

static void PutByte(MpWriter *writer, unsigned char byte) {
    if (Reserve(writer, 1)) writer->data[writer->size++] = byte;
}

static void PutBigEndian(MpWriter *writer, uint64_t value, int bytes) {
    for (int shift = (bytes - 1) * 8; shift >= 0; shift -= 8) PutByte(writer, (unsigned char)(value >> shift));
}

static void PutBytes(MpWriter *writer, const void *data, size_t length) {
    if (length > 0 && Reserve(writer, length)) {
        memcpy(writer->data + writer->size, data, length);
        writer->size += length;
    }
}

void MpWriterInit(MpWriter *writer) {
    memset(writer, 0, sizeof(*writer));
}

void MpWriterFree(MpWriter *writer) {
    free(writer->data);
    memset(writer, 0, sizeof(*writer));
}

void MpMapHeader(MpWriter *writer, uint32_t count) {
    if (count < 16) {
        PutByte(writer, (unsigned char)(0x80 | count));
    } else if (count < 65536) {
        PutByte(writer, 0xde);
        PutBigEndian(writer, count, 2);
    } else {
        PutByte(writer, 0xdf);
        PutBigEndian(writer, count, 4);
    }
}

void MpStrN(MpWriter *writer, const char *text, size_t length) {
    if (length < 32) {
        PutByte(writer, (unsigned char)(0xa0 | length));
    } else if (length < 256) {
        PutByte(writer, 0xd9);
        PutBigEndian(writer, length, 1);
    } else if (length < 65536) {
        PutByte(writer, 0xda);
        PutBigEndian(writer, length, 2);
    } else {
        PutByte(writer, 0xdb);
        PutBigEndian(writer, length, 4);
    }
    PutBytes(writer, text, length);
}

void MpStr(MpWriter *writer, const char *text) {
    MpStrN(writer, text, strlen(text));
}

void MpUint(MpWriter *writer, uint64_t value) {
    if (value < 128) {
        PutByte(writer, (unsigned char)value);
    } else if (value <= 0xff) {
        PutByte(writer, 0xcc);
        PutBigEndian(writer, value, 1);
    } else if (value <= 0xffff) {
        PutByte(writer, 0xcd);
        PutBigEndian(writer, value, 2);
    } else if (value <= 0xffffffffu) {
        PutByte(writer, 0xce);
        PutBigEndian(writer, value, 4);
    } else {
        PutByte(writer, 0xcf);
        PutBigEndian(writer, value, 8);
    }
}

void MpInt(MpWriter *writer, int64_t value) {
    if (value >= 0) {
        MpUint(writer, (uint64_t)value);
    } else if (value >= -32) {
        PutByte(writer, (unsigned char)(0xe0 | (value & 0x1f)));
    } else if (value >= INT8_MIN) {
        PutByte(writer, 0xd0);
        PutBigEndian(writer, (uint64_t)value, 1);
    } else if (value >= INT16_MIN) {
        PutByte(writer, 0xd1);
        PutBigEndian(writer, (uint64_t)value, 2);
    } else if (value >= INT32_MIN) {
        PutByte(writer, 0xd2);
        PutBigEndian(writer, (uint64_t)value, 4);
    } else {
        PutByte(writer, 0xd3);
        PutBigEndian(writer, (uint64_t)value, 8);
    }
}

void MpBool(MpWriter *writer, bool value) {
    PutByte(writer, value ? 0xc3 : 0xc2);
}

void MpFloat64(MpWriter *writer, double value) {
    uint64_t bits;
    memcpy(&bits, &value, sizeof(bits));
    PutByte(writer, 0xcb);
    PutBigEndian(writer, bits, 8);
}

void MpBin(MpWriter *writer, const void *data, size_t length) {
    if (length < 256) {
        PutByte(writer, 0xc4);
        PutBigEndian(writer, length, 1);
    } else if (length < 65536) {
        PutByte(writer, 0xc5);
        PutBigEndian(writer, length, 2);
    } else {
        PutByte(writer, 0xc6);
        PutBigEndian(writer, length, 4);
    }
    PutBytes(writer, data, length);
}

static bool ReadUnsigned(MpReader *reader, int bytes, uint64_t *out) {
    if ((size_t)(reader->end - reader->cursor) < (size_t)bytes) return false;
    uint64_t value = 0;
    for (int i = 0; i < bytes; i++) value = (value << 8) | *reader->cursor++;
    *out = value;
    return true;
}

static bool ReadBlob(MpReader *reader, MpValue *value, MpType type, uint64_t length) {
    if (length > (uint64_t)(reader->end - reader->cursor)) return false;
    value->type = type;
    value->bytes = reader->cursor;
    value->length = (size_t)length;
    reader->cursor += length;
    return true;
}

static bool ReadSized(MpReader *reader, MpValue *value, MpType type, int lengthBytes) {
    uint64_t length;
    return ReadUnsigned(reader, lengthBytes, &length) && ReadBlob(reader, value, type, length);
}

static bool ReadCount(MpReader *reader, MpValue *value, MpType type, int lengthBytes) {
    uint64_t count;
    if (!ReadUnsigned(reader, lengthBytes, &count)) return false;
    value->type = type;
    value->length = (size_t)count;
    return true;
}

static bool ReadInteger(MpReader *reader, MpValue *value, int bytes, bool isSigned) {
    uint64_t raw;
    if (!ReadUnsigned(reader, bytes, &raw)) return false;
    if (isSigned) {
        int shift = 64 - 8 * bytes;
        value->type = MP_INT;
        value->integer = (int64_t)(raw << shift) >> shift;
    } else {
        value->type = MP_UINT;
        value->unsignedInteger = raw;
    }
    return true;
}

bool MpReadValue(MpReader *reader, MpValue *value) {
    memset(value, 0, sizeof(*value));
    if (reader->cursor >= reader->end) return false;
    unsigned char tag = *reader->cursor++;
    if (tag <= 0x7f) {
        value->type = MP_UINT;
        value->unsignedInteger = tag;
        return true;
    }
    if (tag >= 0xe0) {
        value->type = MP_INT;
        value->integer = (int8_t)tag;
        return true;
    }
    if ((tag & 0xe0) == 0xa0) return ReadBlob(reader, value, MP_STR, tag & 0x1f);
    if ((tag & 0xf0) == 0x90) {
        value->type = MP_ARRAY;
        value->length = tag & 0x0f;
        return true;
    }
    if ((tag & 0xf0) == 0x80) {
        value->type = MP_MAP;
        value->length = tag & 0x0f;
        return true;
    }
    uint64_t bits;
    switch (tag) {
        case 0xc0: value->type = MP_NIL; return true;
        case 0xc2: value->type = MP_BOOL; value->boolean = false; return true;
        case 0xc3: value->type = MP_BOOL; value->boolean = true; return true;
        case 0xc4: return ReadSized(reader, value, MP_BIN, 1);
        case 0xc5: return ReadSized(reader, value, MP_BIN, 2);
        case 0xc6: return ReadSized(reader, value, MP_BIN, 4);
        case 0xca:
            if (!ReadUnsigned(reader, 4, &bits)) return false;
            {
                uint32_t narrow = (uint32_t)bits;
                float real;
                memcpy(&real, &narrow, sizeof(real));
                value->type = MP_FLOAT;
                value->real = real;
            }
            return true;
        case 0xcb:
            if (!ReadUnsigned(reader, 8, &bits)) return false;
            value->type = MP_FLOAT;
            memcpy(&value->real, &bits, sizeof(bits));
            return true;
        case 0xcc: return ReadInteger(reader, value, 1, false);
        case 0xcd: return ReadInteger(reader, value, 2, false);
        case 0xce: return ReadInteger(reader, value, 4, false);
        case 0xcf: return ReadInteger(reader, value, 8, false);
        case 0xd0: return ReadInteger(reader, value, 1, true);
        case 0xd1: return ReadInteger(reader, value, 2, true);
        case 0xd2: return ReadInteger(reader, value, 4, true);
        case 0xd3: return ReadInteger(reader, value, 8, true);
        case 0xd9: return ReadSized(reader, value, MP_STR, 1);
        case 0xda: return ReadSized(reader, value, MP_STR, 2);
        case 0xdb: return ReadSized(reader, value, MP_STR, 4);
        case 0xdc: return ReadCount(reader, value, MP_ARRAY, 2);
        case 0xdd: return ReadCount(reader, value, MP_ARRAY, 4);
        case 0xde: return ReadCount(reader, value, MP_MAP, 2);
        case 0xdf: return ReadCount(reader, value, MP_MAP, 4);
        default: return false;
    }
}

bool MpSkip(MpReader *reader, int depth) {
    if (depth > MP_MAX_DEPTH) return false;
    MpValue value;
    if (!MpReadValue(reader, &value)) return false;
    size_t items = 0;
    if (value.type == MP_ARRAY) items = value.length;
    if (value.type == MP_MAP) items = value.length * 2;
    for (size_t i = 0; i < items; i++)
        if (!MpSkip(reader, depth + 1)) return false;
    return true;
}

bool MpIsValidMap(const unsigned char *payload, size_t size) {
    MpReader reader = {payload, payload + size};
    MpValue header;
    if (!MpReadValue(&reader, &header) || header.type != MP_MAP) return false;
    for (size_t i = 0; i < header.length; i++) {
        MpValue key;
        if (!MpReadValue(&reader, &key) || key.type != MP_STR) return false;
        if (!MpSkip(&reader, 1)) return false;
    }
    return reader.cursor == reader.end;
}

bool MpFindKey(const unsigned char *payload, size_t size, const char *key, MpValue *out) {
    MpReader reader = {payload, payload + size};
    MpValue header;
    if (!MpReadValue(&reader, &header) || header.type != MP_MAP) return false;
    size_t keyLength = strlen(key);
    for (size_t i = 0; i < header.length; i++) {
        MpValue name;
        if (!MpReadValue(&reader, &name) || name.type != MP_STR) return false;
        if (name.length == keyLength && memcmp(name.bytes, key, keyLength) == 0) return MpReadValue(&reader, out);
        if (!MpSkip(&reader, 1)) return false;
    }
    return false;
}

bool MpGetInt(const unsigned char *payload, size_t size, const char *key, int64_t *out) {
    MpValue value;
    if (!MpFindKey(payload, size, key, &value)) return false;
    if (value.type == MP_INT) {
        *out = value.integer;
        return true;
    }
    if (value.type == MP_UINT && value.unsignedInteger <= INT64_MAX) {
        *out = (int64_t)value.unsignedInteger;
        return true;
    }
    return false;
}

bool MpGetDouble(const unsigned char *payload, size_t size, const char *key, double *out) {
    MpValue value;
    if (!MpFindKey(payload, size, key, &value)) return false;
    if (value.type == MP_FLOAT) *out = value.real;
    else if (value.type == MP_INT) *out = (double)value.integer;
    else if (value.type == MP_UINT) *out = (double)value.unsignedInteger;
    else return false;
    return true;
}

bool MpGetBool(const unsigned char *payload, size_t size, const char *key, bool *out) {
    MpValue value;
    if (!MpFindKey(payload, size, key, &value) || value.type != MP_BOOL) return false;
    *out = value.boolean;
    return true;
}

bool MpGetString(const unsigned char *payload, size_t size, const char *key, char *out, size_t capacity) {
    MpValue value;
    if (!MpFindKey(payload, size, key, &value) || value.type != MP_STR || value.length >= capacity) return false;
    if (memchr(value.bytes, '\0', value.length) != NULL) return false;
    memcpy(out, value.bytes, value.length);
    out[value.length] = '\0';
    return true;
}

bool MpGetBin(const unsigned char *payload, size_t size, const char *key, const unsigned char **data, size_t *length) {
    MpValue value;
    if (!MpFindKey(payload, size, key, &value) || value.type != MP_BIN) return false;
    *data = value.bytes;
    *length = value.length;
    return true;
}
