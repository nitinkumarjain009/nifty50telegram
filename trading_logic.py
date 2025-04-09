# trading_logic.py
import pandas as pd
import json
import os
import logging
from datetime import datetime

PORTFOLIO_FILE = 'paper_portfolio.json'
INITIAL_CASH = 100000.00

def generate_recommendations(symbol, df):
    """Generates Buy/Sell/Hold signal based on indicator values."""
    if df.empty or len(df) < 2:
        return None # Not enough data

    last = df.iloc[-1]
    prev = df.iloc[-2] # Previous day's data for crossovers

    signal = 'HOLD'
    reason = "Neutral"
    target = None
    stop_loss_pct = 0.03 # e.g., 3% stop loss
    target_pct = 0.05    # e.g., 5% target profit

    # --- Simple Strategy Example (Combine multiple indicators) ---
    # Buy Conditions:
    buy_condition_1 = last['SMA_20'] > last['SMA_50'] and prev['SMA_20'] <= prev['SMA_50'] # SMA Crossover
    buy_condition_2 = last['RSI'] < 70 # Not overbought
    buy_condition_3 = last['MACD'] > last['MACD_Signal'] # MACD confirms bullish
    buy_condition_4 = last['Close'] > last['Bollinger_Lower'] # Not hugging lower band too tightly

    # Sell Conditions (Exit Long):
    sell_condition_1 = last['SMA_20'] < last['SMA_50'] and prev['SMA_20'] >= prev['SMA_50'] # SMA Crossunder
    sell_condition_2 = last['RSI'] > 30 # Not oversold (avoid selling at bottom)
    sell_condition_3 = last['MACD'] < last['MACD_Signal'] # MACD confirms bearish

    if buy_condition_1 and buy_condition_2 and buy_condition_3:
        signal = 'BUY'
        target = last['Close'] * (1 + target_pct)
        reason = f"SMA Crossover, RSI<70, MACD Bullish"
    elif sell_condition_1 and sell_condition_2 and sell_condition_3:
        signal = 'SELL' # Indicating exit long position
        target = None # No specific target when selling to exit
        reason = f"SMA Crossunder, RSI>30, MACD Bearish"
    else:
        # Refine Hold based on position relative to bands/averages
        if last['Close'] > last['SMA_50'] and last['RSI'] > 50:
             reason = "Above SMA50, RSI>50 - Potential Hold/Weak Buy"
        elif last['Close'] < last['SMA_50'] and last['RSI'] < 50:
             reason = "Below SMA50, RSI<50 - Potential Hold/Weak Sell"
        # Keep signal as 'HOLD' if no strong buy/sell

    if signal != 'HOLD':
        return {
            'symbol': symbol,
            'signal': signal,
            'price': last['Close'],
            'target': target,
            'stop_loss': last['Close'] * (1 - stop_loss_pct) if signal == 'BUY' else None,
            'reason': reason,
            'timestamp': last.name.strftime('%Y-%m-%d') # Use index (date)
        }
    return None # No strong signal


# --- Paper Trading ---

def load_portfolio():
    """Loads the paper trading portfolio from a JSON file."""
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            logging.error(f"Error decoding JSON from {PORTFOLIO_FILE}. Resetting portfolio.")
            # Fall through to return default if file is corrupted
        except Exception as e:
            logging.error(f"Error loading portfolio file {PORTFOLIO_FILE}: {e}. Resetting portfolio.")
            # Fall through to return default

    # Return default if file doesn't exist or loading failed
    return {'cash': INITIAL_CASH, 'holdings': {}}

def save_portfolio(portfolio):
    """Saves the paper trading portfolio to a JSON file."""
    try:
        with open(PORTFOLIO_FILE, 'w') as f:
            json.dump(portfolio, f, indent=4)
    except IOError as e:
        logging.error(f"Error saving portfolio file {PORTFOLIO_FILE}: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred saving portfolio: {e}")


def update_paper_portfolio(recommendations, current_prices):
    """Updates the paper portfolio based on recommendations and current prices."""
    portfolio = load_portfolio()
    trades_executed = [] # Keep track of trades for logging/display

    for rec in recommendations:
        symbol = rec['symbol']
        signal = rec['signal']
        price = current_prices.get(symbol) # Use the latest price for execution

        if price is None:
            logging.warning(f"Skipping trade for {symbol}: No current price available.")
            continue

        # --- Execute BUY ---
        if signal == 'BUY' and symbol not in portfolio['holdings']:
            # Simple logic: Invest a fixed amount or percentage of cash per trade
            trade_value = min(portfolio['cash'] * 0.1, 10000) # Invest 10% or 10k, whichever is smaller
            if portfolio['cash'] >= trade_value and trade_value > price: # Can afford at least 1 share
                quantity = int(trade_value // price)
                cost = quantity * price
                portfolio['cash'] -= cost
                portfolio['holdings'][symbol] = {'quantity': quantity, 'buy_price': price}
                trades_executed.append(f"BOUGHT {quantity} {symbol} @ {price:.2f}")
                logging.info(f"Paper Trade Executed: BOUGHT {quantity} {symbol} @ {price:.2f}")
            else:
                logging.info(f"Skipping BUY for {symbol}: Insufficient cash or trade value too low.")

        # --- Execute SELL (Exit Long) ---
        elif signal == 'SELL' and symbol in portfolio['holdings']:
            holding = portfolio['holdings'][symbol]
            quantity = holding['quantity']
            proceeds = quantity * price
            portfolio['cash'] += proceeds
            buy_price = holding['buy_price']
            pnl = (price - buy_price) * quantity
            del portfolio['holdings'][symbol]
            trades_executed.append(f"SOLD {quantity} {symbol} @ {price:.2f} (P/L: {pnl:.2f})")
            logging.info(f"Paper Trade Executed: SOLD {quantity} {symbol} @ {price:.2f} (P/L: {pnl:.2f})")

    save_portfolio(portfolio)
    return portfolio, trades_executed


def get_portfolio_value(portfolio, current_prices):
    """Calculates the total value of the paper portfolio."""
    holdings_value = 0
    detailed_holdings = []
    for symbol, data in portfolio.get('holdings', {}).items():
        current_price = current_prices.get(symbol)
        if current_price:
            value = data['quantity'] * current_price
            pnl = (current_price - data['buy_price']) * data['quantity']
            holdings_value += value
            detailed_holdings.append({
                'symbol': symbol,
                'quantity': data['quantity'],
                'buy_price': data['buy_price'],
                'current_price': current_price,
                'value': value,
                'pnl': pnl
            })
        else:
             # If price is missing, use buy price for value calculation (conservative)
            value = data['quantity'] * data['buy_price']
            holdings_value += value
            detailed_holdings.append({
                'symbol': symbol,
                'quantity': data['quantity'],
                'buy_price': data['buy_price'],
                'current_price': 'N/A',
                'value': value,
                'pnl': 0.0
            })


    total_value = portfolio.get('cash', 0) + holdings_value
    return total_value, portfolio.get('cash', 0), detailed_holdings


# --- Backtesting Engine ---

def run_backtest(symbol, historical_data, initial_capital=100000):
    """Runs a simple backtest for a single stock using the defined strategy."""
    from indicators import calculate_all_indicators # Avoid circular import if moved

    if historical_data.empty or len(historical_data) < 50: # Need enough data for indicators
        return {"error": "Not enough historical data for backtest.", "performance": {}}

    # Calculate indicators on the full historical dataset
    data = calculate_all_indicators(historical_data.copy()) # Use a copy
    data = data.dropna() # Remove rows where indicators couldn't be calculated

    cash = initial_capital
    holdings = 0 # Quantity of the stock held
    portfolio_values = [] # Track portfolio value over time
    trades = [] # Record trades

    for i in range(1, len(data)): # Start from 1 to compare with previous day
        current_row = data.iloc[i]
        prev_row = data.iloc[i-1]
        current_date = data.index[i]
        current_price = current_row['Close']

        # --- Generate Signal based on current and previous row data ---
        signal = 'HOLD'
        # Buy Conditions (using indicators *up to this point*)
        if current_row['SMA_20'] > current_row['SMA_50'] and prev_row['SMA_20'] <= prev_row['SMA_50'] \
           and current_row['RSI'] < 70 and current_row['MACD'] > current_row['MACD_Signal']:
            signal = 'BUY'

        # Sell Conditions
        elif current_row['SMA_20'] < current_row['SMA_50'] and prev_row['SMA_20'] >= prev_row['SMA_50'] \
             and current_row['RSI'] > 30 and current_row['MACD'] < current_row['MACD_Signal']:
            signal = 'SELL'

        # --- Execute Trades ---
        if signal == 'BUY' and holdings == 0: # Buy only if not holding
            # Simple allocation: buy as many shares as possible with available cash
            quantity_to_buy = int(cash // current_price)
            if quantity_to_buy > 0:
                cost = quantity_to_buy * current_price
                cash -= cost
                holdings = quantity_to_buy
                trades.append({'date': current_date.strftime('%Y-%m-%d'), 'type': 'BUY', 'price': current_price, 'quantity': quantity_to_buy})

        elif signal == 'SELL' and holdings > 0: # Sell only if holding
            proceeds = holdings * current_price
            cash += proceeds
            trades.append({'date': current_date.strftime('%Y-%m-%d'), 'type': 'SELL', 'price': current_price, 'quantity': holdings})
            holdings = 0

        # --- Calculate Portfolio Value for the day ---
        current_portfolio_value = cash + (holdings * current_price)
        portfolio_values.append(current_portfolio_value)

    # --- Calculate Performance Metrics ---
    final_portfolio_value = portfolio_values[-1] if portfolio_values else initial_capital
    total_return_pct = ((final_portfolio_value / initial_capital) - 1) * 100
    # Max Drawdown (simplified)
    peak = max(portfolio_values) if portfolio_values else initial_capital
    trough = min(portfolio_values) if portfolio_values else initial_capital # Simplification
    max_drawdown_pct = ((peak - trough) / peak) * 100 if peak > 0 else 0 # Basic drawdown calc

    # More advanced metrics like Sharpe Ratio would require risk-free rate data and daily returns

    performance = {
        "symbol": symbol,
        "period_start": data.index[0].strftime('%Y-%m-%d'),
        "period_end": data.index[-1].strftime('%Y-%m-%d'),
        "initial_capital": initial_capital,
        "final_portfolio_value": final_portfolio_value,
        "total_return_pct": total_return_pct,
        "max_drawdown_pct": max_drawdown_pct, # Simplified
        "number_of_trades": len(trades),
    }

    # Limit trades shown if too many
    max_trades_to_show = 20
    if len(trades) > max_trades_to_show:
       trades = trades[:max_trades_to_show//2] + trades[-max_trades_to_show//2:] # Show start and end

    return {"performance": performance, "trades": trades}
