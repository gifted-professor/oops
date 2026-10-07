"""Expiring, person-scoped enrollment invitations; independent of the app API key."""
import base64
import hashlib
import hmac
import json
import time


def issue_invite(secret, name, expires_at):
    body = base64.urlsafe_b64encode(json.dumps({"name": name, "exp": int(expires_at)},
        ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return "vp1." + body + "." + signature


def invite_person(secret, token, team, now=None):
    if not secret or len(token) > 512:
        return None
    try:
        version, body, signature = token.split(".")
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        if version != "vp1" or not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if data["name"] not in team or type(data["exp"]) is not int or data["exp"] <= (time.time() if now is None else now):
            return None
        return data["name"]
    except (ValueError, KeyError, TypeError):
        return None
