"""Transport adapter for the unchanged, owner-locked QQ2 matcher."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "locked_qq2"


def locked_module():
    manifest = json.loads((ROOT / "INTEGRITY.json").read_text())
    for name, expected in manifest.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Locked QQ2 file changed: " + name)
    spec = importlib.util.spec_from_file_location("sfb_locked_qq2", ROOT / "app.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prepare(buyer_text, seller_text):
    locked = locked_module()
    data = {"buyer_text": buyer_text, "seller_text": seller_text}
    return locked, data, locked.payload(data)


def accept(locked, data, response):
    if response.status != "completed":
        raise ValueError("QQ2 response not completed: " + str(response.status))
    answer = locked.parse(response.output_text)
    validated = locked.validate_answer(answer, data)
    return {
        "match": answer["decision"] == "Send",
        "confidence": None,  # QQ2 does not produce a probability or score.
        "reason": validated["Reason"],
        "matcher": "locked-qq2",
        "decision": answer["decision"],
        "assessment": answer,
        "validated": validated,
    }
