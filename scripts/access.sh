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
#   scripts/access.sh project-key [--rotate] <project>
#       The S3 key of the services of <project>: user <project>-svc on bucket <project>, written to
#       $SQUADRACK_PROJECTS_DIR/<project>/secrets/s3-credentials in the format of ~/.aws/credentials.
#       Mode 0640 plus a read ACL for the host user <project>-svc, which the services run as.
#       --rotate replaces the key in that file; the old key stops working at once.
#   scripts/access.sh project-check <project>
#       A container running as <project>-svc reads that file and uses the key, as a service of the
#       project would: its own bucket works, the MLflow artifact bucket and new buckets do not.
#   scripts/access.sh list
#       S3 users with their policies and MLflow users, no secrets.
#
# The stack is reached with `docker compose exec`. By default the compose file of this clone;
# in a deployment, through the deploy repo's up.sh, which sets SQUADRACK_COMPOSE and
# SQUADRACK_PROJECTS_DIR from deploy.env:
#   deploy/up.sh access list
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
usage() { sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2; }

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
# to the secret file, every other line to the terminal. Format env: lines for an env file;
# ini: a profile of ~/.aws/credentials, read by boto3 and DVC from AWS_SHARED_CREDENTIALS_FILE.
write_key_pair() {
  local format=${2:-env}
  weed_shell | awk -v out="$out" -v fmt="$format" '
    BEGIN { if (fmt == "ini") print "[default]" > out }
    /^Access Key: / { print (fmt == "ini" ? "aws_access_key_id = " : "AWS_ACCESS_KEY_ID=") $3 > out; next }
    /^Secret Key: / { print (fmt == "ini" ? "aws_secret_access_key = " : "AWS_SECRET_ACCESS_KEY=") $3 > out; next }
    /^Save these credentials/ { next }
    NF { print "s3        " $0 }'
  [ "$(grep -ciE '^aws_[a-z_]+ ?= ?.' "$out")" = 2 ] ||
    die "no key pair in the output of weed shell; check the user with: s3.user.show -name $1"
}

ensure_bucket() {
  local buckets
  buckets=$(printf 's3.bucket.list\n' | weed_shell) || die "cannot list buckets: $buckets"
  if ! printf '%s\n' "$buckets" | awk -v b="$1" '$1 == b { found = 1 } END { exit !found }'; then
    printf 's3.bucket.create -name %s\n' "$1" | weed_shell | grep -q "created bucket $1" ||
      die "could not create bucket $1"
    echo "s3        bucket $1 created"
  fi
}

only_access_key() {
  local keys
  keys=$(s3_user_show "$1" | grep -o '"access_key":"[^"]*"' | cut -d'"' -f4)
  [ "$(printf '%s\n' "$keys" | grep -c .)" = 1 ] ||
    die "S3 user $1 has $(printf '%s\n' "$keys" | grep -c .) keys, expected 1: rotate by hand (s3.accesskey.list -user $1)"
  echo "$keys"
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
  local user=$1 bucket=$2 shown
  check_user "$user"
  check_bucket "$bucket"

  shown=$(s3_user_show "$user")
  [ -z "$shown" ] || die "S3 user $user exists. A new key for it: scripts/access.sh s3-rotate $user <out-file>"

  ensure_bucket "$bucket"
  new_secret_file "$3"
  printf 's3.user.provision -name %s -bucket %s -role %s\n' "$user" "$bucket" "$role" |
    write_key_pair "$user"
  echo "s3        $user: $role on s3://$bucket only"
  keep_secret_file
}

s3_rotate() {
  [ $# -eq 2 ] || usage
  local user=$1 key shown
  check_user "$user"
  shown=$(s3_user_show "$user")
  [ -n "$shown" ] || die "no S3 user $user"
  key=$(only_access_key "$user")
  new_secret_file "$2"
  printf 's3.accesskey.rotate -user %s -access_key %s\n' "$user" "$key" | write_key_pair "$user"
  echo "s3        $user: new key, the old one stops working now; replace it wherever it is used"
  keep_secret_file
}

# --- Project keys in SQUADRACK_PROJECTS_DIR ---------------------------------------------------

project_secrets_dir() {
  [ -n "${SQUADRACK_PROJECTS_DIR:-}" ] ||
    die "set SQUADRACK_PROJECTS_DIR: the directory with <project>/secrets/ (in a deployment: in deploy.env, and run deploy/up.sh access ...)"
  [ -d "$SQUADRACK_PROJECTS_DIR" ] ||
    die "no directory $SQUADRACK_PROJECTS_DIR: an admin creates it once, readable by admins only"
  echo "$SQUADRACK_PROJECTS_DIR/$1/secrets"
}

# The file is created empty, made readable by the project user, and only then gets the key.
# chown to the project user would need root; a named ACL entry does not: the owner may set it.
open_project_secret() {
  local file=$1 user=$2
  new_secret_file "$file"
  chmod 640 "$file"
  reader="group $(ls -ld "$file" | awk '{print $4}')"
  if ! id -u "$user" > /dev/null 2>&1; then
    echo "warning   no user $user on this host: only you and the group of $file can read it" >&2
  elif ! command -v setfacl > /dev/null; then
    echo "warning   no setfacl: $user cannot read $file; install acl and run: setfacl -m u:$user:r $file" >&2
  else
    # The mask is given explicitly: otherwise setfacl recalculates it from all group entries, and a
    # default ACL of the projects directory (g:<admins>:rwX) would make the file group-writable again
    setfacl -m "u:$user:r,m::r" "$file"
    reader="$reader and $user (ACL)"
  fi
}

reader=
keep_project_secret() {
  trap - EXIT
  echo "secrets   written to $out (mode 640: you, $reader); mount it with compose secrets:, see README"
}

project_key() {
  local rotate=
  if [ "${1:-}" = "--rotate" ]; then rotate=1; shift; fi
  [ $# -eq 1 ] || usage
  local project=$1 user=$1-svc dir file shown
  check_bucket "$project"
  check_user "$user"
  dir=$(project_secrets_dir "$project")
  file=$dir/s3-credentials
  shown=$(s3_user_show "$user")

  if [ -z "$rotate" ]; then
    [ ! -e "$file" ] || die "$file exists: a new key with scripts/access.sh project-key --rotate $project"
    [ -z "$shown" ] ||
      die "S3 user $user exists, but $file does not: issue its key elsewhere with s3-rotate, or remove the user"
    ( umask 027; mkdir -p "$dir" )
    ensure_bucket "$project"
    open_project_secret "$file" "$user"
    printf 's3.user.provision -name %s -bucket %s -role readwrite\n' "$user" "$project" |
      write_key_pair "$user" ini
    echo "s3        $user: readwrite on s3://$project only"
  else
    [ -f "$file" ] || die "no $file to rotate: create it with scripts/access.sh project-key $project"
    [ -n "$shown" ] || die "no S3 user $user"
    local key
    key=$(only_access_key "$user")
    # The new key goes to a new file next to the old one, which is replaced only when it is complete
    open_project_secret "$file.new" "$user"
    printf 's3.accesskey.rotate -user %s -access_key %s\n' "$user" "$key" | write_key_pair "$user" ini
    mv -f "$file.new" "$file"
    out=$file
    echo "s3        $user: new key, the old one stops working now; restart the services of $project"
  fi
  keep_project_secret
}

PROJECT_CHECK=$(cat <<'EOF'
import os, sys, uuid
# The image of the mlflow service carries the admin key in its environment: drop it, so that
# boto3 takes the key from the mounted file and nothing else
for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
    os.environ.pop(name, None)
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

bucket, artifacts, path = sys.argv[1], sys.argv[2], os.environ["AWS_SHARED_CREDENTIALS_FILE"]
try:
    open(path).close()
except OSError as e:
    sys.exit(f"FAIL: uid {os.getuid()} cannot read the key file: {e.strerror}")
print(f"check     uid {os.getuid()} reads the key file")

s3 = boto3.client("s3", endpoint_url="http://s3:8333",
                  config=Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 1}))
key, body = f"squadrack-check/{uuid.uuid4().hex}", os.urandom(1024)
try:
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    try:
        if s3.get_object(Bucket=bucket, Key=key)["Body"].read() != body:
            sys.exit(f"FAIL: s3://{bucket}/{key} reads back different bytes")
    finally:
        s3.delete_object(Bucket=bucket, Key=key)
except (ClientError, BotoCoreError) as e:
    sys.exit(f"FAIL: own bucket s3://{bucket}: {e}")
print(f"check     put, get and delete in s3://{bucket}")

new_bucket = f"squadrack-check-{uuid.uuid4().hex[:8]}"
for what, call in [(f"write to s3://{artifacts}", lambda: s3.put_object(Bucket=artifacts, Key=key, Body=b"x")),
                   (f"create bucket {new_bucket}", lambda: s3.create_bucket(Bucket=new_bucket))]:
    try:
        call()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("AccessDenied", "403"):
            continue
        raise
    sys.exit(f"FAIL: the key can {what}: it is not limited to s3://{bucket}")
print(f"check     write to s3://{artifacts} and creating a bucket denied")
print("OK")
EOF
)

project_check() {
  [ $# -eq 1 ] || usage
  local project=$1 user=$1-svc file uid gid
  check_bucket "$project"
  file=$(project_secrets_dir "$project")/s3-credentials
  [ -f "$file" ] || die "no $file: create it with scripts/access.sh project-key $project"
  uid=$(id -u "$user" 2> /dev/null) && gid=$(id -g "$user") ||
    die "no user $user on this host: the services of $project run as it"
  # A one-off container of the mlflow image (it has boto3) in the stack network, like a project
  # service: the key file mounted read-only, the process running as the project user
  "${compose[@]}" run --rm --no-deps -T --user "$uid:$gid" \
    --volume "$(cd "$(dirname "$file")" && pwd)/s3-credentials:/run/secrets/s3-credentials:ro" \
    --env AWS_SHARED_CREDENTIALS_FILE=/run/secrets/s3-credentials \
    --entrypoint python mlflow -c "$PROJECT_CHECK" "$project" "$ARTIFACT_BUCKET"
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
  project-key) shift; project_key "$@" ;;
  project-check) shift; project_check "$@" ;;
  mlflow-user) shift; mlflow_user "$@" ;;
  list) list ;;
  *) usage ;;
esac
