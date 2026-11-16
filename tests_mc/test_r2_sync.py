"""live_ci/r2_sync.py against a fake S3 client: changed / unchanged / stale / deadline / budget / no secrets in logs (multi-scale guide 10, part B)."""
import datetime as dt
import hashlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "live_ci"))
import r2_sync  # noqa: E402

NOW = 1.8e9


class Fake:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})   # key -> {"Body", "LastModified", "Meta"}
        self.calls = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        self.calls.append("list")
        c = [{"Key": k, "Size": len(v["Body"]), "ETag": '"%s"' % hashlib.md5(v["Body"]).hexdigest(), "LastModified": v["LastModified"]} for k, v in self.objects.items() if k.startswith(Prefix)]
        return {"Contents": c, "IsTruncated": False}

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.calls.append("put")
        self.objects[Key] = {"Body": Body, "LastModified": dt.datetime.fromtimestamp(NOW, dt.timezone.utc), "Meta": {"cc": CacheControl, "ct": ContentType}}

    def delete_objects(self, Bucket, Delete):
        self.calls.append("delete")
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)


def old(h):
    return dt.datetime.fromtimestamp(NOW - h * 3600, dt.timezone.utc)


class R2Sync(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        (self.d / "a.png").write_bytes(b"AAA")
        (self.d / "b.png").write_bytes(b"BBBB")
        (self.d / "index.json").write_text('{"frames":["a.png","b.png"]}')
        self.logs = []

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, c, **kw):
        return r2_sync.sync(self.d, "fog5/", c, "bkt", now=lambda: NOW, log=self.logs.append, **kw)

    def test_new_files_upload_with_cache_headers_index_last(self):
        c = Fake()
        s = self.go(c)
        self.assertEqual(s["uploaded"], 3)
        self.assertEqual(c.calls, ["list", "put", "put", "put"])
        self.assertEqual(c.objects["fog5/a.png"]["Meta"]["cc"], r2_sync.IMMUTABLE)
        self.assertEqual(c.objects["fog5/index.json"]["Meta"]["cc"], r2_sync.SHORT)
        self.assertEqual(list(c.objects)[-1], "fog5/index.json")

    def test_unchanged_not_reuploaded_changed_is(self):
        c = Fake()
        self.go(c)
        c.calls.clear()
        s = self.go(c)
        self.assertEqual((s["uploaded"], s["unchanged"]), (0, 3))
        self.assertEqual(c.calls, ["list"])
        (self.d / "index.json").write_text('{"frames":["b.png"]}')
        s = self.go(c)
        self.assertEqual((s["uploaded"], s["unchanged"]), (1, 2))

    def test_stale_deleted_only_when_older_than_two_hours(self):
        c = Fake({"fog5/gone_old.png": {"Body": b"x", "LastModified": old(3)}, "fog5/gone_new.png": {"Body": b"y", "LastModified": old(1)},
                  "other/keep.png": {"Body": b"z", "LastModified": old(9)}})
        s = self.go(c)
        self.assertEqual(s["deleted"], 1)
        self.assertNotIn("fog5/gone_old.png", c.objects)
        self.assertIn("fog5/gone_new.png", c.objects)
        self.assertIn("other/keep.png", c.objects)   # outside the prefix: untouched

    def test_deadline_holds_back_index(self):
        c = Fake()
        clock = iter([NOW, NOW + 100] + [NOW + 100] * 50)   # first call (t0) then every later check is past the deadline
        s = r2_sync.sync(self.d, "fog5/", c, "bkt", now=lambda: next(clock), log=self.logs.append)
        self.assertEqual(s["uploaded"], 0)
        self.assertTrue(s["index_held_back"] or s["skipped"] == 3)
        self.assertNotIn("fog5/index.json", c.objects)

    def test_operation_budget(self):
        for i in range(100):
            (self.d / ("f%03d.png" % i)).write_bytes(b"%d" % i)
        c = Fake()
        s = self.go(c, max_ops=20)
        self.assertLessEqual(s["ops"], 20)
        self.assertNotIn("fog5/index.json", c.objects)   # incomplete upload: index not published
        self.assertTrue(s["index_held_back"])

    def test_failed_upload_holds_back_index(self):
        c = Fake()
        orig = c.put_object

        def bad(**kw):
            if kw["Key"].endswith("a.png"):
                raise RuntimeError("secret-looking text")
            return orig(**kw)
        c.put_object = bad
        self.go(c)
        self.assertNotIn("fog5/index.json", c.objects)
        self.assertNotIn("secret-looking", " ".join(self.logs))

    def test_run_skips_without_secrets_and_never_fails(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = r2_sync.run([str(self.d), "fog5"], env={})
            rc2 = r2_sync.run([str(self.d) + "_missing", "x"], env={})
        self.assertEqual((rc, rc2), (0, 0))
        self.assertIn("skipped", buf.getvalue())

    def test_run_never_fails_and_logs_no_secret(self):
        class Boom(Fake):
            def list_objects_v2(self, **kw):
                raise RuntimeError("Authorization: SECRETVALUE")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = r2_sync.run([str(self.d), "fog5"], env={"R2_BUCKET": "b"}, client=Boom())
        self.assertEqual(rc, 0)
        self.assertNotIn("SECRETVALUE", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
