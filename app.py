# app.py
from flask import Flask, render_template
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone # Added timezone
import os
import logging
import time
import gc

# Import local modules
from indicators import calculate_all_indicators
from trading_logic import (
    generate_recommendations, update_paper_portfolio, get_portfolio_value,
    run_backtest, load_portfolio, INITIAL_CASH
)
from telegram_sender import notify_recommendations

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Constants ---
STOCK_LIST_FILE = 'nifty50_stocks.csv'
DATA_FETCH_PERIOD = "6mo"
BACKTEST_SYMBOL = "RELIANCE.NS"
BACKTEST_PERIOD = "6mo"
CACHE_DURATION_SECONDS = 3600 # Cache results for 1 hour

# --- Simple In-Memory Cache ---
app_cache = {
    "last_update_time": None,
    "recommendations": [],
    "portfolio_display": None,
    "backtest_results": None,
    "trades_executed": [],
    "processing_error": None
}
# -----------------------------

# ************************************************************
# ********** HELPER FUNCTIONS (DEFINITIONS ADDED BACK) *******
# ************************************************************

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
        # Filter out potential non-string entries just in case
        symbols = [s for s in symbols if isinstance(s, str) and s.strip()]
        logging.info(f"Loaded {len(symbols)} valid symbols from {STOCK_LIST_FILE}")
        return symbols
    except FileNotFoundError:
        logging.error(f"Error: {STOCK_LIST_FILE} not found during read_csv.")
        return []
    except pd.errors.EmptyDataError:
         logging.error(f"Error: {STOCK_LIST_FILE} is empty.")
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

    # Ensure symbols is a list of non-empty strings
    if isinstance(symbols, str):
        symbols = [symbols]
    symbols = [s for s in symbols if isinstance(s, str) and s.strip()]
    if not symbols:
        logging.warning("fetch_stock_data called with empty or invalid symbols after filtering.")
        return pd.DataFrame()


    try:
        logging.info(f"Fetching {period} data for symbols: {symbols}...")
        start_time = time.time()

        if len(symbols) == 1:
            ticker_str = symbols[0]
            data = yf.download(ticker_str, period=period, auto_adjust=True, progress=False)
        else:
            # Use multiple symbols download (less used now but kept)
            data = yf.download(symbols, period=period, group_by='ticker', auto_adjust=True, progress=False)

        end_time = time.time()
        logging.info(f"Data fetch for {symbols} completed in {end_time - start_time:.2f} seconds.")

        if data.empty:
            logging.warning(f"No data returned by yfinance for symbols: {symbols}")
            return pd.DataFrame()

        return data

    except Exception as e:
        logging.error(f"Error during yfinance download/processing for {symbols}: {e}", exc_info=True)
        return pd.DataFrame()

# ************************************************************
# ******************* END HELPER FUNCTIONS *******************
# ************************************************************


# --- Background Data Processing Function ---
def process_all_data():
    """Fetches data, calculates indicators/recommendations, updates portfolio, runs backtest."""
    global app_cache
    logging.info("--- Starting Background Data Processing ---")
    start_process_time = time.time()

    # Reset state for this run
    local_recommendations = []
    local_current_prices = {}
    local_trades_executed = []
    local_portfolio_state = None
    local_backtest_results = None
    local_error = None # Reset error specific to this processing run

    # *** CRITICAL: Call the now defined function ***
    symbols = get_stock_symbols()

    if not symbols:
        local_error = f"Could not load stock symbols from {STOCK_LIST_FILE} or file is empty/invalid."
        logging.error(local_error)
        # Still try to load portfolio to display holdings even if symbols fail
        try:
            local_portfolio_state = load_portfolio()
        except Exception as load_err:
             logging.error(f"Failed to load portfolio even when symbols failed: {load_err}")
             local_error += f" | Failed to load portfolio: {load_err}"
             # Set a default empty state if loading fails critically
             local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
    else:
        logging.info(f"Processing {len(symbols)} symbols sequentially...")
        # --- Symbol Loop ---
        for symbol in symbols:
            symbol_data = pd.DataFrame()
            df_with_indicators = pd.DataFrame()
            try:
                #logging.debug(f"--- Processing symbol: {symbol} ---") # Use debug for less noise
                # *** CRITICAL: Call the now defined function ***
                symbol_data = fetch_stock_data([symbol], period=DATA_FETCH_PERIOD)

                if symbol_data.empty:
                    #logging.debug(f"No data fetched for {symbol}. Skipping.")
                    continue

                df_symbol = symbol_data.copy()
                df_symbol = df_symbol.dropna(subset=['Close'])
                if df_symbol.empty:
                    #logging.debug(f"No valid 'Close' data for {symbol} after dropna. Skipping.")
                    continue

                #logging.debug(f"Calculating indicators for {symbol}...")
                df_with_indicators = calculate_all_indicators(df_symbol)
                if not df_with_indicators.empty and 'Close' in df_with_indicators.columns and not df_with_indicators['Close'].empty:
                    recommendation = generate_recommendations(symbol, df_with_indicators)
                    if recommendation:
                        local_recommendations.append(recommendation)
                    # Ensure index exists before accessing iloc[-1]
                    if not df_with_indicators.empty:
                        local_current_prices[symbol] = df_with_indicators['Close'].iloc[-1]
                #else:
                    #logging.debug(f"Indicator calculation empty or missing 'Close' for {symbol}")

            except IndexError:
                 logging.warning(f"IndexError likely processing indicators/prices for {symbol}. Skipping price/rec.", exc_info=False) # Less verbose log
            except Exception as e:
                logging.error(f"Error processing symbol {symbol}: {e}", exc_info=True) # Full traceback for unexpected
                local_error = "Error during symbol processing (see logs for details)." # Set general error flag
            finally:
                del symbol_data, df_with_indicators
                gc.collect()
        # --- End Symbol Loop ---
        logging.info(f"Finished processing {len(symbols)} symbols.")

        # --- Paper Trading Update ---
        if local_recommendations:
            logging.info("Updating paper trading portfolio...")
            valid_recs = [rec for rec in local_recommendations if rec['symbol'] in local_current_prices]
            if valid_recs:
                 try:
                     local_portfolio_state, local_trades_executed = update_paper_portfolio(valid_recs, local_current_prices)
                     # Send Telegram Notification ONLY when recommendations are generated/updated
                     logging.info(f"Sending {len(local_recommendations)} recommendations via Telegram...")
                     notify_recommendations(local_recommendations)
                 except Exception as trade_err:
                      logging.error(f"Error updating paper portfolio: {trade_err}", exc_info=True)
                      local_error = "Error during paper trading update."
                      local_portfolio_state = load_portfolio() # Load previous state if update fails
            else:
                 logging.warning("No valid recommendations with current prices found for trading.")
                 local_portfolio_state = load_portfolio() # Load if no valid recs
        else:
            logging.info("No recommendations generated during this run.")
            try:
                local_portfolio_state = load_portfolio() # Load portfolio state if no recommendations
            except Exception as load_err:
                 logging.error(f"Failed to load portfolio when no recommendations were generated: {load_err}")
                 local_error = (local_error + f" | Failed to load portfolio: {load_err}" if local_error else
                                f"Failed to load portfolio: {load_err}")
                 local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}

    # --- Get Portfolio Display Data ---
    local_portfolio_display = None
    try:
         if local_portfolio_state is None:
             logging.info("Portfolio state was None, attempting load.")
             local_portfolio_state = load_portfolio()

         # Fetch missing prices if needed (ensure local_current_prices exists)
         if local_current_prices is None: local_current_prices = {} # Defensive check
         portfolio_symbols_needing_price = [
             sym for sym in local_portfolio_state.get('holdings',{}).keys() if sym not in local_current_prices
         ]
         if portfolio_symbols_needing_price:
             logging.info(f"Fetching display prices for holdings: {portfolio_symbols_needing_price}")
             # *** CRITICAL: Call the now defined function ***
             data_now = fetch_stock_data(portfolio_symbols_needing_price, period="5d")
             if not data_now.empty:
                 # Logic to extract prices from data_now (handle single/multi index)
                 if len(portfolio_symbols_needing_price) == 1 and isinstance(data_now.columns, pd.Index):
                     sym = portfolio_symbols_needing_price[0]
                     try:
                         # Ensure 'Close' exists and data is not empty
                         if 'Close' in data_now.columns and not data_now['Close'].empty:
                              local_current_prices[sym] = data_now['Close'].iloc[-1]
                         else: logging.warning(f"Missing 'Close' or empty data for {sym} in single fetch.")
                     except IndexError: logging.warning(f"IndexError getting price for {sym} (single).")
                     except Exception as e: logging.error(f"Error getting price for {sym} (single): {e}")
                 elif isinstance(data_now.columns, pd.MultiIndex):
                     for sym in portfolio_symbols_needing_price:
                         if sym in data_now.columns.levels[0]:
                             try:
                                 # Ensure 'Close' exists and data is not empty
                                 close_col = data_now[(sym, 'Close')]
                                 if not close_col.empty:
                                      local_current_prices[sym] = close_col.iloc[-1]
                                 else: logging.warning(f"Empty 'Close' data for {sym} in multi fetch.")
                             except IndexError: logging.warning(f"IndexError getting price for {sym} (multi).")
                             except KeyError: logging.warning(f"KeyError getting ('Close') price for {sym} (multi).")
                             except Exception as e: logging.error(f"Error getting price for {sym} (multi): {e}")
         else:
              logging.info("No extra price fetching needed for portfolio display.")


         total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
         local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}
    except Exception as e:
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        local_error = (local_error + " | Error calculating portfolio value." if local_error else
                       "Error calculating portfolio value.")
        # Provide default structure even on error
        local_portfolio_display = {'total_value': 'Error', 'cash': 'Error', 'holdings': []}
        # Try to get cash if portfolio state exists
        if local_portfolio_state:
            local_portfolio_display['cash'] = local_portfolio_state.get('cash', 'Error')


    # --- Run Backtesting Example ---
    logging.info(f"Running backtest for {BACKTEST_SYMBOL}...")
    try:
        # *** CRITICAL: Call the now defined function ***
        backtest_data = fetch_stock_data([BACKTEST_SYMBOL], period=BACKTEST_PERIOD)
        if not backtest_data.empty:
             local_backtest_results = run_backtest(BACKTEST_SYMBOL, backtest_data.copy(), initial_capital=INITIAL_CASH)
        else:
             logging.error(f"Failed to fetch data for backtesting symbol {BACKTEST_SYMBOL}.")
             local_backtest_results = {"error": f"Could not fetch data for {BACKTEST_SYMBOL}."}
    except Exception as e:
        logging.error(f"Error running backtest for {BACKTEST_SYMBOL}: {e}", exc_info=True)
        local_backtest_results = {"error": f"An error occurred during backtesting: {e}"}

    # --- Update Cache ---
    # Store results regardless of error status, error is stored separately
    app_cache['recommendations'] = local_recommendations
    app_cache['portfolio_display'] = local_portfolio_display
    app_cache['backtest_results'] = local_backtest_results
    app_cache['trades_executed'] = local_trades_executed # Store trades from THIS run
    app_cache['last_update_time'] = datetime.now(timezone.utc)
    app_cache['processing_error'] = local_error # Store specific error from this run

    end_process_time = time.time()
    logging.info(f"--- Background Data Processing Finished ({end_process_time - start_process_time:.2f} seconds) ---")
    if local_error:
         logging.error(f"Processing finished with error: {local_error}")
    else:
         logging.info("Processing finished successfully.")
# --- End Background Data Processing Function ---


# --- Flask Route ---
@app.route('/')
def index():
    """Serves the cached data or triggers processing if cache is stale."""
    now = datetime.now(timezone.utc)
    cache_needs_update = False
    force_update = False # Optional: Add query param later to force? e.g., /?force=true

    # Check cache status
    if app_cache['last_update_time'] is None:
        logging.info("Cache is empty. Triggering initial data processing.")
        cache_needs_update = True
    else:
        time_since_update = now - app_cache['last_update_time']
        if time_since_update.total_seconds() > CACHE_DURATION_SECONDS or force_update:
            if force_update:
                 logging.info("Forcing cache update via request.")
            else:
                 logging.info(f"Cache expired ({time_since_update.total_seconds():.0f}s > {CACHE_DURATION_SECONDS}s). Triggering data processing.")
            cache_needs_update = True
        else:
            logging.info("Serving data from cache.")

    # Trigger processing if needed
    if cache_needs_update:
        try:
            # Run the processing function (blocks the request on first load/expiry)
            process_all_data()
        except Exception as e:
             # Catch any unexpected critical error during the process_all_data call itself
             logging.error(f"Critical error calling process_all_data: {e}", exc_info=True)
             # Store this critical error in the cache to display
             app_cache['processing_error'] = f"Failed to run update process: {e}"
             # If cache was never populated, return error immediately
             if app_cache['last_update_time'] is None:
                  return render_template('index.html',
                                         error=f"Initial data processing failed: {e}",
                                         last_updated="Never")
             # Otherwise, fall through and serve potentially stale cache data with the new error message

    # --- Render Template using data from app_cache ---
    last_updated_str = app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC') if app_cache['last_update_time'] else "Processing..."

    # Display the processing_error stored in the cache (if any)
    display_error = app_cache.get('processing_error') # Use .get for safety

    return render_template(
        'index.html',
        recommendations=app_cache.get('recommendations', []), # Use .get with defaults
        paper_portfolio=app_cache.get('portfolio_display'),
        initial_capital=INITIAL_CASH,
        trades_executed=app_cache.get('trades_executed', []),
        backtest_results=app_cache.get('backtest_results'),
        last_updated=last_updated_str,
        error=display_error # Display the error message from the cache
    )

# --- Main Execution ---
if __name__ == '__main__':
    # Perform an initial data load on startup before starting the web server.
    # This helps ensure data is available immediately for the first request.
    logging.info("Performing initial data load on startup...")
    process_all_data()
    logging.info("Initial data load complete. Starting web server (via Gunicorn usually)...")

    # Gunicorn will typically run the app via the Procfile on Render.
    # This app.run() is mainly for local development testing.
    port = int(os.environ.get('PORT', 8080))
    # Set debug=True ONLY for local testing if needed, NEVER in production/Render.
    # app.run(host='0.0.0.0', port=port, debug=False)
