# Example deploy repo

A deployment of squadrack is two git clones and two host-only things side by side:

```
<root>/
├── squadrack/   clone of this product; up.sh checks out SQUADRACK_VERSION
├── deploy/      your private deploy repo: a copy of this folder with your values
├── .env         secrets, from squadrack/stack/.env.example; never in git, not readable by others
├── projects/    optional: keys of project services, <project>/secrets/; readable by the admins only
└── data/        service data: pg/ and s3/, created by the services
```

The product holds no host-specific values. The deploy repo holds them – version, paths, ports, compose overrides – and its history shows what changed on the host and when.

## Set up

```bash
mkdir <root> && cd <root>
git clone https://github.com/kachvanov/squadrack
cp -r squadrack/examples/deploy deploy        # then make it a private git repo
cp squadrack/stack/.env.example .env && chmod 600 .env   # fill in the secrets
deploy/up.sh
```

`up.sh` refuses to start when the product clone has local changes to tracked files or the secrets file is readable by everyone.

## Files

| File | What |
|---|---|
| `deploy.env` | product version, paths, compose project name, host ports, `SUDO` for hosts where you are not in the `docker` group |
| `compose.override.yaml` | additions to `stack/compose.yaml`: log limits here; resource limits, extra mounts and the like on a real host |
| `up.sh` | checks out the pinned version and runs `docker compose up --build --wait`; `./up.sh ps`, `logs`, `down` pass through to compose |

## Upgrade

Change `SQUADRACK_VERSION` in `deploy.env`, commit, run `./up.sh`. Read the CHANGELOG of the product first: a new version may need new variables in `.env`.

## Access for projects and people

`./up.sh access <command>` runs `scripts/access.sh` of the checked-out product on the stack of this deployment, with `SQUADRACK_PROJECTS_DIR` from `deploy.env`:

```bash
./up.sh access list
./up.sh access project-key demo-project      # writes <projects dir>/demo-project/secrets/s3-credentials
./up.sh access project-check demo-project
```

Create the projects directory once, readable by the admins only, for example `mkdir -m 770 <root>/projects`.

See "Access for projects and people" in the product README.

## Check

From the product clone, with a client `.env` (template: `.env.example` in the product root):

```bash
uv run --env-file .env scripts/doctor.py
```
