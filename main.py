"""
Main entry — minimal stub (rebuild in progress per agent.md).

Old strategy/position logic removed. Build new system per docs/agent.md.
Keeps: API auth, symbol manager, data collector, market-hours loop.
"""

import os
import sys
import logging
import yaml
import time
from datetime import datetime
from api_helper import ShoonyaApiPy
from symbol_manager import SymbolManager
from data_collector import DataCollector
from strategy_runner import is_market_hours, is_market_closed_ist
from colorama import init, Fore, Style

try:
    init(autoreset=True)
except ImportError:
    print("colorama module not found. Please install it using 'pip install colorama'")
    sys.exit(1)


def setup_logging():
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"trading_system_{datetime.now().strftime('%Y%m%d')}.log")
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    logging.info("Log file: %s", os.path.abspath(log_path))


def load_credentials():
    try:
        with open("cred.yml", "r") as f:
            creds = yaml.safe_load(f)
            logging.info(Fore.GREEN + "Credentials loaded successfully")
            return creds
    except FileNotFoundError:
        logging.error(Fore.RED + "cred.yml not found. Create from cred.yml.template")
        return None
    except Exception as e:
        logging.error(Fore.RED + "Error loading credentials: %s", e)
        return None


def initialize_api():
    creds = load_credentials()
    if not creds:
        raise ValueError("Failed to load credentials")
    api = ShoonyaApiPy()
    factor2 = os.environ.get("TWOFA", "").strip()
    if not factor2:
        if not sys.stdin.isatty():
            raise ValueError(
                "No TTY and TWOFA not set. Run in a terminal or: TWOFA=<code> python main.py"
            )
        factor2 = input(Fore.CYAN + "Enter your 2FA code: ").strip()
    if not factor2:
        raise ValueError("2FA code is required")
    logging.info("Attempting to login to Shoonya API...")
    ok = api.login(
        userid=creds["user"],
        password=creds["pwd"],
        twoFA=factor2,
        vendor_code=creds["vc"],
        api_secret=creds["apikey"],
        imei=creds["imei"],
    )
    if not ok:
        raise ValueError("Login failed")
    logging.info(Fore.GREEN + "Successfully logged in to Shoonya API")
    return api


def main():
    setup_logging()
    logger = logging.getLogger("main")
    logger.info("=== Trading system (rebuild stub) ===")
    logger.info(Fore.YELLOW + "Strategy/position logic removed. Rebuild per agent.md")

    api = initialize_api()
    symbol_manager = SymbolManager(api)
    try:
        symbol_manager.load_symbol_files()
    except FileNotFoundError:
        logger.warning("Symbol files not found, downloading...")
        symbol_manager.download_master_files()
        symbol_manager.load_symbol_files()

    collector = DataCollector(api, symbol_manager)
    collection_started = False

    if is_market_hours():
        collector.start_collection()
        collection_started = True
        logger.info("Data collection started (market open)")
    else:
        logger.info("Waiting for market hours (9:15–15:30 IST) before data collection...")

    try:
        while True:
            if is_market_closed_ist():
                logger.info("Market closed. Stopping.")
                if collector:
                    collector.stop_collection()
                break
            if not collection_started and is_market_hours():
                collector.start_collection()
                collection_started = True
                logger.info("Data collection started")
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Interrupted")
        if collector:
            collector.stop_collection()


if __name__ == "__main__":
    main()
