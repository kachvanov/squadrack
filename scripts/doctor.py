"""Check the whole stack in one run: S3, MLflow tracking, artifact proxy and model registry.

Every check writes a small probe, reads it back and removes it. The first failed check stops
the run with a message on what to fix; whatever was created up to that point is removed anyway.

Connection settings come from the environment, not from this file (see ../.env.example):
    S3      AWS_ENDPOINT_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    MLflow  MLFLOW_TRACKING_URI, MLFLOW_TRACKING_USERNAME, MLFLOW_TRACKING_PASSWORD

Run from the repo root:

    uv run --env-file .env scripts/doctor.py               # S3 and MLflow
    uv run --env-file .env scripts/doctor.py --only mlflow # an MLflow client without an S3 key
    uv run --env-file .env scripts/doctor.py --project-key # a key from scripts/access.sh s3-key

With --project-key the S3 key must reach the project bucket (--bucket) and nothing else: writing
to the MLflow artifact bucket, creating a bucket and writing to a missing one must be denied.

What stays behind: the experiment `squadrack-doctor/<user>`, reused by every run, and the
probe runs inside it, marked deleted: hidden in the UI, a few rows in Postgres, no files.
`mlflow gc` on the server removes them for good, but without --experiment-ids it also purges
every deleted experiment. Nothing stays in S3 or in the model registry. A probe left by
a killed run is removed on the next start.

Exit code 0 means every check passed, 1 means a check or the cleanup failed.
"""

import os

# Fail fast instead of retrying for minutes: by default the MLflow client retries
# a failed request 7 times with backoff and waits up to 120 s for an answer.
os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "0")
os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "15")
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
# The client otherwise fetches its telemetry config from mlflow-telemetry.io on every start
os.environ.setdefault("MLFLOW_DISABLE_TELEMETRY", "true")

import argparse  # noqa: E402
import base64  # noqa: E402
import hashlib  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402

import boto3  # noqa: E402
from boto3.exceptions import Boto3Error  # noqa: E402
from botocore.config import Config  # noqa: E402
from botocore.exceptions import BotoCoreError, ClientError  # noqa: E402
from mlflow import MlflowClient  # noqa: E402
from mlflow.entities import Metric, Param, RunTag, ViewType  # noqa: E402
from mlflow.exceptions import MlflowException  # noqa: E402
from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository  # noqa: E402

ARTIFACT_BUCKET = "mlflow"  # --artifacts-destination of the MLflow server in stack/compose.yaml
PROXY_SCHEME = "mlflow-artifacts:/"
S3_PROBE_PREFIX = "doctor/"
PROBE_SIZE = 64 * 1024
S3_ENV = ("AWS_ENDPOINT_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
MLFLOW_ENV = ("MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_USERNAME", "MLFLOW_TRACKING_PASSWORD")
# One connection attempt with short timeouts: a stopped stack fails in seconds, not minutes
S3_CONFIG = Config(connect_timeout=3, read_timeout=10, retries={"total_max_attempts": 1})
HTTP_TIMEOUT = 5


class CheckFailed(Exception):
    pass


class Report:
    """Prints one line per step with the time it took, like `s3_check.py`."""

    def __init__(self) -> None:
        self.start = self.last = time.perf_counter()

    def step(self, name: str, message: str) -> None:
        now = time.perf_counter()
        print(f"{name:<9} {message}  [{(now - self.last) * 1000:.0f} ms]", flush=True)
        self.last = now

    def total(self) -> float:
        return time.perf_counter() - self.start


class Cleanup:
    """Undo steps, run in reverse order. A failed undo is reported and does not hide the others."""

    def __init__(self, report: Report) -> None:
        self.report = report
        self.steps: list[tuple[str, Callable[[], None]]] = []

    def add(self, what: str, undo: Callable[[], None]) -> None:
        self.steps.append((what, undo))

    def run(self) -> bool:
        ok = True
        while self.steps:
            what, undo = self.steps.pop()
            try:
                undo()
                self.report.step("cleanup", what)
            except Exception as e:  # report every leftover, keep cleaning up
                print(f"cleanup   FAILED {what}: {e}", file=sys.stderr)
                ok = False
        return ok


def require_env(names: tuple[str, ...]) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise CheckFailed(f"not set: {', '.join(missing)}. Run with `uv run --env-file .env` "
                          "from the repo root, see .env.example")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- S3 -----------------------------------------------------------------------------------------

def make_s3(report: Report):
    s3 = boto3.client("s3", config=S3_CONFIG)
    endpoint = s3.meta.endpoint_url
    if "amazonaws.com" in endpoint:
        raise CheckFailed(f"S3 endpoint is {endpoint}: refusing to talk to real AWS")
    try:
        s3.list_buckets()
    except (BotoCoreError, Boto3Error) as e:
        raise CheckFailed(f"S3 is not reachable at {endpoint}: {e}. Is the stack up? "
                          "(docker compose -f stack/compose.yaml ps)") from e
    report.step("s3", f"{endpoint} answers, key accepted")
    return s3


def check_bucket(s3, bucket: str, report: Report) -> None:
    # SeaweedFS creates a missing bucket on the first write with the admin key,
    # so without this check a wrong bucket name would pass
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket", "NotFound"):
            raise CheckFailed(f"bucket {bucket} does not exist (-bucket of the seaweedfs service in "
                              "stack/compose.yaml, or scripts/access.sh s3-key)") from e
        if code in ("403", "AccessDenied"):
            hint = ("a project key? Run with --project-key" if bucket == ARTIFACT_BUCKET
                    else "the key of another bucket? Set --bucket to the bucket of the key")
            raise CheckFailed(f"the S3 key has no access to bucket {bucket}: {hint}") from e
        raise
    report.step("s3", f"bucket {bucket} exists")


def check_s3_roundtrip(s3, bucket: str, cleanup: Cleanup, report: Report) -> None:
    for left in s3_keys(s3, bucket, S3_PROBE_PREFIX):  # left by a killed run
        s3.delete_object(Bucket=bucket, Key=left)
        report.step("cleanup", f"leftover s3://{bucket}/{left} of an earlier doctor deleted")
    key = f"{S3_PROBE_PREFIX}{os.urandom(8).hex()}.bin"
    data = os.urandom(PROBE_SIZE)
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    cleanup.add(f"s3://{bucket}/{key} deleted", lambda: s3.delete_object(Bucket=bucket, Key=key))
    got = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if sha256(got) != sha256(data):
        raise CheckFailed(f"s3://{bucket}/{key}: downloaded bytes differ from the uploaded ones")
    report.step("s3", f"put and get s3://{bucket}/{key}: {PROBE_SIZE // 1024} KiB, sha256 matches")


def expect_denied(what: str, call: Callable[[], object], undo: Callable[[], None],
                  cleanup: Cleanup) -> None:
    try:
        call()
    except ClientError as e:
        if e.response["Error"]["Code"] in ("AccessDenied", "403"):
            return
        raise
    cleanup.add(f"{what}: undone", undo)
    raise CheckFailed(f"the project key can {what}: it is not limited to its bucket. "
                      "Use a key from scripts/access.sh s3-key, not the admin key")


def check_key_limits(s3, bucket: str, cleanup: Cleanup, report: Report) -> None:
    """A project key from scripts/access.sh reaches its own bucket only."""
    key = f"{S3_PROBE_PREFIX}{os.urandom(8).hex()}.bin"
    expect_denied(f"write to s3://{ARTIFACT_BUCKET}",
                  lambda: s3.put_object(Bucket=ARTIFACT_BUCKET, Key=key, Body=b"probe"),
                  lambda: s3.delete_object(Bucket=ARTIFACT_BUCKET, Key=key), cleanup)
    new_bucket = f"squadrack-doctor-{os.urandom(4).hex()}"
    expect_denied(f"create bucket {new_bucket}",
                  lambda: s3.create_bucket(Bucket=new_bucket),
                  lambda: s3.delete_bucket(Bucket=new_bucket), cleanup)

    # With the admin key SeaweedFS creates a missing bucket on the first write
    def undo_write() -> None:
        s3.delete_object(Bucket=new_bucket, Key=key)
        s3.delete_bucket(Bucket=new_bucket)
    expect_denied(f"write to the missing bucket {new_bucket}",
                  lambda: s3.put_object(Bucket=new_bucket, Key=key, Body=b"probe"), undo_write, cleanup)
    visible = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    report.step("s3", f"key limited to {bucket}: writing to {ARTIFACT_BUCKET}, creating a bucket "
                      f"and writing to a missing one denied; sees {', '.join(visible) or 'no buckets'}")


def s3_keys(s3, bucket: str, prefix: str) -> list[str]:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return [obj["Key"] for obj in resp.get("Contents", [])]


# --- MLflow -------------------------------------------------------------------------------------

def http_get(url: str, auth: tuple[str, str] | None = None) -> int:
    request = urllib.request.Request(url)
    if auth:
        token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        request.add_header("Authorization", f"Basic {token}")
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def check_mlflow_server(report: Report) -> str:
    uri = os.environ["MLFLOW_TRACKING_URI"].rstrip("/")
    user = os.environ["MLFLOW_TRACKING_USERNAME"]
    try:
        status = http_get(f"{uri}/health")
    except (urllib.error.URLError, TimeoutError) as e:
        reason = getattr(e, "reason", e)
        raise CheckFailed(f"MLflow is not reachable at {uri}: {reason}. Is the stack up? "
                          "(docker compose -f stack/compose.yaml ps)") from e
    if status != 200:
        raise CheckFailed(f"{uri}/health answered {status}, expected 200")
    report.step("mlflow", f"{uri}/health answers")

    status = http_get(f"{uri}/api/2.0/mlflow/experiments/search?max_results=1",
                      auth=(user, os.environ["MLFLOW_TRACKING_PASSWORD"]))
    if status == 401:
        raise CheckFailed(f"MLflow rejected the login of {user}: check MLFLOW_TRACKING_USERNAME "
                          "and MLFLOW_TRACKING_PASSWORD (shell variables win over --env-file)")
    if status != 200:
        raise CheckFailed(f"MLflow API answered {status} for {user}, expected 200")
    report.step("mlflow", f"login as {user} accepted")
    return user


def remove_run(client: MlflowClient, run) -> None:
    # Artifacts first, through the proxy: deleting the run leaves its files in S3
    if run.info.artifact_uri.startswith(PROXY_SCHEME):
        get_artifact_repository(run.info.artifact_uri).delete_artifacts()
    client.delete_run(run.info.run_id)


def remove_leftovers(client: MlflowClient, experiment_id: str, model_name: str,
                     report: Report) -> None:
    """A run killed before its cleanup leaves an active probe run and the probe model."""
    for run in client.search_runs([experiment_id], run_view_type=ViewType.ACTIVE_ONLY):
        remove_run(client, run)
        report.step("cleanup", f"leftover run {run.info.run_id} of an earlier doctor deleted")
    try:
        client.delete_registered_model(model_name)
        report.step("cleanup", f"leftover model {model_name} of an earlier doctor deleted")
    except MlflowException as e:
        if e.error_code != "RESOURCE_DOES_NOT_EXIST":
            raise


def check_tracking(client: MlflowClient, experiment_name: str, model_name: str,
                   cleanup: Cleanup, report: Report):
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        experiment_id = client.create_experiment(experiment_name)
        report.step("mlflow", f"experiment {experiment_name} created (id {experiment_id})")
    else:
        experiment_id = experiment.experiment_id
        if experiment.lifecycle_stage == "deleted":
            client.restore_experiment(experiment_id)
        report.step("mlflow", f"experiment {experiment_name} (id {experiment_id})")
    remove_leftovers(client, experiment_id, model_name, report)

    run = client.create_run(experiment_id, run_name="doctor")
    run_id = run.info.run_id
    cleanup.add(f"run {run_id} deleted with its artifacts", lambda: remove_run(client, run))

    token = os.urandom(8).hex()
    now = int(time.time() * 1000)
    # One request for everything: each request with basic auth costs about 110 ms
    client.log_batch(
        run_id,
        metrics=[Metric("probe", 1.0, now, step) for step in range(3)],
        params=[Param("token", token)],
        tags=[RunTag("squadrack.doctor", "true")],
    )
    data = client.get_run(run_id).data
    if data.params.get("token") != token or data.metrics.get("probe") != 1.0:
        raise CheckFailed(f"run {run_id}: params or metrics read back differ from the logged ones")
    history = client.get_metric_history(run_id, "probe")
    if sorted(m.step for m in history) != [0, 1, 2]:
        raise CheckFailed(f"run {run_id}: metric history has steps {[m.step for m in history]}")
    report.step("mlflow", f"run {run_id}: param, tag and a metric on 3 steps read back")
    return run


def check_artifacts(run, s3, report: Report) -> None:
    artifact_uri = run.info.artifact_uri
    if not artifact_uri.startswith(PROXY_SCHEME):
        raise CheckFailed(f"artifact_uri of the run is {artifact_uri}, expected {PROXY_SCHEME}...: "
                          "the server does not proxy artifacts (--serve-artifacts) or the "
                          "experiment was created before it did")
    # The repository of the run's artifact_uri talks to the proxy directly. MlflowClient's
    # artifact calls go the same way but look the run up again on each call: 2 to 3 more requests.
    repo = get_artifact_repository(artifact_uri)
    data = os.urandom(PROBE_SIZE)
    with tempfile.TemporaryDirectory(prefix="doctor-") as tmp_dir:
        src = Path(tmp_dir) / "probe.bin"
        src.write_bytes(data)
        repo.log_artifact(str(src), artifact_path="doctor")
        listed = [f.path for f in repo.list_artifacts("doctor")]
        if "doctor/probe.bin" not in listed:
            raise CheckFailed(f"uploaded artifact is not listed, got {listed}")
        dst = repo.download_artifacts("doctor/probe.bin", dst_path=tmp_dir)
        if sha256(Path(dst).read_bytes()) != sha256(data):
            raise CheckFailed("downloaded artifact differs from the uploaded one (sha256 mismatch)")
    report.step("mlflow", "artifact through the proxy: upload, list, download, sha256 matches")

    if s3 is not None:
        key = f"{artifact_uri.removeprefix(PROXY_SCHEME).strip('/')}/doctor/probe.bin"
        if key not in s3_keys(s3, ARTIFACT_BUCKET, key):
            raise CheckFailed(f"artifact is not in s3://{ARTIFACT_BUCKET}/{key}: the server "
                              "writes artifacts somewhere else (--artifacts-destination)")
        report.step("s3", f"artifact is in s3://{ARTIFACT_BUCKET}/{key}")


def check_registry(client: MlflowClient, run, model_name: str, cleanup: Cleanup,
                   report: Report) -> None:
    client.create_registered_model(model_name, tags={"squadrack.doctor": "true"})
    cleanup.add(f"model {model_name} deleted with its versions",
                lambda: client.delete_registered_model(model_name))
    version = client.create_model_version(model_name, f"runs:/{run.info.run_id}/doctor",
                                          run_id=run.info.run_id, await_creation_for=0)
    client.set_registered_model_alias(model_name, "doctor", version.version)
    resolved = client.get_model_version_by_alias(model_name, "doctor")
    if resolved.version != version.version or resolved.run_id != run.info.run_id:
        raise CheckFailed(f"alias resolves to version {resolved.version} of run {resolved.run_id}, "
                          f"expected {version.version} of run {run.info.run_id}")
    report.step("registry", f"model {model_name}: version {version.version}, "
                            "alias @doctor resolves to it")


def check_mlflow(s3, cleanup: Cleanup, report: Report):
    user = check_mlflow_server(report)
    client = MlflowClient()
    experiment_name, model_name = f"squadrack-doctor/{user}", f"squadrack-doctor-{user}"
    run = check_tracking(client, experiment_name, model_name, cleanup, report)
    check_artifacts(run, s3, report)
    check_registry(client, run, model_name, cleanup, report)
    return run


def check_nothing_left(s3, run, report: Report) -> None:
    prefix = run.info.artifact_uri.removeprefix(PROXY_SCHEME).strip("/") + "/"
    left = s3_keys(s3, ARTIFACT_BUCKET, prefix)
    if left:
        raise CheckFailed(f"run deleted, but its files stay in s3://{ARTIFACT_BUCKET}/{prefix}: {left}")
    report.step("s3", f"s3://{ARTIFACT_BUCKET}/{prefix} is empty after the cleanup")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=["s3", "mlflow"], help="check one service only")
    parser.add_argument("--bucket", default="demo-project", help="project bucket to check (default: demo-project)")
    parser.add_argument("--project-key", action="store_true",
                        help="the S3 key is a project key: check that it reaches --bucket only")
    args = parser.parse_args()
    use_s3, use_mlflow = args.only in (None, "s3"), args.only in (None, "mlflow")
    if args.project_key and not use_s3:
        parser.error("--project-key checks the S3 key: it does not go with --only mlflow")

    report = Report()
    cleanup = Cleanup(report)
    ok = False
    try:
        require_env((S3_ENV if use_s3 else ()) + (MLFLOW_ENV if use_mlflow else ()))
        s3 = None
        if use_s3:
            s3 = make_s3(report)
            if not args.project_key:
                check_bucket(s3, ARTIFACT_BUCKET, report)
            check_bucket(s3, args.bucket, report)
            check_s3_roundtrip(s3, args.bucket, cleanup, report)
            if args.project_key:
                check_key_limits(s3, args.bucket, cleanup, report)
        # A project key cannot read the artifact bucket: artifacts are checked through MLflow only
        artifact_s3 = None if args.project_key else s3
        run = check_mlflow(artifact_s3, cleanup, report) if use_mlflow else None
        # Clean up now rather than at exit, to see that nothing is left
        if not cleanup.run():
            raise CheckFailed("cleanup failed, see above")
        if artifact_s3 is not None and run is not None:
            check_nothing_left(artifact_s3, run, report)
        ok = True
    except CheckFailed as e:
        print(f"FAIL: {e}", file=sys.stderr)
    except ClientError as e:
        err = e.response["Error"]
        print(f"FAIL: S3 error {err.get('Code')}: {err.get('Message')}", file=sys.stderr)
        if err.get("Code") in ("403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            print("      check AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY "
                  "(shell variables win over --env-file)", file=sys.stderr)
    except (BotoCoreError, Boto3Error) as e:
        print(f"FAIL: S3: {e}", file=sys.stderr)
    except MlflowException as e:
        print(f"FAIL: MLflow: {e}", file=sys.stderr)
    except KeyboardInterrupt:
        print("interrupted, cleaning up", file=sys.stderr)
    finally:
        ok = cleanup.run() and ok

    print(f"{'OK' if ok else 'FAILED'} in {report.total():.2f} s")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
