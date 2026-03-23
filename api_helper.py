from NorenRestApiPy.NorenApi import NorenApi
from threading import Timer
import pandas as pd
import time
import concurrent.futures
import json
import logging
import urllib.parse
import requests
import hashlib

logger = logging.getLogger(__name__)
api = None
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

    def login(self, userid, password, twoFA, vendor_code, api_secret, imei):
        """Override login to prevent swallowing error messages (emsg) on failure."""
        config = getattr(self, "_NorenApi__service_config", None) or getattr(NorenApi, "_NorenApi__service_config", None)
        if not config:
            logger.error("Login: no service config")
            return None

        url = f"{config['host']}{config['routes']['authorize']}"
        pwd = hashlib.sha256(password.encode('utf-8')).hexdigest()
        u_app_key = '{0}|{1}'.format(userid, api_secret)
        app_key = hashlib.sha256(u_app_key.encode('utf-8')).hexdigest()

        values = {
            "source": "API",
            "apkversion": "1.0.0",
            "uid": userid,
            "pwd": pwd,
            "factor2": twoFA,
            "vc": vendor_code,
            "appkey": app_key,
            "imei": imei
        }

        payload = 'jData=' + json.dumps(values)
        try:
            res = requests.post(url, data=payload, timeout=30)
            res.raise_for_status()
            res_dict = json.loads(res.text)
            
            if res_dict.get('stat') != 'Ok':
                emsg = res_dict.get('emsg') or res_dict.get('rejreason', 'Unknown error')
                logger.error("Shoonya login rejected: %s", emsg)
                # Return the dict so caller in main.py can see emsg
                return res_dict

            # Set private attributes for NorenApi base class methods
            self._NorenApi__username = userid
            self._NorenApi__accountid = userid
            self._NorenApi__password = password
            self._NorenApi__susertoken = res_dict['susertoken']
            
            return res_dict
        except Exception as e:
            logger.exception("Shoonya login exception: %s", e)
            return None

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
