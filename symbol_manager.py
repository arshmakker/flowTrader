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
        self.bse = None  # Added BSE dataframe
        
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
        
        # Define stock symbols to monitor - NIFTY 50, BANKNIFTY, and FINNIFTY constituents
        # Note: This list combines all unique stocks from the three indices
        self.stock_symbols = [
            # NIFTY 50 stocks (50)
            'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK', 'HINDUNILVR', 'ITC', 
            'SBIN', 'BHARTIARTL', 'KOTAKBANK', 'LT', 'AXISBANK', 'ASIANPAINT', 'MARUTI', 
            'TITAN', 'BAJFINANCE', 'HCLTECH', 'SUNPHARMA', 'ULTRACEMCO', 'NESTLEIND', 
            'WIPRO', 'ONGC', 'NTPC', 'POWERGRID', 'TATAMOTORS', 'M&M', 'ADANIENT', 
            'JSWSTEEL', 'HINDALCO', 'COALINDIA', 'GRASIM', 'BRITANNIA', 'DIVISLAB', 
            'TECHM', 'BAJAJFINSV', 'INDUSINDBK', 'HEROMOTOCO', 'CIPLA', 'DRREDDY', 
            'EICHERMOT', 'APOLLOHOSP', 'BPCL', 'TATACONSUM', 'ADANIPORTS', 'SBILIFE', 
            'LTIM', 'HDFCLIFE', 'TATASTEEL', 'PIDILITIND', 'HAVELLS',
            # BANKNIFTY stocks (12)
            'BANKBARODA', 'PNB', 'FEDERALBNK', 'IDFCFIRSTB', 'BANDHANBNK', 'AUBANK',
            # FINNIFTY stocks (additional)
            'ICICIGI', 'HDFCAMC', 'MUTHOOTFIN', 'CHOLAFIN', 'SHRIRAMFIN', 'LICHSGFIN',
            # Index symbols
            'NIFTY', 'FINNIFTY', 'BANKNIFTY'
        ]
        
        # Initialize empty ETF symbols list - will be populated after scanning NSE and BSE files
        self.etf_symbols = []
        
        # Initial reference ETFs to help with pattern matching
        self.reference_etfs = [
            'NIFTYBEES',    # Nifty 50 ETF
            'BANKBEES',     # Bank Nifty ETF
            'GOLDBEES',     # Gold BeES
            'LIQUIDBEES',   # Liquid BeES
            'SETFNIF50',    # SBI Nifty 50 ETF
            'NETFBEES',     # Nippon ETF
            'KOTAKBKETF',   # Kotak Bank ETF
            'BSEBANKEX',    # BSE Bankex ETF
            'BSESENSEX'     # BSE Sensex ETF
        ]
        
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
                self.nse_cash.columns = [col.lower() for col in self.nse_cash.columns]
                self.logger.info(f"Loaded NSE Cash symbols: {len(self.nse_cash)} symbols")
            else:
                self.logger.error(f"NSE symbol file not found: {nse_file}")
                raise FileNotFoundError(f"NSE symbol file not found: {nse_file}")
            
            # Load BSE symbols
            bse_file = os.path.join(self.symbols_directory, 'BSE.csv')
            if os.path.exists(bse_file):
                self.logger.debug(f"Loading BSE symbols from {bse_file}")
                self.bse = pd.read_csv(bse_file)
                self.bse.columns = [col.lower() for col in self.bse.columns]
                self.logger.info(f"Loaded BSE symbols: {len(self.bse)} symbols")
                
                # After loading both files, scan for ETFs and update the list
                self.logger.info("Scanning for ETFs across exchanges...")
                etf_analysis = self.scan_common_etfs()
                if etf_analysis:
                    self.logger.info(f"Updated ETF list with {len(self.etf_symbols)} symbols")
            else:
                self.logger.warning(f"BSE symbol file not found: {bse_file}")
            
            # Load NFO symbols
            nfo_file = os.path.join(self.symbols_directory, 'NFO.csv')
            if os.path.exists(nfo_file):
                self.logger.debug(f"Loading NFO symbols from {nfo_file}")
                self.nse_fo = pd.read_csv(nfo_file)
                column_map = {
                    'Exchange': 'exchange',
                    'Token': 'token',
                    'LotSize': 'lotsize',
                    'Symbol': 'symbol',
                    'TradingSymbol': 'tradingsymbol',
                    'Expiry': 'expiry',
                    'Instrument': 'instrument',
                    'OptionType': 'optiontype',
                    'StrikePrice': 'strikeprice',
                    'TickSize': 'ticksize'
                }
                self.nse_fo.rename(columns=column_map, inplace=True)
                self.logger.info(f"Loaded NFO symbols: {len(self.nse_fo)} symbols")
            else:
                self.logger.error(f"NFO symbol file not found: {nfo_file}")
                raise FileNotFoundError(f"NFO symbol file not found: {nfo_file}")
            
            return True
                
        except Exception as e:
            self.logger.error(f"Error loading symbol files: {str(e)}", exc_info=True)
            raise
            
    def download_master_files(self):
        """Download master files from the exchange"""
        try:
            self.logger.info("Downloading master files from exchange...")
            
            # Download NSE Cash master using searchscrip
            self.logger.info("Downloading NSE Cash symbols...")
            nse_symbols = []
            for stock in self.stock_symbols:
                try:
                    search_resp = self.api.searchscrip(exchange='NSE', searchtext=stock)
                    if search_resp and 'values' in search_resp:
                        for symbol in search_resp['values']:
                            if symbol['symbol'] == stock:  # Exact match
                                nse_symbols.append({
                                    'token': symbol['token'],
                                    'symbol': symbol['symbol'],
                                    'lotsize': symbol.get('lotsize', '1'),
                                    'ticksize': symbol.get('ticksize', '0.05'),
                                    'tradingsymbol': symbol['tsym']
                                })
                                self.logger.debug(f"Found NSE symbol: {symbol['symbol']}")
                                break
                except Exception as e:
                    self.logger.error(f"Error searching NSE symbol {stock}: {str(e)}")
                    continue
            
            if nse_symbols:
                self.nse_cash = pd.DataFrame(nse_symbols)
                # Save to both master and symbols directory
                self._save_master_file(self.nse_cash, 'nse_cash')
                nse_symbol_file = os.path.join(self.symbols_directory, 'NSE.csv')
                self.nse_cash.to_csv(nse_symbol_file, index=False)
                self.logger.info(f"Saved {len(nse_symbols)} NSE Cash symbols to {nse_symbol_file}")
            else:
                raise ValueError("Failed to download any NSE Cash symbols")
                
            # Download NFO symbols
            self.logger.info("Downloading NFO symbols...")
            nfo_symbols = []
            
            # Get futures
            for stock in self.stock_symbols:
                try:
                    # Search for stock futures
                    search_resp = self.api.searchscrip(exchange='NFO', searchtext=stock)
                    if search_resp and 'values' in search_resp:
                        for symbol in search_resp['values']:
                            # Add both futures and options
                            if symbol['symbol'] == stock:
                                nfo_symbols.append({
                                    'token': symbol['token'],
                                    'symbol': symbol['symbol'],
                                    'lotsize': symbol.get('lotsize', '1'),
                                    'ticksize': symbol.get('ticksize', '0.05'),
                                    'tradingsymbol': symbol['tsym'],
                                    'expiry': symbol.get('exd', ''),  # Expiry date
                                    'instrument': symbol.get('inst', ''),  # FUTSTK, OPTSTK, etc.
                                    'optiontype': symbol.get('optt', 'XX'),  # CE, PE, XX
                                    'strikeprice': symbol.get('strprc', '0')  # Strike price for options
                                })
                except Exception as e:
                    self.logger.error(f"Error searching NFO symbol {stock}: {str(e)}")
                    continue
            
            # Get index derivatives
            for index in ['NIFTY', 'BANKNIFTY', 'FINNIFTY']:
                try:
                    search_resp = self.api.searchscrip(exchange='NFO', searchtext=index)
                    if search_resp and 'values' in search_resp:
                        for symbol in search_resp['values']:
                            if symbol['symbol'] == index:
                                nfo_symbols.append({
                                    'token': symbol['token'],
                                    'symbol': symbol['symbol'],
                                    'lotsize': symbol.get('lotsize', '1'),
                                    'ticksize': symbol.get('ticksize', '0.05'),
                                    'tradingsymbol': symbol['tsym'],
                                    'expiry': symbol.get('exd', ''),
                                    'instrument': symbol.get('inst', ''),
                                    'optiontype': symbol.get('optt', 'XX'),
                                    'strikeprice': symbol.get('strprc', '0')
                                })
                except Exception as e:
                    self.logger.error(f"Error searching NFO index {index}: {str(e)}")
                    continue
            
            if nfo_symbols:
                self.nse_fo = pd.DataFrame(nfo_symbols)
                # Save to both master and symbols directory
                self._save_master_file(self.nse_fo, 'nse_fo')
                nfo_symbol_file = os.path.join(self.symbols_directory, 'NFO.csv')
                self.nse_fo.to_csv(nfo_symbol_file, index=False)
                self.logger.info(f"Saved {len(nfo_symbols)} NFO symbols to {nfo_symbol_file}")
            else:
                raise ValueError("Failed to download any NFO symbols")
                
            self.logger.info("Successfully downloaded and saved master files")
            
        except Exception as e:
            self.logger.error(f"Error downloading master files: {str(e)}")
            raise
            
    def _save_master_file(self, df, name):
        """Save master file to CSV"""
        if df is not None:
            try:
                # Ensure master directory exists
                if not os.path.exists(self.master_directory):
                    os.makedirs(self.master_directory)
                
                filename = os.path.join(
                    self.master_directory,
                    f"{name}_master_{datetime.now().strftime('%Y%m%d')}.csv"
                )
                df.to_csv(filename, index=False)
                self.logger.info(f"Saved {name} master file to {filename}")
            except Exception as e:
                self.logger.error(f"Error saving master file {name}: {str(e)}")
                raise
            
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
                
            # Search for the symbol (case-insensitive) - try both symbol and tradingsymbol columns
            symbol_match = df[
                (df['symbol'].str.lower() == symbol.lower())
            ]
            
            # If not found by symbol, try trading symbol (if column exists)
            if len(symbol_match) == 0 and 'tradingsymbol' in df.columns:
                symbol_match = df[
                    (df['tradingsymbol'].str.lower() == symbol.lower())
                ]
            
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
            # First, ensure we have the correct column names
            required_columns = ['exchange', 'token', 'lotsize', 'symbol', 'tradingsymbol', 
                              'expiry', 'instrument', 'optiontype']
            if not all(col in self.nse_fo.columns for col in required_columns):
                self.logger.error(f"Missing required columns in NFO file. Available columns: {self.nse_fo.columns}")
                return []

            # Filter for index futures
            futures_df = self.nse_fo[
                (self.nse_fo['instrument'] == 'FUTIDX') &  # Index futures
                (self.nse_fo['optiontype'] == 'XX')        # Futures have XX as option type
            ].copy()
            
            if futures_df.empty:
                self.logger.error("No index futures found in NFO file")
                return []
                
            self.logger.info(f"Found {len(futures_df)} total index futures")
            self.logger.debug(f"Sample futures: {futures_df[['symbol', 'tradingsymbol', 'expiry']].head()}")
            
            # Convert expiry to datetime for sorting
            futures_df['expiry_date'] = pd.to_datetime(futures_df['expiry'], format='%d-%b-%Y')
            
            # Filter out expired contracts (keep only contracts expiring today or later)
            today = datetime.now().date()
            futures_df = futures_df[futures_df['expiry_date'].dt.date >= today]
            
            if futures_df.empty:
                self.logger.warning("No active futures contracts found (all expired)")
                return []
            
            futures_df = futures_df.sort_values('expiry_date')
            
            # Define the indices we're interested in
            target_indices = {
                'NIFTY': {'symbol': 'NIFTY', 'lot_size': 75},
                'BANKNIFTY': {'symbol': 'BANKNIFTY', 'lot_size': 30},
                'FINNIFTY': {'symbol': 'FINNIFTY', 'lot_size': 65}
            }
            
            # For each index we're interested in
            for index_name, index_info in target_indices.items():
                try:
                    # Filter for the specific index using exact symbol match
                    index_futures = futures_df[futures_df['symbol'].str.strip() == index_info['symbol']]
                    
                    if index_futures.empty:
                        self.logger.warning(f"No active futures found for {index_name}")
                        self.logger.debug(f"Available symbols: {futures_df['symbol'].unique()}")
                        continue
                    
                    # Get the nearest expiry contract (first active one)
                    current_future = index_futures.iloc[0]
                    
                    # Debug log the matched future details
                    self.logger.debug(
                        f"Found future for {index_name}: "
                        f"Symbol={current_future['symbol']}, "
                        f"TradingSymbol={current_future['tradingsymbol']}, "
                        f"Expiry={current_future['expiry']}, "
                        f"LotSize={current_future['lotsize']}"
                    )
                    
                    symbols.append({
                        'symbol': current_future['tradingsymbol'],
                        'token': str(current_future['token']),
                        'exchange': 'NFO',
                        'lot_size': int(current_future['lotsize']),
                        'index_name': index_name,
                        'expiry': current_future['expiry'],
                        'instrument': 'FUTIDX'
                    })
                    
                    self.logger.info(
                        f"Selected {index_name} future: {current_future['tradingsymbol']} "
                        f"(Expiry: {current_future['expiry']})"
                    )
                        
                except Exception as e:
                    self.logger.error(f"Error processing {index_name} futures: {str(e)}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error getting index futures: {str(e)}")
            return []
            
        if not symbols:
            self.logger.error("No valid index futures found")
        else:
            self.logger.info(f"Successfully found {len(symbols)} index futures")
            
        return symbols

    def get_index_options(self, index_name=None, expiry=None, strike_range=5):
        """Get list of index options
        Args:
            index_name: Specific index to get options for (NIFTY, BANKNIFTY, FINNIFTY)
            expiry: Specific expiry date (format: DD-MMM-YYYY)
            strike_range: Number of strikes above and below current price
        """
        if self.nse_fo is None:
            self.logger.error("NFO symbols not loaded. Call load_symbol_files() first.")
            return []
            
        symbols = []
        try:
            # Filter for index options
            options_df = self.nse_fo[
                (self.nse_fo['instrument'] == 'OPTIDX') &  # Index options
                (self.nse_fo['optiontype'].isin(['CE', 'PE']))  # Call and Put options
            ].copy()
            
            if options_df.empty:
                self.logger.error("No index options found in NFO file")
                return []
            
            self.logger.info(f"Found {len(options_df)} total index options")
                
            # Convert expiry to datetime for sorting
            options_df['expiry_date'] = pd.to_datetime(options_df['expiry'], format='%d-%b-%Y')
            options_df = options_df.sort_values(['expiry_date', 'strikeprice'])
            
            # Filter by index if specified
            if index_name:
                options_df = options_df[options_df['symbol'] == index_name]
                self.logger.info(f"Found {len(options_df)} options for {index_name}")
            
            # Filter by expiry if specified
            if expiry:
                options_df = options_df[options_df['expiry'] == expiry]
            else:
                # Get nearest expiry
                min_expiry = options_df['expiry_date'].min()
                options_df = options_df[options_df['expiry_date'] == min_expiry]
                self.logger.info(f"Filtered to nearest expiry: {min_expiry.strftime('%d-%b-%Y')}")
            
            # Get current market price for ATM strike selection
            if index_name:
                indices = [index_name]
            else:
                indices = ['NIFTY', 'BANKNIFTY', 'FINNIFTY']
                
            for index in indices:
                try:
                    # Get current future price as reference
                    futures = self.get_index_futures()
                    current_future = next((f for f in futures if f['index_name'] == index), None)
                    
                    if not current_future:
                        self.logger.warning(f"No future found for {index}, skipping options")
                        continue
                        
                    # Get quote for current price
                    quote = self.api.get_quotes('NFO', current_future['token'])
                    if not quote:
                        self.logger.warning(f"No quote available for {index} future, skipping options")
                        continue
                        
                    current_price = float(quote.get('lp', 0))
                    if current_price <= 0:
                        self.logger.warning(f"Invalid price for {index} future, skipping options")
                        continue
                    
                    # Filter options for this index
                    index_options = options_df[options_df['symbol'] == index].copy()
                    
                    if index_options.empty:
                        self.logger.warning(f"No options found for {index}")
                        continue
                    
                    # Find ATM strike
                    index_options['strike_diff'] = abs(index_options['strikeprice'] - current_price)
                    atm_strike = index_options.loc[index_options['strike_diff'].idxmin(), 'strikeprice']
                    
                    self.logger.info(
                        f"{index}: Future price = {current_price:.2f}, "
                        f"ATM strike = {atm_strike:.2f}"
                    )
                    
                    # Get strikes within range
                    strike_interval = index_options['strikeprice'].diff().mode().iloc[0]
                    min_strike = atm_strike - (strike_range * strike_interval)
                    max_strike = atm_strike + (strike_range * strike_interval)
                    
                    selected_options = index_options[
                        (index_options['strikeprice'] >= min_strike) &
                        (index_options['strikeprice'] <= max_strike)
                    ]
                    
                    self.logger.info(
                        f"Selected strikes for {index}: "
                        f"{min_strike:.2f} to {max_strike:.2f} "
                        f"(interval: {strike_interval:.2f})"
                    )
                    
                    # Add to symbols list
                    for _, option in selected_options.iterrows():
                        symbols.append({
                            'symbol': option['tradingsymbol'],
                            'token': str(option['token']),
                            'exchange': 'NFO',
                            'lot_size': int(option['lotsize']),
                            'index_name': index,
                            'expiry': option['expiry'],
                            'strike': float(option['strikeprice']),
                            'option_type': option['optiontype'],
                            'instrument': 'OPTIDX'
                        })
                        
                        self.logger.debug(
                            f"Added {index} {option['optiontype']} "
                            f"@ {option['strikeprice']} "
                            f"(Token: {option['token']})"
                        )
                        
                except Exception as e:
                    self.logger.error(f"Error processing {index} options: {str(e)}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error getting index options: {str(e)}")
            return []
            
        if not symbols:
            self.logger.error("No valid index options found")
        else:
            self.logger.info(f"Successfully selected {len(symbols)} options")
            
        return symbols

    def get_all_index_derivatives(self):
        """Get both futures and options for indices"""
        derivatives = []
        
        # Get futures
        self.logger.info("Fetching index futures...")
        futures = self.get_index_futures()
        if futures:
            derivatives.extend(futures)
            self.logger.info(f"Added {len(futures)} futures contracts")
            
        # Get options (5 strikes above and below ATM for each index)
        self.logger.info("Fetching index options...")
        for index in ['NIFTY', 'BANKNIFTY', 'FINNIFTY']:
            options = self.get_index_options(index_name=index, strike_range=5)
            if options:
                derivatives.extend(options)
                ce_count = len([opt for opt in options if opt['option_type'] == 'CE'])
                pe_count = len([opt for opt in options if opt['option_type'] == 'PE'])
                self.logger.info(
                    f"Added {ce_count} calls and {pe_count} puts for {index}"
                )
        
        self.logger.info(
            f"Total derivatives selected: {len(derivatives)} "
            f"(Futures: {len(futures)}, "
            f"Options: {len(derivatives) - len(futures)})"
        )
        return derivatives

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

    def get_stock_symbols(self):
        """Get list of specified stock symbols from NSE Cash segment"""
        if self.nse_cash is None:
            self.logger.error("NSE Cash symbols not loaded. Call load_symbol_files() first.")
            return []
            
        symbols = []
        try:
            # Filter for our specific stock list
            for stock in self.stock_symbols:
                stock_data = self.nse_cash[
                    self.nse_cash['symbol'].str.upper() == stock.upper()
                ]
                
                if not stock_data.empty:
                    symbol_info = stock_data.iloc[0]
                    symbols.append({
                        'symbol': symbol_info['symbol'],
                        'token': str(symbol_info['token']),
                        'exchange': 'NSE',
                        'instrument': 'EQ',
                        'lotsize': int(symbol_info.get('lotsize', 1)),
                        'tick_size': float(symbol_info.get('ticksize', 0.05))
                    })
                    self.logger.debug(f"Added NSE Cash symbol: {symbol_info['symbol']}")
                else:
                    self.logger.warning(f"Symbol not found in NSE Cash: {stock}")
                    
        except Exception as e:
            self.logger.error(f"Error getting stock symbols: {str(e)}")
            
        self.logger.info(f"Found {len(symbols)} NSE Cash symbols")
        return symbols

    def get_stock_futures(self):
        """Get list of stock futures for specified symbols"""
        if self.nse_fo is None:
            self.logger.error("NFO symbols not loaded. Call load_symbol_files() first.")
            return []
            
        symbols = []
        try:
            # Filter for stock futures of our specific stocks
            futures_df = self.nse_fo[
                (self.nse_fo['instrument'] == 'FUTSTK') &  # Stock futures
                (self.nse_fo['optiontype'] == 'XX') &      # Futures have XX as option type
                (self.nse_fo['symbol'].isin(self.stock_symbols))  # Only our stocks
            ].copy()
            
            if futures_df.empty:
                self.logger.error("No stock futures found in NFO file")
                return []
                
            # Convert expiry to datetime for sorting
            futures_df['expiry_date'] = pd.to_datetime(futures_df['expiry'], format='%d-%b-%Y')
            futures_df = futures_df.sort_values('expiry_date')
            
            # Group by symbol and get nearest expiry for each
            for stock in self.stock_symbols:
                stock_futures = futures_df[futures_df['symbol'] == stock]
                if not stock_futures.empty:
                    # Get nearest expiry contract
                    current_future = stock_futures.iloc[0]
                    symbols.append({
                        'symbol': current_future['tradingsymbol'],
                        'token': str(current_future['token']),
                        'exchange': 'NFO',
                        'lot_size': int(current_future['lotsize']),
                        'stock_name': stock,
                        'expiry': current_future['expiry'],
                        'instrument': 'FUTSTK'
                    })
                    self.logger.debug(f"Added future for {stock}: {current_future['tradingsymbol']}")
                else:
                    self.logger.warning(f"No futures found for {stock}")
                    
        except Exception as e:
            self.logger.error(f"Error getting stock futures: {str(e)}")
            return []
            
        self.logger.info(f"Found {len(symbols)} stock futures")
        return symbols

    def get_stock_options(self, strike_range=5):
        """Get list of stock options for specified symbols"""
        if self.nse_fo is None:
            self.logger.error("NFO symbols not loaded. Call load_symbol_files() first.")
            return []
            
        symbols = []
        try:
            # Filter for stock options of our specific stocks
            options_df = self.nse_fo[
                (self.nse_fo['instrument'] == 'OPTSTK') &  # Stock options
                (self.nse_fo['optiontype'].isin(['CE', 'PE'])) &  # Call and Put options
                (self.nse_fo['symbol'].isin(self.stock_symbols))  # Only our stocks
            ].copy()
            
            if options_df.empty:
                self.logger.error("No stock options found in NFO file")
                return []
                
            # Convert expiry to datetime for sorting
            options_df['expiry_date'] = pd.to_datetime(options_df['expiry'], format='%d-%b-%Y')
            options_df = options_df.sort_values(['expiry_date', 'strikeprice'])
            
            # Get nearest expiry
            min_expiry = options_df['expiry_date'].min()
            options_df = options_df[options_df['expiry_date'] == min_expiry]
            
            # Process each stock
            for stock in self.stock_symbols:
                try:
                    # Get current stock price from NSE Cash
                    stock_info = self.get_token_info(stock, 'NSE')
                    if not stock_info:
                        self.logger.warning(f"No price info found for {stock}")
                        continue
                        
                    quote = self.api.get_quotes('NSE', stock_info['token'])
                    if not quote:
                        self.logger.warning(f"No quote available for {stock}")
                        continue
                        
                    current_price = float(quote.get('lp', 0))
                    if current_price <= 0:
                        self.logger.warning(f"Invalid price for {stock}")
                        continue
                    
                    # Filter options for this stock
                    stock_options = options_df[options_df['symbol'] == stock].copy()
                    
                    if stock_options.empty:
                        self.logger.warning(f"No options found for {stock}")
                        continue
                    
                    # Find ATM strike
                    stock_options['strike_diff'] = abs(stock_options['strikeprice'] - current_price)
                    atm_strike = stock_options.loc[stock_options['strike_diff'].idxmin(), 'strikeprice']
                    
                    # Get strikes within range
                    strike_interval = stock_options['strikeprice'].diff().mode().iloc[0]
                    min_strike = atm_strike - (strike_range * strike_interval)
                    max_strike = atm_strike + (strike_range * strike_interval)
                    
                    selected_options = stock_options[
                        (stock_options['strikeprice'] >= min_strike) &
                        (stock_options['strikeprice'] <= max_strike)
                    ]
                    
                    # Add selected options to symbols list
                    for _, option in selected_options.iterrows():
                        symbols.append({
                            'symbol': option['tradingsymbol'],
                            'token': str(option['token']),
                            'exchange': 'NFO',
                            'lot_size': int(option['lotsize']),
                            'stock_name': stock,
                            'expiry': option['expiry'],
                            'strike': float(option['strikeprice']),
                            'option_type': option['optiontype'],
                            'instrument': 'OPTSTK'
                        })
                        self.logger.debug(
                            f"Added {stock} {option['optiontype']} @ {option['strikeprice']}"
                        )
                        
                except Exception as e:
                    self.logger.error(f"Error processing {stock} options: {str(e)}")
                    continue
                    
        except Exception as e:
            self.logger.error(f"Error getting stock options: {str(e)}")
            return []
            
        self.logger.info(f"Found {len(symbols)} stock options")
        return symbols

    def get_etf_symbols(self):
        """Get list of ETF symbols from both NSE and BSE, tracking common ETFs on both exchanges"""
        etf_symbols = []
        
        try:
            # First get the common ETFs if not already scanned
            if not self.etf_symbols:
                self.scan_common_etfs()
            
            # Track common ETFs from both exchanges
            common_etfs = set()
            nse_etfs = {}
            bse_etfs = {}
            
            # Get ETFs from NSE
            if self.nse_cash is not None:
                nse_matches = self.nse_cash[
                    (self.nse_cash['symbol'].isin(self.etf_symbols)) |
                    (self.nse_cash['symbol'].str.contains('ETF|BEES', case=False, na=False))
                ]
                
                for _, etf in nse_matches.iterrows():
                    symbol = etf['symbol'].upper()
                    nse_etfs[symbol] = {
                        'symbol': symbol,
                        'token': str(etf['token']),
                        'exchange': 'NSE',
                        'instrument': 'ETF',
                        'lotsize': int(etf.get('lotsize', 1)),
                        'tick_size': float(etf.get('ticksize', 0.05))
                    }
                    
            # Get ETFs from BSE
            if self.bse is not None:
                bse_matches = self.bse[
                    (self.bse['symbol'].isin(self.etf_symbols)) |
                    (self.bse['symbol'].str.contains('ETF|BEES', case=False, na=False))
                ]
                
                for _, etf in bse_matches.iterrows():
                    symbol = etf['symbol'].upper()
                    bse_etfs[symbol] = {
                        'symbol': symbol,
                        'token': str(etf['token']),
                        'exchange': 'BSE',
                        'instrument': 'ETF',
                        'lotsize': int(etf.get('lotsize', 1)),
                        'tick_size': float(etf.get('ticksize', 0.05))
                    }
            
            # Add all NSE ETFs to the tracking list
            for symbol, etf_data in nse_etfs.items():
                etf_symbols.append(etf_data)
                if symbol in bse_etfs:
                    common_etfs.add(symbol)
                    # Also add BSE version for common ETFs
                    etf_symbols.append(bse_etfs[symbol])
                    self.logger.debug(f"Tracking ETF {symbol} on both NSE and BSE")
                else:
                    self.logger.debug(f"Tracking ETF {symbol} on NSE only")
            
            # Add BSE-only ETFs
            for symbol, etf_data in bse_etfs.items():
                if symbol not in nse_etfs:
                    etf_symbols.append(etf_data)
                    self.logger.debug(f"Tracking ETF {symbol} on BSE only")
            
            self.logger.info(
                f"Total ETFs to track: {len(etf_symbols)} "
                f"(Common: {len(common_etfs)}, "
                f"NSE total: {len(nse_etfs)}, "
                f"BSE total: {len(bse_etfs)})"
            )
            
        except Exception as e:
            self.logger.error(f"Error getting ETF symbols: {str(e)}")
            
        return etf_symbols

    def get_all_symbols(self):
        """Get all symbols (stocks, indices, ETFs and their derivatives)"""
        all_symbols = []
        
        # Get ETF symbols
        self.logger.info("Fetching ETF symbols...")
        etf_symbols = self.get_etf_symbols()
        if etf_symbols:
            all_symbols.extend(etf_symbols)
            self.logger.info(f"Added {len(etf_symbols)} ETF symbols")
        
        # Get stock symbols from NSE Cash
        self.logger.info("Fetching NSE Cash symbols...")
        cash_symbols = self.get_stock_symbols()
        if cash_symbols:
            all_symbols.extend(cash_symbols)
            self.logger.info(f"Added {len(cash_symbols)} NSE Cash symbols")
            
        # Get index derivatives
        self.logger.info("Fetching index derivatives...")
        index_derivatives = self.get_all_index_derivatives()
        if index_derivatives:
            all_symbols.extend(index_derivatives)
            self.logger.info(f"Added {len(index_derivatives)} index derivatives")
            
        # Get stock futures
        self.logger.info("Fetching stock futures...")
        stock_futures = self.get_stock_futures()
        if stock_futures:
            all_symbols.extend(stock_futures)
            self.logger.info(f"Added {len(stock_futures)} stock futures")
            
        # Get stock options
        self.logger.info("Fetching stock options...")
        stock_options = self.get_stock_options()
        if stock_options:
            all_symbols.extend(stock_options)
            self.logger.info(f"Added {len(stock_options)} stock options")
            
        self.logger.info(
            f"Total symbols selected: {len(all_symbols)} "
            f"(ETFs: {len(etf_symbols)}, "
            f"Cash: {len(cash_symbols)}, "
            f"Index Derivatives: {len(index_derivatives)}, "
            f"Stock Futures: {len(stock_futures)}, "
            f"Stock Options: {len(stock_options)})"
        )
        return all_symbols

    def get_data_collection_symbols(self):
        """Get symbols for data collection only - cash stocks + index derivatives"""
        all_symbols = []
        
        # Get stock symbols from NSE Cash (equity data for all constituent stocks)
        self.logger.info("Fetching NSE Cash symbols for data collection...")
        cash_symbols = self.get_stock_symbols()
        if cash_symbols:
            all_symbols.extend(cash_symbols)
            self.logger.info(f"Added {len(cash_symbols)} NSE Cash symbols")
            
        # Get index derivatives (futures + ATM options for NIFTY, BANKNIFTY, FINNIFTY)
        self.logger.info("Fetching index derivatives for data collection...")
        index_derivatives = self.get_all_index_derivatives()
        if index_derivatives:
            all_symbols.extend(index_derivatives)
            self.logger.info(f"Added {len(index_derivatives)} index derivatives")
        
        # Count by type
        futures_count = len([s for s in all_symbols if s.get('instrument') in ['FUTIDX', 'FUTSTK']])
        options_count = len([s for s in all_symbols if s.get('instrument') in ['OPTIDX', 'OPTSTK']])
        cash_count = len([s for s in all_symbols if s.get('instrument') == 'EQ'])
        
        self.logger.info(
            f"Data collection symbols selected: {len(all_symbols)} total "
            f"(Cash: {cash_count}, Futures: {futures_count}, Options: {options_count})"
        )
        return all_symbols

    def scan_common_etfs(self):
        """Scan and find common ETFs between NSE and BSE"""
        try:
            if self.nse_cash is None or self.bse is None:
                self.logger.error("Both NSE and BSE files must be loaded first")
                return
            
            # Find ETFs in NSE
            nse_etfs = self.nse_cash[
                self.nse_cash['symbol'].str.contains('ETF|BEES', case=False, na=False)
            ]
            
            # Find ETFs in BSE
            bse_etfs = self.bse[
                self.bse['symbol'].str.contains('ETF|BEES', case=False, na=False)
            ]
            
            # Get unique ETF symbols
            nse_etf_symbols = set(nse_etfs['symbol'].str.upper())
            bse_etf_symbols = set(bse_etfs['symbol'].str.upper())
            
            # Find common ETFs
            common_etfs = nse_etf_symbols.intersection(bse_etf_symbols)
            
            # Find ETFs unique to each exchange
            nse_only = nse_etf_symbols - bse_etf_symbols
            bse_only = bse_etf_symbols - nse_etf_symbols
            
            # Log findings
            self.logger.info(f"\nETF Analysis:")
            self.logger.info(f"Total ETFs in NSE: {len(nse_etf_symbols)}")
            self.logger.info(f"Total ETFs in BSE: {len(bse_etf_symbols)}")
            self.logger.info(f"Common ETFs: {len(common_etfs)}")
            
            self.logger.info("\nCommon ETFs between NSE and BSE:")
            for etf in sorted(common_etfs):
                self.logger.info(f"- {etf}")
            
            self.logger.info("\nETFs only in NSE:")
            for etf in sorted(nse_only):
                self.logger.info(f"- {etf}")
            
            self.logger.info("\nETFs only in BSE:")
            for etf in sorted(bse_only):
                self.logger.info(f"- {etf}")
            
            # Update the etf_symbols list with all discovered ETFs
            self.etf_symbols = list(nse_etf_symbols.union(bse_etf_symbols))
            self.logger.info(f"\nUpdated ETF tracking list with {len(self.etf_symbols)} symbols")
            
            return {
                'common': common_etfs,
                'nse_only': nse_only,
                'bse_only': bse_only,
                'nse_total': len(nse_etf_symbols),
                'bse_total': len(bse_etf_symbols)
            }
            
        except Exception as e:
            self.logger.error(f"Error scanning ETFs: {str(e)}")
            return None 