# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] – 2026-11-20

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

[0.1.1]: https://github.com/kachvanov/squadrack/releases/tag/v0.1.1
[0.1.0]: https://github.com/kachvanov/squadrack/releases/tag/v0.1.0
