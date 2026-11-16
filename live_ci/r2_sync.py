"""Upload a folder to Cloudflare R2 (S3 protocol), multi-scale guide 10 part B.

usage: python live_ci/r2_sync.py <local dir> <key prefix>      e.g.  r2_sync.py live_beta_new/fog5 fog5/    or    r2_sync.py live_new backup/live/

Secrets come from the environment only (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET); without them the script logs one line and exits 0 (forks, local runs).
Never fails the build: every error is logged (without any secret) and the exit code is always 0. Hard deadline 60 s.

Rules (guide 10):
- one list call, then upload only files that are new or whose size / MD5 differ from the object (ETag = MD5 for single-part uploads, so we always use put_object and never multipart);
- Cache-Control immutable for content-named files, max-age=60 for the short-lived names (index.json, status.json);
- index files are uploaded LAST, and only when everything else went up, so an index never points at a missing file;
- objects under the prefix that are no longer in the folder are deleted, but only when older than 2 hours (a browser may still be reading them);
- operation budget: every list, put and delete call is a write-class operation; at most MAX_OPS (60) per run, logged.
"""
import hashlib
import mimetypes
import os
import sys
import time
from pathlib import Path

DEADLINE_S = 60          # hard limit for the whole sync
MAX_OPS = 60             # free tier: 1 M write-class ops a month / 144 runs a day = about 230 per run; stay far below
KEEP_S = 2 * 3600        # do not delete objects younger than this
SHORT_NAMES = ("index.json", "status.json")     # max-age=60; everything else is content-named and immutable
IMMUTABLE = "public, max-age=31536000, immutable"
SHORT = "public, max-age=60"


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_client(env=os.environ):
    """boto3 S3 client for R2, or None when the secrets or boto3 are missing."""
    need = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    if not all(env.get(k) for k in need):
        return None
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        print("r2_sync: boto3 is not installed: skipped")
        return None
    return boto3.client("s3", endpoint_url=f"https://{env['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com", aws_access_key_id=env["R2_ACCESS_KEY_ID"],
                        aws_secret_access_key=env["R2_SECRET_ACCESS_KEY"], region_name="auto", config=Config(retries={"max_attempts": 2}, connect_timeout=10, read_timeout=20))


def sync(local_dir, prefix, client, bucket, deadline_s=DEADLINE_S, max_ops=MAX_OPS, now=time.time, log=print):
    """Returns a dict of counts. Raises nothing the caller must handle except what the client raises; run() wraps it."""
    t0 = now()
    ops = {"n": 0}
    stats = {"uploaded": 0, "unchanged": 0, "deleted": 0, "skipped": 0, "ops": 0, "bytes": 0, "index_held_back": False}
    local = Path(local_dir)
    files = sorted(p for p in local.rglob("*") if p.is_file())
    keys = {prefix + p.relative_to(local).as_posix(): p for p in files}

    def out_of_time():
        return now() - t0 > deadline_s

    # one list (paginated: each page is one operation)
    remote = {}
    token = None
    while True:
        ops["n"] += 1
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        r = client.list_objects_v2(**kw)
        for o in r.get("Contents", []):
            remote[o["Key"]] = o
        if not r.get("IsTruncated"):
            break
        token = r.get("NextContinuationToken")
        if out_of_time() or ops["n"] >= max_ops:
            break

    def upload(key, p):
        body = p.read_bytes()
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        short = p.name in SHORT_NAMES
        client.put_object(Bucket=bucket, Key=key, Body=body, ContentType=ctype, CacheControl=SHORT if short else IMMUTABLE)
        ops["n"] += 1
        stats["uploaded"] += 1
        stats["bytes"] += len(body)

    order = sorted(keys, key=lambda k: (keys[k].name in SHORT_NAMES, k))   # data first, index files last
    held = False
    for k in order:
        p = keys[k]
        is_index = p.name in SHORT_NAMES
        o = remote.get(k)
        if o is not None and o.get("Size") == p.stat().st_size and str(o.get("ETag", "")).strip('"') == _md5(p):
            stats["unchanged"] += 1
            continue
        if is_index and held:
            stats["skipped"] += 1
            stats["index_held_back"] = True
            continue
        if out_of_time() or ops["n"] >= max_ops - 1:   # keep one operation for the delete call
            stats["skipped"] += 1
            held = True
            continue
        try:
            upload(k, p)
        except Exception as e:   # one bad file must not stop the rest, but its index must not go up
            log(f"r2_sync: upload of {k} failed: {type(e).__name__}")
            stats["skipped"] += 1
            held = True

    stale = [k for k, o in remote.items() if k not in keys and (t0 - o["LastModified"].timestamp()) > KEEP_S] if not held else []
    stale = stale[:1000]
    if stale and not out_of_time() and ops["n"] < max_ops:
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in stale], "Quiet": True})
        ops["n"] += 1
        stats["deleted"] = len(stale)
    stats["ops"] = ops["n"]
    log(f"r2_sync {prefix}: {stats['uploaded']} uploaded ({stats['bytes']} bytes), {stats['unchanged']} unchanged, {stats['deleted']} deleted, {stats['skipped']} skipped, "
        f"{stats['ops']} write-class operations in {now() - t0:.1f} s" + (", index held back" if stats["index_held_back"] else ""))
    return stats


def run(argv, env=os.environ, client=None):
    """Always returns 0."""
    try:
        if len(argv) < 2:
            print(__doc__)
            return 0
        local_dir, prefix = argv[0], argv[1]
        if prefix and not prefix.endswith("/"):
            prefix += "/"
        if not Path(local_dir).is_dir():
            print(f"r2_sync: {local_dir} is not a folder: nothing to do")
            return 0
        c = client or make_client(env)
        if c is None:
            print("r2_sync: R2 secrets absent (or boto3 missing): skipped")
            return 0
        sync(local_dir, prefix, c, env.get("R2_BUCKET", ""))
    except Exception as e:
        print(f"r2_sync: failed ({type(e).__name__}); ignored")   # exception text could echo a request: log the type only
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
