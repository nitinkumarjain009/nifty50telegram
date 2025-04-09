# app.py
from flask import Flask, render_template, request # Added request for potential future use
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os
import logging
import time # For timing execution

# Import local modules
from indicators import calculate_all_indicators
from trading_logic import (
    generate_recommendations,
    update_paper_portfolio,
    get_portfolio_value,
    run_backtest,
    INITIAL_CASH # Import initial cash constant
)
from telegram_sender import notify_recommendations # Import the notification function

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Environment Variables ---
# For Telegram, secrets are handled in telegram_sender.py using os.environ
# Example: Set TELEGRAM_TOKEN='your_token' TELEGRAM_CHAT_ID='your_chat_id' ...
#          in your Render environment settings.

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Constants ---
STOCK_LIST_FILE = 'nifty50_stocks.csv'
DATA_FETCH_PERIOD = "6mo" # Fetch 1 year of data for indicator calculation
BACKTEST_SYMBOL = "BSE.NS" # Stock to run backtest example on
BACKTEST_PERIOD = "3mo" # Historical data period for backtesting

# --- Helper Function ---
def get_stock_symbols():
    """Reads stock symbols from the CSV file."""
    try:
        df = pd.read_csv(STOCK_LIST_FILE)
        if 'Symbol' not in df.columns:
             logging.error(f"'Symbol' column not found in {STOCK_LIST_FILE}")
             return []
        symbols = df['Symbol'].dropna().unique().tolist()
        logging.info(f"Loaded {len(symbols)} symbols from {STOCK_LIST_FILE}")
        return symbols
    except FileNotFoundError:
        logging.error(f"Error: {STOCK_LIST_FILE} not found.")
        return []
    except Exception as e:
        logging.error(f"Error reading {STOCK_LIST_FILE}: {e}")
        return []

def fetch_stock_data(symbols, period="6mo"):
    """Fetches historical data for a list of symbols using yfinance."""
    if not symbols:
        return pd.DataFrame()
    try:
        logging.info(f"Fetching {period} data for {len(symbols)} symbols...")
        start_time = time.time()
        # Download data for all symbols at once (more efficient)
        data = yf.download(symbols, period=period, group_by='ticker', auto_adjust=True)
        end_time = time.time()
        logging.info(f"Data fetch completed in {end_time - start_time:.2f} seconds.")

        # If only one symbol, yfinance doesn't create a MultiIndex header. Add it for consistency.
        if len(symbols) == 1 and isinstance(data.columns, pd.Index):
             data.columns = pd.MultiIndex.from_product([symbols, data.columns])

        return data
    except Exception as e:
        logging.error(f"Error fetching data from yfinance: {e}")
        return pd.DataFrame() # Return empty DataFrame on error


# --- Flask Routes ---
@app.route('/')
def index():
    """Main route to display recommendations, portfolio, and backtest."""
    start_render_time = time.time()
    error_message = None
    recommendations_list = []
    current_prices = {} # Store latest close price for portfolio valuation

    symbols = get_stock_symbols()
    if not symbols:
        error_message = f"Could not load stock symbols from {STOCK_LIST_FILE}."
    else:
        # Fetch data for recommendations
        stock_data = fetch_stock_data(symbols, period=DATA_FETCH_PERIOD)

        if stock_data.empty:
            error_message = "Failed to fetch stock data. Please check logs."
        else:
            logging.info("Calculating indicators and generating recommendations...")
            calc_start_time = time.time()
            for symbol in symbols:
                try:
                     # Extract data for the current symbol
                     # Handle potential missing symbols in downloaded data (if yf download failed for some)
                    if symbol in stock_data.columns.levels[0]:
                        df_symbol = stock_data[symbol].copy()
                        df_symbol = df_symbol.dropna(subset=['Close']) # Ensure 'Close' has data

                        if not df_symbol.empty:
                             # Calculate indicators
                            df_with_indicators = calculate_all_indicators(df_symbol)

                            # Get recommendation
                            recommendation = generate_recommendations(symbol, df_with_indicators)
                            if recommendation:
                                recommendations_list.append(recommendation)

                            # Store last close price for portfolio valuation
                            if not df_with_indicators.empty:
                                current_prices[symbol] = df_with_indicators['Close'].iloc[-1]
                        else:
                             logging.warning(f"No data available for {symbol} after dropping NaNs.")
                    else:
                         logging.warning(f"Data for symbol {symbol} not found in downloaded dataset.")

                except Exception as e:
                    logging.error(f"Error processing symbol {symbol}: {e}")
                    # Continue processing other symbols

            calc_end_time = time.time()
            logging.info(f"Indicator calculation and recommendation generation took {calc_end_time - calc_start_time:.2f} seconds.")

            # --- Paper Trading Update ---
            logging.info("Updating paper trading portfolio...")
            paper_portfolio_state, trades_executed = update_paper_portfolio(recommendations_list, current_prices)
            logging.info("Paper trading portfolio update complete.")

            # --- Send Telegram Notification (Only if new recommendations were generated) ---
            if recommendations_list: # Send only if there are actual signals
                logging.info("Sending Telegram notifications...")
                notify_recommendations(recommendations_list)
            else:
                logging.info("No new recommendations to notify via Telegram.")


    # --- Get Paper Portfolio Display Data ---
    portfolio_display = None
    if 'paper_portfolio_state' in locals(): # Check if it was defined
         total_value, cash, holdings_details = get_portfolio_value(paper_portfolio_state, current_prices)
         portfolio_display = {
             'total_value': total_value,
             'cash': cash,
             'holdings': holdings_details
         }
    else:
        # Load portfolio even if recommendations failed, to show current state
         logging.warning("Recommendations failed, loading portfolio state directly.")
         paper_portfolio_state = load_portfolio()
         # Attempt to get current prices again if needed (could be slow)
         if not current_prices and symbols:
             data_now = fetch_stock_data(list(paper_portfolio_state.get('holdings',{}).keys()), period="5d") # Fetch recent data
             if not data_now.empty:
                for sym in paper_portfolio_state.get('holdings',{}).keys():
                    if sym in data_now.columns.levels[0]:
                       current_prices[sym] = data_now[(sym, 'Close')].iloc[-1]
             else:
                 logging.error("Could not fetch current prices for portfolio valuation.")

         total_value, cash, holdings_details = get_portfolio_value(paper_portfolio_state, current_prices)
         portfolio_display = {
             'total_value': total_value,
             'cash': cash,
             'holdings': holdings_details
         }


    # --- Run Backtesting Example ---
    logging.info(f"Running backtest for {BACKTEST_SYMBOL}...")
    backtest_start_time = time.time()
    backtest_results = None
    try:
        backtest_data = fetch_stock_data([BACKTEST_SYMBOL], period=BACKTEST_PERIOD)
        if not backtest_data.empty and BACKTEST_SYMBOL in backtest_data.columns.levels[0]:
             backtest_results = run_backtest(BACKTEST_SYMBOL, backtest_data[BACKTEST_SYMBOL].copy(), initial_capital=INITIAL_CASH)
        elif backtest_data.empty:
            logging.error(f"Failed to fetch data for backtesting symbol {BACKTEST_SYMBOL}.")
            backtest_results = {"error": f"Could not fetch data for {BACKTEST_SYMBOL}."}
        else:
             logging.error(f"Data structure unexpected for backtest symbol {BACKTEST_SYMBOL}.")
             backtest_results = {"error": f"Data structure error for {BACKTEST_SYMBOL}."}
    except Exception as e:
        logging.error(f"Error running backtest for {BACKTEST_SYMBOL}: {e}")
        backtest_results = {"error": f"An error occurred during backtesting: {e}"}
    backtest_end_time = time.time()
    logging.info(f"Backtest execution took {backtest_end_time - backtest_start_time:.2f} seconds.")


    # --- Render Template ---
    end_render_time = time.time()
    logging.info(f"Total page rendering time: {end_render_time - start_render_time:.2f} seconds.")

    return render_template(
        'index.html',
        recommendations=recommendations_list,
        paper_portfolio=portfolio_display,
        initial_capital=INITIAL_CASH, # Pass initial capital to template
        trades_executed=trades_executed if 'trades_executed' in locals() else [],
        backtest_results=backtest_results,
        last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'),
        error=error_message
    )

# --- Main Execution ---
if __name__ == '__main__':
    # Use environment variable for port, default to 8080 for broader compatibility
    port = int(os.environ.get('PORT', 8080))
    # Run with host='0.0.0.0' to be accessible externally (needed for Render)
    app.run(host='0.0.0.0', port=port) # Use debug=False for production
