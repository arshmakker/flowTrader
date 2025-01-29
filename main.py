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
        
        # Prompt for 2FA code
        factor2 = input(Fore.CYAN + "Enter your 2FA code: ")
        if not factor2:
            logging.error(Fore.RED + "2FA code is required")
            raise ValueError("2FA code is required")
            
        # Login to API
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
    """Main function to run the trading system"""
    collector = None
    trader = None
    start_time = datetime.now()
    
    try:
        # Setup logging
        setup_logging()
        logging.info(Fore.CYAN + "=== Trading System Starting ===")
        logging.info(Fore.CYAN + f"Start Time: {start_time}")
        
        # Log system information
        log_system_info()
        
        # Initialize API
        logging.info("Initializing API connection...")
        api = initialize_api()
        
        # Initialize components
        logging.info("Initializing symbol manager...")
        symbol_manager = SymbolManager(api)
        symbol_manager.load_symbol_files()
        
        # Initialize data collector
        logging.info("Initializing data collector...")
        collector = DataCollector(api)
        
        # Start collecting data for index futures
        logging.info("Starting data collection for index futures...")
        collector.start_collection()
        
        # Initialize paper trader with ₹9,00,000 capital
        logging.info(Fore.GREEN + "Initializing paper trader with capital: ₹9,00,000")
        trader = PaperTrader(data_collector=collector, initial_capital=900000)
        
        # Run until interrupted
        logging.info(Fore.CYAN + "=== System Running ===")
        last_system_info = datetime.now()
        
        try:
            while True:
                current_time = datetime.now()
                runtime = current_time - start_time
                
                # Process market data and check positions
                if trader.is_trading_time():
                    trader.process_market_data()
                
                # Log system info every 5 minutes
                if (current_time - last_system_info).total_seconds() >= 300:
                    log_system_info()
                    last_system_info = current_time
                
                # Get and log position summary
                summary = trader.get_position_summary()
                logging.info(Fore.GREEN + f"Runtime: {runtime} - Position Summary: {summary}")
                
                time.sleep(5)
                
        except KeyboardInterrupt:
            logging.info(Fore.RED + "\n=== Graceful Shutdown Initiated ===")
            if collector:
                logging.info("Stopping data collection...")
                collector.stop_collection()
            
            if trader:
                final_summary = trader.get_position_summary()
                logging.info(Fore.YELLOW + f"Final Position Summary: {final_summary}")
            
            logging.info(Fore.CYAN + f"Total Runtime: {datetime.now() - start_time}")
            
    except Exception as e:
        logging.error(Fore.RED + f"Critical error in main: {str(e)}", exc_info=True)
        if collector:
            logging.info("Stopping data collection due to error...")
            collector.stop_collection()
            
    finally:
        if collector:
            try:
                collector.stop_collection()
            except:
                pass
        
        if trader:
            try:
                final_summary = trader.get_position_summary()
                logging.info(Fore.YELLOW + f"Final Trading Summary: {final_summary}")
            except:
                pass
                
        logging.info(Fore.CYAN + "=== Trading System Stopped ===")
        logging.info(Fore.CYAN + f"End Time: {datetime.now()}")
        logging.info(Fore.CYAN + f"Total Runtime: {datetime.now() - start_time}")

if __name__ == "__main__":
    main()