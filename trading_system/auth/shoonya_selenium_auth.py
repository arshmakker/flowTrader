"""In-process Shoonya OAuth auth-code capture via headless Chrome.

Replaces the previous out-of-process subprocess call to
`Shoonya_oAuth_API.py/tests/getAuthCode.py`. Running in the same Python process
as the token exchange eliminates the timing window where the browser may
complete the OAuth redirect (and the auth_code's single-use slot may be
consumed) before the exchange call fires.

Usage:
    from trading_system.auth.shoonya_selenium_auth import fetch_auth_code
    code = fetch_auth_code(creds)  # returns auth_code string, or "" on failure
"""

import json
import logging
import time
from typing import Mapping
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


REQUIRED_KEYS = ("selenium_user_id", "selenium_password", "selenium_totp_secret")
DEFAULT_LOGIN_URL_TEMPLATE = (
    "https://trade.shoonya.com/OAuthlogin/investor-entry-level/login"
    "?api_key={client_id}&route_to=abc"
)


def is_configured(creds: Mapping) -> bool:
    return all(str(creds.get(k, "")).strip() for k in REQUIRED_KEYS)


def _build_login_url(creds: Mapping) -> str:
    template = str(creds.get("selenium_login_url_template", "")).strip() or DEFAULT_LOGIN_URL_TEMPLATE
    client_id = str(creds.get("client_id", "")).strip()
    if not client_id:
        raise ValueError("client_id missing in cred.yml; required to build login URL")
    return template.format(client_id=client_id)


def _scan_network_for_code(driver) -> str:
    try:
        logs = driver.get_log("performance")
    except Exception:
        return ""
    for entry in logs:
        try:
            message = json.loads(entry["message"])["message"]
        except Exception:
            continue
        if message.get("method") != "Network.requestWillBeSent":
            continue
        url = message.get("params", {}).get("request", {}).get("url", "")
        if "code=" in url and "shoonya" in url.lower():
            try:
                code = parse_qs(urlparse(url).query).get("code", [None])[0]
            except Exception:
                code = None
            if code:
                return code
    return ""


def _fast_fill(element, value: str) -> None:
    element.click()
    time.sleep(0.1)
    element.clear()
    element.send_keys(value)
    time.sleep(0.1)


def fetch_auth_code(creds: Mapping, timeout_sec: int = 60) -> str:
    """Drive headless Chrome through Shoonya OAuth login; return captured auth_code.

    Returns empty string on any failure. Logs the failure reason. Does not raise.
    """
    if not is_configured(creds):
        logger.warning("Selenium auth: missing required cred fields %s", REQUIRED_KEYS)
        return ""

    try:
        from selenium import webdriver
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import pyotp
    except ImportError as exc:
        logger.error("Selenium auth: dependency import failed (%s); run pip install -r requirements.txt", exc)
        return ""

    user_id = str(creds["selenium_user_id"]).strip()
    password = str(creds["selenium_password"]).strip()
    totp_secret = str(creds["selenium_totp_secret"]).strip()

    try:
        login_url = _build_login_url(creds)
    except ValueError as exc:
        logger.error("Selenium auth: %s", exc)
        return ""

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = None
    try:
        driver = webdriver.Chrome(options=options)
        wait = WebDriverWait(driver, 30)
        logger.info("Selenium auth: opening login page")
        driver.get(login_url)

        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "input[type='password']")))
        time.sleep(1)

        all_inputs = driver.find_elements(
            By.CSS_SELECTOR,
            "input:not([type='hidden']):not([type='checkbox']):not([type='radio'])",
        )
        visible_inputs = [inp for inp in all_inputs if inp.is_displayed()]
        if len(visible_inputs) < 3:
            logger.error("Selenium auth: expected >=3 visible inputs, found %d", len(visible_inputs))
            return ""

        _fast_fill(visible_inputs[0], user_id)
        _fast_fill(visible_inputs[1], password)
        otp_value = pyotp.TOTP(totp_secret).now()
        _fast_fill(visible_inputs[2], otp_value)

        wait.until(
            EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='LOGIN']"))
        ).click()
        logger.info("Selenium auth: credentials submitted, capturing auth code")

        start = time.time()
        while True:
            code = _scan_network_for_code(driver)
            if code:
                logger.info("Selenium auth: auth code captured")
                return code

            if time.time() - start > timeout_sec:
                # Try a fresh OTP once in case the first one expired mid-submit.
                new_otp = pyotp.TOTP(totp_secret).now()
                if new_otp != otp_value:
                    logger.info("Selenium auth: timeout, retrying with fresh OTP")
                    _fast_fill(visible_inputs[2], new_otp)
                    wait.until(
                        EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='LOGIN']"))
                    ).click()
                    start = time.time()
                    otp_value = new_otp
                    continue
                logger.error("Selenium auth: timeout waiting for auth code (%ss)", timeout_sec)
                return ""

            time.sleep(0.5)
    except Exception as exc:
        logger.error("Selenium auth: unexpected failure: %s", exc, exc_info=True)
        return ""
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
