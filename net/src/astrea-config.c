#include "astrea-config.h"

#include <ctype.h>
#include <errno.h>
#include <libgen.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_ENTRIES 128
#define MAX_LIST 8
#define TEXT_BYTES 512

typedef struct {
    char section[96];
    char key[64];
    bool isText;
    bool isList;
    char text[TEXT_BYTES];
    double number;
    char list[MAX_LIST][NODE_NAME_BYTES];
    int listCount;
} Entry;

typedef struct {
    Entry entries[MAX_ENTRIES];
    int count;
} Table;

static void SetError(char *error, size_t capacity, const char *format, const char *a, int line) {
    if (error != NULL && capacity > 0) snprintf(error, capacity, format, a, line);
}

static char *Trim(char *text) {
    while (isspace((unsigned char)*text)) text++;
    char *end = text + strlen(text);
    while (end > text && isspace((unsigned char)end[-1])) *--end = '\0';
    return text;
}

static char *ParseQuoted(char *cursor, char *out, size_t capacity) {
    size_t length = 0;
    cursor++;
    while (*cursor != '\0' && *cursor != '"') {
        char c = *cursor++;
        if (c == '\\' && *cursor != '\0') c = *cursor++;
        if (length + 1 >= capacity) return NULL;
        out[length++] = c;
    }
    if (*cursor != '"') return NULL;
    out[length] = '\0';
    return cursor + 1;
}

static bool ParseValue(char *text, Entry *entry) {
    if (*text == '"') {
        char *rest = ParseQuoted(text, entry->text, sizeof(entry->text));
        if (rest == NULL) return false;
        entry->isText = true;
        rest = Trim(rest);
        return *rest == '\0' || *rest == '#';
    }
    if (*text == '[') {
        entry->isList = true;
        char *cursor = text + 1;
        for (;;) {
            while (isspace((unsigned char)*cursor) || *cursor == ',') cursor++;
            if (*cursor == ']') return true;
            if (*cursor != '"' || entry->listCount >= MAX_LIST) return false;
            cursor = ParseQuoted(cursor, entry->list[entry->listCount++], NODE_NAME_BYTES);
            if (cursor == NULL) return false;
        }
    }
    char *end;
    errno = 0;
    entry->number = strtod(text, &end);
    if (end == text || errno != 0) return false;
    end = Trim(end);
    return *end == '\0' || *end == '#';
}

static bool ParseFile(FILE *file, Table *table, char *error, size_t errorCapacity) {
    char line[1024];
    char section[96] = "";
    int number = 0;
    while (fgets(line, sizeof(line), file) != NULL) {
        number++;
        char *text = Trim(line);
        if (*text == '\0' || *text == '#') continue;
        if (*text == '[') {
            char *close = strchr(text, ']');
            if (close == NULL || (size_t)(close - text) >= sizeof(section)) {
                SetError(error, errorCapacity, "bad table header at line %2$d%1$s", "", number);
                return false;
            }
            *close = '\0';
            snprintf(section, sizeof(section), "%s", Trim(text + 1));
            continue;
        }
        char *equals = strchr(text, '=');
        if (equals == NULL || table->count >= MAX_ENTRIES) {
            SetError(error, errorCapacity, "cannot read line %2$d%1$s", "", number);
            return false;
        }
        *equals = '\0';
        Entry *entry = &table->entries[table->count];
        memset(entry, 0, sizeof(*entry));
        snprintf(entry->section, sizeof(entry->section), "%s", section);
        snprintf(entry->key, sizeof(entry->key), "%s", Trim(text));
        if (!ParseValue(Trim(equals + 1), entry)) {
            SetError(error, errorCapacity, "cannot read the value of '%1$s' at line %2$d", entry->key, number);
            return false;
        }
        table->count++;
    }
    return true;
}

static const Entry *Find(const Table *table, const char *section, const char *key) {
    for (int i = 0; i < table->count; i++)
        if (strcmp(table->entries[i].section, section) == 0 && strcmp(table->entries[i].key, key) == 0) return &table->entries[i];
    return NULL;
}

static bool TextOf(const Table *table, const char *section, const char *key, char *out, size_t capacity, bool required, char *error, size_t errorCapacity) {
    const Entry *entry = Find(table, section, key);
    if (entry == NULL || !entry->isText) {
        if (required) snprintf(error, errorCapacity, "missing text value %s.%s", section, key);
        return !required;
    }
    if (strlen(entry->text) >= capacity) {
        snprintf(error, errorCapacity, "%s.%s is too long", section, key);
        return false;
    }
    snprintf(out, capacity, "%s", entry->text);
    return true;
}

static bool NumberOf(const Table *table, const char *section, const char *key, double *out, char *error, size_t errorCapacity) {
    const Entry *entry = Find(table, section, key);
    if (entry == NULL) return true;
    if (entry->isText || entry->isList) {
        snprintf(error, errorCapacity, "%s.%s must be a number", section, key);
        return false;
    }
    *out = entry->number;
    return true;
}

static void Resolve(const char *baseDir, const char *path, char *out, size_t capacity) {
    if (path[0] == '/') snprintf(out, capacity, "%s", path);
    else snprintf(out, capacity, "%s/%s", baseDir, path);
}

static bool LoadNodes(const Table *table, ClusterConfig *config, char *error, size_t errorCapacity) {
    for (int i = 0; i < table->count; i++) {
        const Entry *entry = &table->entries[i];
        if (strncmp(entry->section, "nodes.", 6) != 0 || strcmp(entry->key, "roles") != 0) continue;
        if (config->nodeCount >= MAX_NODES || !entry->isList || entry->listCount == 0) {
            snprintf(error, errorCapacity, "node '%s' needs a non-empty list of roles", entry->section + 6);
            return false;
        }
        NodeConfig *node = &config->nodes[config->nodeCount++];
        snprintf(node->name, sizeof(node->name), "%s", entry->section + 6);
        for (int role = 0; role < entry->listCount; role++) {
            Role parsed = RoleFromName(entry->list[role]);
            if (parsed == ROLE_NONE) {
                snprintf(error, errorCapacity, "node '%s' has the invalid role '%s'", node->name, entry->list[role]);
                return false;
            }
            node->roles |= parsed;
        }
    }
    return true;
}

static bool Validate(const ClusterConfig *config, char *error, size_t errorCapacity) {
    const NodeConfig *hub = ConfigNode(config, config->hubNode);
    if (hub == NULL || !(hub->roles & ROLE_COORDINATOR)) {
        snprintf(error, errorCapacity, "hub node '%s' must be a node with the coordinator role", config->hubNode);
        return false;
    }
    if (config->hubPort < 1 || config->hubPort > 65535 || config->hubDevicePort < 1 || config->hubDevicePort > 65535 || config->hubDevicePort == config->hubPort) {
        snprintf(error, errorCapacity, "hub.port and hub.device-port must be different ports between 1 and 65535");
        return false;
    }
    const NetworkSettings *net = &config->network;
    if (net->heartbeatIntervalS <= 0 || net->heartbeatTimeoutS <= net->heartbeatIntervalS || net->connectTimeoutS <= 0 || net->reconnectInitialS <= 0 || net->reconnectMaxS < net->reconnectInitialS) {
        snprintf(error, errorCapacity, "the [network] values are inconsistent");
        return false;
    }
    return true;
}

bool ConfigLoad(const char *path, ClusterConfig *config, char *error, size_t errorCapacity) {
    FILE *file = fopen(path, "r");
    if (file == NULL) {
        snprintf(error, errorCapacity, "cannot open %s: %s", path, strerror(errno));
        return false;
    }
    Table *table = calloc(1, sizeof(*table));
    if (table == NULL) {
        fclose(file);
        snprintf(error, errorCapacity, "out of memory");
        return false;
    }
    bool ok = ParseFile(file, table, error, errorCapacity);
    fclose(file);
    memset(config, 0, sizeof(*config));
    config->network = (NetworkSettings){2.0, 6.0, 5.0, 0.5, 5.0};
    char baseDir[PATH_BYTES], pathCopy[PATH_BYTES], caText[PATH_BYTES];
    snprintf(pathCopy, sizeof(pathCopy), "%s", path);
    snprintf(baseDir, sizeof(baseDir), "%s", dirname(pathCopy));
    double port = 0, devicePort = 0;
    ok = ok && TextOf(table, "hub", "node", config->hubNode, sizeof(config->hubNode), true, error, errorCapacity);
    ok = ok && TextOf(table, "hub", "address", config->hubAddress, sizeof(config->hubAddress), true, error, errorCapacity);
    ok = ok && TextOf(table, "hub", "bind-address", config->hubBindAddress, sizeof(config->hubBindAddress), false, error, errorCapacity);
    ok = ok && NumberOf(table, "hub", "port", &port, error, errorCapacity);
    ok = ok && TextOf(table, "tls", "ca-file", caText, sizeof(caText), true, error, errorCapacity);
    ok = ok && TextOf(table, "tls", "cert-file", config->certTemplate, sizeof(config->certTemplate), true, error, errorCapacity);
    ok = ok && TextOf(table, "tls", "key-file", config->keyTemplate, sizeof(config->keyTemplate), true, error, errorCapacity);
    if (ok) {
        config->hubPort = (int)port;
        devicePort = port + 1;
        ok = NumberOf(table, "hub", "device-port", &devicePort, error, errorCapacity);
        config->hubDevicePort = (int)devicePort;
        if (config->hubBindAddress[0] == '\0') snprintf(config->hubBindAddress, sizeof(config->hubBindAddress), "%s", config->hubAddress);
        Resolve(baseDir, caText, config->caFile, sizeof(config->caFile));
        char certText[PATH_BYTES], keyText[PATH_BYTES];
        snprintf(certText, sizeof(certText), "%s", config->certTemplate);
        snprintf(keyText, sizeof(keyText), "%s", config->keyTemplate);
        Resolve(baseDir, certText, config->certTemplate, sizeof(config->certTemplate));
        Resolve(baseDir, keyText, config->keyTemplate, sizeof(config->keyTemplate));
    }
    NetworkSettings *net = &config->network;
    ok = ok && NumberOf(table, "network", "heartbeat-interval-s", &net->heartbeatIntervalS, error, errorCapacity);
    ok = ok && NumberOf(table, "network", "heartbeat-timeout-s", &net->heartbeatTimeoutS, error, errorCapacity);
    ok = ok && NumberOf(table, "network", "connect-timeout-s", &net->connectTimeoutS, error, errorCapacity);
    ok = ok && NumberOf(table, "network", "reconnect-initial-s", &net->reconnectInitialS, error, errorCapacity);
    ok = ok && NumberOf(table, "network", "reconnect-max-s", &net->reconnectMaxS, error, errorCapacity);
    ok = ok && LoadNodes(table, config, error, errorCapacity);
    ok = ok && Validate(config, error, errorCapacity);
    free(table);
    return ok;
}

const NodeConfig *ConfigNode(const ClusterConfig *config, const char *name) {
    for (int i = 0; i < config->nodeCount; i++)
        if (strcmp(config->nodes[i].name, name) == 0) return &config->nodes[i];
    return NULL;
}

static void Expand(const char *templatePath, const char *node, char *out, size_t capacity) {
    const char *marker = strstr(templatePath, "{node}");
    if (marker == NULL) {
        snprintf(out, capacity, "%s", templatePath);
        return;
    }
    snprintf(out, capacity, "%.*s%s%s", (int)(marker - templatePath), templatePath, node, marker + 6);
}

void ConfigCertPath(const ClusterConfig *config, const char *node, char *out, size_t capacity) {
    Expand(config->certTemplate, node, out, capacity);
}

void ConfigKeyPath(const ClusterConfig *config, const char *node, char *out, size_t capacity) {
    Expand(config->keyTemplate, node, out, capacity);
}
