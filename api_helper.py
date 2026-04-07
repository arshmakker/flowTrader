from NorenRestApiPy.NorenApi import NorenApi
from threading import Timer, Lock
from collections import deque
import pandas as pd
import time
import concurrent.futures
import json
import logging
import urllib.parse
import requests
import hashlib
import os

logger = logging.getLogger(__name__)
api = None
DEBUG_LOG_PATH = "/Users/arshdeep/git/regimetrader/.cursor/debug-84bb37.log"
DEBUG_SESSION_ID = "84bb37"


def _agent_debug_log(hypothesis_id, location, message, data=None, run_id=None):
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": run_id or os.environ.get("AGENT_DEBUG_RUN_ID", "run1"),
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


class Order:
     def __init__(self, buy_or_sell:str = None, product_type:str = None,
                 exchange: str = None, tradingsymbol:str =None, 
                 price_type: str = None, quantity: int = None, 
                 price: float = None,trigger_price:float = None, discloseqty: int = 0,
                 retention:str = 'DAY', remarks: str = "tag",
                 order_id:str = None):
        self.buy_or_sell=buy_or_sell
        self.product_type=product_type
        self.exchange=exchange
        self.tradingsymbol=tradingsymbol
        self.quantity=quantity
        self.discloseqty=discloseqty
        self.price_type=price_type
        self.price=price
        self.trigger_price=trigger_price
        self.retention=retention
        self.remarks=remarks
        self.order_id=None


    #print(ret)

    


def get_time(time_string):
    data = time.strptime(time_string,'%d-%m-%Y %H:%M:%S')

    return time.mktime(data)


class ShoonyaApiPy(NorenApi):
    def __init__(self):
        NorenApi.__init__(self, host='https://api.shoonya.com/NorenWClientTP/', websocket='wss://api.shoonya.com/NorenWSTP/')        
        global api
        api = self
        self._last_broker_error = None
        self._quote_limiter_enabled = str(os.environ.get("SHOONYA_QUOTE_LIMIT_ENABLED", "1")).strip().lower() not in ("0", "false", "no")
        # Hard safety cap requested: never exceed 10 quote calls per second.
        self._quote_hard_max_per_sec = 10
        self._quote_max_per_sec = min(self._safe_int_env("SHOONYA_QUOTE_MAX_PER_SEC", 10), self._quote_hard_max_per_sec)
        self._quote_max_per_min = self._safe_int_env("SHOONYA_QUOTE_MAX_PER_MIN", 170)
        self._quote_low_max_per_sec = self._safe_int_env("SHOONYA_QUOTE_LOW_MAX_PER_SEC", 4)
        self._quote_low_max_per_min = self._safe_int_env("SHOONYA_QUOTE_LOW_MAX_PER_MIN", 120)
        self._quote_rate_lock = Lock()
        self._quote_sec_hits = deque()
        self._quote_min_hits = deque()
        self._quote_low_sec_hits = deque()
        self._quote_low_min_hits = deque()

    @staticmethod
    def _safe_int_env(name, default):
        raw = str(os.environ.get(name, "")).strip()
        if not raw:
            return int(default)
        try:
            return max(1, int(raw))
        except ValueError:
            return int(default)

    @staticmethod
    def _trim_hits(hits, cutoff):
        while hits and hits[0] <= cutoff:
            hits.popleft()

    def _acquire_quote_slot(self, priority="high"):
        """
        Global quote limiter with reserved capacity for high-priority paths.
        - high: strategy/risk/real-time paths
        - low:  bulk/background polling (collector)
        """
        if not self._quote_limiter_enabled:
            return
        lane = "low" if str(priority or "").lower() == "low" else "high"
        while True:
            wait_for = 0.0
            with self._quote_rate_lock:
                now = time.monotonic()
                self._trim_hits(self._quote_sec_hits, now - 1.0)
                self._trim_hits(self._quote_min_hits, now - 60.0)
                self._trim_hits(self._quote_low_sec_hits, now - 1.0)
                self._trim_hits(self._quote_low_min_hits, now - 60.0)

                global_sec_full = len(self._quote_sec_hits) >= self._quote_max_per_sec
                global_min_full = len(self._quote_min_hits) >= self._quote_max_per_min
                low_sec_full = len(self._quote_low_sec_hits) >= min(self._quote_low_max_per_sec, self._quote_max_per_sec)
                low_min_full = len(self._quote_low_min_hits) >= min(self._quote_low_max_per_min, self._quote_max_per_min)

                can_take = not global_sec_full and not global_min_full
                if lane == "low":
                    can_take = can_take and (not low_sec_full) and (not low_min_full)

                if can_take:
                    self._quote_sec_hits.append(now)
                    self._quote_min_hits.append(now)
                    if lane == "low":
                        self._quote_low_sec_hits.append(now)
                        self._quote_low_min_hits.append(now)
                    return

                waits = []
                if global_sec_full and self._quote_sec_hits:
                    waits.append(max(0.0, 1.0 - (now - self._quote_sec_hits[0])))
                if global_min_full and self._quote_min_hits:
                    waits.append(max(0.0, 60.0 - (now - self._quote_min_hits[0])))
                if lane == "low":
                    if low_sec_full and self._quote_low_sec_hits:
                        waits.append(max(0.0, 1.0 - (now - self._quote_low_sec_hits[0])))
                    if low_min_full and self._quote_low_min_hits:
                        waits.append(max(0.0, 60.0 - (now - self._quote_low_min_hits[0])))
                wait_for = min([w for w in waits if w > 0.0], default=0.01)

            time.sleep(min(max(wait_for, 0.01), 1.0))

    def _set_last_broker_error(self, msg):
        self._last_broker_error = str(msg or "").strip()

    def get_last_broker_error(self):
        return self._last_broker_error or ""

    def _clear_last_broker_error(self):
        self._last_broker_error = None

    def login(self, userid, password, twoFA, vendor_code, api_secret, imei):
        """Legacy QuickAuth login with full broker error visibility."""
        config = getattr(self, "_NorenApi__service_config", None) or getattr(NorenApi, "_NorenApi__service_config", None)
        if not config:
            msg = "Login failed: no service config"
            self._set_last_broker_error(msg)
            logger.error(msg)
            return None
        host = (config.get("host") or "").rstrip("/")
        routes = config.get("routes") or {}
        path = (routes.get("authorize") or "").lstrip("/")
        url = f"{host}/{path}" if path else host

        pwd = hashlib.sha256(str(password).encode("utf-8")).hexdigest()
        app_key = hashlib.sha256(f"{userid}|{api_secret}".encode("utf-8")).hexdigest()
        values = {
            "source": "API",
            "apkversion": "1.0.0",
            "uid": userid,
            "pwd": pwd,
            "factor2": twoFA,
            "vc": vendor_code,
            "appkey": app_key,
            "imei": imei,
        }
        payload = "jData=" + json.dumps(values)
        try:
            res = requests.post(url, data=payload, timeout=30)
            text = (res.text or "").strip()
            if not res.ok:
                msg = f"QuickAuth HTTP {res.status_code}: {text[:500] or 'empty body'}"
                self._set_last_broker_error(msg)
                logger.error(msg)
                return None
            if not text:
                msg = "QuickAuth returned empty body"
                self._set_last_broker_error(msg)
                logger.error(msg)
                return None
            try:
                res_dict = json.loads(text)
            except json.JSONDecodeError:
                msg = f"QuickAuth returned non-JSON body: {text[:500]}"
                self._set_last_broker_error(msg)
                logger.error(msg)
                return None

            if str(res_dict.get("stat", "")).lower() != "ok":
                emsg = res_dict.get("emsg") or res_dict.get("rejreason") or str(res_dict)
                msg = f"QuickAuth rejected: {emsg}"
                self._set_last_broker_error(msg)
                logger.error(msg)
                return res_dict

            self._NorenApi__username = userid
            self._NorenApi__accountid = userid
            self._NorenApi__password = password
            self._NorenApi__susertoken = res_dict.get("susertoken")
            self._clear_last_broker_error()
            return res_dict
        except requests.RequestException as exc:
            body = ""
            if getattr(exc, "response", None) is not None:
                body = (exc.response.text or "")[:500]
            msg = f"QuickAuth request failed: {exc}" + (f" | body={body}" if body else "")
            self._set_last_broker_error(msg)
            logger.error(msg)
            return None
        except Exception as exc:
            msg = f"QuickAuth unexpected failure: {exc}"
            self._set_last_broker_error(msg)
            logger.error(msg, exc_info=True)
            return None

    def _oauth_post_json(self, route_key, values):
        """
        Perform OAuth-authenticated POST and return (ok, data, error_msg).
        Keeps broker/body errors visible instead of surfacing JSON decode only.
        """
        config = getattr(self, "_NorenApi__service_config", None) or getattr(NorenApi, "_NorenApi__service_config", None)
        if not config:
            return False, None, "OAuth call failed: no service config"
        host = (config.get("host") or "").rstrip("/")
        routes = config.get("routes") or {}
        path = (routes.get(route_key) or "").lstrip("/")
        if not path:
            return False, None, f"OAuth call failed: missing route '{route_key}'"
        url = f"{host}/{path}"
        payload = "jData=" + json.dumps(values or {})
        headers = getattr(self, "_NorenApi__OAuthHeaders", None)
        if not headers:
            return False, None, "OAuth call failed: missing OAuth headers"
        try:
            res = requests.post(url, data=payload, headers=headers, timeout=30)
            text = (res.text or "").strip()
            # region agent log
            _agent_debug_log(
                "H3",
                "api_helper.py:_oauth_post_json:response",
                "oauth_route_response",
                {
                    "route_key": route_key,
                    "http_status": res.status_code,
                    "host": host,
                    "has_invalid_session_key": ("Invalid Session Key" in text),
                    "using_oauth_headers": bool(headers),
                },
            )
            # endregion
            if res.status_code == 401 and "Invalid Session Key" in text:
                # Some broker routes may reject OAuth-header auth intermittently.
                ok2, data2, err2 = self._oauth_post_with_jkey(url, values, route_key)
                # region agent log
                _agent_debug_log(
                    "H3",
                    "api_helper.py:_oauth_post_json:jkey_retry",
                    "oauth_route_jkey_retry_result",
                    {"route_key": route_key, "retry_ok": bool(ok2), "retry_err_present": bool(err2)},
                )
                # endregion
                if ok2:
                    return True, data2, ""
                if err2:
                    return False, data2, err2
            if not res.ok:
                return False, None, f"{route_key} HTTP {res.status_code}: {text[:500] or 'empty body'}"
            if not text:
                return False, None, f"{route_key} returned empty body"
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return False, None, f"{route_key} returned non-JSON body: {text[:500]}"
            if isinstance(data, dict) and str(data.get("stat", "")).lower() == "ok":
                return True, data, ""
            emsg = data.get("emsg") if isinstance(data, dict) else str(data)
            return False, data, f"{route_key} rejected: {emsg or data}"
        except requests.RequestException as exc:
            body = ""
            if getattr(exc, "response", None) is not None:
                body = (exc.response.text or "")[:500]
            return False, None, f"{route_key} request failed: {exc}" + (f" | body={body}" if body else "")
        except Exception as exc:
            return False, None, f"{route_key} unexpected failure: {exc}"

    def _oauth_post_with_jkey(self, url, values, route_key):
        session_key = getattr(self, "_NorenApi__susertoken", None)
        if not session_key:
            return False, None, ""
        payload = "jData=" + json.dumps(values or {}) + "&jKey=" + str(session_key)
        try:
            res = requests.post(url, data=payload, timeout=30)
            text = (res.text or "").strip()
            # region agent log
            _agent_debug_log(
                "H3",
                "api_helper.py:_oauth_post_with_jkey:response",
                "oauth_route_jkey_response",
                {"route_key": route_key, "http_status": res.status_code, "has_body": bool(text)},
            )
            # endregion
            if not res.ok:
                return False, None, f"{route_key} (jKey retry) HTTP {res.status_code}: {text[:500] or 'empty body'}"
            if not text:
                return False, None, f"{route_key} (jKey retry) returned empty body"
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return False, None, f"{route_key} (jKey retry) returned non-JSON body: {text[:500]}"
            if isinstance(data, dict) and str(data.get("stat", "")).lower() == "ok":
                return True, data, ""
            emsg = data.get("emsg") if isinstance(data, dict) else str(data)
            return False, data, f"{route_key} (jKey retry) rejected: {emsg or data}"
        except requests.RequestException as exc:
            body = ""
            if getattr(exc, "response", None) is not None:
                body = (exc.response.text or "")[:500]
            return False, None, f"{route_key} (jKey retry) request failed: {exc}" + (f" | body={body}" if body else "")
        except Exception as exc:
            return False, None, f"{route_key} (jKey retry) unexpected failure: {exc}"

    def _quote_request(self, exchange, token):
        """Low-level quote request with explicit parse/HTTP diagnostics."""
        config = getattr(self, "_NorenApi__service_config", None) or getattr(NorenApi, "_NorenApi__service_config", None)
        if not config:
            return None, "getquotes failed: no service config"
        host = (config.get("host") or "").rstrip("/")
        routes = config.get("routes") or {}
        path = (routes.get("getquotes") or "").lstrip("/")
        if not path:
            return None, "getquotes failed: missing route 'getquotes'"
        url = f"{host}/{path}"

        uid = getattr(self, "_NorenApi__username", None)
        values = {"uid": uid, "exch": exchange, "token": token}

        oauth_headers = getattr(self, "_NorenApi__OAuthHeaders", None)
        session_key = getattr(self, "_NorenApi__susertoken", None)
        headers = oauth_headers or None
        if headers:
            payload = "jData=" + json.dumps(values)
        else:
            if not session_key:
                return None, "getquotes failed: missing session key"
            payload = "jData=" + json.dumps(values) + "&jKey=" + str(session_key)

        try:
            res = requests.post(url, data=payload, headers=headers, timeout=15)
            text = (res.text or "").strip()
            if (
                headers
                and res.status_code == 401
                and "Invalid Session Key" in text
                and session_key
            ):
                # Broker may reject OAuth-header auth for getquotes even when
                # OAuth validation routes succeed; retry once with jKey.
                retry_payload = "jData=" + json.dumps(values) + "&jKey=" + str(session_key)
                retry_res = requests.post(url, data=retry_payload, timeout=15)
                retry_text = (retry_res.text or "").strip()
                if retry_res.ok and retry_text:
                    try:
                        retry_data = json.loads(retry_text)
                    except json.JSONDecodeError:
                        return None, f"getquotes jKey retry returned non-JSON body: {retry_text[:500]}"
                    if str(retry_data.get("stat", "")).lower() == "ok":
                        return retry_data, ""
                    retry_emsg = retry_data.get("emsg") or retry_data.get("rejreason") or str(retry_data)
                    return None, f"getquotes jKey retry rejected: {retry_emsg}"
                return None, f"getquotes jKey retry HTTP {retry_res.status_code}: {retry_text[:500] or 'empty body'}"
            if not res.ok:
                return None, f"getquotes HTTP {res.status_code}: {text[:500] or 'empty body'}"
            if not text:
                return None, "getquotes returned empty body"
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return None, f"getquotes returned non-JSON body: {text[:500]}"
            if str(data.get("stat", "")).lower() != "ok":
                emsg = data.get("emsg") or data.get("rejreason") or str(data)
                return None, f"getquotes rejected: {emsg}"
            return data, ""
        except requests.RequestException as exc:
            body = ""
            if getattr(exc, "response", None) is not None:
                body = (exc.response.text or "")[:500]
            return None, f"getquotes request failed: {exc}" + (f" | body={body}" if body else "")
        except Exception as exc:
            return None, f"getquotes unexpected failure: {exc}"

    def get_quotes_safe(self, exchange, token, retries=1, backoff_sec=0.2, context=None, priority="high"):
        """
        Resilient quote fetcher with retry and explicit broker diagnostics.
        Returns quote dict on success, else None.
        """
        last_err = ""
        attempts = max(1, int(retries) + 1)
        for i in range(attempts):
            self._acquire_quote_slot(priority=priority)
            quote, err = self._quote_request(exchange, token)
            if quote is not None:
                self._clear_last_broker_error()
                return quote
            last_err = err or "unknown getquotes error"
            if i < attempts - 1:
                time.sleep(max(0.0, float(backoff_sec)))

        self._set_last_broker_error(last_err)
        if context:
            logger.error("Broker quote error (%s): %s", context, last_err)
        else:
            logger.error("Broker quote error: %s", last_err)
        return None

    def get_quotes(self, exchange, token, priority="high", context=None):
        """
        Override SDK get_quotes to avoid hidden JSON parse errors.
        Keeps call signature compatible with existing code.
        """
        return self.get_quotes_safe(
            exchange=exchange,
            token=token,
            retries=1,
            backoff_sec=0.2,
            priority=priority,
            context=context,
        )

    def get_oauth_url(self, oauth_url, client_id):
        """Build broker OAuth login URL."""
        try:
            url = super().getOAuthURL(oauth_url, client_id)
            self._clear_last_broker_error()
            return url
        except Exception as exc:
            msg = f"OAuth URL build failed: {exc}"
            self._set_last_broker_error(msg)
            logger.error(msg, exc_info=True)
            return None

    def configure_oauth_service_host(self, host=None, websocket_endpoint=None):
        """
        Switch SDK service host to OAuth-friendly API endpoints.
        Noren OAuth methods (watchlist/limits/etc.) read class-level service config.
        """
        try:
            cfg = getattr(NorenApi, "_NorenApi__service_config", None)
            if not isinstance(cfg, dict):
                msg = "OAuth host switch skipped: service config unavailable"
                self._set_last_broker_error(msg)
                logger.warning(msg)
                return False
            cfg["host"] = host or "https://api.shoonya.com/NorenWClientAPI/"
            cfg["websocket_endpoint"] = websocket_endpoint or "wss://api.shoonya.com/NorenWS/"
            self._clear_last_broker_error()
            return True
        except Exception as exc:
            msg = f"OAuth host switch failed: {exc}"
            self._set_last_broker_error(msg)
            logger.error(msg, exc_info=True)
            return False

    def exchange_auth_code(self, auth_code, secret_code, client_id, uid, token_url=None):
        """Exchange auth code for (access_token, user_id, refresh_token, account_id)."""
        try:
            config = getattr(self, "_NorenApi__service_config", None) or getattr(NorenApi, "_NorenApi__service_config", None)
            host = (config.get("host") or "").rstrip("/") if config else ""
            routes = config.get("routes") or {} if config else {}
            path = (routes.get("gen_acs_tok") or "").lstrip("/")
            route_url = f"{host}/{path}" if (host and path) else ""
            # OAuth token exchange is served on NorenWClientAPI; TP host often returns non-JSON.
            default_token_url = "https://api.shoonya.com/NorenWClientAPI//GenAcsTok"
            url = str(token_url or "").strip() or route_url.replace("NorenWClientTP", "NorenWClientAPI") or default_token_url

            checksum_src = f"{client_id}{secret_code}{auth_code}".encode("utf-8")
            checksum = hashlib.sha256(checksum_src).hexdigest()
            values = {"code": auth_code, "checksum": checksum, "uid": uid}
            payload = "jData=" + json.dumps(values)

            res = requests.post(url, data=payload, timeout=30)
            text = (res.text or "").strip()
            if not res.ok:
                msg = f"OAuth token exchange HTTP {res.status_code}: {text[:500] or 'empty body'}"
                self._set_last_broker_error(msg)
                logger.error(msg)
                return None
            if not text:
                msg = "OAuth token exchange returned empty body"
                self._set_last_broker_error(msg)
                logger.error(msg)
                return None

            try:
                res_dict = json.loads(text)
            except json.JSONDecodeError:
                msg = f"OAuth token exchange returned non-JSON body: {text[:500]}"
                self._set_last_broker_error(msg)
                logger.error(msg)
                return None

            if "access_token" not in res_dict:
                emsg = res_dict.get("emsg") or res_dict.get("message") or str(res_dict)
                msg = f"OAuth token exchange rejected: {emsg}"
                self._set_last_broker_error(msg)
                logger.error(msg)
                return None

            access_token = res_dict.get("access_token")
            user_id = res_dict.get("USERID") or uid
            refresh_token = res_dict.get("refresh_token")
            account_id = res_dict.get("actid") or uid
            session_token = res_dict.get("susertoken")
            # region agent log
            _agent_debug_log(
                "H2",
                "api_helper.py:exchange_auth_code:parsed",
                "oauth_exchange_parsed",
                {
                    "http_status": res.status_code,
                    "has_access_token": bool(access_token),
                    "has_session_token": bool(session_token),
                    "has_user_id": bool(user_id),
                    "has_account_id": bool(account_id),
                },
            )
            # endregion

            # Keep SDK internals aligned so downstream broker methods work.
            if session_token:
                self._NorenApi__susertoken = session_token
            self._NorenApi__username = user_id
            self._NorenApi__accountid = account_id
            self._NorenApi__access_token = access_token

            self.inject_oauth_header(access_token, user_id, account_id)
            self._clear_last_broker_error()
            return access_token, user_id, refresh_token, account_id
        except Exception as exc:
            msg = f"OAuth token exchange failed: {exc}"
            self._set_last_broker_error(msg)
            logger.error(msg, exc_info=True)
            return None

    def inject_oauth_header(self, access_token, uid, account_id):
        """Inject bearer token into API headers and SDK session fields."""
        try:
            headers = super().injectOAuthHeader(access_token, uid, account_id)
            self._clear_last_broker_error()
            return headers
        except Exception as exc:
            msg = f"Inject OAuth header failed: {exc}"
            self._set_last_broker_error(msg)
            logger.error(msg, exc_info=True)
            return None

    def validate_oauth_session(self):
        """Check whether current OAuth token can access account APIs."""
        checks = [
            ("watchlist_names", {"ordersource": "API", "uid": getattr(self, "_NorenApi__username", None)}),
            ("limits", {"uid": getattr(self, "_NorenApi__username", None), "actid": getattr(self, "_NorenApi__accountid", None)}),
        ]
        last_error = ""
        for route_key, values in checks:
            ok, _data, err = self._oauth_post_json(route_key, values)
            # region agent log
            _agent_debug_log(
                "H5",
                "api_helper.py:validate_oauth_session:route_result",
                "oauth_validate_route_result",
                {"route_key": route_key, "ok": bool(ok), "error_sample": (err or "")[:160]},
            )
            # endregion
            if ok:
                self._clear_last_broker_error()
                return True
            last_error = err
            logger.warning("OAuth session validation via %s failed: %s", route_key, err)
        if last_error:
            self._set_last_broker_error(last_error)
        else:
            msg = "OAuth session validation failed: no successful stat=Ok response"
            self._set_last_broker_error(msg)
            logger.warning(msg)
        return False

    def place_basket(self, orders):

        resp_err = 0
        resp_ok  = 0
        result   = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:

            future_to_url = {executor.submit(self.place_order, order): order for order in  orders}
            for future in concurrent.futures.as_completed(future_to_url):
                url = future_to_url[future]
            try:
                result.append(future.result())
            except Exception as exc:
                logger.error("Basket order failed: %s", exc, exc_info=True)
                resp_err = resp_err + 1
            else:
                resp_ok = resp_ok + 1

        return result
                
    def placeOrder(self, order: Order):
        return self.place_order(
            buy_or_sell=order.buy_or_sell,
            product_type=order.product_type,
            exchange=order.exchange,
            tradingsymbol=order.tradingsymbol,
            quantity=order.quantity,
            discloseqty=order.discloseqty or 0,
            price_type=order.price_type,
            price=order.price,
            trigger_price=order.trigger_price,
            retention=order.retention or "DAY",
            remarks=order.remarks or "tag",
        )

    def place_order(self, buy_or_sell, product_type=None, exchange=None, tradingsymbol=None, quantity=None,
                    discloseqty=0, price_type=None, price=0.0, trigger_price=None, retention="DAY", amo="NO", remarks=None):
        """
        Place order via direct HTTP. NorenApi.place_order() returns None when broker
        returns stat != 'Ok', so we do the request here and always return the full
        response so callers get emsg on rejection (same approach as bsensearb).
        """
        # place_basket calls place_order(order); accept Order as first arg and unpack
        if isinstance(buy_or_sell, Order):
            o = buy_or_sell
            buy_or_sell = o.buy_or_sell
            product_type = o.product_type
            exchange = o.exchange
            tradingsymbol = o.tradingsymbol
            quantity = o.quantity
            discloseqty = o.discloseqty or 0
            price_type = o.price_type
            price = o.price
            trigger_price = o.trigger_price
            retention = o.retention or "DAY"
            remarks = o.remarks or "tag"
        try:
            config = getattr(self, "_NorenApi__service_config", None) or getattr(NorenApi, "_NorenApi__service_config", None)
            if not config:
                logger.error("Place order: no service config")
                return None
            host = (config.get("host") or "").rstrip("/")
            routes = config.get("routes") or {}
            path = (routes.get("placeorder") or "").lstrip("/")
            url = f"{host}/{path}" if path else host
            uid = getattr(self, "_NorenApi__username", None)
            actid = getattr(self, "_NorenApi__accountid", None)
            token = getattr(self, "_NorenApi__susertoken", None)
            if not all([uid, actid, token]):
                logger.error("Place order: not logged in (missing uid/actid/token)")
                return None
            trgprc = trigger_price if trigger_price is not None else 0
            values = {
                "ordersource": "API",
                "uid": uid,
                "actid": actid,
                "trantype": buy_or_sell,
                "prd": product_type,
                "exch": exchange,
                "tsym": urllib.parse.quote_plus(tradingsymbol),
                "qty": str(int(quantity)),
                "dscqty": str(int(discloseqty or 0)),
                "prctyp": price_type,
                "prc": str(price),
                "trgprc": str(trgprc),
                "ret": retention or "DAY",
                "remarks": remarks or "convex",
            }
            payload = "jData=" + json.dumps(values) + "&jKey=" + str(token)
            res = requests.post(url, data=payload, timeout=30)
            if not res.ok:
                try:
                    body = json.loads(res.text)
                    emsg = body.get("emsg") or body.get("rejreason") or body.get("remarks") or res.text[:500]
                except Exception:
                    emsg = res.text[:500] if res.text else (getattr(res, "reason", None) or "unknown")
                logger.error("Place order HTTP %s: %s | url=%s", res.status_code, emsg, url)
                logger.debug("Place order HTTP body: %s", res.text)
                return None
            res_dict = json.loads(res.text)
            if res_dict.get("stat") != "Ok":
                emsg = res_dict.get("emsg") or res_dict.get("rejreason") or res_dict.get("remarks", "")
                logger.error("Place order rejected: %s", emsg or res_dict.get("stat"))
                logger.debug("Place order full response: %s", res_dict)
            return res_dict
        except requests.RequestException as e:
            err_body = ""
            if hasattr(e, "response") and e.response is not None and getattr(e.response, "text", None):
                err_body = " | body=%s" % (e.response.text[:500],)
                logger.debug("Place order request exception body: %s", e.response.text)
            logger.error("Place order request failed: %s%s", e, err_body)
            return None
        except (ValueError, KeyError) as e:
            logger.error("Place order response parse error: %s", e)
            return None
