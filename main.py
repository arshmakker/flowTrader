import os
import logging
import yaml
import time
import psutil
from datetime import datetime
from api_helper import ShoonyaApiPy
from symbol_manager import SymbolManager
from data_collector import DataCollector
from paper_trader import PaperTrader
from strategy_tester import StrategyTester
from strategy_runner import run_iron_condor_strategy, is_market_hours
from colorama import init, Fore, Style

try:
    init(autoreset=True)
except ImportError:
    print("colorama module not found. Please install it using 'pip install colorama'")
    exit(1)

def setup_logging():
    """Setup logging configuration"""
    log_dir = 'logs'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # File handler for detailed logging
    file_handler = logging.FileHandler(
        os.path.join(log_dir, f'trading_system_{datetime.now().strftime("%Y%m%d")}.log')
    )
    file_handler.setFormatter(detailed_formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler for important messages
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
    
    logging.info("Logging system initialized")

def log_system_info():
    """Log system information"""
    cpu_percent = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    logging.info(Fore.YELLOW + "=== System Information ===")
    logging.info(Fore.YELLOW + f"CPU Usage: {cpu_percent}%")
    logging.info(Fore.YELLOW + f"Memory Usage: {memory.percent}% (Used: {memory.used/1024/1024:.1f}MB, Available: {memory.available/1024/1024:.1f}MB)")
    logging.info(Fore.YELLOW + f"Disk Usage: {disk.percent}% (Used: {disk.used/1024/1024/1024:.1f}GB, Free: {disk.free/1024/1024/1024:.1f}GB)")
    logging.info(Fore.YELLOW + "========================")

def load_credentials():
    """Load API credentials from cred.yml"""
    try:
        logging.debug("Attempting to load credentials from cred.yml")
        with open('cred.yml', 'r') as file:
            creds = yaml.safe_load(file)
            logging.info(Fore.GREEN + "Credentials loaded successfully")
            return creds
    except FileNotFoundError:
        logging.error(Fore.RED + "cred.yml file not found. Please create it from the template.")
        return None
    except Exception as e:
        logging.error(Fore.RED + f"Error loading credentials: {str(e)}")
        return None

def initialize_api():
    """Initialize and connect to Shoonya API"""
    creds = load_credentials()
    if not creds:
        raise ValueError("Failed to load credentials")
    
    api = ShoonyaApiPy()
    try:
        logging.info("Attempting to login to Shoonya API...")
        logging.debug(f"Using credentials - User: {creds['user']}, Vendor: {creds['vc']}")
        
        # Always prompt for 2FA code
        factor2 = input(Fore.CYAN + "Enter your 2FA code: ")
        
        if not factor2:
            logging.error(Fore.RED + "2FA code is required")
            raise ValueError("2FA code is required")
        
        # Ensure factor2 is a string (API requires string format)
        factor2 = str(factor2).strip()
        
        # Attempt login
        logging.info("Attempting to login to Shoonya API...")
        login_status = api.login(
            userid=creds['user'],
            password=creds['pwd'],
            twoFA=factor2,
            vendor_code=creds['vc'],
            api_secret=creds['apikey'],
            imei=creds['imei']
        )
        
        if login_status:
            logging.info(Fore.GREEN + "Successfully logged in to Shoonya API")
            logging.debug(f"Login response: {login_status}")
            return api
        else:
            logging.error(Fore.RED + "Login failed - API returned False")
            logging.debug("Please verify your credentials and API connectivity")
            raise ValueError("Login failed")
            
    except Exception as e:
        logging.error(Fore.RED + f"Error initializing API: {str(e)}", exc_info=True)
        logging.debug("Check if the API service is available and credentials are correct")
        raise

def main():
    collector = None
    trader = None
    start_time = datetime.now()
    
    try:
        # Initialize logging
        setup_logging()
        logger = logging.getLogger('main')
        logger.info("=== Starting Data Collection System ===")
        logger.info(f"Start Time: {datetime.now()}")
        
        # Initialize API
        api = initialize_api()
        if not api:
            logger.error("Failed to initialize API")
            return
            
        # Initialize components
        symbol_manager = SymbolManager(api)
        try:
            symbol_manager.load_symbol_files()
            
            # Scan for ETFs
            logger.info("Scanning for ETFs across exchanges...")
            etf_analysis = symbol_manager.scan_common_etfs()
            if etf_analysis:
                logger.info("ETF scan complete. Found:")
                logger.info(f"- {etf_analysis['nse_total']} ETFs in NSE")
                logger.info(f"- {etf_analysis['bse_total']} ETFs in BSE")
                logger.info(f"- {len(etf_analysis['common'])} common ETFs")
            
        except FileNotFoundError:
            logger.warning("Symbol files not found, downloading from exchange...")
            symbol_manager.download_master_files()
            symbol_manager.load_symbol_files()
            
        collector = DataCollector(api, symbol_manager)
        # Paper trading disabled for data collection focus
        trader = None
        
        # Start data collection
        collector.start_collection()
        logger.info("Data collection started")
        
        # Strategy check timing
        STRATEGY_CHECK_INTERVAL = 300  # Check every 5 minutes (300 seconds)
        last_strategy_check = datetime.now()
        
        logger.info(Fore.CYAN + "Iron Condor strategy integration enabled")
        logger.info(f"Strategy checks will run every {STRATEGY_CHECK_INTERVAL // 60} minutes during market hours")
        
        # Main loop - data collection and strategy checks
        try:
            while True:
                # Paper trading disabled - only collecting data
                # if trader:
                #     try:
                #         trader.process_market_data()
                #     except Exception as e:
                #         logger.error(f"Error in paper trader: {str(e)}")
                
                # Run Iron Condor strategy check periodically during market hours
                current_time = datetime.now()
                time_since_last_check = (current_time - last_strategy_check).total_seconds()
                
                if time_since_last_check >= STRATEGY_CHECK_INTERVAL:
                    if is_market_hours():
                        try:
                            logger.info(Fore.CYAN + "Running Iron Condor strategy check...")
                            trade_proposal = run_iron_condor_strategy(api, symbol_manager)
                            if trade_proposal:
                                logger.info(Fore.GREEN + f"✅ Trade proposal generated: {trade_proposal['lots']} lots, "
                                          f"Credit: ₹{trade_proposal['net_credit_total']:.2f}")
                            else:
                                logger.debug("No valid trade proposal generated")
                        except Exception as e:
                            logger.error(f"Error running Iron Condor strategy: {str(e)}", exc_info=True)
                    
                    last_strategy_check = current_time
                
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("\n=== Received shutdown signal, cleaning up... ===")
            
            # Stop data collection first
            if collector:
                logger.info("Stopping data collection...")
                collector.stop_collection()
            
            # Save final trading state
            if trader:
                try:
                    # Log final positions
                    positions = trader.get_positions()
                    if positions:
                        logger.info("Final Positions:")
                        for pos in positions:
                            logger.info(
                                f"{pos['symbol']}: {pos['quantity']} units @ {pos['current_price']:.2f} "
                                f"(Value: {pos['value']:.2f})"
                            )
                    
                    # Log final summary
                    summary = trader.get_position_summary()
                    logger.info(f"Final Trading Summary: {summary}")
                    
                    # Save trading statistics
                    trader.save_trading_stats()
                    
                except Exception as e:
                    logger.error(f"Error saving final trading state: {str(e)}")
            
            logger.info("=== Data Collection System Stopped ===")
            logger.info(f"End Time: {datetime.now()}")
            runtime = datetime.now() - start_time
            logger.info(f"Total Runtime: {runtime}")
            
    except Exception as e:
        logger.error(f"Critical error in main: {str(e)}", exc_info=True)
        
    finally:
        # Ensure cleanup happens even on error
        if collector:
            try:
                collector.stop_collection()
                logger.info("Data collection stopped")
            except:
                pass
                
        if trader:
            try:
                trader.save_trading_stats()
                logger.info("Trading statistics saved")
            except:
                pass
                
        logger.info("=== Cleanup complete ===")

if __name__ == "__main__":
    start_time = datetime.now()
    main()