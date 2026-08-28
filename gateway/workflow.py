"""Load user-supplied ComfyUI workflows and substitute placeholders.

Workflows are JSON, not code, because node graphs and model filenames differ per
install. Export yours from ComfyUI with Workflow -> Export (API).
"""
import json, os, re
from .config import WORKFLOW_DIR

_NUM = re.compile(r"^-?\d+$")


def load(filename: str, **params):
    path = os.path.join(WORKFLOW_DIR, filename)
    with open(path) as f:
        raw = f.read()
    for k, v in params.items():
        raw = raw.replace('"{{%s}}"' % k, json.dumps(v))   # bare value
        raw = raw.replace("{{%s}}" % k, str(v).replace('"', '\\"'))  # inside string
    wf = json.loads(raw)
    wf.pop("_comment", None)
    return wf
