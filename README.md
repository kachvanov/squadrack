# squadrack

Self-hosted MLOps stack for a small ML team sharing one GPU server: S3, MLflow, Postgres and shared caches for many projects.

> **Status:** v0.3.0 – one stack on one host, S3 keys per project and MLflow logins per person, a check for it and an example deployment. Host setup scripts and onboarding a person in one command are planned, not there yet.

## Who it is for

A few people, several ML projects, one GPU server you do not fully own. squadrack gives every project the same experiment tracking, artifact storage and data versioning without a Kubernetes cluster or a cloud account.

## What is inside (v0.3)

| Service | Role |
|---|---|
| SeaweedFS | S3-compatible storage: DVC remote, MLflow artifacts, project buckets |
| HAProxy | the `s3` endpoint: passes only the S3 API and the Admin UI of SeaweedFS out of its isolated network |
| Postgres | MLflow backend store: runs, metrics, model registry, users |
| MLflow | Experiment tracking and model registry with basic auth |

Plus `scripts/access.sh` to give a project or a person its own S3 key and MLflow login, `scripts/doctor.py` to check a running stack and `examples/deploy/`, a template of a deploy repo. Planned: scripts to prepare the host, onboard a person in one command and publish a model for serving.

## Quick start

Needs Docker Engine 28+ with Compose v2.24+, `openssl` and [uv](https://docs.astral.sh/uv/). Runs on one machine, everything on `127.0.0.1`.

```bash
git clone https://github.com/kachvanov/squadrack && cd squadrack

# 1. Stack secrets: every change-me becomes its own random value
awk '/change-me/ { c = "openssl rand -hex 32"; c | getline s; close(c); sub(/change-me[^ ]*/, s) } 1' \
  stack/.env.example > stack/.env && chmod 600 stack/.env

# 2. Start Postgres, S3 and MLflow and wait until all of them are healthy (the first build takes a few minutes)
docker compose -f stack/compose.yaml up -d --build --wait

# 3. Client settings for scripts/ and examples/: the same S3 key and MLflow password
( . stack/.env && sed -e "s|^AWS_ACCESS_KEY_ID=.*|AWS_ACCESS_KEY_ID=$S3_ADMIN_ACCESS_KEY|" \
    -e "s|^AWS_SECRET_ACCESS_KEY=.*|AWS_SECRET_ACCESS_KEY=$S3_ADMIN_SECRET_KEY|" \
    -e "s|^MLFLOW_TRACKING_PASSWORD=.*|MLFLOW_TRACKING_PASSWORD=$MLFLOW_ADMIN_PASSWORD|" \
    .env.example > .env ) && chmod 600 .env

# 4. Check the stack, then train a model and load it back from the registry
uv run --env-file .env scripts/doctor.py
uv run --env-file .env examples/train_demo.py
uv run --env-file .env examples/predict_demo.py
```

MLflow UI: http://localhost:5000, user `admin`, password `MLFLOW_ADMIN_PASSWORD` from `stack/.env`. S3 API: http://localhost:8333; SeaweedFS Admin UI: http://localhost:12535, user `admin`, password `S3_ADMIN_UI_PASSWORD`.

Stop with `docker compose -f stack/compose.yaml down`; data stays in `stack/data/`. For a server, use a deploy repo instead – see [Deployments](#deployments).

## Layout

```
stack/             compose file, Dockerfiles and configs of MLflow and the S3 proxy, .env.example
scripts/           access.sh, s3_check.py, doctor.py
examples/          train and predict demo for MLflow
examples/deploy/   template of a deploy repo: up.sh, deploy.env, compose.override.yaml
```

## Check a running stack

```
uv run --env-file .env scripts/doctor.py
```

`doctor.py` writes a small probe to S3, MLflow tracking, the artifact proxy and the model registry, reads it back and removes it. It stops at the first failed check with a hint on what to fix. CI runs it on a fresh stack for every pull request (`.github/workflows/e2e.yml`).

## Access for projects and people

The admin S3 key and the `admin` MLflow login from `stack/.env` are for the stack itself and for its admins. Everyone else gets their own, from `scripts/access.sh`, run by an admin on the host of the stack:

```bash
scripts/access.sh s3-key demo-project-svc demo-project ~/demo-project-svc.s3.env   # S3 key of a project
scripts/access.sh mlflow-user alice ~/alice.mlflow.env                             # MLflow login of a person
scripts/access.sh list                                                             # who has access, no secrets
```

- **S3 key:** read and write objects in one bucket (`--readonly`: read only). No other bucket, no new buckets, no bucket deletion; the bucket is created if it is missing. Made with `s3.user.provision` of SeaweedFS, stored in its filer, so it survives restarts. A new key instead of the old one: `scripts/access.sh s3-rotate <user> <out-file>`.
- **MLflow login:** not an admin. With `default_permission = READ` in `stack/mlflow/basic_auth.ini` it reads every experiment and model and gets `MANAGE` on what it creates; anything else is granted by an admin. Artifacts go through the MLflow server, so a person needs no S3 key for MLflow.
- **Secrets never reach the terminal:** each command writes a new file with mode 600 as lines for an env file, and refuses to overwrite one. Combine it with the addresses from `.env.example` into the client `.env` and check: `uv run --env-file .env scripts/doctor.py --project-key`.

In a deployment, point the script at the deploy repo's `up.sh`: `SQUADRACK_COMPOSE=../deploy/up.sh scripts/access.sh list`.

## Network

The stack publishes only MLflow, the S3 API and the SeaweedFS Admin UI, and only on `127.0.0.1`. SeaweedFS also listens on master, volume, filer and IAM ports that take requests without a key, so it runs alone in the `storage` network: `internal`, with gateway mode `isolated`, the bridge has no address on the host. Neither host processes nor containers of other networks reach SeaweedFS; the `s3` service, HAProxy in TCP mode, passes only the S3 API and the Admin UI. Containers in the stack network reach S3 at `http://s3:8333`. CI checks this on every pull request.

## Deployments

The product holds no host-specific values. Every installation keeps them in its own **deploy repo** (product version, paths, ports, compose overrides) and its secrets in a `.env` file that never goes to git. Start from [`examples/deploy/`](examples/deploy/README.md); changes are listed in the [CHANGELOG](CHANGELOG.md).

## License

Apache-2.0
