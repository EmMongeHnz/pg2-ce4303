/* Cluster configuration read from the same `config.toml` the Python components use. Only the TOML subset that file
 * needs is understood: tables, strings, numbers and one-line arrays of strings. */
#ifndef ASTREA_CONFIG_H
#define ASTREA_CONFIG_H

#include <stdbool.h>
#include <stddef.h>

#include "astrea-protocol.h"

#define MAX_NODES 16
#define PATH_BYTES 512

typedef struct {
    char name[NODE_NAME_BYTES];
    int roles;
} NodeConfig;

typedef struct {
    double heartbeatIntervalS;
    double heartbeatTimeoutS;
    double connectTimeoutS;
    double reconnectInitialS;
    double reconnectMaxS;
} NetworkSettings;

typedef struct {
    char hubNode[NODE_NAME_BYTES];
    char hubAddress[128];
    char hubBindAddress[128];
    int hubPort;
    int hubDevicePort;
    char caFile[PATH_BYTES];
    char certTemplate[PATH_BYTES];
    char keyTemplate[PATH_BYTES];
    NetworkSettings network;
    NodeConfig nodes[MAX_NODES];
    int nodeCount;
} ClusterConfig;

bool ConfigLoad(const char *path, ClusterConfig *config, char *error, size_t errorCapacity);
const NodeConfig *ConfigNode(const ClusterConfig *config, const char *name);
void ConfigCertPath(const ClusterConfig *config, const char *node, char *out, size_t capacity);
void ConfigKeyPath(const ClusterConfig *config, const char *node, char *out, size_t capacity);

#endif
