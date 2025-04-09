# app.py
from flask import Flask, render_template
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone
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
from telegram_sender import notify_recommendations_photo

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Constants ---
STOCK_LIST_FILE = 'nifty50_stocks.csv'
DATA_FETCH_PERIOD = "6mo"
BACKTEST_SYMBOL = "RELIANCE.NS" # Example symbol for backtest
BACKTEST_PERIOD = "6mo"
CACHE_DURATION_SECONDS = 3600 # Cache results for 1 hour

# --- Simple In-Memory Cache ---
app_cache = {
    "last_update_time": None,
    "all_stock_data": [],
    "portfolio_display": None,
    "backtest_results": None,
    "trades_executed": [],
    "processing_error": None,
    "dataframe_summary": None
}
# -----------------------------

# --- Helper Functions ---

def get_stock_symbols():
    """
    Reads stock symbols (expected to include .NS suffix) from the CSV file,
    cleans them (removes whitespace), and returns a unique list.
    """
    try:
        if not os.path.exists(STOCK_LIST_FILE):
             logging.error(f"Error: Stock list file '{STOCK_LIST_FILE}' not found at CWD: {os.getcwd()}")
             return []

        logging.info(f"Reading symbols from {STOCK_LIST_FILE}")
        # Explicitly set dtype might help prevent type issues, though usually inferred ok
        df = pd.read_csv(STOCK_LIST_FILE, dtype={'Symbol': str})

        if 'Symbol' not in df.columns:
             logging.error(f"'Symbol' column not found in {STOCK_LIST_FILE}")
             return []

        # Get the raw symbols from the 'Symbol' column
        raw_symbols = df['Symbol'].tolist()
        logging.debug(f"Raw symbols read from CSV: {raw_symbols}")

        # Clean and validate the symbols
        symbols = []
        for s in raw_symbols:
            if pd.isna(s): # Skip pandas missing values
                logging.debug(f"Skipping NaN symbol.")
                continue
            # Ensure it's treated as a string
            s_str = str(s)
            # Remove leading/trailing whitespace ONLY. This preserves '.NS'
            cleaned_symbol = s_str.strip()
            if cleaned_symbol: # Ensure it's not an empty string after stripping
                # Check if it looks like a valid symbol (basic check)
                if '.' in cleaned_symbol and len(cleaned_symbol) > 3: # Simple check for format like 'XXX.NS'
                    symbols.append(cleaned_symbol)
                    logging.debug(f"Accepted symbol: {repr(cleaned_symbol)}")
                else:
                    logging.warning(f"Skipping potentially invalid symbol format after cleaning: {repr(cleaned_symbol)}")
            else:
                logging.warning(f"Skipping empty symbol after stripping from raw value: {repr(s_str)}")

        # Get unique symbols - using set then converting back to list
        unique_symbols = sorted(list(set(symbols)))

        if not unique_symbols:
             logging.error(f"No valid symbols found in {STOCK_LIST_FILE} after cleaning.")
             return []

        logging.info(f"Loaded {len(unique_symbols)} unique, valid symbols ending with .NS (expected): {unique_symbols[:10]}...") # Log first 10
        return unique_symbols

    except FileNotFoundError:
        # Should be caught by os.path.exists, but good practice
        logging.error(f"Error: {STOCK_LIST_FILE} not found during read_csv.")
        return []
    except pd.errors.EmptyDataError:
         logging.error(f"Error: {STOCK_LIST_FILE} is empty or header-only.")
         return []
    except Exception as e:
        logging.error(f"Critical error reading or processing {STOCK_LIST_FILE}: {e}", exc_info=True)
        return []


def fetch_stock_data(symbols, period="1y"):
    """
    Fetches historical data for a list of symbols (expected to have .NS) using yfinance.
    """
    if not symbols:
        logging.warning("fetch_stock_data called with empty symbols list.")
        return pd.DataFrame()

    # Input should already be a list of cleaned strings from get_stock_symbols
    if isinstance(symbols, str): # Should not happen if called correctly, but safe check
        symbols = [symbols]
    if not all(isinstance(s, str) and s for s in symbols):
         logging.error(f"fetch_stock_data received invalid input (non-strings or empty): {symbols}")
         return pd.DataFrame()

    try:
        # Use INFO level for less frequent logging, DEBUG if needed for every fetch
        logging.debug(f"Fetching {period} data for symbols: {symbols}...")
        start_time = time.time()

        # yfinance handles lists directly. No need to check len(symbols) == 1 explicitly here.
        # It returns standard DF for one symbol, MultiIndex for multiple with group_by=None (default)
        # Use auto_adjust=True for adjusted prices, progress=False to reduce log noise
        data = yf.download(symbols, period=period, auto_adjust=True, progress=False)

        end_time = time.time()
        logging.debug(f"Data fetch for {symbols} completed in {end_time - start_time:.2f} seconds.")

        if data.empty:
            logging.warning(f"No data returned by yfinance for symbols: {symbols}")
            return pd.DataFrame()

        # If only one symbol was requested, yfinance returns a simple DataFrame.
        # If multiple, it returns a MultiIndex DataFrame (columns are tuples like ('Close', 'RELIANCE.NS'))
        # Check and potentially reformat if needed downstream, but usually yf handles it well.
        # If multiple symbols were passed, and we NEEDED a specific format (like symbols as top level cols),
        # we might need to add group_by='ticker' back and handle MultiIndex columns later.
        # For sequential processing, group_by isn't needed as we pass one symbol at a time.

        return data

    except Exception as e:
        # Log the specific exception from yfinance or pandas
        logging.error(f"Error during yfinance download/processing for {symbols}: {e}", exc_info=True)
        return pd.DataFrame() # Return empty DataFrame on error

# --- Background Data Processing Function ---
def process_all_data():
    """Fetches data, calculates all required values, updates portfolio, runs backtest."""
    global app_cache
    logging.info("--- Starting Background Data Processing ---")
    start_process_time = time.time()

    # Reset state for this run
    local_all_stock_data = []
    local_recommendations_for_trade = []
    local_current_prices = {}
    local_trades_executed = []
    local_portfolio_state = None
    local_backtest_results = None
    local_error = None
    dataframe_for_telegram = pd.DataFrame() # Initialize DF

    # *** Get symbols using the refined function ***
    symbols = get_stock_symbols()

    if not symbols:
        local_error = f"Could not load valid symbols from {STOCK_LIST_FILE}. Cannot process."
        logging.error(local_error)
        # Attempt to load portfolio state even if symbols fail
        try: local_portfolio_state = load_portfolio()
        except Exception as load_err:
             logging.error(f"Failed to load portfolio state: {load_err}")
             local_error += f" | Failed to load portfolio: {load_err}"
             local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}} # Default state on error
    else:
        logging.info(f"Processing {len(symbols)} symbols sequentially...")
        # --- Symbol Loop ---
        for symbol in symbols: # 'symbol' here correctly contains '.NS' if read properly
            symbol_data = pd.DataFrame()
            df_with_indicators = pd.DataFrame()
            logging.debug(f"--- Processing symbol: {repr(symbol)} ---")
            try:
                # Fetch data for the single symbol (pass as list)
                symbol_data = fetch_stock_data([symbol], period=DATA_FETCH_PERIOD)

                # Robust checks after fetching
                if symbol_data.empty or len(symbol_data) < 2:
                    logging.warning(f"Skipping {repr(symbol)}: Insufficient data fetched ({len(symbol_data)} rows).")
                    continue

                df_symbol = symbol_data.copy()
                # Check specifically for 'Close' column existence
                if 'Close' not in df_symbol.columns:
                     logging.warning(f"Skipping {repr(symbol)}: 'Close' column missing in fetched data.")
                     continue
                df_symbol = df_symbol.dropna(subset=['Close'])
                if df_symbol.empty or len(df_symbol) < 2:
                    logging.warning(f"Skipping {repr(symbol)}: Insufficient valid 'Close' data ({len(df_symbol)} rows).")
                    continue

                # Calculate indicators
                df_with_indicators = calculate_all_indicators(df_symbol)
                if df_with_indicators.empty or len(df_with_indicators) < 2 or 'Close' not in df_with_indicators.columns:
                     logging.warning(f"Skipping {repr(symbol)}: Indicator calculation failed or insufficient data.")
                     continue

                # Extract CMP and Previous Close
                current_close = df_with_indicators['Close'].iloc[-1]
                prev_close = df_with_indicators['Close'].iloc[-2]
                local_current_prices[symbol] = current_close # Store for portfolio

                # Calculate % Change
                percent_change = ((current_close - prev_close) / prev_close) * 100 if prev_close else 0.0

                # Get Recommendation
                recommendation_result = generate_recommendations(symbol, df_with_indicators)
                signal = recommendation_result.get('signal', 'HOLD') if recommendation_result else "HOLD"
                target = recommendation_result.get('target') if recommendation_result else None
                if recommendation_result and signal in ['BUY', 'SELL']:
                     local_recommendations_for_trade.append(recommendation_result)

                # Store data for HTML table and DataFrame
                stock_info = {'symbol': symbol, 'cmp': current_close, 'percent_change': percent_change, 'signal': signal, 'target': target}
                local_all_stock_data.append(stock_info)

            except IndexError as idx_err:
                 logging.warning(f"IndexError processing {repr(symbol)} (likely price access): {idx_err}. Skipping symbol.")
            except Exception as e:
                logging.error(f"Unhandled error processing symbol {repr(symbol)}: {e}", exc_info=True)
                local_error = f"Error processing {symbol} (see logs)." # Set general error flag

            finally:
                del symbol_data, df_with_indicators # Explicit cleanup
                gc.collect()
        # --- End Symbol Loop ---
        logging.info(f"Finished processing symbols.")

        # --- Create DataFrame for Telegram ---
        if local_all_stock_data:
             try:
                 dataframe_for_telegram = pd.DataFrame(local_all_stock_data)
                 df_display = dataframe_for_telegram[['symbol', 'cmp', 'percent_change', 'signal', 'target']].copy()
                 df_display.rename(columns={'symbol': 'Symbol', 'cmp': 'CMP', 'percent_change': '% Change', 'signal': 'Signal', 'target': 'Target'}, inplace=True)
                 df_display['CMP'] = df_display['CMP'].map('{:,.2f}'.format)
                 df_display['% Change'] = df_display['% Change'].map('{:,.2f}%'.format)
                 df_display['Target'] = df_display['Target'].map(lambda x: '{:,.2f}'.format(x) if pd.notnull(x) else 'N/A')
                 app_cache['dataframe_summary'] = df_display # Store formatted DF
             except Exception as df_err:
                 logging.error(f"Error creating or formatting DataFrame for Telegram: {df_err}", exc_info=True)
                 local_error = "Error preparing data for Telegram image."
                 dataframe_for_telegram = pd.DataFrame() # Ensure it's empty on error

        # --- Paper Trading Update ---
        if local_recommendations_for_trade:
            logging.info("Updating paper trading portfolio...")
            valid_trade_recs = [rec for rec in local_recommendations_for_trade if rec['symbol'] in local_current_prices]
            if valid_trade_recs:
                 try:
                     local_portfolio_state, local_trades_executed = update_paper_portfolio(valid_trade_recs, local_current_prices)
                 except Exception as trade_err:
                      logging.error(f"Error updating paper portfolio: {trade_err}", exc_info=True)
                      local_error = "Error during paper trading update."
                      try: local_portfolio_state = load_portfolio()
                      except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}} # Default on error
            else:
                 logging.warning("No valid recommendations with current prices found for trading.")
                 try: local_portfolio_state = load_portfolio()
                 except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
        else:
            logging.info("No BUY/SELL recommendations generated for paper trading.")
            try: local_portfolio_state = load_portfolio()
            except Exception as load_err:
                 logging.error(f"Failed to load portfolio state: {load_err}")
                 local_error = (local_error + f" | Failed to load portfolio: {load_err}" if local_error else f"Failed to load portfolio: {load_err}")
                 local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}

        # --- Send Telegram Notification PHOTO ---
        df_summary_to_send = app_cache.get('dataframe_summary') # Get the formatted DF from cache
        if df_summary_to_send is not None and not df_summary_to_send.empty:
             logging.info("Sending Telegram notification photo...")
             notify_recommendations_photo(df_summary_to_send) # Send the formatted one
        elif not local_all_stock_data: # Only warn if no data was processed at all
             logging.warning("Skipping Telegram photo: No stock data was processed.")
        else: # Warn if data processed but DF creation failed
             logging.warning("Skipping Telegram photo: DataFrame could not be generated.")

    # --- Get Portfolio Display Data --- (Robust loading logic)
    local_portfolio_display = None
    try:
         if local_portfolio_state is None:
             logging.info("Portfolio state was None after processing, attempting load.")
             try: local_portfolio_state = load_portfolio()
             except Exception as load_err:
                  logging.error(f"Final attempt to load portfolio failed: {load_err}")
                  local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}} # Default on error

         if local_current_prices is None: local_current_prices = {}

         # Fetch missing prices if needed... (rest of logic is same as before)
         portfolio_symbols_needing_price = [ sym for sym in local_portfolio_state.get('holdings',{}).keys() if sym not in local_current_prices ]
         if portfolio_symbols_needing_price:
             # ... (fetching logic remains the same) ...
             logging.info(f"Fetching display prices for holdings: {portfolio_symbols_needing_price}")
             data_now = fetch_stock_data(portfolio_symbols_needing_price, period="5d")
             if not data_now.empty:
                 if len(portfolio_symbols_needing_price) == 1 and isinstance(data_now.columns, pd.Index):
                     sym = portfolio_symbols_needing_price[0]
                     try:
                         if 'Close' in data_now.columns and not data_now['Close'].empty: local_current_prices[sym] = data_now['Close'].iloc[-1]
                     except: pass
                 elif isinstance(data_now.columns, pd.MultiIndex):
                     for sym in portfolio_symbols_needing_price:
                         if sym in data_now.columns.levels[0]:
                             try:
                                 close_col = data_now[(sym, 'Close')]
                                 if not close_col.empty: local_current_prices[sym] = close_col.iloc[-1]
                             except: pass

         total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
         local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}

    except Exception as e:
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        local_error = (local_error + " | Error calculating portfolio value." if local_error else "Error calculating portfolio value.")
        local_portfolio_display = {'total_value': 'Error', 'cash': 'Error', 'holdings': []}
        if local_portfolio_state: local_portfolio_display['cash'] = local_portfolio_state.get('cash', 'Error')


    # --- Run Backtesting Example --- (Logic remains the same)
    logging.info(f"Running backtest for {BACKTEST_SYMBOL}...")
    try:
        backtest_data = fetch_stock_data([BACKTEST_SYMBOL], period=BACKTEST_PERIOD)
        if not backtest_data.empty:
             local_backtest_results = run_backtest(BACKTEST_SYMBOL, backtest_data.copy(), initial_capital=INITIAL_CASH)
        else:
             local_backtest_results = {"error": f"Could not fetch data for {BACKTEST_SYMBOL}."}
    except Exception as e:
        logging.error(f"Error running backtest for {BACKTEST_SYMBOL}: {e}", exc_info=True)
        local_backtest_results = {"error": f"An error occurred during backtesting: {e}"}

    # --- Update Cache ---
    app_cache['all_stock_data'] = local_all_stock_data
    app_cache['portfolio_display'] = local_portfolio_display
    app_cache['backtest_results'] = local_backtest_results
    app_cache['trades_executed'] = local_trades_executed
    app_cache['last_update_time'] = datetime.now(timezone.utc)
    app_cache['processing_error'] = local_error
    # dataframe_summary was updated earlier if successful

    end_process_time = time.time()
    logging.info(f"--- Background Data Processing Finished ({end_process_time - start_process_time:.2f} seconds) ---")
    if local_error: logging.error(f"Processing finished with error(s): {local_error}")
    else: logging.info("Processing finished successfully.")
# --- End Background Data Processing Function ---


# --- Flask Route --- (Logic remains the same)
@app.route('/')
def index():
    """Serves the cached data or triggers processing if cache is stale."""
    now = datetime.now(timezone.utc)
    cache_needs_update = False

    if app_cache['last_update_time'] is None:
        logging.info("Cache is empty. Triggering initial data processing.")
        cache_needs_update = True
    else:
        time_since_update = now - app_cache['last_update_time']
        if time_since_update.total_seconds() > CACHE_DURATION_SECONDS:
            logging.info(f"Cache expired. Triggering data processing.")
            cache_needs_update = True
        else:
            logging.info("Serving data from cache.")

    if cache_needs_update:
        try:
            process_all_data() # Blocks request on first/expired load
        except Exception as e:
             logging.error(f"Critical error calling process_all_data: {e}", exc_info=True)
             app_cache['processing_error'] = f"Failed to run update process: {e}"
             if app_cache['last_update_time'] is None:
                  return render_template('index.html', error=f"Initial data processing failed: {e}", last_updated="Never")

    # --- Render Template using data from app_cache ---
    last_updated_str = app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC') if app_cache['last_update_time'] else "Processing..."
    display_error = app_cache.get('processing_error')

    return render_template(
        'index.html',
        all_stock_data=app_cache.get('all_stock_data', []),
        paper_portfolio=app_cache.get('portfolio_display'),
        initial_capital=INITIAL_CASH,
        trades_executed=app_cache.get('trades_executed', []),
        backtest_results=app_cache.get('backtest_results'),
        last_updated=last_updated_str,
        error=display_error
    )

# --- Main Execution --- (Logic remains the same)
if __name__ == '__main__':
    logging.info("Performing initial data load on startup...")
    process_all_data()
    logging.info("Initial data load complete. Starting web server (via Gunicorn usually)...")
    # Gunicorn runs based on Procfile in Render
    # port = int(os.environ.get('PORT', 8080))
    # app.run(host='0.0.0.0', port=port, debug=False) # For local testing only
