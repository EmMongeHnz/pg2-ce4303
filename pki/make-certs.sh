#!/usr/bin/env bash
# Generates the AstreaView private CA and one certificate per node (ECDSA P-256) for mutual TLS.
#
# Usage: pki/make-certs.sh [--out DIR] [--nodes "host worker-b pi"] [--ip NODE=ADDRESS]... [--days N] [--force]
#
# Output in DIR: ca.crt, ca.key, <node>.crt, <node>.key. The node name is the DNS SAN the peers check
# against config.toml; --ip adds an IP SAN for tools that connect by address.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$SCRIPT_DIR/out"
NODES="host worker-b pi"
DAYS=825
CA_DAYS=3650
FORCE=0
declare -A NODE_IPS=()

Fail() {
    echo "make-certs: $*" >&2
    exit 1
}

Usage() {
    sed -n '2,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out) OUT_DIR="${2:?--out needs a directory}"; shift 2 ;;
        --nodes) NODES="${2:?--nodes needs a list}"; shift 2 ;;
        --ip)
            pair="${2:?--ip needs NODE=ADDRESS}"
            [[ "$pair" == *=* ]] || Fail "--ip expects NODE=ADDRESS, got '$pair'"
            NODE_IPS["${pair%%=*}"]="${pair#*=}"
            shift 2
            ;;
        --days) DAYS="${2:?--days needs a number}"; shift 2 ;;
        --force) FORCE=1; shift ;;
        -h|--help) Usage; exit 0 ;;
        *) Fail "unknown argument '$1'" ;;
    esac
done

command -v openssl >/dev/null || Fail "openssl is required"

read -r -a NODE_LIST <<< "$NODES"
[[ ${#NODE_LIST[@]} -gt 0 ]] || Fail "at least one node is required"
for node in "${NODE_LIST[@]}"; do
    [[ "$node" =~ ^[a-z0-9][a-z0-9-]*$ ]] || Fail "invalid node name '$node' (lowercase letters, digits and hyphens)"
done
for node in "${!NODE_IPS[@]}"; do
    [[ " $NODES " == *" $node "* ]] || Fail "--ip names unknown node '$node'"
done

mkdir -p "$OUT_DIR"
if [[ $FORCE -eq 0 ]]; then
    for target in ca.crt ca.key "${NODE_LIST[@]/%/.crt}" "${NODE_LIST[@]/%/.key}"; do
        [[ ! -e "$OUT_DIR/$target" ]] || Fail "$OUT_DIR/$target exists (use --force to overwrite)"
    done
fi

umask 077
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

GenerateKey() {
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$1"
}

RandomSerial() {
    echo "0x$(openssl rand -hex 16)"
}

CreateCa() {
    GenerateKey "$OUT_DIR/ca.key"
    openssl req -x509 -new -key "$OUT_DIR/ca.key" -sha256 -days "$CA_DAYS" \
        -subj "/O=AstreaView/CN=AstreaView CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "subjectKeyIdentifier=hash" \
        -out "$OUT_DIR/ca.crt"
}

NodeExtensions() {
    local node="$1"
    local altNames="DNS:$node"
    [[ -z "${NODE_IPS[$node]:-}" ]] || altNames+=",IP:${NODE_IPS[$node]}"
    cat <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=$altNames
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
EOF
}

CreateNodeCertificate() {
    local node="$1"
    GenerateKey "$OUT_DIR/$node.key"
    openssl req -new -key "$OUT_DIR/$node.key" -subj "/O=AstreaView/CN=$node" -out "$WORK_DIR/$node.csr"
    NodeExtensions "$node" > "$WORK_DIR/$node.ext"
    openssl x509 -req -in "$WORK_DIR/$node.csr" -CA "$OUT_DIR/ca.crt" -CAkey "$OUT_DIR/ca.key" \
        -set_serial "$(RandomSerial)" -sha256 -days "$DAYS" -extfile "$WORK_DIR/$node.ext" \
        -out "$OUT_DIR/$node.crt"
}

CreateCa
for node in "${NODE_LIST[@]}"; do
    CreateNodeCertificate "$node"
done

chmod 600 "$OUT_DIR"/*.key
chmod 644 "$OUT_DIR"/*.crt
echo "make-certs: CA and certificates for ${NODE_LIST[*]} written to $OUT_DIR"
