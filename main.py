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
from strategy_runner import run_strategy_with_regime, run_iron_condor_strategy, is_market_hours, get_nifty_spot_price, get_option_chain_data
from strategy_runner import get_all_eligible_expiries
from strategies.iron_condor.position_tracker import IronCondorPositionTracker
from technical_indicators import calculate_iv_percentile
from datetime import timedelta
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
        
        # Initialize position tracker
        position_tracker = IronCondorPositionTracker()
        logger.info("Position tracker initialized")
        
        # Run synthetic regime tests if enabled
        enable_synthetic_tests = os.getenv('ENABLE_SYNTHETIC_TESTS', 'False').lower() == 'true'
        enable_replay_mode = os.getenv('ENABLE_REPLAY_MODE', 'False').lower() == 'true'
        
        if enable_synthetic_tests or enable_replay_mode:
            try:
                logger.info("=" * 60)
                logger.info("Running Synthetic Regime Detection Tests")
                logger.info("=" * 60)
                from diagnostics.synthetic_regime_tests import run_synthetic_regime_tests
                test_summary = run_synthetic_regime_tests()
                
                logger.info("=" * 60)
                logger.info("Synthetic Tests Summary:")
                logger.info(f"  Total Cases: {test_summary['total_cases']}")
                logger.info(f"  Passed: {test_summary['passed']}")
                logger.info(f"  Failed: {test_summary['failed']}")
                if test_summary.get('sanity_errors', 0) > 0:
                    logger.info(f"  Sanity Errors: {test_summary['sanity_errors']}")
                logger.info("=" * 60)
                
                if test_summary['failed'] > 0 or test_summary.get('sanity_errors', 0) > 0:
                    logger.warning("Some synthetic tests failed, but continuing execution...")
                else:
                    logger.info("All synthetic tests passed!")
                
            except Exception as e:
                logger.error(f"Error running synthetic tests: {str(e)}", exc_info=True)
                logger.warning("Continuing execution despite test errors...")
        
        # Start data collection
        collector.start_collection()
        logger.info("Data collection started")
        
        # Strategy check timing
        STRATEGY_CHECK_INTERVAL = 300  # Check every 5 minutes (300 seconds)
        last_strategy_check = datetime.now()
        
        # Position monitoring timing
        POSITION_CHECK_INTERVAL = 60  # Check positions every 1 minute (60 seconds)
        last_position_check = datetime.now()
        
        # IV calculation timing (more frequent to build historical data faster)
        IV_CALCULATION_INTERVAL = 120  # Calculate IV every 2 minutes (120 seconds)
        last_iv_calculation = datetime.now()
        
        logger.info(Fore.CYAN + "Iron Condor strategy integration enabled")
        logger.info(f"Strategy checks will run every {STRATEGY_CHECK_INTERVAL // 60} minutes during market hours")
        logger.info(f"IV calculations will run every {IV_CALCULATION_INTERVAL // 60} minutes to build historical data")
        
        # Main loop - data collection and strategy checks
        try:
            while True:
                # Paper trading disabled - only collecting data
                # if trader:
                #     try:
                #         trader.process_market_data()
                #     except Exception as e:
                #         logger.error(f"Error in paper trader: {str(e)}")
                
                # Check if market has closed (after 3:30 PM)
                current_time = datetime.now()
                market_close_time = current_time.replace(hour=15, minute=30, second=0, microsecond=0)
                
                # If it's past 3:30 PM on a weekday, stop the system
                if current_time.weekday() < 5 and current_time >= market_close_time:
                    logger.info(Fore.YELLOW + "Market has closed (3:30 PM). Stopping system...")
                    # Stop data collection before exiting
                    if collector:
                        logger.info("Stopping data collection...")
                        collector.stop_collection()
                    logger.info("=== Data Collection System Stopped ===")
                    logger.info(f"End Time: {datetime.now()}")
                    runtime = datetime.now() - start_time
                    logger.info(f"Total Runtime: {runtime}")
                    break
                
                # Calculate IV more frequently to build historical data (independent of strategy checks)
                time_since_iv_calc = (current_time - last_iv_calculation).total_seconds()
                
                if time_since_iv_calc >= IV_CALCULATION_INTERVAL:
                    if is_market_hours():
                        try:
                            # Get spot price and nearest expiry for IV calculation
                            spot_price = get_nifty_spot_price(api, symbol_manager)
                            if spot_price:
                                # Get the nearest expiry (for IV calculation)
                                eligible_expiries = get_all_eligible_expiries(symbol_manager, max_expiries_to_check=1)
                                if eligible_expiries:
                                    expiry_date = eligible_expiries[0]
                                    # Get minimal option chain (just 10 strikes for IV calculation - lighter weight)
                                    option_chain = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=10)
                                    if not option_chain.empty:
                                        # Calculate days to expiry (expiry_date from get_all_eligible_expiries is a date object)
                                        from datetime import date
                                        if isinstance(expiry_date, date):
                                            days_to_expiry = (expiry_date - current_time.date()).days
                                        elif isinstance(expiry_date, datetime):
                                            days_to_expiry = (expiry_date.date() - current_time.date()).days
                                        else:
                                            # Fallback: try to parse as string
                                            expiry_date_obj = datetime.strptime(str(expiry_date), '%Y-%m-%d')
                                            days_to_expiry = (expiry_date_obj.date() - current_time.date()).days
                                        
                                        # Calculate and save IV (this will save to historical data)
                                        iv_percentile = calculate_iv_percentile(option_chain, spot_price, days_to_expiry)
                                        if iv_percentile is not None:
                                            logger.debug(f"IV calculated: {iv_percentile:.1f}% (saved for historical data)")
                        except Exception as e:
                            logger.debug(f"Error calculating IV (non-critical): {str(e)}")
                    
                    last_iv_calculation = current_time
                
                # Run Iron Condor strategy check periodically during market hours
                time_since_last_check = (current_time - last_strategy_check).total_seconds()
                
                if time_since_last_check >= STRATEGY_CHECK_INTERVAL:
                    if is_market_hours():
                        try:
                            logger.info(Fore.CYAN + "Running strategy check with regime detection...")
                            trade_proposal = run_strategy_with_regime(api, symbol_manager, position_tracker, capital=1000000.0)
                            if trade_proposal:
                                strategy_name = trade_proposal.get('strategy', 'UNKNOWN')
                                lots = trade_proposal.get('lots', 0)
                                
                                if strategy_name == 'CALL_BACKSPREAD':
                                    logger.info(Fore.GREEN + f"✅ Convex Backspread trade proposal generated: {lots} lots, "
                                              f"Debit: ₹{trade_proposal.get('net_debit_total', 0):.2f}")
                                    logger.info(f"   Max Loss: ₹{trade_proposal.get('max_loss', 0):.2f}")
                                else:
                                    logger.info(Fore.GREEN + f"✅ Iron Condor trade proposal generated: {lots} lots, "
                                              f"Credit: ₹{trade_proposal.get('net_credit_total', 0):.2f}")
                                    if trade_proposal.get('margin_used'):
                                        logger.info(f"   Margin used: ₹{trade_proposal['margin_used']:.2f}, "
                                                  f"Profit target: ₹{trade_proposal.get('profit_target_margin', 0):.2f}")
                            else:
                                logger.debug("No valid trade proposal generated")
                        except Exception as e:
                            logger.error(f"Error running Iron Condor strategy: {str(e)}", exc_info=True)
                    
                    last_strategy_check = current_time
                
                # Monitor open positions for profit taking
                time_since_position_check = (current_time - last_position_check).total_seconds()
                
                if time_since_position_check >= POSITION_CHECK_INTERVAL:
                    if is_market_hours():
                        try:
                            active_positions = position_tracker.get_active_positions()
                            
                            if active_positions:
                                logger.info(f"Checking {len(active_positions)} open position(s) for profit target...")
                                
                                # Get current spot price
                                spot_price = get_nifty_spot_price(api, symbol_manager)
                                if spot_price:
                                    # Check each position
                                    for position in active_positions[:]:  # Use slice to allow removal
                                        try:
                                            # Get expiry date
                                            expiry_date = datetime.strptime(position['expiry'], '%Y-%m-%d').date()
                                            
                                            # Get current option prices
                                            option_chain = get_option_chain_data(
                                                api, symbol_manager, spot_price, expiry_date, count=50
                                            )
                                            
                                            if option_chain.empty:
                                                logger.debug(f"No option chain for position {position['trade_id']}")
                                                continue
                                            
                                            # Build current prices dict
                                            current_prices = {}
                                            for leg in position['legs']:
                                                strike = int(leg['strike'])
                                                option_type = leg['option_type']
                                                
                                                # Find price in option chain
                                                leg_data = option_chain[
                                                    (option_chain['strike'] == strike) &
                                                    (option_chain['option_type'] == option_type)
                                                ]
                                                
                                                if not leg_data.empty:
                                                    current_prices[f"{option_type}{strike}"] = leg_data.iloc[0]['mid_price']
                                                else:
                                                    # Use entry price as fallback
                                                    current_prices[f"{option_type}{strike}"] = leg['price']
                                            
                                            # Calculate current P&L
                                            current_pnl = position_tracker.calculate_current_pnl(
                                                position, current_prices
                                            )
                                            
                                            # Check for convex exit conditions (if convex position)
                                            should_exit_convex = False
                                            convex_exit_reason = None
                                            if position.get('book') == 'CONVEX' or 'BACKSPREAD' in position.get('strategy', '').upper():
                                                # Get current regime for convex exit check
                                                from strategy_runner import build_market_state_from_chain
                                                from regime import RegimeDetector
                                                
                                                market_state = build_market_state_from_chain(
                                                    api, symbol_manager, spot_price, expiry_date, option_chain
                                                )
                                                if market_state:
                                                    regime_detector = RegimeDetector()
                                                    recent_candles = regime_detector.get_recent_candles(api, symbol_manager, spot_price)
                                                    regime_info = regime_detector.detect_regime(market_state, recent_candles, api, symbol_manager)
                                                    current_regime = regime_info.get('regime', 'NEUTRAL')
                                                    
                                                    # Calculate days to expiry
                                                    days_to_expiry = (expiry_date - current_time.date()).days
                                                    entry_days_to_expiry = position.get('days_to_expiry', days_to_expiry)
                                                    
                                                    # Get entry spot and range state
                                                    entry_spot = position.get('entry_spot', spot_price)
                                                    entry_range_state = position.get('entry_range_state', None)
                                                    
                                                    should_exit_convex, convex_exit_reason = position_tracker.check_convex_exit_conditions(
                                                        position,
                                                        current_regime,
                                                        spot_price,
                                                        entry_spot,
                                                        days_to_expiry,
                                                        entry_days_to_expiry,
                                                        current_atr_percentile=regime_info.get('atr_percentile'),
                                                        entry_range_state=entry_range_state,
                                                        current_range_state=regime_info.get('range_state')
                                                    )
                                            
                                            # Check for calendar exit conditions (if calendar position)
                                            should_exit_calendar = False
                                            calendar_exit_reason = None
                                            if position.get('book') == 'NEUTRAL' or 'CALENDAR' in position.get('strategy', '').upper():
                                                # Get current regime for calendar exit check
                                                from strategy_runner import build_market_state_from_chain
                                                from regime import RegimeDetector
                                                
                                                market_state = build_market_state_from_chain(
                                                    api, symbol_manager, spot_price, expiry_date, option_chain
                                                )
                                                if market_state:
                                                    regime_detector = RegimeDetector()
                                                    recent_candles = regime_detector.get_recent_candles(api, symbol_manager, spot_price)
                                                    regime_info = regime_detector.detect_regime(market_state, recent_candles, api, symbol_manager)
                                                    current_regime = regime_info.get('regime', 'NEUTRAL')
                                                    
                                                    # Calculate days to short expiry
                                                    days_to_expiry_short = (expiry_date - current_time.date()).days
                                                    entry_days_to_expiry_short = position.get('days_to_expiry_short', days_to_expiry_short)
                                                    
                                                    # Get entry spot and prices
                                                    entry_spot = position.get('entry_spot', spot_price)
                                                    entry_prices = position.get('entry_prices', {leg['option_type'] + str(int(leg['strike'])): leg['price'] for leg in position['legs']})
                                                    
                                                    # Get IV percentiles
                                                    current_iv_percentile = market_state.get('iv_percentile')
                                                    entry_iv_percentile = position.get('entry_iv_percentile', current_iv_percentile)
                                                    
                                                    should_exit_calendar, calendar_exit_reason = position_tracker.check_calendar_exit_conditions(
                                                        position,
                                                        current_regime,
                                                        spot_price,
                                                        entry_spot,
                                                        days_to_expiry_short,
                                                        entry_days_to_expiry_short,
                                                        current_prices,
                                                        entry_prices,
                                                        current_iv_percentile=current_iv_percentile,
                                                        entry_iv_percentile=entry_iv_percentile
                                                    )
                                            
                                            # Check profit target (1% of margin) for Iron Condor
                                            if not should_exit_convex and not should_exit_calendar and position_tracker.check_profit_target(position, current_pnl):
                                                logger.info(
                                                    f"✅ Profit target reached for position {position['trade_id']}: "
                                                    f"P&L=₹{current_pnl:.2f}, "
                                                    f"Target=₹{position.get('profit_target_margin', 0):.2f}"
                                                )
                                                
                                                # Close position
                                                position_tracker.close_position(
                                                    position, 
                                                    "profit_target_margin", 
                                                    current_pnl
                                                )
                                                
                                                # TODO: Execute actual exit orders via API
                                                # For now, just log and mark as closed
                                                logger.info(f"Position {position['trade_id']} marked for exit")
                                            
                                            # Check calendar exit conditions
                                            elif should_exit_calendar:
                                                logger.info(
                                                    f"⚠️ Calendar exit condition triggered for position {position['trade_id']}: "
                                                    f"{calendar_exit_reason}, P&L=₹{current_pnl:.2f}"
                                                )
                                                
                                                # Close position
                                                position_tracker.close_position(
                                                    position,
                                                    f"calendar_exit_{calendar_exit_reason}",
                                                    current_pnl
                                                )
                                                
                                                logger.info(f"Position {position['trade_id']} marked for exit (calendar)")
                                            
                                            # Check convex exit conditions
                                            elif should_exit_convex:
                                                logger.info(
                                                    f"⚠️ Convex exit condition triggered for position {position['trade_id']}: "
                                                    f"{convex_exit_reason}, P&L=₹{current_pnl:.2f}"
                                                )
                                                
                                                # Close position
                                                position_tracker.close_position(
                                                    position,
                                                    f"convex_exit_{convex_exit_reason}",
                                                    current_pnl
                                                )
                                                
                                                logger.info(f"Position {position['trade_id']} marked for exit (convex)")
                                            
                                            else:
                                                # Log current status
                                                margin_used = position.get('margin_used', 0)
                                                profit_target = margin_used * 0.01 if margin_used else 0
                                                logger.debug(
                                                    f"Position {position['trade_id']}: "
                                                    f"P&L=₹{current_pnl:.2f}, "
                                                    f"Target=₹{profit_target:.2f}"
                                                )
                                                
                                        except Exception as e:
                                            logger.error(f"Error checking position {position.get('trade_id', 'unknown')}: {e}")
                                            continue
                                            
                        except Exception as e:
                            logger.error(f"Error in position monitoring: {e}")
                    
                    last_position_check = current_time
                
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