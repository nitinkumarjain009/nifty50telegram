# app.py
from flask import Flask, render_template # Removed 'request' as it wasn't used directly in index
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import os
import logging
import time # For timing execution
import gc # Garbage Collector

# Import local modules
from indicators import calculate_all_indicators
from trading_logic import (
    generate_recommendations,
    update_paper_portfolio,
    get_portfolio_value,
    run_backtest,
    load_portfolio, # Explicitly import load_portfolio
    INITIAL_CASH # Import initial cash constant
)
from telegram_sender import notify_recommendations # Import the notification function

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Environment Variables ---
# For Telegram, secrets are handled in telegram_sender.py using os.environ
# Ensure TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_GROUP_CHANNEL are set in Render Env Vars.

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Constants ---
STOCK_LIST_FILE = 'nifty50_stocks.csv'
# *** UPDATED DATA PERIODS TO 6 MONTHS ***
DATA_FETCH_PERIOD = "6mo" # Fetch 6 months of data for indicator calculation
BACKTEST_SYMBOL = "RELIANCE.NS" # Stock to run backtest example on
BACKTEST_PERIOD = "6mo" # Historical data period for backtesting (changed from 2y)

# --- Helper Functions ---

def get_stock_symbols():
    """Reads stock symbols from the CSV file."""
    try:
        # Use absolute path if needed, but usually relative works if run from project root
        if not os.path.exists(STOCK_LIST_FILE):
             logging.error(f"Error: Stock list file '{STOCK_LIST_FILE}' not found at CWD: {os.getcwd()}")
             return []

        df = pd.read_csv(STOCK_LIST_FILE)
        if 'Symbol' not in df.columns:
             logging.error(f"'Symbol' column not found in {STOCK_LIST_FILE}")
             return []
        symbols = df['Symbol'].dropna().unique().tolist()
        logging.info(f"Loaded {len(symbols)} symbols from {STOCK_LIST_FILE}")
        return symbols
    except FileNotFoundError:
        # This catch might be redundant due to the os.path.exists check, but kept for safety
        logging.error(f"Error: {STOCK_LIST_FILE} not found during read_csv.")
        return []
    except Exception as e:
        logging.error(f"Error reading {STOCK_LIST_FILE}: {e}", exc_info=True)
        return []

def fetch_stock_data(symbols, period="1y"):
    """
    Fetches historical data for a list of symbols using yfinance.
    Handles single and multiple symbols, returning appropriate DataFrame structure.
    """
    if not symbols:
        logging.warning("fetch_stock_data called with empty symbols list.")
        return pd.DataFrame()

    # Ensure symbols is a list
    if isinstance(symbols, str):
        symbols = [symbols]

    try:
        logging.info(f"Fetching {period} data for symbols: {symbols}...")
        start_time = time.time()

        if len(symbols) == 1:
            # --- SINGLE SYMBOL ---
            ticker_str = symbols[0]
            data = yf.download(ticker_str, period=period, auto_adjust=True, progress=False)
        else:
            # --- MULTIPLE SYMBOLS ---
            # Note: This path is less used with sequential processing but kept for potential future use.
            # May still cause memory issues on free tier if used with many symbols.
            data = yf.download(symbols, period=period, group_by='ticker', auto_adjust=True, progress=False)

        end_time = time.time()
        logging.info(f"Data fetch for {symbols} completed in {end_time - start_time:.2f} seconds.")

        if data.empty:
            logging.warning(f"No data returned by yfinance for symbols: {symbols}")
            return pd.DataFrame()

        return data

    except Exception as e:
        # Log the specific exception from yfinance or pandas
        logging.error(f"Error during yfinance download/processing for {symbols}: {e}", exc_info=True) # Add traceback
        return pd.DataFrame() # Return empty DataFrame on error


# --- Flask Routes ---
@app.route('/')
def index():
    """Main route to display recommendations, portfolio, and backtest."""
    start_render_time = time.time()
    error_message = None
    recommendations_list = []
    current_prices = {} # Store latest close price for portfolio valuation
    trades_executed = [] # Initialize list to store paper trades for display
    paper_portfolio_state = None # Initialize portfolio state
    backtest_results = None # Initialize backtest results

    symbols = get_stock_symbols()
    if not symbols:
        error_message = f"Could not load stock symbols from {STOCK_LIST_FILE} or file is empty."
        logging.error(error_message)
        # Try to load portfolio even if symbols fail, to show current state
        paper_portfolio_state = load_portfolio()
    else:
        logging.info(f"Processing {len(symbols)} symbols sequentially...")
        processing_start_time = time.time()

        for symbol in symbols:
            # Define variables outside try block to ensure they exist in finally
            symbol_data = pd.DataFrame()
            df_with_indicators = pd.DataFrame()
            try:
                logging.info(f"--- Processing symbol: {symbol} ---")
                # *** Fetch data for ONE symbol at a time using the revised function ***
                symbol_data = fetch_stock_data([symbol], period=DATA_FETCH_PERIOD)

                if symbol_data.empty:
                    logging.warning(f"No data fetched for {symbol}. Skipping.")
                    continue # Skip to the next symbol

                # *** Directly use the returned DataFrame (it's standard format) ***
                df_symbol = symbol_data.copy()
                df_symbol = df_symbol.dropna(subset=['Close']) # Ensure 'Close' has data

                if not df_symbol.empty:
                    logging.info(f"Calculating indicators for {symbol}...")
                    df_with_indicators = calculate_all_indicators(df_symbol)

                    # Check if indicators were successfully calculated and data exists
                    if not df_with_indicators.empty and 'Close' in df_with_indicators.columns:
                        recommendation = generate_recommendations(symbol, df_with_indicators)
                        if recommendation:
                            recommendations_list.append(recommendation)

                        # Store last close price for portfolio valuation
                        current_prices[symbol] = df_with_indicators['Close'].iloc[-1]
                    else:
                        logging.warning(f"Indicator calculation resulted in empty DataFrame or missing 'Close' for {symbol}")
                else:
                    logging.warning(f"No data available for {symbol} after dropping NaNs in 'Close'.")

            except Exception as e:
                # Log error specific to this symbol processing loop
                logging.error(f"Error processing symbol {symbol} in main loop: {e}", exc_info=True) # Add traceback
                # Optionally set a general error message: error_message = "Error processing some symbols."
            finally:
                # Explicitly delete potentially large DataFrames to free memory sooner
                del symbol_data
                del df_with_indicators # df_symbol is implicitly handled by loop scope
                gc.collect() # Trigger garbage collection

        processing_end_time = time.time()
        logging.info(f"Sequential symbol processing finished in {processing_end_time - processing_start_time:.2f} seconds.")

        # --- Paper Trading Update (Run only if recommendations were generated) ---
        if recommendations_list:
            logging.info("Updating paper trading portfolio based on generated signals...")
            # Ensure current_prices has entries for recommended stocks before updating
            valid_recs = [rec for rec in recommendations_list if rec['symbol'] in current_prices]
            if len(valid_recs) != len(recommendations_list):
                logging.warning("Some recommendations skipped in paper trading due to missing current price after processing.")

            if valid_recs: # Only update if there are valid recommendations with prices
                 paper_portfolio_state, trades_executed = update_paper_portfolio(valid_recs, current_prices)
                 logging.info(f"Paper trading portfolio update complete. Trades executed: {len(trades_executed)}")
            else:
                 logging.warning("No valid recommendations with current prices found. Skipping paper trade update.")
                 # Load current state if no update happened
                 paper_portfolio_state = load_portfolio()

            # --- Send Telegram Notification (Run only if recommendations were generated) ---
            logging.info("Sending Telegram notifications for generated signals...")
            notify_recommendations(recommendations_list) # Notify about all originally generated signals

        else:
            logging.info("No recommendations generated. Skipping paper trade update and Telegram notification.")
            # Load portfolio to display current state even if no recommendations
            paper_portfolio_state = load_portfolio()


    # --- Get Paper Portfolio Display Data ---
    portfolio_display = None
    try:
        # Ensure paper_portfolio_state is loaded if it wasn't already
        if paper_portfolio_state is None:
             paper_portfolio_state = load_portfolio()

        # Attempt to get current prices for portfolio holdings if they weren't fetched during processing
        portfolio_symbols_needing_price = [
            sym for sym in paper_portfolio_state.get('holdings',{}).keys() if sym not in current_prices
        ]
        if portfolio_symbols_needing_price:
            logging.info(f"Fetching current prices for existing portfolio holdings: {portfolio_symbols_needing_price}")
            # Fetch minimal data just for current price
            data_now = fetch_stock_data(portfolio_symbols_needing_price, period="5d") # Fetch recent data
            if not data_now.empty:
                 # Handle both single and multi-symbol results from fetch_stock_data
                if len(portfolio_symbols_needing_price) == 1 and isinstance(data_now.columns, pd.Index):
                    # Single symbol result (standard DataFrame)
                    sym = portfolio_symbols_needing_price[0]
                    try:
                        current_prices[sym] = data_now['Close'].iloc[-1]
                    except IndexError:
                         logging.warning(f"Could not get current price for {sym} from single fetch.")
                elif isinstance(data_now.columns, pd.MultiIndex):
                     # Multi-symbol result (MultiIndex DataFrame)
                    for sym in portfolio_symbols_needing_price:
                        if sym in data_now.columns.levels[0]:
                            try:
                                # Access Close price using tuple for MultiIndex
                                current_prices[sym] = data_now[(sym, 'Close')].iloc[-1]
                            except IndexError:
                                logging.warning(f"Could not get current price for {sym} from multi fetch (IndexError).")
                            except KeyError:
                                 logging.warning(f"Could not get current price for {sym} from multi fetch (KeyError).")
                        else:
                             logging.warning(f"Symbol {sym} not found in multi-fetch result columns.")
            else:
                logging.error("Could not fetch current prices for some portfolio holdings valuation.")


        total_value, cash, holdings_details = get_portfolio_value(paper_portfolio_state, current_prices)
        portfolio_display = {
            'total_value': total_value,
            'cash': cash,
            'holdings': holdings_details
        }
    except Exception as e:
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        error_message = (error_message + " | Error calculating portfolio value." if error_message else
                         "Error calculating portfolio value.")
        # Ensure portfolio_display is at least an empty structure if calculation fails
        portfolio_display = {'total_value': 0, 'cash': 0, 'holdings': []}
        if paper_portfolio_state: # Log cash if available
             portfolio_display['cash'] = paper_portfolio_state.get('cash', 0)


    # --- Run Backtesting Example ---
    logging.info(f"Running backtest for {BACKTEST_SYMBOL} using {BACKTEST_PERIOD} of data...")
    backtest_start_time = time.time()
    try:
        # Fetch data specifically for the backtest symbol
        backtest_data = fetch_stock_data([BACKTEST_SYMBOL], period=BACKTEST_PERIOD)
        if not backtest_data.empty:
             # Pass the standard DataFrame directly
             backtest_results = run_backtest(BACKTEST_SYMBOL, backtest_data.copy(), initial_capital=INITIAL_CASH)
        else:
            logging.error(f"Failed to fetch data for backtesting symbol {BACKTEST_SYMBOL}.")
            backtest_results = {"error": f"Could not fetch data for {BACKTEST_SYMBOL}."}
    except Exception as e:
        logging.error(f"Error running backtest for {BACKTEST_SYMBOL}: {e}", exc_info=True)
        backtest_results = {"error": f"An error occurred during backtesting: {e}"}
    backtest_end_time = time.time()
    logging.info(f"Backtest execution took {backtest_end_time - backtest_start_time:.2f} seconds.")


    # --- Render Template ---
    end_render_time = time.time()
    logging.info(f"Total page processing and rendering time: {end_render_time - start_render_time:.2f} seconds.")

    # Ensure trades_executed is passed, even if empty
    if 'trades_executed' not in locals():
        trades_executed = []

    return render_template(
        'index.html',
        recommendations=recommendations_list,
        paper_portfolio=portfolio_display,
        initial_capital=INITIAL_CASH, # Pass initial capital to template
        trades_executed=trades_executed, # Pass trades executed during this run
        backtest_results=backtest_results,
        last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC'),
        error=error_message
    )

# --- Main Execution ---
if __name__ == '__main__':
    # Use environment variable for port, default for local testing might be 5000 or 8080
    # Render/Heroku typically set the PORT environment variable.
    port = int(os.environ.get('PORT', 8080))
    # Run with host='0.0.0.0' to be accessible externally (like in Render)
    # debug=False for production/deployment
    app.run(host='0.0.0.0', port=port, debug=False)
