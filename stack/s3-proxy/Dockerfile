# The s3 service: HAProxy in TCP mode, the only way from outside into the storage network.
# Alpine for busybox wget in the healthcheck: the Debian variant has no HTTP client.
FROM haproxy:3.4.8-alpine

# The config is part of the image, not a bind mount: on a shared host the clone has an ACL with
# no bits for others, and BuildKit applies it even after COPY --chmod (see stack/mlflow/Dockerfile).
USER root
COPY haproxy.cfg /usr/local/etc/haproxy/haproxy.cfg
RUN chmod 0644 /usr/local/etc/haproxy/haproxy.cfg && haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg
USER haproxy

EXPOSE 8333 12535
