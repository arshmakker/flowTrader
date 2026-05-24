"""
Shoonya broker proxy — one OAuth session shared across multiple trading processes.

Reads the existing Access_token from cred.yml (does NOT trigger a new login).
If the token is invalid, exits loudly so the operator can refresh it via regimetrader.

Start:
    python broker_proxy.py [--port 7890] [--cred-file ../regimetrader/cred.yml]

Both flowTrader and regimetrader set:
    BROKER_PROXY_URL=http://127.0.0.1:7890
and use BrokerClient instead of ShoonyaApiPy directly.
"""

import argparse
import logging
import os
import sys

import yaml
from flask import Flask, jsonify, request

# Must run from flowTrader root (or sys.path must include it).
sys.path.insert(0, os.path.dirname(__file__))
from api_helper import ShoonyaApiPy

log = logging.getLogger("broker_proxy")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

_DEFAULT_CRED = os.path.join(os.path.dirname(__file__), "../regimetrader/cred.yml")

# Single shared instance — all routes use this. Never instantiate a second one.
_api: ShoonyaApiPy | None = None

app = Flask(__name__)


def _init_api(cred_file: str) -> ShoonyaApiPy:
    cred_file = os.path.abspath(cred_file)
    if not os.path.exists(cred_file):
        log.error("cred file not found: %s", cred_file)
        sys.exit(1)

    with open(cred_file) as f:
        creds = yaml.safe_load(f)

    access_token = (creds.get("Access_token") or "").strip()
    uid = (creds.get("UID") or creds.get("uid") or "").strip()
    account_id = (creds.get("Account_ID") or creds.get("actid") or uid).strip()

    if not access_token:
        log.error(
            "No Access_token in %s — start regimetrader first so it refreshes the token, " "then start the proxy.",
            cred_file,
        )
        sys.exit(1)

    api = ShoonyaApiPy()
    api.inject_oauth_header(access_token, uid, account_id)
    api._NorenApi__username = uid
    api._NorenApi__accountid = account_id

    if not api.validate_oauth_session():
        log.error(
            "Access_token from %s is stale — let regimetrader do a fresh OAuth login "
            "(it will update cred.yml), then restart this proxy.",
            cred_file,
        )
        sys.exit(1)

    log.info("Proxy ready — session valid uid=%s cred=%s", uid, cred_file)
    return api


@app.route("/health", methods=["GET"])
def health():
    ok = _api is not None and _api.validate_oauth_session()
    return jsonify({"ok": ok})


@app.route("/call", methods=["POST"])
def call_method():
    data = request.get_json(force=True, silent=True) or {}
    method_name = data.get("method")
    args = data.get("args", [])
    kwargs = data.get("kwargs", {})

    if not method_name:
        return jsonify({"error": "missing 'method'"}), 400

    method = getattr(_api, method_name, None)
    if method is None:
        return jsonify({"error": f"unknown method: {method_name}"}), 400

    try:
        result = method(*args, **kwargs)
        # ShoonyaApiPy methods return dicts, lists, or None.
        if result is None:
            return jsonify(None), 200
        return jsonify(result), 200
    except Exception as exc:
        log.error("Proxy call %s failed: %s", method_name, exc, exc_info=True)
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shoonya OAuth broker proxy")
    parser.add_argument("--port", type=int, default=7890)
    parser.add_argument(
        "--cred-file",
        default=os.environ.get("SHOONYA_CRED_FILE", _DEFAULT_CRED),
        help="Path to cred.yml containing a valid Access_token",
    )
    args = parser.parse_args()

    _api = _init_api(args.cred_file)
    # threaded=True: Flask handles concurrent requests in separate threads.
    # The ShoonyaApiPy rate limiter uses threading.Lock internally — thread-safe.
    # Never use debug=True here (spawns a second process, second ShoonyaApiPy instance).
    app.run(host="127.0.0.1", port=args.port, threaded=True, debug=False)
