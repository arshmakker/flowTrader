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
from strategy_runner import run_strategy_with_regime, run_iron_condor_strategy, is_market_hours, is_market_closed_ist, get_now_ist, get_nifty_spot_price, get_option_chain_data
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

import json

def generate_daily_trade_summary(logger):
    """
    Generate end-of-day trade summary from active_positions.json
    
    Returns a summary dict with today's trade statistics
    """
    try:
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        # Load positions
        positions_file = 'active_positions.json'
        if not os.path.exists(positions_file):
            logger.info("No positions file found for summary")
            return None
        
        with open(positions_file, 'r') as f:
            all_positions = json.load(f)
        
        # Filter today's closed trades
        todays_trades = []
        for pos in all_positions:
            exit_time = pos.get('exit_time', '')
            if exit_time and exit_time.startswith(today_str) and pos.get('status') == 'CLOSED':
                todays_trades.append(pos)
        
        if not todays_trades:
            logger.info(f"\n{'='*60}")
            logger.info(Fore.YELLOW + "END OF DAY SUMMARY")
            logger.info(f"{'='*60}")
            logger.info("No trades were executed today")
            logger.info(f"{'='*60}\n")
            return None
        
        # Calculate statistics
        total_pnl = sum(t.get('final_pnl', 0) for t in todays_trades)
        winning_trades = [t for t in todays_trades if t.get('final_pnl', 0) > 0]
        losing_trades = [t for t in todays_trades if t.get('final_pnl', 0) < 0]
        breakeven_trades = [t for t in todays_trades if t.get('final_pnl', 0) == 0]
        
        win_rate = (len(winning_trades) / len(todays_trades)) * 100 if todays_trades else 0
        
        avg_winner = sum(t.get('final_pnl', 0) for t in winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loser = sum(t.get('final_pnl', 0) for t in losing_trades) / len(losing_trades) if losing_trades else 0
        
        max_profit = max((t.get('final_pnl', 0) for t in todays_trades), default=0)
        max_loss = min((t.get('final_pnl', 0) for t in todays_trades), default=0)
        
        # Group by strategy
        by_strategy = {}
        for t in todays_trades:
            strat = t.get('strategy', 'UNKNOWN')
            if strat not in by_strategy:
                by_strategy[strat] = {'count': 0, 'pnl': 0}
            by_strategy[strat]['count'] += 1
            by_strategy[strat]['pnl'] += t.get('final_pnl', 0)
        
        # Group by exit reason
        by_exit_reason = {}
        for t in todays_trades:
            reason = t.get('exit_reason', 'UNKNOWN')
            # Clean up the reason for display
            if 'futures_exit_' in reason:
                reason = reason.replace('futures_exit_', '')
            if reason not in by_exit_reason:
                by_exit_reason[reason] = {'count': 0, 'pnl': 0}
            by_exit_reason[reason]['count'] += 1
            by_exit_reason[reason]['pnl'] += t.get('final_pnl', 0)
        
        # Print summary
        logger.info(f"\n{'='*60}")
        logger.info(Fore.CYAN + Style.BRIGHT + "END OF DAY TRADE SUMMARY")
        logger.info(f"Date: {today_str}")
        logger.info(f"{'='*60}")
        
        # Overall stats
        pnl_color = Fore.GREEN if total_pnl >= 0 else Fore.RED
        logger.info(f"\n{Fore.WHITE}Overall Performance:")
        logger.info(f"  Total Trades:    {len(todays_trades)}")
        logger.info(f"  Winning:         {len(winning_trades)}")
        logger.info(f"  Losing:          {len(losing_trades)}")
        logger.info(f"  Breakeven:       {len(breakeven_trades)}")
        logger.info(f"  Win Rate:        {win_rate:.1f}%")
        logger.info(f"  {pnl_color}Total P&L:       ₹{total_pnl:,.2f}")
        
        if winning_trades:
            logger.info(f"  {Fore.GREEN}Avg Winner:      ₹{avg_winner:,.2f}")
        if losing_trades:
            logger.info(f"  {Fore.RED}Avg Loser:       ₹{avg_loser:,.2f}")
        logger.info(f"  Max Profit:      ₹{max_profit:,.2f}")
        logger.info(f"  Max Loss:        ₹{max_loss:,.2f}")
        
        # By strategy
        logger.info(f"\n{Fore.WHITE}By Strategy:")
        for strat, stats in by_strategy.items():
            strat_color = Fore.GREEN if stats['pnl'] >= 0 else Fore.RED
            logger.info(f"  {strat}: {stats['count']} trades, {strat_color}₹{stats['pnl']:,.2f}")
        
        # By exit reason
        logger.info(f"\n{Fore.WHITE}By Exit Reason:")
        for reason, stats in by_exit_reason.items():
            reason_color = Fore.GREEN if stats['pnl'] >= 0 else Fore.RED
            logger.info(f"  {reason}: {stats['count']} trades, {reason_color}₹{stats['pnl']:,.2f}")
        
        logger.info(f"\n{'='*60}")
        
        # Final verdict
        if total_pnl > 0:
            logger.info(Fore.GREEN + Style.BRIGHT + f"✅ PROFITABLE DAY: +₹{total_pnl:,.2f}")
        elif total_pnl < 0:
            logger.info(Fore.RED + Style.BRIGHT + f"❌ LOSS DAY: ₹{total_pnl:,.2f}")
        else:
            logger.info(Fore.YELLOW + Style.BRIGHT + "➖ BREAKEVEN DAY")
        
        logger.info(f"{'='*60}\n")
        
        return {
            'date': today_str,
            'total_trades': len(todays_trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_winner': avg_winner,
            'avg_loser': avg_loser,
            'max_profit': max_profit,
            'max_loss': max_loss,
            'by_strategy': by_strategy,
            'by_exit_reason': by_exit_reason
        }
        
    except Exception as e:
        logger.error(f"Error generating trade summary: {str(e)}")
        return None

def setup_logging():
    """Setup logging configuration"""
    log_dir = 'logs'
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'trading_system_{datetime.now().strftime("%Y%m%d")}.log')
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    )
    
    # File handler for detailed logging
    file_handler = logging.FileHandler(log_path)
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
    root_logger.info(f"Log file: {os.path.abspath(log_path)}")
    
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
        
        # Data collection will start only during market hours (no API calls before 9:15 AM)
        collection_started = False
        if is_market_hours():
            collector.start_collection()
            collection_started = True
            logger.info("Data collection started (market is open)")
        else:
            logger.info(Fore.YELLOW + "Waiting for market to open (9:15 AM) before starting data collection...")
            logger.info("No API calls will be made until market hours")
        
        # Strategy check timing
        STRATEGY_CHECK_INTERVAL = 300  # Check every 5 minutes (300 seconds)
        last_strategy_check = datetime.now()
        
        # Position monitoring timing
        POSITION_CHECK_INTERVAL = 60  # Check positions every 1 minute (60 seconds)
        last_position_check = datetime.now()
        
        # IV calculation timing (more frequent to build historical data faster)
        IV_CALCULATION_INTERVAL = 120  # Calculate IV every 2 minutes (120 seconds)
        last_iv_calculation = datetime.now()
        
        # Flag to prevent re-entry after market close exit
        # Once we exit a position due to MARKET_CLOSE_APPROACHING, don't enter new trades for the rest of the day
        market_close_exit_triggered = False
        
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
                
                # Check if market has closed (3:30 PM IST)
                current_time = datetime.now()
                if is_market_closed_ist():
                    logger.info(Fore.YELLOW + "Market has closed (3:30 PM IST). Stopping system...")
                    
                    # Generate end-of-day trade summary
                    generate_daily_trade_summary(logger)
                    
                    # Stop data collection before exiting
                    if collector:
                        logger.info("Stopping data collection...")
                        collector.stop_collection()
                    logger.info("=== Data Collection System Stopped ===")
                    logger.info(f"End Time: {datetime.now()}")
                    runtime = datetime.now() - start_time
                    logger.info(f"Total Runtime: {runtime}")
                    break
                
                # Start data collection when market opens (if not already started)
                if not collection_started and is_market_hours():
                    logger.info(Fore.GREEN + "Market is now open! Starting data collection...")
                    collector.start_collection()
                    collection_started = True
                    logger.info("Data collection started")
                
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
                    # Skip strategy checks if we already exited for market close
                    if market_close_exit_triggered:
                        logger.debug("Skipping strategy check - market close exit already triggered, no new entries today")
                    elif is_market_hours():
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
                                    # #region agent log
                                    import json
                                    try:
                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                            f.write(json.dumps({"location":"main.py:307","message":"Iron Condor trade logged","data":{"lots":lots,"net_credit_total":trade_proposal.get('net_credit_total'),"net_credit":trade_proposal.get('net_credit'),"has_net_credit_total":'net_credit_total' in trade_proposal,"trade_proposal_keys":list(trade_proposal.keys())},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"credit-debug","hypothesisId":"C4"})+"\n")
                                    except: pass
                                    # #endregion
                                    logger.info(Fore.GREEN + f"✅ Iron Condor trade proposal generated: {lots} lots, "
                                              f"Credit: ₹{trade_proposal.get('net_credit_total', 0):.2f}")
                                    if trade_proposal.get('margin_used'):
                                        logger.info(f"   Margin used: ₹{trade_proposal['margin_used']:.2f}, exit by TSL only")
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
                                            # Check if this is a futures strategy (no expiry/legs)
                                            strategy = position.get('strategy', '').upper()
                                            is_futures_strategy = 'FUTURE' in strategy or position.get('instrument', '').upper() == 'NIFTY_FUTURE'
                                            
                                            if is_futures_strategy:
                                                # Check exit conditions for futures positions (Trend Following)
                                                from strategies.trend.trend_follow_futures import get_nifty_futures_price, get_ema_structure
                                                from strategies.trend.config import TRAILING_STOP_LOSS_ATR_MULTIPLIER, EXIT_ON_REGIME_CHANGE, EXIT_ON_EMA_BREAK
                                                from strategy_runner import build_market_state_from_chain
                                                from regime import RegimeDetector
                                                
                                                # #region agent log
                                                import json
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:347","message":"Entering futures position monitoring","data":{"position_id":position.get('trade_id'),"strategy":strategy,"is_futures_strategy":is_futures_strategy},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H5"})+"\n")
                                                except: pass
                                                # #endregion
                                                
                                                # Get current futures price
                                                current_futures_price = get_nifty_futures_price(api, symbol_manager)
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:356","message":"Futures price result","data":{"current_futures_price":current_futures_price,"is_none":current_futures_price is None,"type":type(current_futures_price).__name__,"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H1"})+"\n")
                                                except: pass
                                                # #endregion
                                                if not current_futures_price:
                                                    logger.debug(f"Could not get futures price for position {position['trade_id']}")
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:360","message":"Skipping position - no futures price","data":{"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H1"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                    continue
                                                
                                                # Get current market state for regime and indicators
                                                # Use a dummy expiry for market state (futures don't have expiry)
                                                # We'll use the first available expiry just for market state calculation
                                                available_expiries = get_all_eligible_expiries(symbol_manager, max_expiries_to_check=1)
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:363","message":"Available expiries result","data":{"available_expiries":available_expiries,"is_empty":not available_expiries,"len":len(available_expiries) if available_expiries else 0,"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H2"})+"\n")
                                                except: pass
                                                # #endregion
                                                if not available_expiries:
                                                    logger.debug(f"Could not get expiry for market state calculation")
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:368","message":"Skipping position - no available expiries","data":{"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H2"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                    continue
                                                
                                                expiry_date = available_expiries[0]
                                                option_chain = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=30)
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:375","message":"Option chain result","data":{"option_chain_empty":option_chain.empty if hasattr(option_chain, 'empty') else True,"option_chain_len":len(option_chain) if hasattr(option_chain, '__len__') else 0,"expiry_date":str(expiry_date),"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H3"})+"\n")
                                                except: pass
                                                # #endregion
                                                if option_chain.empty:
                                                    logger.debug(f"Could not get option chain for market state")
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:380","message":"Skipping position - empty option chain","data":{"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H3"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                    continue
                                                
                                                market_state = build_market_state_from_chain(api, symbol_manager, spot_price, expiry_date, option_chain)
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:386","message":"Market state result","data":{"market_state_is_none":market_state is None,"has_market_state":market_state is not None,"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H4"})+"\n")
                                                except: pass
                                                # #endregion
                                                if not market_state:
                                                    logger.debug(f"Could not build market state for futures position")
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:392","message":"Skipping position - no market state","data":{"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H4"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                    continue
                                                
                                                # Get regime
                                                regime_detector = RegimeDetector()
                                                recent_candles = regime_detector.get_recent_candles(api, symbol_manager, spot_price, lookback_days=20)
                                                regime_info = regime_detector.detect_regime(market_state, recent_candles, api, symbol_manager)
                                                current_regime = regime_info.get('regime', 'NEUTRAL')
                                                current_atr = regime_info.get('atr', position.get('atr', 0))
                                                
                                                # Get EMA structure
                                                # #region agent log
                                                import json
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:387","message":"Getting EMA structure for position monitoring","data":{"position_id":position.get('trade_id'),"direction":position.get('direction')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H1"})+"\n")
                                                except: pass
                                                # #endregion
                                                ema_structure = get_ema_structure(api, symbol_manager, spot_price)
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:390","message":"EMA structure result","data":{"ema_structure_is_none":ema_structure is None,"has_ema_50":ema_structure.get('ema_50') is not None if ema_structure else False,"has_ema_100":ema_structure.get('ema_100') is not None if ema_structure else False,"ema_50":ema_structure.get('ema_50') if ema_structure else None,"ema_100":ema_structure.get('ema_100') if ema_structure else None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H1"})+"\n")
                                                except: pass
                                                # #endregion
                                                
                                                # Get position details
                                                direction = position.get('direction')
                                                entry_price = position.get('entry_price')
                                                initial_stop = position.get('stop_loss_price')
                                                # Use current_stop_price if available, otherwise fall back to initial stop
                                                current_stop = position.get('current_stop_price')
                                                if current_stop is None:
                                                    current_stop = initial_stop
                                                    # Initialize current_stop_price if not set
                                                    position['current_stop_price'] = initial_stop
                                                    position_tracker._save_active_positions()
                                                
                                                # Calculate current P&L
                                                if direction == 'LONG':
                                                    current_pnl = (current_futures_price - entry_price) * position.get('quantity', 0)
                                                else:  # SHORT
                                                    current_pnl = (entry_price - current_futures_price) * position.get('quantity', 0)
                                                
                                                # Calculate position age (for logging and minimum duration check)
                                                entry_time = datetime.fromisoformat(position['entry_time'])
                                                current_time = datetime.now()
                                                position_age = current_time - entry_time
                                                position_age_minutes = position_age.total_seconds() / 60
                                                
                                                should_exit = False
                                                exit_reason = None
                                                
                                                # Exit condition 0: Market close approaching (3:30 PM IST)
                                                if not should_exit:
                                                    from strategies.trend.config import EXIT_BEFORE_MARKET_CLOSE, MARKET_CLOSE_EXIT_MINUTES
                                                    if EXIT_BEFORE_MARKET_CLOSE:
                                                        now_ist = get_now_ist()
                                                        market_close_time = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
                                                        exit_before_close_time = market_close_time - timedelta(minutes=MARKET_CLOSE_EXIT_MINUTES)
                                                        # Exit all futures positions N minutes before market close (IST)
                                                        if now_ist.weekday() < 5 and now_ist >= exit_before_close_time:
                                                            should_exit = True
                                                            exit_reason = 'MARKET_CLOSE_APPROACHING'
                                                            # Set flag to prevent re-entry for rest of day
                                                            market_close_exit_triggered = True
                                                            logger.info(
                                                                f"Closing futures position {position['trade_id']} before market close "
                                                                f"({MARKET_CLOSE_EXIT_MINUTES} minutes remaining). "
                                                                f"No new entries will be made today."
                                                            )
                                                
                                                # Exit condition 1: Stop loss hit
                                                if direction == 'LONG':
                                                    if current_futures_price <= current_stop:
                                                        should_exit = True
                                                        exit_reason = 'STOP_LOSS_HIT'
                                                else:  # SHORT
                                                    if current_futures_price >= current_stop:
                                                        should_exit = True
                                                        exit_reason = 'STOP_LOSS_HIT'
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:418","message":"After stop loss check","data":{"should_exit":should_exit,"exit_reason":exit_reason,"current_price":current_futures_price,"stop_loss":current_stop,"direction":direction},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H4"})+"\n")
                                                except: pass
                                                # #endregion
                                                
                                                # Exit condition 1b: Max intraday loss (circuit breaker)
                                                if not should_exit:
                                                    from strategies.trend.config import MAX_INTRADAY_LOSS_INR
                                                    if current_pnl <= -MAX_INTRADAY_LOSS_INR:
                                                        should_exit = True
                                                        exit_reason = 'MAX_LOSS_CAP'
                                                        logger.info(
                                                            f"Futures position {position['trade_id']}: unrealized loss ₹{current_pnl:.2f} "
                                                            f"exceeds max cap ₹{MAX_INTRADAY_LOSS_INR}, closing position"
                                                        )
                                                
                                                # Exit condition 1c: Time in loss (avoid holding wrong trade all day)
                                                if not should_exit:
                                                    from strategies.trend.config import MAX_TIME_IN_LOSS_MINUTES
                                                    if current_pnl < 0 and position_age_minutes >= MAX_TIME_IN_LOSS_MINUTES:
                                                        should_exit = True
                                                        exit_reason = 'TIME_IN_LOSS'
                                                        logger.info(
                                                            f"Futures position {position['trade_id']}: in loss (₹{current_pnl:.2f}) for "
                                                            f"{position_age_minutes:.0f} min (≥ {MAX_TIME_IN_LOSS_MINUTES} min), closing position"
                                                        )
                                                
                                                # Exit condition 2: Regime change (with confirmation to reduce whipsaw)
                                                if not should_exit and EXIT_ON_REGIME_CHANGE:
                                                    from strategies.trend.config import (
                                                        REGIME_CHANGE_CONFIRMATION_CHECKS,
                                                        IGNORE_REGIME_CHANGE_WHEN_IN_PROFIT,
                                                    )
                                                    # When in profit, optionally skip regime-change exit (reduce regime-change losses)
                                                    if IGNORE_REGIME_CHANGE_WHEN_IN_PROFIT and current_pnl > 0:
                                                        pass  # do not exit on regime change; let trailing stop / other exits handle
                                                    else:
                                                        if 'regime_change_count' not in position:
                                                            position['regime_change_count'] = 0
                                                            position_tracker._save_active_positions()
                                                        if current_regime != 'TREND_CONTINUATION':
                                                            position['regime_change_count'] = position.get('regime_change_count', 0) + 1
                                                            position_tracker._save_active_positions()
                                                            if position['regime_change_count'] >= REGIME_CHANGE_CONFIRMATION_CHECKS:
                                                                should_exit = True
                                                                exit_reason = 'REGIME_CHANGE'
                                                                logger.info(
                                                                    f"Futures position {position['trade_id']}: regime {current_regime} for "
                                                                    f"{position['regime_change_count']} consecutive checks, closing position"
                                                                )
                                                        else:
                                                            if position.get('regime_change_count', 0) > 0:
                                                                position['regime_change_count'] = 0
                                                                position_tracker._save_active_positions()
                                                                logger.debug(f"Regime restored to TREND_CONTINUATION, reset regime_change_count for {position['trade_id']}")
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:424","message":"After regime change check","data":{"should_exit":should_exit,"exit_reason":exit_reason,"current_regime":current_regime,"EXIT_ON_REGIME_CHANGE":EXIT_ON_REGIME_CHANGE},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H4"})+"\n")
                                                except: pass
                                                # #endregion
                                                
                                                # Exit condition 2b: ATR profit target (optional; None = trailing only)
                                                if not should_exit and current_atr > 0:
                                                    from strategies.trend.config import PROFIT_TARGET_ATR_MULTIPLIER
                                                    if PROFIT_TARGET_ATR_MULTIPLIER is not None:
                                                        qty = position.get('quantity', 0)
                                                        if qty and qty > 0:
                                                            unrealized_pnl_points = abs(current_pnl) / qty if current_pnl > 0 else 0
                                                            if current_pnl > 0 and unrealized_pnl_points >= (current_atr * PROFIT_TARGET_ATR_MULTIPLIER):
                                                                should_exit = True
                                                                exit_reason = 'PROFIT_TARGET_ATR'
                                                                logger.info(
                                                                    f"ATR profit target hit: P&L=₹{current_pnl:.2f} (≥ {PROFIT_TARGET_ATR_MULTIPLIER}× ATR = {current_atr * PROFIT_TARGET_ATR_MULTIPLIER:.1f} pts), closing position"
                                                                )
                                                
                                                # Exit condition 3: Expiry date approaching
                                                if not should_exit:
                                                    from strategies.trend.config import EXIT_DAYS_BEFORE_EXPIRY
                                                    expiry_str = position.get('expiry')
                                                    if expiry_str:
                                                        try:
                                                            expiry_date = datetime.fromisoformat(expiry_str).date() if isinstance(expiry_str, str) else expiry_str
                                                            today = datetime.now().date()
                                                            days_to_expiry = (expiry_date - today).days
                                                            
                                                            if days_to_expiry <= EXIT_DAYS_BEFORE_EXPIRY:
                                                                should_exit = True
                                                                exit_reason = f'EXPIRY_APPROACHING (expires in {days_to_expiry} days)'
                                                                logger.info(
                                                                    f"Futures position {position['trade_id']}: "
                                                                    f"Expiry approaching ({days_to_expiry} days), closing position"
                                                                )
                                                        except Exception as e:
                                                            logger.debug(f"Error parsing expiry date: {str(e)}")
                                                
                                                # Exit condition 4: EMA structure breaks (with confirmation and smart logic)
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:427","message":"Before EMA structure check","data":{"should_exit":should_exit,"EXIT_ON_EMA_BREAK":EXIT_ON_EMA_BREAK,"ema_structure_is_none":ema_structure is None,"will_check_ema":not should_exit and EXIT_ON_EMA_BREAK and ema_structure is not None},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H1"})+"\n")
                                                except: pass
                                                # #endregion
                                                if not should_exit and EXIT_ON_EMA_BREAK and ema_structure:
                                                    from strategies.trend.config import (
                                                        EMA_BREAK_CONFIRMATION_CHECKS,
                                                        EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS,
                                                        EMA_BREAK_TOLERANCE_PCT,
                                                        PRIORITIZE_TRAILING_STOP_IN_PROFIT,
                                                        TRAILING_STOP_PRIORITY_DISTANCE_ATR
                                                    )
                                                    # When in loss, require fewer confirmations for faster exit on trend flip
                                                    ema_required_checks = EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS if current_pnl < 0 else EMA_BREAK_CONFIRMATION_CHECKS
                                                    
                                                    ema_50 = ema_structure.get('ema_50')
                                                    ema_100 = ema_structure.get('ema_100')
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:432","message":"Inside EMA structure check","data":{"ema_50":ema_50,"ema_100":ema_100,"has_both_values":ema_50 is not None and ema_100 is not None,"direction":direction,"current_futures_price":current_futures_price},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H2"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                    
                                                    if ema_50 and ema_100:
                                                        # Initialize EMA break count if not present
                                                        if 'ema_break_count' not in position:
                                                            position['ema_break_count'] = 0
                                                            position_tracker._save_active_positions()
                                                        
                                                        # Check EMA structure with tolerance
                                                        if direction == 'LONG':
                                                            price_above_ema50 = current_futures_price > ema_50 * (1 - EMA_BREAK_TOLERANCE_PCT / 100)
                                                            ema50_above_ema100 = ema_50 > ema_100 * (1 - EMA_BREAK_TOLERANCE_PCT / 100)
                                                            structure_valid = price_above_ema50 and ema50_above_ema100
                                                        else:  # SHORT
                                                            price_below_ema50 = current_futures_price < ema_50 * (1 + EMA_BREAK_TOLERANCE_PCT / 100)
                                                            ema50_below_ema100 = ema_50 < ema_100 * (1 + EMA_BREAK_TOLERANCE_PCT / 100)
                                                            structure_valid = price_below_ema50 and ema50_below_ema100
                                                        
                                                        # #region agent log
                                                        try:
                                                            with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                                f.write(json.dumps({"location":"main.py:436","message":"EMA structure check with tolerance","data":{"structure_valid":structure_valid,"price":current_futures_price,"ema_50":ema_50,"ema_100":ema_100,"direction":direction,"tolerance_pct":EMA_BREAK_TOLERANCE_PCT},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H3"})+"\n")
                                                        except: pass
                                                        # #endregion
                                                        
                                                        if not structure_valid:
                                                            # Increment break count
                                                            position['ema_break_count'] = position.get('ema_break_count', 0) + 1
                                                            position_tracker._save_active_positions()
                                                            
                                                            # Smart exit logic: Check if we should exit based on profit and distance to stop
                                                            in_profit = current_pnl > 0
                                                            distance_to_stop = 0
                                                            if direction == 'LONG':
                                                                distance_to_stop = current_futures_price - current_stop
                                                            else:  # SHORT
                                                                distance_to_stop = current_stop - current_futures_price
                                                            
                                                            close_to_stop = distance_to_stop < (current_atr * TRAILING_STOP_PRIORITY_DISTANCE_ATR) if current_atr > 0 else False
                                                            
                                                            # Decision logic:
                                                            # - If in profit AND far from stop AND prioritizing trailing stop: ignore EMA break
                                                            # - Otherwise: check confirmation count
                                                            should_exit_on_ema = False
                                                            
                                                            if PRIORITIZE_TRAILING_STOP_IN_PROFIT and in_profit and not close_to_stop:
                                                                # In profit and far from stop - ignore EMA break, let trailing stop handle it
                                                                logger.debug(
                                                                    f"EMA structure broken but position in profit (₹{current_pnl:.2f}) "
                                                                    f"and far from stop (₹{distance_to_stop:.2f}), letting trailing stop handle exit. "
                                                                    f"Break count: {position['ema_break_count']}/{ema_required_checks}"
                                                                )
                                                                # Reset break count since we're ignoring it
                                                                position['ema_break_count'] = 0
                                                                position_tracker._save_active_positions()
                                                            else:
                                                                # In loss OR close to stop - check confirmation count
                                                                if position['ema_break_count'] >= ema_required_checks:
                                                                    should_exit_on_ema = True
                                                                    logger.info(
                                                                        f"EMA structure broken for {position['ema_break_count']} consecutive checks, "
                                                                        f"exiting position {position['trade_id']}. "
                                                                        f"P&L=₹{current_pnl:.2f}, Distance to stop=₹{distance_to_stop:.2f}"
                                                                    )
                                                                else:
                                                                    logger.debug(
                                                                        f"EMA structure broken (count: {position['ema_break_count']}/{ema_required_checks}), "
                                                                        f"waiting for confirmation. P&L=₹{current_pnl:.2f}"
                                                                    )
                                                            
                                                            if should_exit_on_ema:
                                                                should_exit = True
                                                                exit_reason = 'EMA_STRUCTURE_BROKEN'
                                                        else:
                                                            # Structure is valid - reset break count
                                                            if position.get('ema_break_count', 0) > 0:
                                                                position['ema_break_count'] = 0
                                                                position_tracker._save_active_positions()
                                                                logger.debug(f"EMA structure restored, reset break count for position {position['trade_id']}")
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:449","message":"After EMA structure check","data":{"should_exit":should_exit,"exit_reason":exit_reason},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H3"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                else:
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:455","message":"EMA structure check skipped","data":{"should_exit":should_exit,"EXIT_ON_EMA_BREAK":EXIT_ON_EMA_BREAK,"ema_structure_is_none":ema_structure is None,"skip_reason":"should_exit=True" if should_exit else ("EXIT_ON_EMA_BREAK=False" if not EXIT_ON_EMA_BREAK else "ema_structure=None")},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H1"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                
                                                # Update trailing stop loss (if not exiting)
                                                if not should_exit and current_atr > 0:
                                                    # Import hybrid trailing stop config (PnL in INR for phases and lock)
                                                    from strategies.trend.config import (
                                                        USE_HYBRID_TRAILING_STOP,
                                                        HYBRID_MIN_PNL_LOCK_INR,
                                                        LOCK_PROFIT_MIN_INR,
                                                        HYBRID_BREAKEVEN_THRESHOLD_INR,
                                                        HYBRID_PHASE2_THRESHOLD_INR,
                                                        HYBRID_PHASE3_THRESHOLD_INR,
                                                        HYBRID_PHASE4_THRESHOLD_INR,
                                                        HYBRID_PHASE5_THRESHOLD_INR,
                                                        HYBRID_PHASE6_THRESHOLD_INR,
                                                        HYBRID_PHASE6_PLUS_THRESHOLD_INR,
                                                        HYBRID_PHASE1_MULTIPLIER,
                                                        HYBRID_PHASE2_MULTIPLIER,
                                                        HYBRID_PHASE3_MULTIPLIER,
                                                        HYBRID_PHASE4_MULTIPLIER,
                                                        HYBRID_PHASE5_MULTIPLIER,
                                                        HYBRID_PHASE6_MULTIPLIER,
                                                    )
                                                    
                                                    # Use current P&L in ₹ for phase and breakeven lock (not points/ATR)
                                                    pnl_inr = current_pnl if current_pnl is not None else 0.0
                                                    
                                                    # Determine trailing multiplier based on profit phase (PnL terms)
                                                    if USE_HYBRID_TRAILING_STOP:
                                                        # HYBRID mode: Phase thresholds in ₹
                                                        if pnl_inr <= 0:
                                                            trailing_multiplier = HYBRID_PHASE1_MULTIPLIER
                                                            trail_phase = 'PHASE1'
                                                        elif pnl_inr < HYBRID_BREAKEVEN_THRESHOLD_INR:
                                                            trailing_multiplier = HYBRID_PHASE1_MULTIPLIER
                                                            trail_phase = 'PHASE1_NEAR_BE'
                                                        elif pnl_inr < HYBRID_PHASE3_THRESHOLD_INR:
                                                            trailing_multiplier = HYBRID_PHASE2_MULTIPLIER
                                                            trail_phase = 'PHASE2_BREAKEVEN'
                                                        elif pnl_inr < HYBRID_PHASE4_THRESHOLD_INR:
                                                            trailing_multiplier = HYBRID_PHASE3_MULTIPLIER
                                                            trail_phase = 'PHASE3_TIGHT'
                                                        elif pnl_inr < HYBRID_PHASE5_THRESHOLD_INR:
                                                            trailing_multiplier = HYBRID_PHASE4_MULTIPLIER
                                                            trail_phase = 'PHASE4_VERY_TIGHT'
                                                        elif pnl_inr < HYBRID_PHASE6_THRESHOLD_INR:
                                                            trailing_multiplier = HYBRID_PHASE5_MULTIPLIER
                                                            trail_phase = 'PHASE5_TIGHT'
                                                        elif pnl_inr < HYBRID_PHASE6_PLUS_THRESHOLD_INR:
                                                            trailing_multiplier = HYBRID_PHASE5_MULTIPLIER
                                                            trail_phase = 'PHASE6_VERY_TIGHT'
                                                        else:
                                                            trailing_multiplier = HYBRID_PHASE6_MULTIPLIER
                                                            trail_phase = 'PHASE6_PLUS'
                                                    else:
                                                        trailing_multiplier = TRAILING_STOP_LOSS_ATR_MULTIPLIER
                                                        trail_phase = 'FIXED'
                                                    position['trail_phase'] = trail_phase
                                                    
                                                    trailing_stop_distance = current_atr * trailing_multiplier
                                                    
                                                    # Lock at least LOCK_PROFIT_MIN_INR when PnL >= threshold (₹)
                                                    lock_be = (USE_HYBRID_TRAILING_STOP and
                                                               (current_pnl is not None and current_pnl >= HYBRID_MIN_PNL_LOCK_INR))
                                                    qty = position.get('quantity', 0) or (position.get('lots', 0) * position.get('lot_size', 65))
                                                    
                                                    if direction == 'LONG':
                                                        new_trailing_stop = current_futures_price - trailing_stop_distance
                                                        if lock_be and qty > 0:
                                                            min_stop_lock = entry_price + (LOCK_PROFIT_MIN_INR / qty)
                                                            new_trailing_stop = max(new_trailing_stop, min_stop_lock)
                                                        updated_stop = max(current_stop, new_trailing_stop)
                                                        # Never relax once in profit: keep at least LOCK_PROFIT_MIN_INR locked
                                                        if current_stop >= entry_price and qty > 0:
                                                            min_lock = entry_price + (LOCK_PROFIT_MIN_INR / qty)
                                                            updated_stop = max(updated_stop, min_lock)
                                                    else:  # SHORT
                                                        new_trailing_stop = current_futures_price + trailing_stop_distance
                                                        if lock_be and qty > 0:
                                                            min_stop_lock = entry_price - (LOCK_PROFIT_MIN_INR / qty)
                                                            new_trailing_stop = min(new_trailing_stop, min_stop_lock)
                                                        updated_stop = min(current_stop, new_trailing_stop)
                                                        # Never relax once in profit: keep at least LOCK_PROFIT_MIN_INR locked
                                                        if current_stop <= entry_price and qty > 0:
                                                            max_lock = entry_price - (LOCK_PROFIT_MIN_INR / qty)
                                                            updated_stop = min(updated_stop, max_lock)
                                                    
                                                    # Update position with new trailing stop
                                                    if updated_stop != current_stop:
                                                        # Check if we moved to lock (stop now at/above entry for LONG, at/below for SHORT)
                                                        moved_to_lock = False
                                                        if direction == 'LONG' and current_stop < entry_price and updated_stop >= entry_price:
                                                            moved_to_lock = True
                                                        elif direction == 'SHORT' and current_stop > entry_price and updated_stop <= entry_price:
                                                            moved_to_lock = True
                                                        
                                                        position['current_stop_price'] = updated_stop
                                                        position_tracker._save_active_positions()
                                                        
                                                        if moved_to_lock:
                                                            logger.info(
                                                                f"🛡️ Locked min ₹{LOCK_PROFIT_MIN_INR} profit for position {position['trade_id']}: "
                                                                f"Stop ₹{current_stop:.2f} → ₹{updated_stop:.2f} (entry: ₹{entry_price:.2f})"
                                                            )
                                                        else:
                                                            logger.debug(
                                                                f"Updated trailing stop [{trail_phase}] for position {position['trade_id']}: "
                                                                f"₹{current_stop:.2f} → ₹{updated_stop:.2f} (mult: {trailing_multiplier}×ATR)"
                                                            )
                                                
                                                # Close position if exit condition met
                                                # #region agent log
                                                try:
                                                    with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                        f.write(json.dumps({"location":"main.py:463","message":"Final exit decision","data":{"should_exit":should_exit,"exit_reason":exit_reason,"position_id":position.get('trade_id')},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H5"})+"\n")
                                                except: pass
                                                # #endregion
                                                if should_exit:
                                                    logger.info(
                                                        f"⚠️ Futures exit condition triggered for position {position['trade_id']}: "
                                                        f"{exit_reason}, P&L=₹{current_pnl:.2f}, "
                                                        f"Entry=₹{entry_price:.2f}, Exit=₹{current_futures_price:.2f}, "
                                                        f"Stop=₹{current_stop:.2f}"
                                                    )
                                                    
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:472","message":"Calling close_position","data":{"position_id":position.get('trade_id'),"exit_reason":exit_reason,"current_pnl":current_pnl},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H5"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                    # Exit order: always use LIMIT (at stop price for STOP_LOSS_HIT, else current price).
                                                    # Stored on position so execution layer places LMT at this price; matches backtest.
                                                    position['exit_order_type'] = 'LMT'
                                                    position['exit_price'] = (
                                                        current_stop if exit_reason == 'STOP_LOSS_HIT' else current_futures_price
                                                    )
                                                    try:
                                                        position_tracker.close_position(
                                                            position,
                                                            f"futures_exit_{exit_reason}",
                                                            current_pnl
                                                        )
                                                        if exit_reason == 'STOP_LOSS_HIT':
                                                            from strategies.trend.entry_state import record_stop_loss_exit
                                                            record_stop_loss_exit(datetime.now())
                                                        # #region agent log
                                                        try:
                                                            with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                                f.write(json.dumps({"location":"main.py:480","message":"close_position completed","data":{"position_id":position.get('trade_id'),"success":True},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H5"})+"\n")
                                                        except: pass
                                                        # #endregion
                                                    except Exception as e:
                                                        # #region agent log
                                                        try:
                                                            with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                                f.write(json.dumps({"location":"main.py:485","message":"close_position failed","data":{"position_id":position.get('trade_id'),"error":str(e)},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"ema-exit-check","hypothesisId":"H5"})+"\n")
                                                        except: pass
                                                        # #endregion
                                                        logger.error(f"Error closing position {position['trade_id']}: {str(e)}", exc_info=True)
                                                    
                                                    logger.info(f"Position {position['trade_id']} marked for exit (futures)")
                                                else:
                                                    # Log current status
                                                    ema_status = ""
                                                    if ema_structure and EXIT_ON_EMA_BREAK:
                                                        ema_50 = ema_structure.get('ema_50')
                                                        ema_100 = ema_structure.get('ema_100')
                                                        if ema_50 and ema_100:
                                                            if direction == 'LONG':
                                                                structure_valid = current_futures_price > ema_50 > ema_100
                                                                ema_status = f", EMA50=₹{ema_50:.2f}, EMA100=₹{ema_100:.2f}, EMA_Structure={'VALID' if structure_valid else 'BROKEN'}"
                                                            else:  # SHORT
                                                                structure_valid = current_futures_price < ema_50 < ema_100
                                                                ema_status = f", EMA50=₹{ema_50:.2f}, EMA100=₹{ema_100:.2f}, EMA_Structure={'VALID' if structure_valid else 'BROKEN'}"
                                                    
                                                    # Add position age and EMA break count to status
                                                    age_str = f", Age={position_age_minutes:.1f} min"
                                                    ema_break_count = position.get('ema_break_count', 0)
                                                    if ema_break_count > 0 and EXIT_ON_EMA_BREAK:
                                                        from strategies.trend.config import EMA_BREAK_CONFIRMATION_CHECKS, EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS
                                                        ema_req = EMA_BREAK_CONFIRMATION_CHECKS_WHEN_IN_LOSS if current_pnl < 0 else EMA_BREAK_CONFIRMATION_CHECKS
                                                        age_str += f", EMA_Break_Count={ema_break_count}/{ema_req}"
                                                    
                                                    # Profit locked if stop is beyond entry (trailing in profit)
                                                    qty = position.get('quantity', 0)
                                                    if direction == 'LONG' and current_stop > entry_price and qty:
                                                        profit_locked = (current_stop - entry_price) * qty
                                                    elif direction == 'SHORT' and current_stop < entry_price and qty:
                                                        profit_locked = (entry_price - current_stop) * qty
                                                    else:
                                                        profit_locked = 0.0
                                                    trail_phase = position.get('trail_phase', 'INITIAL')
                                                    extra = f", Profit_locked=₹{profit_locked:.2f}, Trail={trail_phase}"
                                                    
                                                    logger.info(
                                                        f"Futures position {position['trade_id']}: "
                                                        f"Price=₹{current_futures_price:.2f}, "
                                                        f"P&L=₹{current_pnl:.2f}, "
                                                        f"Stop=₹{current_stop:.2f}{extra}{age_str}, "
                                                        f"Regime={current_regime}{ema_status}"
                                                    )
                                                    # #region agent log
                                                    try:
                                                        with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                            f.write(json.dumps({"location":"main.py:609","message":"Position status logged successfully","data":{"position_id":position.get('trade_id'),"current_futures_price":current_futures_price,"current_pnl":current_pnl,"current_stop":current_stop,"profit_locked":profit_locked,"trail_phase":trail_phase,"current_regime":current_regime},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H5"})+"\n")
                                                    except: pass
                                                    # #endregion
                                                
                                                continue
                                            
                                            # Get expiry date (required for options strategies)
                                            expiry_str = position.get('expiry')
                                            if not expiry_str:
                                                logger.warning(f"Position {position['trade_id']} missing expiry date, skipping profit check")
                                                continue
                                            
                                            expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                                            
                                            # Get current option prices
                                            option_chain = get_option_chain_data(
                                                api, symbol_manager, spot_price, expiry_date, count=50
                                            )
                                            
                                            if option_chain.empty:
                                                logger.debug(f"No option chain for position {position['trade_id']}")
                                                continue
                                            
                                            # Build current prices dict
                                            current_prices = {}
                                            legs = position.get('legs', [])
                                            if not legs:
                                                logger.warning(f"Position {position['trade_id']} has no legs, skipping profit check")
                                                continue
                                            
                                            for leg in legs:
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
                                            # Iron Condor trailing PnL lock: first lock at ₹300, then trail ₹200 below current
                                            should_exit_trailing = False
                                            if position.get('book') == 'INCOME':
                                                should_exit_trailing = position_tracker.update_trailing_lock_and_check(
                                                    position, current_pnl
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
                                                    recent_candles = regime_detector.get_recent_candles(api, symbol_manager, spot_price, lookback_days=20)
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
                                                        current_range_state=regime_info.get('range_state'),
                                                        current_mtm=current_pnl
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
                                                    recent_candles = regime_detector.get_recent_candles(api, symbol_manager, spot_price, lookback_days=20)
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
                                            
                                            # Check trailing stop (Iron Condor: PnL dropped below locked level)
                                            if should_exit_trailing:
                                                lock = position.get('profit_locked_inr', 0)
                                                logger.info(
                                                    f"⚠️ Trailing stop hit for position {position['trade_id']}: "
                                                    f"P&L=₹{current_pnl:.2f} below lock=₹{lock:.2f}"
                                                )
                                                position_tracker.close_position(
                                                    position,
                                                    "trailing_stop_pnl",
                                                    current_pnl
                                                )
                                                logger.info(f"Position {position['trade_id']} marked for exit (trailing stop)")
                                            # Profit-target exit disabled: Convex and Iron Condor use TSL only
                                            elif not should_exit_convex and not should_exit_calendar and position_tracker.check_profit_target(position, current_pnl):
                                                target_inr = position.get('profit_target_inr') or position.get('profit_target_margin') or 0
                                                logger.info(
                                                    f"✅ Profit target reached for position {position['trade_id']}: "
                                                    f"P&L=₹{current_pnl:.2f}, "
                                                    f"Target=₹{target_inr:.2f}"
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
                                                # Log current status with PnL (exit by TSL only, no profit target)
                                                logger.info(
                                                    f"Position {position['trade_id']} ({position.get('book', '?')}): "
                                                    f"current_pnl=₹{current_pnl:.2f}, TSL only, no exit"
                                                )
                                                
                                        except Exception as e:
                                            logger.error(f"Error checking position {position.get('trade_id', 'unknown')}: {e}")
                                            # #region agent log
                                            import json
                                            try:
                                                with open('/Users/arshdeep/git/regimetrader/.cursor/debug.log', 'a') as f:
                                                    f.write(json.dumps({"location":"main.py:800","message":"Exception in position monitoring","data":{"position_id":position.get('trade_id', 'unknown'),"error":str(e),"error_type":type(e).__name__},"timestamp":int(datetime.now().timestamp()*1000),"sessionId":"debug-session","runId":"position-monitoring","hypothesisId":"H5"})+"\n")
                                            except: pass
                                            # #endregion
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