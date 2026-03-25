"""Strategy A: Short Strangle (agent.md §8.1).

Implements a simple short strangle strategy based on VIX and day classification.
"""

import logging
from datetime import datetime
from typing import Any, Optional, Dict

from trading_system.core.iron_condor import IC_Position
from trading_system.core.trade_logger import TradeLogger
from trading_system.core.market_data import MarketData
from trading_system.core.order_manager import OrderManager

logger = logging.getLogger(__name__)


class StrategyA:
    def __init__(self, order_manager: OrderManager, market_data: MarketData) -> None:
        self.om = order_manager
        self.md = market_data
        self._position: Optional[IC_Position] = None
        self.instrument = "NIFTY" # Default instrument

    def is_active(self) -> bool:
        return self._position is not None

    def should_enter(self, regime: str, consensus: str, time: datetime.time) -> bool:
        """Entry conditions for Strategy A."""
        # Placeholder for actual strategy logic
        return regime == "CALM" and consensus == "RANGE" and time < datetime.now().replace(hour=10, minute=0).time()

    def enter(self, spot: float, lots: int, expiry: str, time_str: str) -> bool:
        """Enter a new short strangle position."""
        if self.is_active():
            logger.warning("Strategy A already has an active position.")
            return False
        
        # Placeholder logic: Simplified for demonstration
        # In a real scenario, this would calculate strikes based on spot, VIX, etc.
        call_strike = spot + 100
        put_strike = spot - 100
        entry_credit = 20.0 # Placeholder
        
        self._position = IC_Position(
            instrument=self.instrument,
            sc_strike=call_strike, sp_strike=put_strike, lc_strike=call_strike + 50, lp_strike=put_strike - 50,
            entry_credit=entry_credit, lots=lots, entry_time=time_str
        )
        logger.info(f"Strategy A entered: {self.instrument} Strangle @ {spot}, Strikes (CE:{call_strike}, PE:{put_strike}), Credit={entry_credit}, Lots={lots}")
        return True

    def monitor(self) -> Optional[Dict]:
        """Monitor existing position and check for exit conditions."""
        if not self.is_active() or self._position is None:
            return None

        # Placeholder logic: Exit if profit target hit or loss limit reached
        # In a real scenario, this would involve checking P&L against entry credit and stop loss
        
        # Simulate a profitable exit condition
        if self._position.current_premium(self.md) < self._position.entry_credit * 0.5: # Example: exit if premium is less than half of entry credit
            return self.exit("PROFIT_HARVEST")
            
        # Simulate a loss condition (e.g., if PnL becomes negative beyond a threshold)
        # Example: if current_premium > entry_credit * 1.5: # If premium doubles
        #    return self.exit("LOSS_LIMIT")

        return None # No exit condition met

    def exit(self, reason: str) -> Optional[Dict]:
        if not self.is_active() or self._position is None:
            return None

        # Placeholder for actual exit order placement
        # In a real scenario, this would place orders to close the position
        logger.info(f"Strategy A exiting {self.instrument} position. Reason: {reason}")

        # Simulate PnL calculation (needs actual trade data for accuracy)
        # For now, returning placeholder values
        simulated_pnl = -100.0 # Placeholder loss for testing
        
        result = {
            "instrument": self.instrument,
            "time_exit": datetime.now().strftime("%H:%M:%S"),
            "exit_reason": reason,
            "pnl": simulated_pnl,
            "lots": self._position.lots,
            "entry_time": self._position.entry_time,
            # Other relevant fields would be populated here
        }
        self._position = None
        return result

    def force_exit(self) -> Optional[Dict]:
        """Force exit the position."""
        if not self.is_active():
            return None
        logger.info(f"Strategy A force exiting {self.instrument} position.")
        return self.exit("FORCE_EXIT") # Will return simulated PnL

    def save_state(self) -> Optional[Dict]:
        if self._position:
            return self._position.to_dict()
        return None

    def restore_state(self, state: Optional[Dict]) -> None:
        if state:
            self._position = IC_Position.from_dict(state)
            logger.info(f"Restored Strategy A active position: {self._position.sc_sym} ...")
        else:
            self._position = None
