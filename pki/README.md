# pki

Private CA and one certificate per node (`host`, `worker-b`, `pi`) for mutual TLS 1.3. The node name is the DNS SAN of its certificate and is what the peers check against `config.toml`.

```
pki/make-certs.sh                                  # writes pki/out/ (git-ignored)
pki/make-certs.sh --out pki/example --force        # regenerates the example set
pki/make-certs.sh --nodes "host worker-b pi" --ip host=192.168.1.10
```

Each node needs `ca.crt`, its own `<node>.crt` and `<node>.key`; never copy another node's key. Private keys are git-ignored.

`pki/example/` holds a throw-away set (CA and node keys included) added with `git add -f` so the delivery package is runnable. It must never be used for the demo: generate a fresh set in `pki/out/` instead.

`openssl-chacha20.cnf` limits a process to ChaCha20-Poly1305 (the Pi 3 has no AES acceleration); see its header for the `OPENSSL_CONF` usage.
