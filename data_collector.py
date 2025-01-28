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
    def __init__(self, api):
        self.api = api
        self.logger = logging.getLogger('DataCollector')
        self.data_directory = f"market_data_{datetime.now().strftime('%Y%m%d')}"
        self.ensure_directory()
        self.collection_active = False
        self.collection_thread = None
        self.data_queue = queue.Queue()
        
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
        """Get list of current month futures for indices"""
        expiry = self.get_current_month_expiry()
        symbols = []
        
        # Get the list of available futures from symbol manager
        symbol_manager = SymbolManager(self.api)
        symbol_manager.load_symbol_files()
        available_futures = symbol_manager.get_index_futures()
        
        if not available_futures:
            self.logger.error("No index futures found in symbol files")
            return []
            
        for future in available_futures:
            try:
                # Get the token for the symbol using the trading symbol from NFO file
                token_info = self.api.get_security_info(
                    exchange='NFO',
                    token=future['token']
                )
                
                if token_info:
                    symbols.append({
                        'symbol': future['symbol'],  # This is the trading symbol from NFO
                        'token': future['token'],
                        'exchange': 'NFO',
                        'index_name': future['index_name'],
                        'lot_size': future['lot_size']
                    })
                    self.logger.info(f"Added {future['symbol']} for data collection")
                else:
                    self.logger.warning(f"Could not get token info for {future['symbol']}")
                    
            except Exception as e:
                self.logger.error(f"Error getting token for {future['symbol']}: {str(e)}")
                continue
                
        return symbols

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
                                'ltp': float(quote.get('lp', 0)),
                                'volume': int(quote.get('v', 0)),
                                'bid': float(quote.get('bp1', 0)),
                                'ask': float(quote.get('sp1', 0)),
                                'oi': int(quote.get('oi', 0)),
                                'bid_qty': int(quote.get('bq1', 0)),
                                'ask_qty': int(quote.get('sq1', 0))
                            }
                            
                            # Save raw data
                            self._save_raw_data(data_point)
                            # Put in queue for processing
                            self.data_queue.put(data_point)
                            
                            self.logger.debug(f"Collected data for {symbol['symbol']}: LTP={data_point['ltp']}, OI={data_point['oi']}")
                            
                    except Exception as e:
                        self.logger.error(f"Error collecting data for {symbol['symbol']}: {str(e)}")
                        continue
                
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

            filename = os.path.join(
                raw_data_dir,
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