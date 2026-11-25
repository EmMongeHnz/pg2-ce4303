/* TLS 1.3 with mutual authentication on OpenSSL. Both sides trust only the cluster CA (never the system store) and
 * must present a certificate that carries the node name as a DNS name. */
#ifndef ASTREA_TLS_H
#define ASTREA_TLS_H

#include <openssl/ssl.h>
#include <stdbool.h>
#include <stddef.h>

#include "astrea-config.h"

/* `preferChacha` puts ChaCha20-Poly1305 first, which is faster on a Raspberry Pi 3 (no AES instructions). */
SSL_CTX *TlsServerContext(const ClusterConfig *config, const char *nodeName, bool preferChacha, char *error, size_t errorCapacity);
SSL_CTX *TlsClientContext(const ClusterConfig *config, const char *nodeName, bool preferChacha, char *error, size_t errorCapacity);

bool TlsPeerNodeName(SSL *ssl, char *out, size_t capacity);
void TlsDescribe(SSL *ssl, char *out, size_t capacity);
void TlsLastError(char *out, size_t capacity);

#endif
