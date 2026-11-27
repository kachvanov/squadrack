#!/usr/bin/env bash
# Bring up the squadrack stack of this deployment: the product at the version pinned in
# deploy.env, this repo's compose.override.yaml and the secrets file. Safe to rerun:
# an unchanged stack stays as it is, a new SQUADRACK_VERSION is checked out and rolled out.
#
#   ./up.sh                   check out the pinned version, build and start, wait for healthy
#   ./up.sh ps | logs | down  any other docker compose command on the same stack, no checkout
#   ./up.sh access <command>  scripts/access.sh of the product on this stack: keys and logins
#
# Written for bash 3.2 (macOS /bin/bash) and newer.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)

die() { echo "up.sh: $*" >&2; exit 1; }
# Paths in deploy.env are relative to this directory; `..` is resolved when the parent exists
abs() {
  local p
  case "$1" in /*) p=$1 ;; *) p=$here/$1 ;; esac
  if [ -d "$(dirname "$p")" ]; then
    echo "$(cd "$(dirname "$p")" && pwd)/$(basename "$p")"
  else
    echo "$p"
  fi
}

[ -f "$here/deploy.env" ] || die "no deploy.env next to up.sh"
# shellcheck source=deploy.env
. "$here/deploy.env"

for var in SQUADRACK_VERSION SQUADRACK_DIR SQUADRACK_SECRETS SQUADRACK_DATA_DIR SQUADRACK_PROJECT \
           MLFLOW_PORT S3_PORT S3_ADMIN_UI_PORT; do
  [ -n "${!var:-}" ] || die "set $var in deploy.env"
done
SUDO=${SUDO:-}

product=$(abs "$SQUADRACK_DIR")
secrets=$(abs "$SQUADRACK_SECRETS")
data=$(abs "$SQUADRACK_DATA_DIR")

[ -d "$product/.git" ] || die "no product clone in $product:
  git clone https://github.com/kachvanov/squadrack $product"
[ -f "$secrets" ] || die "no secrets file $secrets: copy $product/stack/.env.example there and fill it in"
# A secrets file readable by every user of the host gives away the S3 key and the passwords
[ -z "$(find "$secrets" -perm -004)" ] || die "$secrets is readable by everyone: chmod o-rwx $secrets"

compose() {
  # sudo resets the environment, so the values compose interpolates are passed explicitly.
  # No default build attestations: the provenance one records the time and the id of each build,
  # so with the containerd image store even a fully cached build gets a new image id, and compose
  # recreates the built services on every run. `provenance: false` in the compose file is not
  # enough before Compose 5.5.1 (docker/compose#14111).
  $SUDO env \
    BUILDX_NO_DEFAULT_ATTESTATIONS=1 \
    SQUADRACK_DATA_DIR="$data" \
    MLFLOW_PORT="$MLFLOW_PORT" \
    S3_PORT="$S3_PORT" \
    S3_ADMIN_UI_PORT="$S3_ADMIN_UI_PORT" \
    docker compose \
      --project-name "$SQUADRACK_PROJECT" \
      --project-directory "$product/stack" \
      --file "$product/stack/compose.yaml" \
      --file "$here/compose.override.yaml" \
      --env-file "$secrets" \
      "$@"
}

# Keys and logins: access.sh of the checked-out product, reaching the stack through this script
if [ "${1:-}" = access ]; then
  shift
  projects=
  [ -z "${SQUADRACK_PROJECTS_DIR:-}" ] || projects=$(abs "$SQUADRACK_PROJECTS_DIR")
  SQUADRACK_COMPOSE="$here/up.sh" SQUADRACK_PROJECTS_DIR="$projects" exec "$product/scripts/access.sh" "$@"
fi

if [ $# -gt 0 ]; then
  compose "$@"
  exit
fi

# Check out the pinned version. Compare commits, not tag names: several tags may point to one commit.
git_p() { git -C "$product" "$@"; }
tag="refs/tags/$SQUADRACK_VERSION"
git_p rev-parse --quiet --verify "$tag^{commit}" > /dev/null || git_p fetch --quiet --tags origin
want=$(git_p rev-parse --quiet --verify "$tag^{commit}") || die "no tag $SQUADRACK_VERSION in $product"
[ -z "$(git_p status --porcelain --untracked-files=no)" ] ||
  die "$product has local changes to tracked files: the stack would not match $SQUADRACK_VERSION"
if [ "$(git_p rev-parse HEAD)" != "$want" ]; then
  git_p -c advice.detachedHead=false checkout --quiet "$tag"
  echo "product   $product -> $SQUADRACK_VERSION"
else
  echo "product   $product is at $SQUADRACK_VERSION"
fi

mkdir -p "$data"
compose up --detach --build --wait --wait-timeout 300 --remove-orphans
compose ps

echo
echo "Check the stack from a clone of the product with a client .env (see its .env.example):"
echo "  uv run --env-file .env scripts/doctor.py"
