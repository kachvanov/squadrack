# squadrack

Self-hosted MLOps stack for a small ML team sharing one GPU server: S3, MLflow, Postgres and shared caches for many projects.

> **Status:** v0.1.0 – one stack on one host, a check for it and an example deployment. Per-project S3 keys, MLflow users and host setup scripts are planned, not there yet.

## Who it is for

A few people, several ML projects, one GPU server you do not fully own. squadrack gives every project the same experiment tracking, artifact storage and data versioning without a Kubernetes cluster or a cloud account.

## What is inside (v0.1)

| Service | Role |
|---|---|
| SeaweedFS | S3-compatible storage: DVC remote, MLflow artifacts, project buckets |
| Postgres | MLflow backend store: runs, metrics, model registry, users |
| MLflow | Experiment tracking and model registry with basic auth |

Plus `scripts/doctor.py` to check a running stack and `examples/deploy/`, a template of a deploy repo. Planned: scripts to prepare the host, add a project or a user and publish a model for serving.

## Layout

```
stack/             compose file, MLflow Dockerfile and config, .env.example
scripts/           s3_check.py, doctor.py
examples/          train and predict demo for MLflow
examples/deploy/   template of a deploy repo: up.sh, deploy.env, compose.override.yaml
```

## Check a running stack

```
uv run --env-file .env scripts/doctor.py
```

`doctor.py` writes a small probe to S3, MLflow tracking, the artifact proxy and the model registry, reads it back and removes it. It stops at the first failed check with a hint on what to fix. CI runs it on a fresh stack for every pull request (`.github/workflows/e2e.yml`).

## Deployments

The product holds no host-specific values. Every installation keeps them in its own **deploy repo** (product version, paths, ports, compose overrides) and its secrets in a `.env` file that never goes to git. Start from [`examples/deploy/`](examples/deploy/README.md); changes are listed in the [CHANGELOG](CHANGELOG.md).

## License

Apache-2.0
