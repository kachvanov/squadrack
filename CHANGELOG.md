# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] – 2026-11-19

### Added

- `scripts/access.sh`: an S3 key per project, limited to one bucket with no right to create or delete buckets (`s3-key`, `s3-rotate`), an MLflow login per person that is not an admin (`mlflow-user`), and `list`. Secrets go straight into new files with mode 600 and never to the terminal. In a deployment it runs through the deploy repo's `up.sh` (`SQUADRACK_COMPOSE`).
- `doctor.py --project-key`: checks that the S3 key reaches its bucket and that writing to the MLflow artifact bucket, creating a bucket and writing to a missing one are denied. Without the flag a project key now fails with a hint instead of a bare 403.
- CI e2e gives `demo-project` its own key and a person a login, runs `doctor.py` with them, rotates the key and checks that the old one is rejected and the admin key keeps working.

### Upgrading

- Nothing to migrate: no service changes. Admins can now move projects and people off the admin key and the `admin` login.

## [0.2.0] – 2026-11-19

### Security

- SeaweedFS is no longer reachable from the host or from other containers. `weed mini` listens on master, volume, filer and IAM ports that take requests without a key, and any user of the host could read and write all S3 data through the filer at the container address. SeaweedFS now runs alone in the network `storage`: `internal`, gateway mode `isolated`, so its bridge has no address on the host. CI e2e checks that the host and the `mlflow` container do not reach these ports.

### Changed

- The `s3` service is now HAProxy 3.4.8 in TCP mode, in the stack network and in `storage`: it passes only the S3 API (8333) and the Admin UI (12535). `http://s3:8333`, `S3_PORT` and `S3_ADMIN_UI_PORT` stay as they were. SeaweedFS itself is the new service `seaweedfs`, with the same data directory `${SQUADRACK_DATA_DIR}/s3`: no data migration.
- Docker Engine 28+ is required, for gateway mode `isolated`.

### Upgrading

- Stop the old `s3` and `mlflow` before `up`, with the compose files of the version you run: compose starts the new `seaweedfs` before it replaces the old `s3`, and two SeaweedFS processes would open one data directory. With a deploy repo: `./up.sh stop mlflow s3`, then set `SQUADRACK_VERSION` and run `./up.sh`.
- Add `seaweedfs` to `compose.override.yaml` of the deploy repo wherever `s3` is, such as log limits: `s3` is the proxy now.

## [0.1.2] – 2026-11-19

### Fixed

- 0.1.1 did not fix the crash loop on a host where the checkout has an ACL: BuildKit applies the ACL of the source file after `COPY --chmod`, so `basic_auth.ini` stayed unreadable. The mode is now set by `chmod` in a `RUN` step, and CI e2e builds from a tree under an ACL instead of a plain `chmod o-rwx`.

## [0.1.1] – 2026-11-19

### Fixed

- MLflow no longer crash-loops with `KeyError: 'mlflow'` when the image is built from a checkout with no read bits for others, such as a clone under a default ACL on a shared host: `basic_auth.ini` is copied with mode 0644 into a 0755 directory. CI e2e now builds from such a tree.

## [0.1.0] – 2026-11-18

First release: one stack on one host, a way to check it and an example deployment.

### Added

- Postgres 18.7 as the MLflow backend store, data in a bind mount, healthcheck by `pg_isready` over TCP.
- SeaweedFS 4.54 (`weed mini`) as S3: buckets `mlflow` and `demo-project` created on start, Admin UI under a password.
- MLflow 3.18.0 tracking server and model registry: basic auth with users in Postgres, artifacts proxied to `s3://mlflow`, so clients need no S3 key.
- `scripts/s3_check.py` – S3 round trip with `boto3`, also for throughput checks.
- `examples/train_demo.py` and `examples/predict_demo.py` – a model tracked in MLflow, registered with the `champion` alias and loaded by it.
- `scripts/doctor.py` – checks S3, MLflow tracking, the artifact proxy and the registry in one run and cleans up after itself.
- CI: pre-commit with gitleaks, compose validation, e2e on a fresh stack with `doctor.py` and the demo.
- `examples/deploy/` – template of a deploy repo with `up.sh`, `deploy.env` and `compose.override.yaml`.
- README quick start: from `git clone` to a model loaded from the registry in four steps.
- Data directory and host ports of `stack/compose.yaml` come from `SQUADRACK_DATA_DIR`, `MLFLOW_PORT`, `S3_PORT`, `S3_ADMIN_UI_PORT`; ports always bind to `127.0.0.1`.

### Security

- MLflow telemetry is off on the server, in the UI it serves and in clients configured from `.env.example` (`MLFLOW_DISABLE_TELEMETRY=true`); SeaweedFS telemetry is off too.

[0.3.0]: https://github.com/kachvanov/squadrack/releases/tag/v0.3.0
[0.2.0]: https://github.com/kachvanov/squadrack/releases/tag/v0.2.0
[0.1.2]: https://github.com/kachvanov/squadrack/releases/tag/v0.1.2
[0.1.1]: https://github.com/kachvanov/squadrack/releases/tag/v0.1.1
[0.1.0]: https://github.com/kachvanov/squadrack/releases/tag/v0.1.0
