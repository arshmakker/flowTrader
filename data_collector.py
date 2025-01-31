import pandas as pd
import numpy as np
from datetime import datetime
import threading
import queue
import logging
import time
import os
import json
from symbol_manager import SymbolManager

class DataCollector:
    def __init__(self, api, symbol_manager=None):
        self.api = api
        self.symbol_manager = symbol_manager
        self.logger = logging.getLogger('DataCollector')
        self.collection_active = False
        self.collection_thread = None
        self.data_queue = queue.Queue()
        self.data_directory = f"market_data_{datetime.now().strftime('%Y%m%d')}"
        self.raw_data_directory = os.path.join(self.data_directory, 'raw_data')
        self.ensure_directory()
        
        # Define index specifications
        self.index_specs = {
            'NIFTY': {
                'exchange': 'NFO',
                'symbol_prefix': 'NIFTY',
                'lot_size': 50
            },
            'BANKNIFTY': {
                'exchange': 'NFO',
                'symbol_prefix': 'BANKNIFTY',
                'lot_size': 15
            },
            'FINNIFTY': {
                'exchange': 'NFO',
                'symbol_prefix': 'FINNIFTY',
                'lot_size': 40
            }
        }
        
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
        """Get list of index derivatives (futures and options)"""
        if self.symbol_manager:
            return self.symbol_manager.get_all_index_derivatives()
        else:
            self.logger.warning("No symbol manager provided, creating temporary one")
            temp_manager = SymbolManager(self.api)
            temp_manager.load_symbol_files()
            return temp_manager.get_all_index_derivatives()

    def start_collection(self, symbols=None):
        """Start collecting data for index futures"""
        if symbols is None:
            symbols = self.get_index_symbols()
            
        if not symbols:
            self.logger.error(symbols)
            self.logger.error("No valid symbols found for data collection")
            return
            
        self.collection_active = True
        self.collection_thread = threading.Thread(
            target=self._collect_data,
            args=(symbols,)
        )
        self.collection_thread.start()
        self.logger.info(f"Started data collection for indices: {[s['symbol'] for s in symbols]}")

    def _collect_data(self, symbols):
        """Collect and store market data"""
        while self.collection_active:
            try:
                timestamp = datetime.now()
                futures_count = 0
                options_count = 0
                
                for symbol in symbols:
                    try:
                        quote = self.api.get_quotes(
                            exchange=symbol['exchange'],
                            token=symbol['token']
                        )
                        
                        if quote:
                            data_point = {
                                'timestamp': timestamp.strftime('%Y-%m-%d %H:%M:%S.%f'),
                                'symbol': symbol['symbol'],
                                'index_name': symbol['index_name'],
                                'instrument': symbol.get('instrument', 'FUTIDX'),
                                'ltp': float(quote.get('lp', 0)),
                                'volume': int(quote.get('v', 0)),
                                'bid': float(quote.get('bp1', 0)),
                                'ask': float(quote.get('sp1', 0)),
                                'oi': int(quote.get('oi', 0)),
                                'bid_qty': int(quote.get('bq1', 0)),
                                'ask_qty': int(quote.get('sq1', 0))
                            }
                            
                            # Add option-specific fields
                            if symbol.get('instrument') == 'OPTIDX':
                                data_point.update({
                                    'strike': symbol['strike'],
                                    'option_type': symbol['option_type'],
                                    'expiry': symbol['expiry']
                                })
                                options_count += 1
                            else:
                                futures_count += 1
                            
                            # Save raw data
                            self._save_raw_data(data_point)
                            # Put in queue for processing
                            self.data_queue.put(data_point)
                            
                            log_level = logging.DEBUG if data_point['instrument'] == 'FUTIDX' else logging.INFO
                            self.logger.log(
                                log_level,
                                f"Collected {data_point['instrument']} data for {symbol['symbol']}: "
                                f"LTP={data_point['ltp']:.2f}, OI={data_point['oi']}"
                            )
                            
                    except Exception as e:
                        self.logger.error(f"Error collecting data for {symbol['symbol']}: {str(e)}")
                        continue
                
                self.logger.info(
                    f"Collection cycle complete - "
                    f"Futures: {futures_count}, Options: {options_count}"
                )
                time.sleep(1)  # 1-second interval
                
            except Exception as e:
                self.logger.error(f"Error in data collection loop: {str(e)}")
                time.sleep(5)  # Wait before retrying

    def stop_collection(self):
        """Stop data collection"""
        self.collection_active = False
        if self.collection_thread:
            self.collection_thread.join()
        self.logger.info("Stopped data collection")

    def _save_raw_data(self, data_point):
        """Save raw data to file"""
        try:
            raw_data_dir = os.path.join(self.data_directory, 'raw_data')
            if not os.path.exists(raw_data_dir):
                os.makedirs(raw_data_dir)

            # Create subdirectories for futures and options
            instrument_type = 'futures' if data_point.get('instrument', 'FUTIDX') == 'FUTIDX' else 'options'
            instrument_dir = os.path.join(raw_data_dir, instrument_type)
            if not os.path.exists(instrument_dir):
                os.makedirs(instrument_dir)
                
            # For options, create subdirectories by index and option type
            if instrument_type == 'options':
                index_dir = os.path.join(instrument_dir, data_point['index_name'])
                if not os.path.exists(index_dir):
                    os.makedirs(index_dir)
                option_type_dir = os.path.join(index_dir, data_point['option_type'].lower())
                if not os.path.exists(option_type_dir):
                    os.makedirs(option_type_dir)
                final_dir = option_type_dir
            else:
                final_dir = instrument_dir

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