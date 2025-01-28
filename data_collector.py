import pandas as pd
import numpy as np
from datetime import datetime
import threading
import queue
import logging
import time
import os
import json

class DataCollector:
    def __init__(self, api):
        self.api = api
        self.logger = logging.getLogger('DataCollector')
        self.data_directory = f"market_data_{datetime.now().strftime('%Y%m%d')}"
        self.ensure_directory()
        self.collection_active = False
        self.collection_thread = None
        self.data_queue = queue.Queue()
        
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

    def start_collection(self, symbols):
        """Start collecting data for given symbols"""
        self.collection_active = True
        self.collection_thread = threading.Thread(
            target=self._collect_data,
            args=(symbols,)
        )
        self.collection_thread.start()
        self.logger.info(f"Started data collection for {len(symbols)} symbols")

    def stop_collection(self):
        """Stop data collection"""
        self.collection_active = False
        if self.collection_thread:
            self.collection_thread.join()
        self.logger.info("Stopped data collection")

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
                                'ltp': float(quote.get('lp', 0)),
                                'volume': int(quote.get('v', 0)),
                                'bid': float(quote.get('bp1', 0)),
                                'ask': float(quote.get('sp1', 0)),
                                'oi': int(quote.get('oi', 0))
                            }
                            
                            # Save raw data
                            self._save_raw_data(data_point)
                            # Put in queue for processing
                            self.data_queue.put(data_point)
                            
                            self.logger.debug(f"Collected data for {symbol['symbol']}: LTP={data_point['ltp']}")
                            
                    except Exception as e:
                        self.logger.error(f"Error collecting data for {symbol['symbol']}: {str(e)}")
                        continue
                
                time.sleep(1)  # 1-second interval
                
            except Exception as e:
                self.logger.error(f"Error in data collection loop: {str(e)}")
                time.sleep(5)  # Wait before retrying

    def _save_raw_data(self, data_point):
        """Save raw data to file"""
        try:
            # Ensure the raw data directory exists
            raw_data_dir = os.path.join(self.data_directory, 'raw_data')
            if not os.path.exists(raw_data_dir):
                os.makedirs(raw_data_dir)
                self.logger.info(f"Created raw data directory: {raw_data_dir}")

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
            # Don't raise the exception to keep the collection running 