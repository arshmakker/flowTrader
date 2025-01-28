import os
import pandas as pd
import logging
from datetime import datetime

class SymbolManager:
    def __init__(self, api, symbols_directory='symbols'):
        self.api = api
        self.logger = logging.getLogger('SymbolManager')
        self.data_directory = f"market_data_{datetime.now().strftime('%Y%m%d')}"
        self.symbols_directory = symbols_directory
        self.master_directory = os.path.join(self.data_directory, 'master_files')
        self.ensure_directory()
        self.nse_cash = None
        self.nse_fo = None
        
    def ensure_directory(self):
        """Create master files directory if it doesn't exist"""
        try:
            # Create main data directory if it doesn't exist
            if not os.path.exists(self.data_directory):
                os.makedirs(self.data_directory)
                self.logger.info(f"Created main data directory: {self.data_directory}")

            # Create master files directory if it doesn't exist
            if not os.path.exists(self.master_directory):
                os.makedirs(self.master_directory)
                self.logger.info(f"Created master files directory: {self.master_directory}")

            # Create symbols directory if it doesn't exist
            if not os.path.exists(self.symbols_directory):
                os.makedirs(self.symbols_directory)
                self.logger.info(f"Created symbols directory: {self.symbols_directory}")

        except Exception as e:
            self.logger.error(f"Error creating directories: {str(e)}")
            raise
            
    def load_symbol_files(self):
        """Load symbol files from the symbols directory"""
        try:
            # Load NSE Cash symbols
            nse_file = os.path.join(self.symbols_directory, 'NSE.csv')
            if os.path.exists(nse_file):
                self.logger.debug(f"Loading NSE symbols from {nse_file}")
                self.nse_cash = pd.read_csv(nse_file)
                # Standardize column names
                self.nse_cash.columns = [col.lower() for col in self.nse_cash.columns]
                self.logger.info(f"Loaded NSE Cash symbols: {len(self.nse_cash)} symbols")
                self.logger.debug(f"NSE columns: {list(self.nse_cash.columns)}")
            else:
                self.logger.error(f"NSE symbol file not found: {nse_file}")
            
            # Load NFO symbols
            nfo_file = os.path.join(self.symbols_directory, 'NFO.csv')
            if os.path.exists(nfo_file):
                self.logger.debug(f"Loading NFO symbols from {nfo_file}")
                self.nse_fo = pd.read_csv(nfo_file)
                # Standardize column names
                self.nse_fo.columns = [col.lower() for col in self.nse_fo.columns]
                self.logger.info(f"Loaded NFO symbols: {len(self.nse_fo)} symbols")
                self.logger.debug(f"NFO columns: {list(self.nse_fo.columns)}")
            else:
                self.logger.error(f"NFO symbol file not found: {nfo_file}")
                
        except Exception as e:
            self.logger.error(f"Error loading symbol files: {str(e)}", exc_info=True)
            
    def download_master_files(self):
        """Download master files from the exchange"""
        try:
            # Download NSE Cash master
            nse_cash_resp = self.api.get_master('NSE')
            if nse_cash_resp:
                self.nse_cash = pd.DataFrame(nse_cash_resp)
                self._save_master_file(self.nse_cash, 'nse_cash')
                
            # Download NSE F&O master
            nse_fo_resp = self.api.get_master('NFO')
            if nse_fo_resp:
                self.nse_fo = pd.DataFrame(nse_fo_resp)
                self._save_master_file(self.nse_fo, 'nse_fo')
                
            self.logger.info("Successfully downloaded and saved master files")
            
        except Exception as e:
            self.logger.error(f"Error downloading master files: {str(e)}")
            
    def _save_master_file(self, df, name):
        """Save master file to CSV"""
        if df is not None:
            filename = os.path.join(
                self.master_directory,
                f"{name}_master_{datetime.now().strftime('%Y%m%d')}.csv"
            )
            df.to_csv(filename, index=False)
            self.logger.info(f"Saved {name} master file to {filename}")
            
    def get_token_info(self, symbol, exchange='NSE'):
        """Get token information for a symbol"""
        try:
            if exchange == 'NSE':
                df = self.nse_cash
            elif exchange == 'NFO':
                df = self.nse_fo
            else:
                raise ValueError(f"Unsupported exchange: {exchange}")
                
            if df is None:
                self.logger.warning(f"Symbol file for {exchange} not loaded")
                return None
                
            # Search for the symbol (case-insensitive)
            symbol_match = df[df['symbol'].str.lower() == symbol.lower()]
            if len(symbol_match) == 0:
                self.logger.warning(f"Symbol {symbol} not found in {exchange}")
                return None
                
            symbol_info = symbol_match.iloc[0].to_dict()
            self.logger.debug(f"Found symbol info: {symbol_info}")
            
            return {
                'symbol': symbol_info['symbol'],
                'token': str(symbol_info['token']),  # Ensure token is string
                'exchange': exchange,
                'lotsize': int(symbol_info.get('lotsize', 1)),
                'tick_size': float(symbol_info.get('ticksize', 0.05))
            }
            
        except Exception as e:
            self.logger.error(f"Error getting token info for {symbol}: {str(e)}")
            return None
            
    def get_active_symbols(self, exchange='NSE', criteria=None):
        """Get list of active symbols based on criteria"""
        try:
            if exchange == 'NSE':
                df = self.nse_cash
            elif exchange == 'NFO':
                df = self.nse_fo
            else:
                raise ValueError(f"Unsupported exchange: {exchange}")
                
            if df is None:
                self.logger.warning(f"Symbol file for {exchange} not loaded")
                return []
            
            self.logger.debug(f"Available columns for filtering: {list(df.columns)}")
            
            # Apply filtering criteria if provided
            if criteria:
                for column, value in criteria.items():
                    column = column.lower()  # Convert to lowercase for matching
                    if column in df.columns:
                        df = df[df[column].str.lower() == value.lower()]
                        self.logger.debug(f"Applied filter {column}={value}, remaining symbols: {len(df)}")
                    else:
                        self.logger.warning(f"Column {column} not found in symbol file")
                        
            # Convert to list of dictionaries with required information
            symbols = []
            for _, row in df.iterrows():
                symbol_info = {
                    'symbol': row['symbol'],
                    'token': str(row['token']),  # Ensure token is string
                    'exchange': exchange,
                    'lotsize': int(row.get('lotsize', 1)),
                    'tick_size': float(row.get('ticksize', 0.05))
                }
                symbols.append(symbol_info)
                
            self.logger.info(f"Found {len(symbols)} symbols matching criteria in {exchange}")
            if symbols:
                self.logger.debug(f"Sample symbol: {symbols[0]}")
                
            return symbols
            
        except Exception as e:
            self.logger.error(f"Error getting active symbols: {str(e)}", exc_info=True)
            return [] 