"""Smoke test for the stack's S3: upload a file, list the bucket, download it back and compare.

Connection settings come from the environment, not from this file: AWS_ENDPOINT_URL,
AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (see ../.env.example). Run from the repo root:

    uv run --env-file .env scripts/s3_check.py                  # 1 MiB round trip
    uv run --env-file .env scripts/s3_check.py --size-mb 1024   # throughput on 1 GiB
    uv run --env-file .env scripts/s3_check.py --keep           # leave the object in S3...
    uv run --env-file .env scripts/s3_check.py --verify         # ...and check it after `down` and `up`

Exit code 0 means every step passed, 1 means a step failed.
"""

import argparse
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

import boto3
from boto3.exceptions import Boto3Error
from botocore.exceptions import BotoCoreError, ClientError

KEY = "s3_check/probe.bin"
SHA256_META = "sha256"  # stored as x-amz-meta-sha256, so --verify knows the expected content
CHUNK = 8 * 1024 * 1024
MIB = 1024 * 1024
LIST_LIMIT = 20


class CheckFailed(Exception):
    pass


def write_random_file(path: Path, size: int) -> None:
    with path.open("wb") as f:
        left = size
        while left:
            n = min(CHUNK, left)
            f.write(os.urandom(n))
            left -= n


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def rate(size: int, seconds: float) -> str:
    return f"{size / MIB:.1f} MiB in {seconds:.2f} s ({size / MIB / seconds:.1f} MiB/s)"


def make_client():
    s3 = boto3.client("s3")
    endpoint = s3.meta.endpoint_url
    if "amazonaws.com" in endpoint:
        raise CheckFailed(f"endpoint is {endpoint}: AWS_ENDPOINT_URL is not set, refusing to talk to real AWS")
    print(f"endpoint  {endpoint}")
    return s3


def check_bucket(s3, bucket: str) -> None:
    # SeaweedFS creates a missing bucket on the first PutObject with the admin key,
    # so a typo in --bucket would silently "pass" without this check.
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchBucket", "NotFound"):
            raise CheckFailed(f"bucket {bucket} does not exist") from e
        raise
    print(f"bucket    {bucket} exists")


def upload(s3, bucket: str, size: int, tmp: Path) -> None:
    src = tmp / "upload.bin"
    write_random_file(src, size)
    digest = sha256_of(src)
    start = time.perf_counter()
    s3.upload_file(str(src), bucket, KEY, ExtraArgs={"Metadata": {SHA256_META: digest}})
    print(f"upload    s3://{bucket}/{KEY}: {rate(size, time.perf_counter() - start)}")


def list_bucket(s3, bucket: str) -> None:
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        keys += [(obj["Key"], obj["Size"]) for obj in page.get("Contents", [])]
    print(f"list      s3://{bucket}: {len(keys)} object(s)")
    for key, size in keys[:LIST_LIMIT]:
        print(f"          {key}  {size} B")
    if len(keys) > LIST_LIMIT:
        print(f"          ... and {len(keys) - LIST_LIMIT} more")
    if KEY not in (key for key, _ in keys):
        raise CheckFailed(f"{KEY} is not in the listing")


def download_and_compare(s3, bucket: str, tmp: Path) -> None:
    try:
        head = s3.head_object(Bucket=bucket, Key=KEY)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            raise CheckFailed(f"s3://{bucket}/{KEY} not found; run with --keep first") from e
        raise
    expected = head["Metadata"].get(SHA256_META)
    if not expected:
        raise CheckFailed(f"{KEY} has no {SHA256_META} metadata, cannot compare")

    dst = tmp / "download.bin"
    start = time.perf_counter()
    s3.download_file(bucket, KEY, str(dst))
    print(f"download  {rate(dst.stat().st_size, time.perf_counter() - start)}")

    if sha256_of(dst) != expected:
        raise CheckFailed("downloaded file differs from the uploaded one (sha256 mismatch)")
    print(f"compare   sha256 matches ({expected[:12]}…)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", default="demo-project")
    parser.add_argument("--size-mb", type=float, default=1, help="size of the test file in MiB (default: 1)")
    parser.add_argument("--keep", action="store_true", help="leave the object in S3 for a later --verify")
    parser.add_argument("--verify", action="store_true", help="skip upload, check the object left by --keep")
    args = parser.parse_args()

    try:
        s3 = make_client()
        check_bucket(s3, args.bucket)
        with tempfile.TemporaryDirectory(prefix="s3_check-") as tmp_dir:
            tmp = Path(tmp_dir)
            if not args.verify:
                upload(s3, args.bucket, int(args.size_mb * MIB), tmp)
            list_bucket(s3, args.bucket)
            download_and_compare(s3, args.bucket, tmp)
        if args.keep:
            print(f"keep      s3://{args.bucket}/{KEY} left for --verify")
        else:
            s3.delete_object(Bucket=args.bucket, Key=KEY)
            print(f"delete    s3://{args.bucket}/{KEY}")
    except CheckFailed as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    except ClientError as e:
        err = e.response["Error"]
        print(f"FAIL: S3 error {err.get('Code')}: {err.get('Message')}", file=sys.stderr)
        if err.get("Code") in ("403", "AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
            print("      check AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (shell variables win over --env-file)",
                  file=sys.stderr)
        return 1
    except (BotoCoreError, Boto3Error) as e:  # no connection, no credentials, failed transfer
        print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
