#include "astrea-tls.h"

#include <openssl/err.h>
#include <openssl/x509v3.h>
#include <stdio.h>
#include <string.h>

#define SUITES_DEFAULT "TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:TLS_AES_128_GCM_SHA256"
#define SUITES_CHACHA_FIRST "TLS_CHACHA20_POLY1305_SHA256:TLS_AES_256_GCM_SHA384:TLS_AES_128_GCM_SHA256"

void TlsLastError(char *out, size_t capacity) {
    unsigned long code = ERR_get_error();
    if (code == 0) snprintf(out, capacity, "unknown TLS error");
    else ERR_error_string_n(code, out, capacity);
    ERR_clear_error();
}

static SSL_CTX *BuildContext(const SSL_METHOD *method, const ClusterConfig *config, const char *nodeName, bool preferChacha, int verifyMode, char *error, size_t errorCapacity) {
    SSL_CTX *context = SSL_CTX_new(method);
    if (context == NULL) {
        TlsLastError(error, errorCapacity);
        return NULL;
    }
    char certPath[PATH_BYTES], keyPath[PATH_BYTES];
    ConfigCertPath(config, nodeName, certPath, sizeof(certPath));
    ConfigKeyPath(config, nodeName, keyPath, sizeof(keyPath));
    bool ok = SSL_CTX_set_min_proto_version(context, TLS1_3_VERSION) == 1 && SSL_CTX_set_max_proto_version(context, TLS1_3_VERSION) == 1;
    ok = ok && SSL_CTX_set_ciphersuites(context, preferChacha ? SUITES_CHACHA_FIRST : SUITES_DEFAULT) == 1;
    ok = ok && SSL_CTX_load_verify_locations(context, config->caFile, NULL) == 1;
    if (!ok) {
        char detail[160];
        TlsLastError(detail, sizeof(detail));
        snprintf(error, errorCapacity, "cannot load the CA %s: %s", config->caFile, detail);
        SSL_CTX_free(context);
        return NULL;
    }
    if (SSL_CTX_use_certificate_chain_file(context, certPath) != 1 || SSL_CTX_use_PrivateKey_file(context, keyPath, SSL_FILETYPE_PEM) != 1 || SSL_CTX_check_private_key(context) != 1) {
        char detail[160];
        TlsLastError(detail, sizeof(detail));
        snprintf(error, errorCapacity, "cannot load the certificate %s or its key: %s", certPath, detail);
        SSL_CTX_free(context);
        return NULL;
    }
    SSL_CTX_set_verify(context, verifyMode, NULL);
    SSL_CTX_set_mode(context, SSL_MODE_ENABLE_PARTIAL_WRITE | SSL_MODE_ACCEPT_MOVING_WRITE_BUFFER);
    SSL_CTX_set_options(context, SSL_OP_IGNORE_UNEXPECTED_EOF);
    return context;
}

SSL_CTX *TlsServerContext(const ClusterConfig *config, const char *nodeName, bool preferChacha, char *error, size_t errorCapacity) {
    return BuildContext(TLS_server_method(), config, nodeName, preferChacha, SSL_VERIFY_PEER | SSL_VERIFY_FAIL_IF_NO_PEER_CERT, error, errorCapacity);
}

SSL_CTX *TlsClientContext(const ClusterConfig *config, const char *nodeName, bool preferChacha, char *error, size_t errorCapacity) {
    return BuildContext(TLS_client_method(), config, nodeName, preferChacha, SSL_VERIFY_PEER, error, errorCapacity);
}

bool TlsPeerNodeName(SSL *ssl, char *out, size_t capacity) {
    X509 *certificate = SSL_get1_peer_certificate(ssl);
    if (certificate == NULL) return false;
    int dnsNames = 0;
    bool usable = false;
    GENERAL_NAMES *names = X509_get_ext_d2i(certificate, NID_subject_alt_name, NULL, NULL);
    for (int i = 0; names != NULL && i < sk_GENERAL_NAME_num(names); i++) {
        const GENERAL_NAME *name = sk_GENERAL_NAME_value(names, i);
        if (name->type != GEN_DNS) continue;
        dnsNames++;
        const unsigned char *text = ASN1_STRING_get0_data(name->d.dNSName);
        size_t length = (size_t)ASN1_STRING_length(name->d.dNSName);
        if (length == 0 || length >= capacity || memchr(text, '\0', length) != NULL) continue;
        memcpy(out, text, length);
        out[length] = '\0';
        usable = true;
    }
    /* A certificate that names several nodes would be an ambiguous identity. */
    bool found = usable && dnsNames == 1;
    if (names != NULL) GENERAL_NAMES_free(names);
    X509_free(certificate);
    return found;
}

void TlsDescribe(SSL *ssl, char *out, size_t capacity) {
    snprintf(out, capacity, "%s %s", SSL_get_version(ssl), SSL_get_cipher_name(ssl));
}
