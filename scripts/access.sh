#!/usr/bin/env bash
# Give a project or a person access to the stack: an S3 key limited to one bucket, an MLflow login.
# Run by an admin of the stack, on its host. Secrets never reach the terminal: they go straight
# from the container into a new file readable by you only, as lines for an env file.
#
#   scripts/access.sh s3-key [--readonly] <user> <bucket> <out-file>
#       S3 user with one key: read and write (or only read) objects in <bucket>, nothing else:
#       no other bucket, no new buckets, no bucket deletion. Creates <bucket> if it is missing.
#   scripts/access.sh s3-rotate <user> <out-file>
#       A new key pair for an S3 user instead of its only one; the old key stops working at once.
#   scripts/access.sh mlflow-user <user> <out-file>
#       MLflow login with a random password, not an admin. It reads every experiment and model
#       (default_permission in stack/mlflow/basic_auth.ini) and manages what it creates.
#   scripts/access.sh list
#       S3 users with their policies and MLflow users, no secrets.
#
# The stack is reached with `docker compose exec`. By default the compose file of this clone;
# in a deployment, the deploy repo's up.sh, which passes any compose command through:
#   SQUADRACK_COMPOSE=../deploy/up.sh scripts/access.sh list
#
# The admin S3 key and the admin MLflow login come from the stack's .env, inside the containers:
# nothing to pass. If the MLflow admin password was changed through the API, change
# MLFLOW_ADMIN_PASSWORD in .env as well, or mlflow-user gets a 401.
#
# Written for bash 3.2 (macOS /bin/bash) and newer.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
ARTIFACT_BUCKET=mlflow  # --artifacts-destination of MLflow: only the stack writes there

die() { echo "access.sh: $*" >&2; exit 1; }
usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

read -r -a compose <<< "${SQUADRACK_COMPOSE:-docker compose -f $here/../stack/compose.yaml}"
weed_shell() { "${compose[@]}" exec -T seaweedfs weed shell; }
# The script goes in argv (no secrets there), the output comes back on stdout
mlflow_python() { "${compose[@]}" exec -T mlflow python -W "ignore:starlette.middleware.wsgi is deprecated" -c "$1" "${@:2}"; }

check_user() {
  [[ $1 =~ ^[a-z0-9][a-z0-9._-]{1,62}$ ]] ||
    die "user name '$1': 2 to 63 characters, lowercase letters, digits, '.', '_' and '-'"
}

check_bucket() {
  [[ $1 =~ ^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$ ]] ||
    die "bucket name '$1': 3 to 63 characters, lowercase letters, digits and '-'"
  [ "$1" != "$ARTIFACT_BUCKET" ] ||
    die "bucket $ARTIFACT_BUCKET holds MLflow artifacts: clients reach it through the MLflow server"
}

# A new file only you can read. chmod after creating it: in a directory with a default ACL
# the umask does not apply, and chmod 600 also clears the ACL mask.
new_secret_file() {
  [ -d "$(dirname "$1")" ] || die "no directory $(dirname "$1")"
  ( set -o noclobber; umask 077; : > "$1" ) 2> /dev/null || die "$1 exists: choose a new file"
  chmod 600 "$1"
  out=$1
  trap '[ -n "${out:-}" ] && rm -f "$out"' EXIT  # a half-written file is removed
}

keep_secret_file() {
  trap - EXIT
  echo "secrets   written to $out (mode 600); hand it over as a file, never paste it into a chat"
}

# s3.user.provision and s3.accesskey.rotate print the new key pair on stdout: the pair goes
# to the secret file, every other line to the terminal
write_key_pair() {
  weed_shell | awk -v out="$out" '
    /^Access Key: / { print "AWS_ACCESS_KEY_ID=" $3 > out; next }
    /^Secret Key: / { print "AWS_SECRET_ACCESS_KEY=" $3 > out; next }
    /^Save these credentials/ { next }
    NF { print "s3        " $0 }'
  [ "$(grep -c '^AWS_[A-Z_]*=.' "$out")" = 2 ] ||
    die "no key pair in the output of weed shell; check the user with: s3.user.show -name $1"
}

s3_user_show() {
  local shown
  shown=$(printf 's3.user.show -name %s\n' "$1" | weed_shell 2>&1 || true)
  case $shown in
    *"\"name\":\"$1\""*) echo "$shown" ;;
    *"not found"*) ;;
    *) die "cannot read S3 users: $shown" ;;
  esac
}

s3_key() {
  local role=readwrite
  if [ "${1:-}" = "--readonly" ]; then role=readonly; shift; fi
  [ $# -eq 3 ] || usage
  local user=$1 bucket=$2
  check_user "$user"
  check_bucket "$bucket"

  [ -z "$(s3_user_show "$user")" ] || die "S3 user $user exists. A new key for it: scripts/access.sh s3-rotate $user <out-file>"

  local buckets
  buckets=$(printf 's3.bucket.list\n' | weed_shell) || die "cannot list buckets: $buckets"
  if ! printf '%s\n' "$buckets" | awk -v b="$bucket" '$1 == b { found = 1 } END { exit !found }'; then
    printf 's3.bucket.create -name %s\n' "$bucket" | weed_shell | grep -q "created bucket $bucket" ||
      die "could not create bucket $bucket"
    echo "s3        bucket $bucket created"
  fi

  new_secret_file "$3"
  printf 's3.user.provision -name %s -bucket %s -role %s\n' "$user" "$bucket" "$role" |
    write_key_pair "$user"
  echo "s3        $user: $role on s3://$bucket only"
  keep_secret_file
}

s3_rotate() {
  [ $# -eq 2 ] || usage
  local user=$1 shown keys
  check_user "$user"
  shown=$(s3_user_show "$user")
  [ -n "$shown" ] || die "no S3 user $user"
  keys=$(printf '%s' "$shown" | grep -o '"access_key":"[^"]*"' | cut -d'"' -f4)
  [ "$(printf '%s\n' "$keys" | grep -c .)" = 1 ] ||
    die "S3 user $user has $(printf '%s\n' "$keys" | grep -c .) keys, expected 1: rotate by hand (s3.accesskey.list -user $user)"
  new_secret_file "$2"
  printf 's3.accesskey.rotate -user %s -access_key %s\n' "$user" "$keys" | write_key_pair "$user"
  echo "s3        $user: new key, the old one stops working now; replace it wherever it is used"
  keep_secret_file
}

# Python run inside the mlflow container, where the admin password is in the environment.
# Errors print only their message: no request with the admin password in a traceback.
MLFLOW_ADMIN_CLIENT=$(cat <<'EOF'
import configparser, os, sys
cfg = configparser.ConfigParser()
cfg.read(os.environ["MLFLOW_AUTH_CONFIG_PATH"])
os.environ.update(
    MLFLOW_TRACKING_USERNAME=cfg["mlflow"]["admin_username"],
    MLFLOW_TRACKING_PASSWORD=os.environ["MLFLOW_AUTH_ADMIN_PASSWORD"],
    MLFLOW_HTTP_REQUEST_MAX_RETRIES="0",
    MLFLOW_HTTP_REQUEST_TIMEOUT="15",
)
from mlflow.server.auth.client import AuthServiceClient
client = AuthServiceClient("http://127.0.0.1:5000")

def fail(e):
    sys.exit(f"MLflow: {getattr(e, 'error_code', type(e).__name__)}: {getattr(e, 'message', e)}")
EOF
)

MLFLOW_CREATE_USER=$MLFLOW_ADMIN_CLIENT$(cat <<'EOF'

import secrets
user, password = sys.argv[1], secrets.token_urlsafe(24)
try:
    client.create_user(user, password)
except Exception as e:
    fail(e)
print(f"MLFLOW_TRACKING_USERNAME={user}")
print(f"MLFLOW_TRACKING_PASSWORD={password}")
EOF
)

MLFLOW_LIST_USERS=$MLFLOW_ADMIN_CLIENT$(cat <<'EOF'

from mlflow.server.auth.routes import LIST_USERS
try:
    users = client._request(LIST_USERS, "GET")["users"]
except Exception as e:
    fail(e)
for u in sorted(users, key=lambda u: u["username"]):
    print(f"  {u['username']}" + ("  (admin)" if u.get("is_admin") else ""))
EOF
)

mlflow_user() {
  [ $# -eq 2 ] || usage
  local user=$1
  check_user "$user"
  new_secret_file "$2"
  mlflow_python "$MLFLOW_CREATE_USER" "$user" >> "$out" || die "MLflow user $user was not created"
  echo "mlflow    user $user created, not an admin"
  keep_secret_file
}

list() {
  echo "S3 users (the admin key from .env is not listed):"
  printf 's3.user.list\n' | weed_shell
  echo
  echo "MLflow users:"
  mlflow_python "$MLFLOW_LIST_USERS"
}

case ${1:-} in
  s3-key) shift; s3_key "$@" ;;
  s3-rotate) shift; s3_rotate "$@" ;;
  mlflow-user) shift; mlflow_user "$@" ;;
  list) list ;;
  *) usage ;;
esac
