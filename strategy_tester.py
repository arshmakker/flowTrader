import logging
from datetime import datetime, time
import time as time_module

class StrategyTester:
    def __init__(self, api, capital=100000):
        self.api = api
        self.logger = logging.getLogger('StrategyTester')
        self.capital = capital
        
    def is_market_hour(self):
        """Check if current time is within market hours"""
        current_time = datetime.now().time()
        market_start = time(9, 15)  # 9:15 AM
        market_end = time(15, 30)   # 3:30 PM
        return market_start <= current_time <= market_end
    
    def run_strategy(self):
        """Run the trading strategy"""
        self.logger.info("Strategy tester initialized")
        
        try:
            while True:
                if not self.is_market_hour():
                    self.logger.info("Outside market hours. Waiting...")
                    time_module.sleep(60)  # Check every minute
                    continue
                
                # Your strategy logic here
                time_module.sleep(5)  # Update every 5 seconds during market hours
                
        except KeyboardInterrupt:
            self.logger.info("Strategy execution interrupted by user")
        except Exception as e:
            self.logger.error(f"Error in strategy execution: {str(e)}")
            raise 