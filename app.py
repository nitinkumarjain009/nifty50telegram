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
# Ensure the correct function name is imported for photo sending
from telegram_sender import notify_recommendations_photo

# Configure logging
# Use INFO for general flow, DEBUG for detailed symbol processing if needed
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Constants ---
STOCK_LIST_FILE = 'nifty50_stocks.csv'
DATA_FETCH_PERIOD = "6mo"
BACKTEST_SYMBOL = "RELIANCE.NS" # Example symbol for backtest
BACKTEST_PERIOD = "6mo"
CACHE_DURATION_SECONDS = 3600 # Cache results for 1 hour

# --- Simple In-Memory Cache ---
# Stores the results of the last successful data processing run
app_cache = {
    "last_update_time": None,
    "all_stock_data": [],       # Data for HTML table
    "portfolio_display": None,  # Formatted portfolio info
    "backtest_results": None,   # Results of the example backtest
    "trades_executed": [],      # Paper trades from the last run
    "processing_error": None,   # Stores error message if processing fails
    "dataframe_summary": None   # Stores formatted DataFrame for Telegram
}
# -----------------------------


# --- Helper Functions ---

def get_stock_symbols():
    """
    Reads stock symbols (expected to include .NS suffix) from the CSV file,
    cleans them (removes whitespace), and returns a unique list.
    """
    try:
        # Check if the file exists relative to the current working directory
        if not os.path.exists(STOCK_LIST_FILE):
             logging.error(f"Error: Stock list file '{STOCK_LIST_FILE}' not found at CWD: {os.getcwd()}")
             return []

        logging.info(f"Reading symbols from {STOCK_LIST_FILE}")
        # Read CSV, explicitly setting dtype for the Symbol column to string
        df = pd.read_csv(STOCK_LIST_FILE, dtype={'Symbol': str})

        # Check if the required 'Symbol' column exists
        if 'Symbol' not in df.columns:
             logging.error(f"'Symbol' column not found in {STOCK_LIST_FILE}")
             return []

        # Get the raw symbols from the 'Symbol' column
        raw_symbols = df['Symbol'].tolist()
        logging.debug(f"Raw symbols read from CSV: {raw_symbols}")

        # Clean and validate the symbols read from the CSV
        symbols = []
        for s in raw_symbols:
            # Skip if the value is considered missing by pandas
            if pd.isna(s):
                logging.debug("Skipping NaN symbol.")
                continue
            # Ensure the value is treated as a string
            s_str = str(s)
            # Remove only leading/trailing whitespace (preserves internal structure like '.NS')
            cleaned_symbol = s_str.strip()
            # Check if the symbol is non-empty after cleaning
            if cleaned_symbol:
                # Basic sanity check for format (e.g., contains '.' and is reasonably long)
                if '.' in cleaned_symbol and len(cleaned_symbol) > 3:
                    symbols.append(cleaned_symbol)
                    logging.debug(f"Accepted symbol: {repr(cleaned_symbol)}")
                else:
                    # Log a warning if the format seems unusual after cleaning
                    logging.warning(f"Skipping potentially invalid symbol format after cleaning: {repr(cleaned_symbol)}")
            else:
                # Log a warning if the symbol was empty or became empty after stripping
                logging.warning(f"Skipping empty symbol after stripping from raw value: {repr(s_str)}")

        # Create a sorted list of unique symbols
        unique_symbols = sorted(list(set(symbols)))

        # Check if any valid symbols were found
        if not unique_symbols:
             logging.error(f"No valid symbols found in {STOCK_LIST_FILE} after cleaning.")
             return []

        # Log the result (showing only the first few symbols for brevity)
        logging.info(f"Loaded {len(unique_symbols)} unique, valid symbols: {unique_symbols[:10]}...")
        return unique_symbols

    except FileNotFoundError:
        # This catch might be redundant due to os.path.exists, but serves as a failsafe
        logging.error(f"Error: {STOCK_LIST_FILE} not found during read_csv operation.")
        return []
    except pd.errors.EmptyDataError:
         # Handle cases where the CSV file is empty or contains only headers
         logging.error(f"Error: {STOCK_LIST_FILE} is empty or contains only headers.")
         return []
    except Exception as e:
        # Catch any other unexpected errors during file reading or processing
        logging.error(f"Critical error reading or processing {STOCK_LIST_FILE}: {e}", exc_info=True)
        return []


def fetch_stock_data(symbols, period="6mo"): # Default period matches DATA_FETCH_PERIOD
    """
    Fetches historical data for a list of symbols (expected to have .NS) using yfinance.
    """
    # Ensure input 'symbols' is a non-empty list
    if not symbols:
        logging.warning("fetch_stock_data called with empty symbols list.")
        return pd.DataFrame()
    # Standardize input to be a list
    if isinstance(symbols, str):
        symbols = [symbols]
    # Ensure all elements in the list are valid, non-empty strings
    if not all(isinstance(s, str) and s for s in symbols):
         logging.error(f"fetch_stock_data received invalid input (non-strings or empty): {symbols}")
         return pd.DataFrame()

    try:
        # Log the fetch operation (use DEBUG for less verbose logs during normal operation)
        logging.debug(f"Fetching {period} data for symbols: {symbols}...")
        start_time = time.time()

        # Call yfinance download
        # auto_adjust=True provides adjusted prices for splits/dividends
        # progress=False disables the progress bar in logs
        data = yf.download(symbols, period=period, auto_adjust=True, progress=False)

        end_time = time.time()
        logging.debug(f"Data fetch for {symbols} completed in {end_time - start_time:.2f} seconds.")

        # Check if yfinance returned an empty DataFrame
        if data.empty:
            logging.warning(f"No data returned by yfinance for symbols: {symbols}")
            return pd.DataFrame()

        # Return the fetched data (structure depends on len(symbols))
        return data

    except Exception as e:
        # Log any exception that occurs during the download process
        logging.error(f"Error during yfinance download/processing for {symbols}: {e}", exc_info=True)
        return pd.DataFrame() # Return an empty DataFrame on error


# --- Background Data Processing Function ---
# This function performs the core logic: fetching, calculating, generating reports
def process_all_data():
    """Fetches data, calculates all required values, updates portfolio, runs backtest."""
    global app_cache # Allow modification of the global cache dictionary
    logging.info("--- Starting Background Data Processing ---")
    start_process_time = time.time()

    # Initialize variables for this processing run
    local_all_stock_data = []               # Stores dicts for the HTML table
    local_recommendations_for_trade = []    # Stores BUY/SELL signals for paper trading
    local_current_prices = {}               # Stores last close price for each symbol
    local_trades_executed = []              # Stores paper trades executed in this run
    local_portfolio_state = None            # Holds the loaded/updated portfolio
    local_backtest_results = None           # Holds backtest results
    local_error = None                      # Accumulates error messages for this run
    dataframe_for_telegram = pd.DataFrame() # DataFrame to generate Telegram image

    # --- Step 1: Get Stock Symbols ---
    symbols = get_stock_symbols()

    # --- Step 2: Process Each Symbol (if symbols were loaded) ---
    if not symbols:
        local_error = f"Could not load valid symbols from {STOCK_LIST_FILE}. Cannot process stocks."
        logging.error(local_error)
        # Attempt to load portfolio state even if symbols fail, to display holdings
        try:
            local_portfolio_state = load_portfolio()
        except Exception as load_err:
             logging.error(f"Failed to load portfolio state after symbol load failed: {load_err}")
             local_error += f" | Failed to load portfolio: {load_err}"
             local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}} # Default empty state
    else:
        # Proceed only if symbols were successfully loaded
        logging.info(f"Processing {len(symbols)} symbols sequentially...")
        # --- Symbol Loop ---
        for symbol in symbols: # 'symbol' includes '.NS' as read from CSV
            # Initialize DataFrames for this iteration
            symbol_data = pd.DataFrame()
            df_with_indicators = pd.DataFrame()
            logging.debug(f"--- Processing symbol: {repr(symbol)} ---")
            try:
                # Fetch data for the current symbol
                symbol_data = fetch_stock_data([symbol], period=DATA_FETCH_PERIOD)

                # --- Data Validation Checks ---
                if symbol_data.empty:
                    logging.warning(f"Skipping {repr(symbol)}: No data fetched.")
                    continue # Move to the next symbol
                if len(symbol_data) < 2: # Need at least 2 rows for % change and some indicators
                    logging.warning(f"Skipping {repr(symbol)}: Insufficient data rows fetched ({len(symbol_data)}).")
                    continue

                df_symbol = symbol_data.copy()

                # *** CRITICAL: Check if 'Close' column exists before using it ***
                if 'Close' not in df_symbol.columns:
                     logging.warning(f"Skipping {repr(symbol)}: 'Close' column MISSING in fetched data. Columns: {df_symbol.columns.tolist()}")
                     continue # Skip if the essential 'Close' column is absent

                # Drop rows where 'Close' price is NaN (missing)
                df_symbol = df_symbol.dropna(subset=['Close'])

                # Check again after dropping NaNs
                if df_symbol.empty:
                    logging.warning(f"Skipping {repr(symbol)}: DataFrame empty after dropna for 'Close'.")
                    continue
                if len(df_symbol) < 2:
                    logging.warning(f"Skipping {repr(symbol)}: Insufficient valid 'Close' data ({len(df_symbol)} rows) after dropna.")
                    continue

                # --- Indicator Calculation ---
                df_with_indicators = calculate_all_indicators(df_symbol)

                # Validate indicator results
                if df_with_indicators.empty:
                    logging.warning(f"Skipping {repr(symbol)}: Indicator calculation resulted in empty DataFrame.")
                    continue
                if 'Close' not in df_with_indicators.columns or len(df_with_indicators) < 2:
                     logging.warning(f"Skipping {repr(symbol)}: Indicators DataFrame missing 'Close' or insufficient rows ({len(df_with_indicators)}).")
                     continue

                # --- Extract Prices & Calculate Change ---
                current_close = df_with_indicators['Close'].iloc[-1]
                prev_close = df_with_indicators['Close'].iloc[-2]
                local_current_prices[symbol] = current_close # Store CMP for portfolio valuation

                percent_change = ((current_close - prev_close) / prev_close) * 100 if prev_close else 0.0

                # --- Generate Trading Signal ---
                recommendation_result = generate_recommendations(symbol, df_with_indicators)
                signal = "HOLD" # Default signal
                target = None
                if recommendation_result:
                    signal = recommendation_result.get('signal', 'HOLD')
                    target = recommendation_result.get('target')
                    # Add to list for paper trading only if BUY or SELL signal
                    if signal in ['BUY', 'SELL']:
                         local_recommendations_for_trade.append(recommendation_result)

                # --- Store Combined Data ---
                stock_info = {
                    'symbol': symbol,
                    'cmp': current_close,
                    'percent_change': percent_change,
                    'signal': signal,
                    'target': target
                }
                local_all_stock_data.append(stock_info)

            # --- Error Handling for the Symbol Loop ---
            except KeyError as ke:
                # Catch errors where a required column name is missing
                 logging.error(f"KeyError processing {repr(symbol)}: {ke}", exc_info=True)
                 local_error = f"Data error for {symbol} (KeyError)."
            except IndexError as idx_err:
                # Catch errors related to accessing data by index (e.g., iloc[-1] on empty data)
                 logging.warning(f"IndexError processing {repr(symbol)} (likely price/indicator access): {idx_err}. Skipping symbol.")
            except Exception as e:
                # Catch any other unexpected errors during processing for this symbol
                logging.error(f"Unhandled error processing symbol {repr(symbol)}: {e}", exc_info=True)
                local_error = f"Unexpected error processing {symbol} (see logs)." # Set general error flag

            finally:
                # --- Cleanup ---
                # Explicitly delete large DataFrames to help free memory sooner
                del symbol_data
                del df_with_indicators
                gc.collect() # Trigger garbage collection
        # --- End Symbol Loop ---
        logging.info(f"Finished processing symbols.")

        # --- Step 3: Prepare Data for Telegram ---
        if local_all_stock_data: # Check if any stock data was successfully processed
             try:
                 # Create DataFrame from the collected stock info
                 dataframe_for_telegram = pd.DataFrame(local_all_stock_data)
                 # Select and rename columns for the display image
                 df_display = dataframe_for_telegram[['symbol', 'cmp', 'percent_change', 'signal', 'target']].copy()
                 df_display.rename(columns={
                     'symbol': 'Symbol', 'cmp': 'CMP', 'percent_change': '% Change',
                     'signal': 'Signal', 'target': 'Target'
                 }, inplace=True)
                 # Format numerical columns for better readability in the image
                 df_display['CMP'] = df_display['CMP'].map('{:,.2f}'.format)
                 df_display['% Change'] = df_display['% Change'].map('{:,.2f}%'.format)
                 df_display['Target'] = df_display['Target'].map(lambda x: '{:,.2f}'.format(x) if pd.notnull(x) else 'N/A')
                 # Store the formatted DataFrame in the cache for sending
                 app_cache['dataframe_summary'] = df_display
                 logging.info("Created and formatted DataFrame for Telegram.")
             except Exception as df_err:
                 logging.error(f"Error creating or formatting DataFrame for Telegram: {df_err}", exc_info=True)
                 local_error = (local_error + " | Error preparing data for Telegram." if local_error else
                                "Error preparing data for Telegram.")
                 dataframe_for_telegram = pd.DataFrame() # Ensure it's empty on error
                 app_cache['dataframe_summary'] = None # Clear from cache on error
        else:
             logging.warning("No stock data processed, cannot create DataFrame for Telegram.")
             app_cache['dataframe_summary'] = None


        # --- Step 4: Update Paper Trading Portfolio ---
        if local_recommendations_for_trade: # Check if there were any BUY/SELL signals
            logging.info("Updating paper trading portfolio based on BUY/SELL signals...")
            # Filter recommendations to include only those with available current prices
            valid_trade_recs = [rec for rec in local_recommendations_for_trade if rec['symbol'] in local_current_prices]
            if valid_trade_recs:
                 try:
                     # Call the update function with valid recommendations and prices
                     local_portfolio_state, local_trades_executed = update_paper_portfolio(valid_trade_recs, local_current_prices)
                     logging.info(f"Paper trading portfolio updated. Trades executed: {len(local_trades_executed)}")
                 except Exception as trade_err:
                      # Handle errors during the portfolio update process
                      logging.error(f"Error updating paper portfolio: {trade_err}", exc_info=True)
                      local_error = (local_error + " | Error during paper trading." if local_error else
                                     "Error during paper trading.")
                      # Attempt to load the previous state if update fails
                      try: local_portfolio_state = load_portfolio()
                      except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
            else:
                 # Log if BUY/SELL signals were generated but lacked price data for execution
                 logging.warning("No valid recommendations with current prices found for paper trading execution.")
                 try: local_portfolio_state = load_portfolio() # Load state if no trades executed
                 except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
        else:
            # Log if no BUY/SELL signals were generated in this run
            logging.info("No BUY/SELL recommendations generated for paper trading in this run.")
            # Load current portfolio state if no trading actions were needed
            try:
                local_portfolio_state = load_portfolio()
            except Exception as load_err:
                 logging.error(f"Failed to load portfolio state when no trades occurred: {load_err}")
                 local_error = (local_error + f" | Failed to load portfolio: {load_err}" if local_error else
                                f"Failed to load portfolio: {load_err}")
                 local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}


        # --- Step 5: Send Telegram Notification ---
        # Retrieve the formatted DataFrame from the cache
        df_summary_to_send = app_cache.get('dataframe_summary')
        # Send only if the DataFrame exists and is not empty
        if df_summary_to_send is not None and not df_summary_to_send.empty:
             logging.info("Sending Telegram notification photo...")
             notify_recommendations_photo(df_summary_to_send) # Send the formatted DataFrame image
        elif not local_all_stock_data: # Only warn if no data was processed at all
             logging.warning("Skipping Telegram photo: No stock data was processed.")
        else: # Warn if data was processed but DF creation/formatting failed
             logging.warning("Skipping Telegram photo: Summary DataFrame is empty or could not be generated.")


    # --- Step 6: Calculate Portfolio Display Value ---
    local_portfolio_display = None
    try:
        # Ensure portfolio state is loaded if it wasn't determined earlier
        if local_portfolio_state is None:
             logging.info("Portfolio state was None after processing, attempting final load.")
             try:
                 local_portfolio_state = load_portfolio()
             except Exception as load_err:
                  logging.error(f"Final attempt to load portfolio failed: {load_err}")
                  local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}} # Use default on error

        # Ensure current prices dictionary exists
        if local_current_prices is None: local_current_prices = {}

        # Identify portfolio holdings that might be missing a current price
        portfolio_symbols_needing_price = [
            sym for sym in local_portfolio_state.get('holdings',{}).keys()
            if sym not in local_current_prices
        ]
        # Fetch prices only if needed
        if portfolio_symbols_needing_price:
             logging.info(f"Fetching display prices for current holdings: {portfolio_symbols_needing_price}")
             data_now = fetch_stock_data(portfolio_symbols_needing_price, period="5d") # Fetch recent data
             if not data_now.empty:
                 # Handle data structure returned by yfinance (single vs multiple)
                 if len(portfolio_symbols_needing_price) == 1 and isinstance(data_now.columns, pd.Index):
                     # Single symbol result (standard DataFrame)
                     sym = portfolio_symbols_needing_price[0]
                     try:
                         if 'Close' in data_now.columns and not data_now['Close'].empty:
                              local_current_prices[sym] = data_now['Close'].iloc[-1]
                     except: pass # Ignore errors fetching individual prices here
                 elif isinstance(data_now.columns, pd.MultiIndex):
                     # Multiple symbol result (MultiIndex DataFrame)
                     for sym in portfolio_symbols_needing_price:
                         # Check if symbol data exists in the columns
                         if sym in data_now.columns.levels[0]:
                             try:
                                 # Access Close price using tuple for MultiIndex
                                 close_col = data_now[(sym, 'Close')]
                                 if not close_col.empty:
                                      local_current_prices[sym] = close_col.iloc[-1]
                             except: pass # Ignore errors fetching individual prices here

        # Calculate final portfolio value using available prices
        total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
        local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}
        logging.info("Portfolio display data calculated.")

    except Exception as e:
        # Handle errors during portfolio value calculation
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        local_error = (local_error + " | Error calculating portfolio value." if local_error else
                       "Error calculating portfolio value.")
        # Provide default display structure on error
        local_portfolio_display = {'total_value': 'Error', 'cash': 'Error', 'holdings': []}
        if local_portfolio_state: # Try to get cash value if state exists
            local_portfolio_display['cash'] = local_portfolio_state.get('cash', 'Error')


    # --- Step 7: Run Backtesting Example ---
    logging.info(f"Running backtest for {BACKTEST_SYMBOL} using {BACKTEST_PERIOD} data...")
    try:
        # Fetch data specifically for the backtest symbol
        backtest_data = fetch_stock_data([BACKTEST_SYMBOL], period=BACKTEST_PERIOD)
        if not backtest_data.empty:
             # Run the backtest function
             local_backtest_results = run_backtest(BACKTEST_SYMBOL, backtest_data.copy(), initial_capital=INITIAL_CASH)
             logging.info(f"Backtest for {BACKTEST_SYMBOL} completed.")
        else:
             # Handle failure to fetch backtest data
             logging.error(f"Failed to fetch data for backtesting symbol {BACKTEST_SYMBOL}.")
             local_backtest_results = {"error": f"Could not fetch data for {BACKTEST_SYMBOL}."}
    except Exception as e:
        # Handle errors during the backtest execution
        logging.error(f"Error running backtest for {BACKTEST_SYMBOL}: {e}", exc_info=True)
        local_backtest_results = {"error": f"An error occurred during backtesting: {e}"}


    # --- Step 8: Update Cache with Results ---
    app_cache['all_stock_data'] = local_all_stock_data
    app_cache['portfolio_display'] = local_portfolio_display
    app_cache['backtest_results'] = local_backtest_results
    app_cache['trades_executed'] = local_trades_executed # Store trades from THIS run
    app_cache['last_update_time'] = datetime.now(timezone.utc) # Record update time
    app_cache['processing_error'] = local_error # Store any accumulated error message
    # 'dataframe_summary' was updated earlier if successful

    end_process_time = time.time()
    logging.info(f"--- Background Data Processing Finished ({end_process_time - start_process_time:.2f} seconds) ---")
    # Log final status
    if local_error:
         logging.error(f"Processing finished with error(s): {local_error}")
    else:
         logging.info("Processing finished successfully.")
# --- End Background Data Processing Function ---


# --- Flask Route ---
# Defines the web endpoint that users access
@app.route('/')
def index():
    """Serves the cached data or triggers processing if cache is stale."""
    now = datetime.now(timezone.utc)
    cache_needs_update = False

    # --- Check Cache Status ---
    if app_cache['last_update_time'] is None:
        # Cache is empty (e.g., first run after startup)
        logging.info("Cache is empty. Triggering initial data processing.")
        cache_needs_update = True
    else:
        # Calculate time since last update
        time_since_update = now - app_cache['last_update_time']
        # Check if cache duration has been exceeded
        if time_since_update.total_seconds() > CACHE_DURATION_SECONDS:
            logging.info(f"Cache expired ({time_since_update.total_seconds():.0f}s > {CACHE_DURATION_SECONDS}s). Triggering data processing.")
            cache_needs_update = True
        else:
            # Cache is still valid
            logging.info("Serving data from cache.")

    # --- Trigger Data Processing if Needed ---
    if cache_needs_update:
        try:
            # Call the main processing function
            # NOTE: This call BLOCKS the web request until processing is complete.
            # For production apps with long tasks, use background workers (Celery, etc.).
            process_all_data()
        except Exception as e:
             # Catch critical errors during the processing function call itself
             logging.error(f"Critical error calling process_all_data: {e}", exc_info=True)
             # Store the error message in cache to display on the page
             app_cache['processing_error'] = f"Failed to run update process: {e}"
             # If cache was never populated (initial run failed), return error page
             if app_cache['last_update_time'] is None:
                  return render_template('index.html',
                                         error=f"Initial data processing failed: {e}",
                                         last_updated="Never")
             # Otherwise, fall through to serve potentially stale cache data with the new error message

    # --- Render Template using data from app_cache ---
    # Safely format the last update time for display
    last_updated_str = app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC') if app_cache['last_update_time'] else "Processing..."

    # Get the error message from cache (if any) to display
    display_error = app_cache.get('processing_error')

    # Render the HTML template, passing cached data
    # Use .get() with defaults for resilience against missing keys
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

# --- Main Execution ---
# This block runs only when the script is executed directly (not imported)
if __name__ == '__main__':
    # --- Initial Data Load ---
    # Perform one full data processing cycle on startup.
    # This populates the cache before the first web request arrives.
    # It helps ensure the app is responsive immediately and passes initial health checks.
    logging.info("Performing initial data load on startup...")
    process_all_data()
    logging.info("Initial data load complete. Web server starting (via Gunicorn on Render)...")

    # --- Start Development Server (for local testing only) ---
    # On Render, Gunicorn is started via the Procfile, so this app.run() won't execute there.
    # Use host='0.0.0.0' to make it accessible on the network.
    # Set debug=True ONLY for local development, NEVER in production.
    # port = int(os.environ.get('PORT', 8080))
    # app.run(host='0.0.0.0', port=port, debug=False)
