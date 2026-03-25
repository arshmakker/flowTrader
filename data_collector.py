import pandas as pd
import numpy as np
from datetime import datetime
import threading
import queue
import logging
import time
import os
import json
import socket
from symbol_manager import SymbolManager

# Seconds between full collection cycles (one get_quotes per symbol per cycle).
# Increase to reduce broker load; see docs/BROKER_API_AUDIT.md.
COLLECTION_CYCLE_INTERVAL_SECONDS = 5

# DNS error retry delay
DNS_ERROR_DELAY_SECONDS = 5


def _is_dns_error(exception):
    """Check if exception is a DNS resolution error"""
    error_str = str(exception).lower()
    dns_indicators = [
        'name resolution',
        'nodename nor servname',
        'failed to resolve',
        'getaddrinfo failed',
    ]
    return any(indicator in error_str for indicator in dns_indicators)


class DataCollector:
    def __init__(self, api, symbol_manager=None):
        self.api = api
        self.symbol_manager = symbol_manager
        self.logger = logging.getLogger('DataCollector')
        self.collection_active = False
        self.collection_thread = None
        self._stop_event = threading.Event()
        self._symbols = []
        self.data_queue = queue.Queue()
        self._refresh_paths()
        self.ensure_directory()
        
        # Define index specifications
        self.index_specs = {
            'NIFTY': {
                'exchange': 'NFO',
                'symbol_prefix': 'NIFTY',
                'lot_size': 65
            },
            'BANKNIFTY': {
                'exchange': 'NFO',
                'symbol_prefix': 'BANKNIFTY',
                'lot_size': 30
            },
            'FINNIFTY': {
                'exchange': 'NFO',
                'symbol_prefix': 'FINNIFTY',
                'lot_size': 60
            }
        }
        
    def _refresh_paths(self):
        self.data_directory = f"market_data_{datetime.now().strftime('%Y%m%d')}"
        self.raw_data_directory = os.path.join(self.data_directory, 'raw_data')

    def refresh_for_current_day(self):
        previous = getattr(self, 'data_directory', None)
        self._refresh_paths()
        if previous != self.data_directory:
            self.logger.info(f"Rotated data collector directory: {previous} -> {self.data_directory}")
            self.ensure_directory()

    def ensure_directory(self):
        """Create data directory if it doesn't exist"""
        try:
            # Create main data directory
            if not os.path.exists(self.data_directory):
                os.makedirs(self.data_directory)
                self.logger.info(f"Created main data directory: {self.data_directory}")

            # Create raw data directory
            raw_data_dir = os.path.join(self.data_directory, 'raw_data')
            if not os.path.exists(raw_data_dir):
                os.makedirs(raw_data_dir)
                self.logger.info(f"Created raw data directory: {raw_data_dir}")

            # Create processed data directory
            processed_data_dir = os.path.join(self.data_directory, 'processed_data')
            if not os.path.exists(processed_data_dir):
                os.makedirs(processed_data_dir)
                self.logger.info(f"Created processed data directory: {processed_data_dir}")

        except Exception as e:
            self.logger.error(f"Error creating directories: {str(e)}")
            raise

    def get_current_month_expiry(self):
        """Get current month expiry date - simplified version"""
        today = datetime.now()
        # Get last Thursday of current month
        next_month = today.replace(day=28) + pd.Timedelta(days=4)
        last_thursday = next_month - pd.Timedelta(days=next_month.weekday()) + pd.Timedelta(days=3)
        if last_thursday.month != today.month:
            last_thursday = last_thursday - pd.Timedelta(weeks=1)
        return last_thursday.strftime('%d%b%y').upper()

    def get_index_symbols(self):
        """Get list of all symbols to monitor for data collection"""
        if self.symbol_manager:
            return self.symbol_manager.get_data_collection_symbols()
        else:
            self.logger.warning("No symbol manager provided, creating temporary one")
            temp_manager = SymbolManager(self.api)
            temp_manager.load_symbol_files()
            return temp_manager.get_data_collection_symbols()

    def start_collection(self, symbols=None):
        """Start collecting data for all symbols"""
        self.refresh_for_current_day()
        if self.collection_thread and self.collection_thread.is_alive():
            self.logger.warning("Data collection already running; ignoring duplicate start request")
            return
        if symbols is None:
            symbols = self.get_index_symbols()
            
        if not symbols:
            self.logger.error("No valid symbols found for data collection")
            return
            
        self._symbols = symbols
        self.collection_active = True
        self._stop_event.clear()
        self.collection_thread = threading.Thread(
            target=self._collect_data,
            args=(symbols,)
        )
        self.collection_thread.start()
        
        # Log summary of symbols being monitored
        instruments = {}
        for s in symbols:
            inst = s.get('instrument', 'EQ')
            if inst not in instruments:
                instruments[inst] = []
            instruments[inst].append(s['symbol'])
            
        for inst, syms in instruments.items():
            self.logger.info(f"Monitoring {len(syms)} {inst} symbols")
            self.logger.debug(f"{inst} symbols: {', '.join(syms)}")

    def _collect_data(self, symbols):
        """Collect and store market data"""
        while self.collection_active:
            try:
                timestamp = datetime.now()
                counts = {
                    'EQ': 0,
                    'FUTIDX': 0,
                    'FUTSTK': 0,
                    'OPTIDX': 0,
                    'OPTSTK': 0
                }
                
                for symbol in symbols:
                    if self._stop_event.is_set():
                        break
                    try:
                        quote = self.api.get_quotes(
                            exchange=symbol['exchange'],
                            token=symbol['token']
                        )
                        
                        if quote:
                            data_point = {
                                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S.%f'),
                                'symbol': symbol['symbol'],
                                'instrument': symbol.get('instrument', 'EQ'),
                                'ltp': float(quote.get('lp', 0)),
                                'volume': int(quote.get('v', 0)),
                                'bid': float(quote.get('bp1', 0)),
                                'ask': float(quote.get('sp1', 0)),
                                'oi': int(quote.get('oi', 0)),
                                'bid_qty': int(quote.get('bq1', 0)),
                                'ask_qty': int(quote.get('sq1', 0))
                            }
                            
                            # Add instrument-specific fields
                            if 'index_name' in symbol:
                                data_point['index_name'] = symbol['index_name']
                            if 'stock_name' in symbol:
                                data_point['stock_name'] = symbol['stock_name']
                            if 'strike' in symbol:
                                data_point['strike'] = symbol['strike']
                            if 'option_type' in symbol:
                                data_point['option_type'] = symbol['option_type']
                            if 'expiry' in symbol:
                                data_point['expiry'] = symbol['expiry']
                            
                            # Update counts
                            inst = symbol.get('instrument', 'EQ')
                            counts[inst] = counts.get(inst, 0) + 1
                            
                            # Save raw data
                            self._save_raw_data(data_point)
                            # Put in queue for processing
                            self.data_queue.put(data_point)
                            
                            # Log at appropriate level (DEBUG for EQ/futures, INFO for options/index)
                            log_level = logging.DEBUG if inst in ['EQ', 'FUTIDX', 'FUTSTK'] else logging.INFO
                            if log_level == logging.DEBUG:
                                self.logger.debug(f"Collected {inst} data for {symbol['symbol']}: "
                                f"LTP={data_point['ltp']:.2f}, "
                                f"Vol={data_point['volume']}, "
                                f"OI={data_point['oi']}")
                            
                            
                    except Exception as e:
                        self.logger.error(f"Error collecting data for {symbol['symbol']}: {str(e)}")
                        if _is_dns_error(e):
                            self.logger.warning(f"DNS error detected. Waiting {DNS_ERROR_DELAY_SECONDS}s...")
                            time.sleep(DNS_ERROR_DELAY_SECONDS)
                        continue
                
                # Log collection summary
                summary = [f"{inst}: {count}" for inst, count in counts.items() if count > 0]
                self.logger.info(f"Collection cycle complete - {', '.join(summary)}")
                if self._stop_event.wait(COLLECTION_CYCLE_INTERVAL_SECONDS):
                    break
                
            except Exception as e:
                self.logger.error(f"Error in data collection loop: {str(e)}")
                if _is_dns_error(e):
                    self.logger.warning(f"DNS error detected. Waiting {DNS_ERROR_DELAY_SECONDS}s...")
                    time.sleep(DNS_ERROR_DELAY_SECONDS)
                if self._stop_event.wait(5):
                    break  # Wait before retrying

    def stop_collection(self):
        """Stop data collection"""
        self.collection_active = False
        self._stop_event.set()
        if self.collection_thread:
            self.collection_thread.join(timeout=10)
            if self.collection_thread.is_alive():
                self.logger.warning("Data collection thread did not stop within timeout")
        self.logger.info("Stopped data collection")

    def _save_raw_data(self, data_point):
        """Save raw data to file"""
        try:
            raw_data_dir = os.path.join(self.data_directory, 'raw_data')
            if not os.path.exists(raw_data_dir):
                os.makedirs(raw_data_dir)

            # Create subdirectories based on instrument type
            instrument_type = data_point.get('instrument', 'EQ')
            if instrument_type == 'EQ':
                final_dir = os.path.join(raw_data_dir, 'cash')
            elif instrument_type in ['FUTIDX', 'FUTSTK']:
                final_dir = os.path.join(raw_data_dir, 'futures')
            elif instrument_type in ['OPTIDX', 'OPTSTK']:
                # For options, create subdirectories by underlying and option type
                underlying = data_point.get('index_name', data_point.get('stock_name', 'unknown'))
                option_type = data_point['option_type'].lower()
                final_dir = os.path.join(raw_data_dir, 'options', underlying, option_type)
            else:
                final_dir = os.path.join(raw_data_dir, 'others')

            # Create all necessary directories
            os.makedirs(final_dir, exist_ok=True)

            filename = os.path.join(
                final_dir,
                f"{data_point['symbol']}_{datetime.now().strftime('%Y%m%d')}.csv"
            )
            
            df = pd.DataFrame([data_point])
            
            if not os.path.exists(filename):
                df.to_csv(filename, index=False)
                self.logger.info(f"Created new data file: {filename}")
            else:
                df.to_csv(filename, mode='a', header=False, index=False)
                
        except Exception as e:
            self.logger.error(f"Error saving raw data for {data_point['symbol']}: {str(e)}")

    def get_last_price(self, exchange, token):
        """Get the last traded price for a symbol"""
        try:
            quote = self.api.get_quotes(exchange=exchange, token=token)
            if quote:
                return float(quote.get('lp', 0))
            return None
        except Exception as e:
            self.logger.error(f"Error getting last price for {exchange}:{token}: {str(e)}")
            return None 
