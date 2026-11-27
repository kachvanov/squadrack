# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Deploy template: `up.sh` recreated the built services (`s3`, `mlflow`) on every run, even with nothing changed. The default provenance attestation gives a cached build a new image id when Docker uses the containerd image store. `up.sh` now builds with `BUILDX_NO_DEFAULT_ATTESTATIONS=1`.

### Upgrading

- Copy `examples/deploy/up.sh` into the deploy repo, or add `BUILDX_NO_DEFAULT_ATTESTATIONS=1` to the `env` line of its `compose()`. The first run after that recreates `s3` and `mlflow` once more; later runs leave them running.

## [0.4.1] – 2026-11-20

### Fixed

- `scripts/access.sh project-key`: the key file is mode 0640 again when the projects directory has a default ACL for the admins' group. `setfacl` recalculated the ACL mask to `rwx`, so the group could write the file, and the script still reported mode 640. The mask is now set to `r` explicitly; e2e runs on a directory with such a default ACL.

### Upgrading

- Key files created by 0.4.0 in a directory with a default ACL: `setfacl -m m::r <projects dir>/<project>/secrets/s3-credentials`, or rotate the key with `project-key --rotate`. No service changes.

## [0.4.0] – 2026-11-19

### Added

- Keys of project services in one directory: `SQUADRACK_PROJECTS_DIR`, `<project>/secrets/s3-credentials` inside. `scripts/access.sh project-key <project>` creates the S3 user `<project>-svc` on bucket `<project>` and writes its key in the format of `~/.aws/credentials`, mode 0640 with a read ACL for the host user `<project>-svc`; `--rotate` replaces it. `project-check <project>` uses the key from a container running as that user.
- `up.sh access <command>` in the deploy template: `scripts/access.sh` on the stack of the deployment, with `SQUADRACK_PROJECTS_DIR` from `deploy.env`.
- README: how a project service mounts its key as a compose secret.

### Upgrading

- Copy `examples/deploy/up.sh` into the deploy repo and add `SQUADRACK_PROJECTS_DIR` to `deploy.env` if you want project keys. Create that directory once, readable by the admins only. No service changes.

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

[0.4.1]: https://github.com/kachvanov/squadrack/releases/tag/v0.4.1
[0.4.0]: https://github.com/kachvanov/squadrack/releases/tag/v0.4.0
[0.3.0]: https://github.com/kachvanov/squadrack/releases/tag/v0.3.0
[0.2.0]: https://github.com/kachvanov/squadrack/releases/tag/v0.2.0
[0.1.2]: https://github.com/kachvanov/squadrack/releases/tag/v0.1.2
[0.1.1]: https://github.com/kachvanov/squadrack/releases/tag/v0.1.1
[0.1.0]: https://github.com/kachvanov/squadrack/releases/tag/v0.1.0
