"""Cache-busting stamp for the published viewer (run by live_ci/refresh.sh on the deploy copy of site/v3, never on the repository files).

The viewer's scripts and styles are served from plain addresses, so a phone or browser can keep running an old app.js against new data for a while. This gives
every file a ?v=<hash of all viewer scripts and styles> address: the hash changes exactly when a script or style changes, so a viewer change reaches everyone with the
next deploy and nothing is re-downloaded in between. Stamped: the three tags in index.html, the relative imports in app.js, and the beta_ui.css address in beta_ui.js.
Idempotent (an existing ?v= is replaced). Usage: python3 live_ci/stamp_version.py [site/v3]"""
import hashlib
import os
import re
import sys

STAMP = re.compile(r"\?v=[0-9a-f]+")


def version(root):
    h = hashlib.sha1()
    for name in sorted(os.listdir(root)):
        if name.endswith((".js", ".css")):
            h.update(name.encode())
            with open(os.path.join(root, name), "rb") as f:
                h.update(STAMP.sub("", f.read().decode("utf-8")).encode())
    return h.hexdigest()[:10]


def _edit(path, subs):
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8")
    for pat, rep in subs:
        text = re.sub(pat, rep, text)
    with open(path, "wb") as f:      # bytes: keep LF
        f.write(text.encode("utf-8"))


def stamp(root="site/v3"):
    v = version(root)
    q = "?v=" + v
    n = r"(?:\?v=[0-9a-f]+)?"
    _edit(os.path.join(root, "index.html"), [
        (r'href="style\.css' + n + '"', 'href="style.css' + q + '"'),
        (r'src="(app|about)\.js' + n + '"', r'src="\1.js' + q + '"')])
    _edit(os.path.join(root, "app.js"), [(r"'\./(\w+)\.js" + n + "'", r"'./\1.js" + q + "'")])
    _edit(os.path.join(root, "beta_ui.js"), [(r"new URL\('beta_ui\.css" + n + "'", "new URL('beta_ui.css" + q + "'")])
    return v


if __name__ == "__main__":
    print("viewer version", stamp(sys.argv[1] if len(sys.argv) > 1 else "site/v3"))
