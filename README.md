# squadrack

Self-hosted MLOps stack for a small ML team sharing one GPU server: S3, MLflow, Postgres and shared caches for many projects.

> **Status:** v0.1 in progress. Not ready for use yet.

## Who it is for

A few people, several ML projects, one GPU server you do not fully own. squadrack gives every project the same experiment tracking, artifact storage and data versioning without a Kubernetes cluster or a cloud account.

## What is inside (v0.1)

| Service | Role |
|---|---|
| SeaweedFS | S3-compatible storage: DVC remote, MLflow artifacts, project buckets |
| Postgres | MLflow backend store: runs, metrics, model registry, users |
| MLflow | Experiment tracking and model registry with basic auth |

Plus shell scripts to prepare the host, add a project or a user, publish a model for serving and check the setup.

## Layout

```
stack/      compose file, Dockerfiles, config templates, .env.example
scripts/    host setup, add-project, add-user, publish-model, doctor
examples/   a sample deployment and a demo project
docs/       architecture, permissions, decisions
```

The product holds no host-specific values. Every installation keeps them in its own **deploy repo** (paths, groups, ports, GPU overrides, product version) and its secrets in a `.env` file that never goes to git.

## License

Apache-2.0
