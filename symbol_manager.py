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
        
        # Define index specifications
        self.index_specs = {
            'NIFTY': {
                'symbol_prefix': 'NIFTY',
                'lot_size': 50
            },
            'BANKNIFTY': {
                'symbol_prefix': 'BANKNIFTY',
                'lot_size': 15
            },
            'FINNIFTY': {
                'symbol_prefix': 'FINNIFTY',
                'lot_size': 40
            }
        }
        
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
            
    def get_current_month_expiry(self):
        """Get current month expiry date - simplified version"""
        today = datetime.now()
        # Get last Thursday of current month
        next_month = today.replace(day=28) + pd.Timedelta(days=4)
        last_thursday = next_month - pd.Timedelta(days=next_month.weekday()) + pd.Timedelta(days=3)
        if last_thursday.month != today.month:
            last_thursday = last_thursday - pd.Timedelta(weeks=1)
        return last_thursday.strftime('%d%b%y').upper()

    def get_index_futures(self):
        """Get list of current month futures for indices"""
        if self.nse_fo is None:
            self.logger.error("NFO symbols not loaded. Call load_symbol_files() first.")
            return []
            
        symbols = []
        
        try:
            # Filter for index futures
            futures_df = self.nse_fo[
                (self.nse_fo['instrument'] == 'FUTIDX') &  # Index futures
                (self.nse_fo['optiontype'] == 'XX') &      # Futures have XX as option type
                (self.nse_fo['tradingsymbol'].str.endswith('F'))  # Trading symbol ends with F
            ].copy()
            
            if futures_df.empty:
                self.logger.error("No index futures found in NFO file")
                return []
                
            self.logger.info(f"Found {len(futures_df)} total index futures")
            
            # Convert expiry to datetime for sorting
            futures_df['expiry_date'] = pd.to_datetime(futures_df['expiry'], format='%d-%b-%Y')
            futures_df = futures_df.sort_values('expiry_date')
            
            # Map of exact symbols as they appear in the NFO file
            symbol_map = {
                'NIFTY': 'NIFTY',
                'BANKNIFTY': 'BANKNIFTY',
                'FINNIFTY': 'FINNIFTY'
            }
            
            # For each index we're interested in
            for index, exact_symbol in symbol_map.items():
                try:
                    # Filter for the specific index using exact symbol match
                    index_filter = futures_df['symbol'] == exact_symbol
                    index_futures = futures_df[index_filter]
                    
                    if index_futures.empty:
                        self.logger.warning(f"No futures found for {index}")
                        continue
                    
                    # Get the nearest expiry contract
                    current_future = index_futures.iloc[0]
                    
                    # Verify the trading symbol pattern (should end with F)
                    if not current_future['tradingsymbol'].endswith('F'):
                        self.logger.warning(f"Unexpected trading symbol pattern for {index}: {current_future['tradingsymbol']}")
                        continue
                        
                    symbols.append({
                        'symbol': current_future['tradingsymbol'],
                        'token': str(current_future['token']),
                        'exchange': 'NFO',
                        'lot_size': int(current_future['lotsize']),
                        'index_name': index,
                        'expiry': current_future['expiry']
                    })
                    
                    self.logger.info(f"Selected {index} future: {current_future['tradingsymbol']} (Expiry: {current_future['expiry']})")
                        
                except Exception as e:
                    self.logger.error(f"Error processing {index} futures: {str(e)}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error getting index futures: {str(e)}")
            return []
            
        if not symbols:
            self.logger.error("No valid index futures found")
            
        return symbols

    def get_active_symbols(self, exchange=None, criteria=None):
        """Get list of active symbols based on criteria"""
        try:
            if exchange == 'NFO':
                if self.nse_fo is None:
                    self.logger.error("NFO symbols not loaded")
                    return []
                    
                df = self.nse_fo
            else:
                self.logger.error(f"Unsupported exchange: {exchange}")
                return []
                
            self.logger.debug(f"Available columns for filtering: {list(df.columns)}")
            
            # Apply filters based on criteria
            if criteria:
                for key, value in criteria.items():
                    if key in df.columns:
                        df = df[df[key] == value]
                    else:
                        self.logger.warning(f"Column {key} not found in symbol file")
            
            # Convert to list of dictionaries
            symbols = df.to_dict('records')
            self.logger.info(f"Found {len(symbols)} symbols matching criteria in {exchange}")
            if symbols:
                self.logger.debug(f"Sample symbol: {symbols[0]}")
                
            return symbols
            
        except Exception as e:
            self.logger.error(f"Error getting active symbols: {str(e)}")
            return [] 