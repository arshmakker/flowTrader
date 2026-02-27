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
from strategy_runner import run_strategy_with_regime, is_market_hours, is_market_closed_ist, get_now_ist, get_nifty_spot_price, get_option_chain_data
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

# Capital for strategy sizing (₹8L)
CAPITAL_8L = 800000.0

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
        
        # No new trades window - prevent entries in last 60 minutes before close
        NO_NEW_TRADES_BEFORE_CLOSE_MINUTES = 60  # No new entries after 2:30 PM IST
        
        # End-of-day liquidation - close all positions 15 minutes before market close
        CLOSE_POSITIONS_BEFORE_CLOSE_MINUTES = 15  # Close all positions at 3:15 PM IST
        
        # Flag to prevent re-entry after market close exit
        # Once we exit a position due to MARKET_CLOSE_APPROACHING, don't enter new trades for the rest of the day
        market_close_exit_triggered = False
        
        logger.info(Fore.CYAN + "Convex-only live trading (Iron Condor disabled)")
        logger.info(f"Strategy checks will run every {STRATEGY_CHECK_INTERVAL // 60} minutes during market hours")
        logger.info(f"IV calculations will run every {IV_CALCULATION_INTERVAL // 60} minutes to build historical data")
        logger.info(f"No new trades window: Last {NO_NEW_TRADES_BEFORE_CLOSE_MINUTES} minutes before market close (no entries after 2:30 PM IST)")
        logger.info(f"End-of-day liquidation: Closing all positions {CLOSE_POSITIONS_BEFORE_CLOSE_MINUTES} minutes before market close (at 3:15 PM IST)")
        
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
                    
                    # CRITICAL: Close all open positions before stopping (even if starting after market hours)
                    try:
                        active_positions = position_tracker.get_active_positions()
                        if active_positions:
                            logger.info(Fore.YELLOW + f"⚠️ Closing {len(active_positions)} open positions before shutdown...")
                            
                            for position in active_positions:
                                try:
                                    # Use LTP from position data (no API call needed when market is closed)
                                    current_prices = {}
                                    for leg in position.get('legs', []):
                                        option_type = leg['option_type']
                                        strike = int(leg['strike'])
                                        ltp = leg.get('ltp', leg.get('price', 0))
                                        current_prices[f"{option_type}{strike}"] = ltp
                                    current_pnl = position_tracker.calculate_current_pnl(position, current_prices)
                                    # When market already closed we only update tracker (broker orders not possible)
                                    position_tracker.close_position(position, "end_of_day_liquidation", current_pnl)
                                    logger.info(f"✅ Closed position {position['trade_id']}: P&L=₹{current_pnl:.2f}")
                                except Exception as e:
                                    logger.error(f"Error closing position {position.get('trade_id')}: {e}")
                            
                            logger.info(Fore.GREEN + "All positions closed!")
                    except Exception as e:
                        logger.error(f"Error during position liquidation: {e}")
                    
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
                
                # Run strategy check periodically during market hours (Convex only)
                time_since_last_check = (current_time - last_strategy_check).total_seconds()
                
                if time_since_last_check >= STRATEGY_CHECK_INTERVAL:
                    # Check if we're in the no-new-trades window (last 60 minutes before close)
                    now_ist = get_now_ist()
                    market_close_time = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
                    minutes_to_close = (market_close_time - now_ist).total_seconds() / 60
                    
                    # End-of-day liquidation: close all positions 15 minutes before market close
                    if 0 < minutes_to_close <= CLOSE_POSITIONS_BEFORE_CLOSE_MINUTES:
                        try:
                            active_positions = position_tracker.get_active_positions()
                            if active_positions:
                                logger.info(Fore.YELLOW + f"⚠️ End-of-day liquidation: Closing {len(active_positions)} positions before market close...")
                                
                                # Get current prices and close each position
                                for position in active_positions:
                                    try:
                                        expiry_str = position.get('expiry')
                                        if not expiry_str:
                                            continue
                                        expiry_date = datetime.strptime(expiry_str, '%Y-%m-%d').date()
                                        spot_price = get_nifty_spot_price(api, symbol_manager)
                                        if not spot_price:
                                            continue
                                        
                                        option_chain = get_option_chain_data(api, symbol_manager, spot_price, expiry_date, count=50)
                                        if option_chain.empty:
                                            continue
                                        
                                        # Build current prices
                                        current_prices = {}
                                        for leg in position.get('legs', []):
                                            strike = int(leg['strike'])
                                            option_type = leg['option_type']
                                            leg_data = option_chain[
                                                (option_chain['strike'] == strike) &
                                                (option_chain['option_type'] == option_type)
                                            ]
                                            if not leg_data.empty:
                                                current_prices[f"{option_type}{strike}"] = leg_data.iloc[0]['mid_price']
                                            else:
                                                current_prices[f"{option_type}{strike}"] = leg['price']
                                        
                                        current_pnl = position_tracker.calculate_current_pnl(position, current_prices)
                                        # Convex: execute broker exit first, then mark closed
                                        if position.get('book') == 'CONVEX':
                                            try:
                                                from strategies.convex.order_builder import close_convex_position
                                                close_result = close_convex_position(api, position)
                                                if not close_result.get('success'):
                                                    logger.error("Convex EOD exit failed: %s", close_result.get('message'))
                                                    continue
                                            except Exception as e:
                                                logger.exception("close_convex_position (EOD) failed: %s", e)
                                                continue
                                        position_tracker.close_position(position, "end_of_day_liquidation", current_pnl)
                                        logger.info(f"✅ Closed position {position['trade_id']}: P&L=₹{current_pnl:.2f}")
                                    except Exception as e:
                                        logger.error(f"Error closing position {position.get('trade_id')}: {e}")
                                
                                logger.info(Fore.GREEN + "End-of-day liquidation complete!")
                        except Exception as e:
                            logger.error(f"Error in end-of-day liquidation: {e}")
                        
                        # Generate summary and stop
                        generate_daily_trade_summary(logger)
                        if collector:
                            collector.stop_collection()
                        logger.info("=== End-of-day: All positions closed ===")
                        break
                    
                    # Skip strategy checks if we're in no-new-trades window
                    if minutes_to_close <= NO_NEW_TRADES_BEFORE_CLOSE_MINUTES and minutes_to_close > 0:
                        logger.info(Fore.YELLOW + f"⏸️ No new trades window - {minutes_to_close:.0f} minutes until market close")
                        last_strategy_check = current_time
                    # Skip strategy checks if we already exited for market close
                    elif market_close_exit_triggered:
                        logger.debug("Skipping strategy check - market close exit already triggered, no new entries today")
                        last_strategy_check = current_time
                    elif is_market_hours():
                        try:
                            logger.info(Fore.CYAN + "Running strategy check with regime detection...")
                            proposals = run_strategy_with_regime(api, symbol_manager, position_tracker, capital=CAPITAL_8L)
                            
                            # Handle both dict (multiple proposals) and single proposal for backward compatibility
                            if isinstance(proposals, dict):
                                # Take the first available proposal
                                trade_proposal = next(iter(proposals.values())) if proposals else None
                            else:
                                trade_proposal = proposals
                            
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
                                                # Two-fork model: Trend Following disabled; skip futures monitoring
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
                                            
                                            # Calendar strategy removed - convex-only mode
                                            
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
                                            # Profit-target exit: Convex must close via broker first; then update tracker
                                            elif not should_exit_convex and position_tracker.check_profit_target(position, current_pnl):
                                                target_inr = position.get('profit_target_inr') or position.get('profit_target_margin') or 0
                                                logger.info(
                                                    f"✅ Profit target reached for position {position['trade_id']}: "
                                                    f"P&L=₹{current_pnl:.2f}, "
                                                    f"Target=₹{target_inr:.2f}"
                                                )
                                                # Convex: execute broker exit first, then mark closed (live sync)
                                                if position.get('book') == 'CONVEX' or 'BACKSPREAD' in position.get('strategy', '').upper():
                                                    try:
                                                        from strategies.convex.order_builder import close_convex_position
                                                        close_result = close_convex_position(api, position)
                                                        if not close_result.get('success'):
                                                            logger.error(
                                                                "Convex profit-target exit orders failed: %s (position not marked closed)",
                                                                close_result.get('message', close_result),
                                                            )
                                                            continue
                                                    except Exception as e:
                                                        logger.exception("close_convex_position (profit target) failed: %s", e)
                                                        continue
                                                position_tracker.close_position(
                                                    position,
                                                    "profit_target_margin",
                                                    current_pnl
                                                )
                                                logger.info(f"Position {position['trade_id']} marked for exit")
                                            
                                            # Check convex exit conditions
                                            elif should_exit_convex:
                                                logger.info(
                                                    f"⚠️ Convex exit condition triggered for position {position['trade_id']}: "
                                                    f"{convex_exit_reason}, P&L=₹{current_pnl:.2f}"
                                                )
                                                # Execute broker exit first; mark closed only if all legs filled.
                                                try:
                                                    from strategies.convex.order_builder import close_convex_position
                                                    close_result = close_convex_position(api, position)
                                                    if not close_result.get('success'):
                                                        logger.error(
                                                            "Convex exit orders failed: %s (position not marked closed)",
                                                            close_result.get('message', close_result),
                                                        )
                                                    else:
                                                        position_tracker.close_position(
                                                            position,
                                                            f"convex_exit_{convex_exit_reason}",
                                                            current_pnl
                                                        )
                                                        logger.info(f"Position {position['trade_id']} marked for exit (convex)")
                                                except Exception as e:
                                                    logger.exception("close_convex_position failed: %s", e)
                                            
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
            
            # Summary of current trading and PnL (same format as end of day)
            generate_daily_trade_summary(logger)
            
            # Show open positions if any
            try:
                if os.path.exists('active_positions.json'):
                    with open('active_positions.json', 'r') as f:
                        all_positions = json.load(f)
                    open_positions = [p for p in all_positions if p.get('status') == 'OPEN']
                    if open_positions:
                        logger.info(f"\n{Fore.CYAN}Open positions ({len(open_positions)}):")
                        for pos in open_positions:
                            strat = pos.get('strategy', 'UNKNOWN')
                            trade_id = pos.get('trade_id', '')[:19] if pos.get('trade_id') else ''
                            entry = pos.get('entry_time', '')[:19] if pos.get('entry_time') else ''
                            logger.info(f"  {strat}  trade_id: {trade_id}  entry: {entry}")
                        logger.info("")
            except Exception as e:
                logger.debug(f"Could not list open positions: {e}")
            
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