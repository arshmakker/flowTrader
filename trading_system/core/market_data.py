class MarketData:
    """
    Placeholder for MarketData class.
    This class is responsible for fetching and providing market data.
    """

    def __init__(self):
        print("MarketData initialized.")
        # In a real implementation, this would connect to a market data source.
        pass

    def get_ltp(self, symbol: str) -> float:
        s = symbol.upper()
        if "BANKNIFTY" in s:
            return 45000.0
        elif "NIFTY" in s and "BANKNIFTY" not in s and "FINNIFTY" not in s:
            return 20000.0
        else:
            return 100.0
