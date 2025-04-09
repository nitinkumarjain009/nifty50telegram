# app.py
from flask import Flask, render_template
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone
import os
import logging
import time
import gc
import numpy as np # Import numpy for the KeyError fix check

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
# Remove STOCK_LIST_FILE constant
DATA_FETCH_PERIOD = "6mo"
BACKTEST_SYMBOL = "RELIANCE.NS" # Example symbol for backtest
BACKTEST_PERIOD = "6mo"
CACHE_DURATION_SECONDS = 3600 # Cache results for 1 hour

# --- Hardcoded Nifty 50 List (with .NS suffix) ---
# Source: Verify periodically (e.g., from NSE website or reliable finance portal)
NIFTY_50_SYMBOLS = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS",
    "HINDUNILVR.NS", "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LICI.NS",
    "KOTAKBANK.NS", "HCLTECH.NS", "LT.NS", "BAJFINANCE.NS", "AXISBANK.NS",
    "MARUTI.NS", "SUNPHARMA.NS", "ASIANPAINT.NS", "TITAN.NS", "ULTRACEMCO.NS",
    "WIPRO.NS", "NESTLEIND.NS", "NTPC.NS", "JSWSTEEL.NS", "M&M.NS",
    "ADANIENT.NS", "POWERGRID.NS", "TATAMOTORS.NS", "BAJAJFINSV.NS", "ADANIPORTS.NS",
    "TATASTEEL.NS", "COALINDIA.NS", "SBILIFE.NS", "HDFCLIFE.NS", "BRITANNIA.NS",
    "INDUSINDBK.NS", "GRASIM.NS", "EICHERMOT.NS", "HINDALCO.NS", "TECHM.NS",
    "CIPLA.NS", "APOLLOHOSP.NS", "DRREDDY.NS", "ONGC.NS", "DIVISLAB.NS",
    "TATACONSUM.NS", "SHRIRAMFIN.NS", "HEROMOTOCO.NS", "BPCL.NS", "BAJAJ-AUTO.NS"
    # Make sure this list has exactly 50, verify symbols if errors persist
]
# ------------------------------------------------

# --- Simple In-Memory Cache --- (Same as before)
app_cache = {
    "last_update_time": None, "all_stock_data": [], "portfolio_display": None,
    "backtest_results": None, "trades_executed": [], "processing_error": None,
    "dataframe_summary": None
}
# -----------------------------


# --- Helper Functions ---

# REMOVE get_stock_symbols() function as it's no longer needed

def fetch_stock_data(symbol, period="6mo"): # Simplify to fetch ONLY ONE symbol
    """
    Fetches historical data for a SINGLE symbol using yfinance.
    Returns a simple DataFrame.
    """
    if not symbol or not isinstance(symbol, str):
        logging.warning(f"fetch_stock_data called with invalid symbol: {symbol}")
        return pd.DataFrame()

    try:
        logging.debug(f"Fetching {period} data for symbol: {symbol}...")
        start_time = time.time()
        # Fetch data for the single symbol
        data = yf.download(symbol, period=period, auto_adjust=True, progress=False)
        end_time = time.time()
        logging.debug(f"Data fetch for {symbol} completed in {end_time - start_time:.2f} seconds.")

        if data.empty:
            logging.warning(f"No data returned by yfinance for symbol: {symbol}")
            return pd.DataFrame()

        # Should return a simple DataFrame for a single symbol
        return data

    except Exception as e:
        logging.error(f"Error during yfinance download for {symbol}: {e}", exc_info=True)
        return pd.DataFrame() # Return empty DataFrame on error

# --- Background Data Processing Function ---
def process_all_data():
    """Fetches data, calculates all required values, updates portfolio, runs backtest."""
    global app_cache
    logging.info("--- Starting Background Data Processing ---")
    start_process_time = time.time()

    # Initialize variables for this processing run
    local_all_stock_data = []
    local_recommendations_for_trade = []
    local_current_prices = {}
    local_trades_executed = []
    local_portfolio_state = None
    local_backtest_results = None
    local_error = None
    dataframe_for_telegram = pd.DataFrame()

    # --- Step 1: Use Hardcoded Symbol List ---
    symbols = NIFTY_50_SYMBOLS
    logging.info(f"Using hardcoded list of {len(symbols)} Nifty 50 symbols.")

    # --- Step 2: Process Each Symbol ---
    if not symbols: # Should not happen with hardcoded list, but good check
        local_error = "Symbol list is empty. Cannot process."
        logging.error(local_error)
        # Load portfolio state anyway
        try: local_portfolio_state = load_portfolio()
        except Exception as load_err:
             local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
    else:
        logging.info(f"Processing {len(symbols)} symbols sequentially...")
        # --- Symbol Loop ---
        for symbol in symbols: # 'symbol' includes '.NS'
            symbol_data = pd.DataFrame()
            df_with_indicators = pd.DataFrame()
            logging.debug(f"--- Processing symbol: {repr(symbol)} ---")
            try:
                # Fetch data for the single symbol
                # Use the simplified fetch_stock_data for ONE symbol
                symbol_data = fetch_stock_data(symbol, period=DATA_FETCH_PERIOD)

                # --- Data Validation Checks ---
                if symbol_data.empty:
                    logging.warning(f"Skipping {repr(symbol)}: No data fetched.")
                    continue
                if len(symbol_data) < 2:
                    logging.warning(f"Skipping {repr(symbol)}: Insufficient data rows fetched ({len(symbol_data)}).")
                    continue

                # *** MORE ROBUST CHECK FOR 'Close' COLUMN ***
                # Check both direct column name and potential first level of MultiIndex
                close_col_found = False
                if isinstance(symbol_data.columns, pd.MultiIndex):
                    if 'Close' in symbol_data.columns.get_level_values(0):
                        close_col_found = True
                        # If MultiIndex, select only the 'Close' column properly
                        # This might happen if yfinance behaves unexpectedly
                        logging.warning(f"Received MultiIndex for single symbol {repr(symbol)}, selecting 'Close'.")
                        df_symbol = symbol_data.xs('Close', axis=1, level=0).copy()
                        # Ensure the result is still a DataFrame with a 'Close' column (it might become a Series)
                        if isinstance(df_symbol, pd.Series):
                            df_symbol= df_symbol.to_frame(name='Close')
                        # Check again if 'Close' exists after extraction
                        if 'Close' not in df_symbol.columns:
                             logging.error(f"Failed to extract 'Close' column from MultiIndex for {repr(symbol)}")
                             continue
                    else:
                         logging.warning(f"Skipping {repr(symbol)}: 'Close' column MISSING in fetched MultiIndex data. Levels: {symbol_data.columns.levels}")
                         continue
                elif 'Close' in symbol_data.columns:
                    close_col_found = True
                    df_symbol = symbol_data.copy() # Standard case
                else:
                     logging.warning(f"Skipping {repr(symbol)}: 'Close' column MISSING in fetched data. Columns available: {symbol_data.columns.tolist()}")
                     continue

                # If Close column was found and df_symbol prepared:
                if not close_col_found: # Should not be reached if logic above is correct, but safety check
                     logging.error(f"Logic error: close_col_found is False for {repr(symbol)} despite checks.")
                     continue

                # Now df_symbol should reliably be a DataFrame with a 'Close' column
                # Drop rows where 'Close' price is NaN
                df_symbol = df_symbol.dropna(subset=['Close']) # THIS LINE SHOULD NOW BE SAFE

                # Check again after dropping NaNs
                if df_symbol.empty:
                    logging.warning(f"Skipping {repr(symbol)}: DataFrame empty after dropna for 'Close'.")
                    continue
                if len(df_symbol) < 2:
                    logging.warning(f"Skipping {repr(symbol)}: Insufficient valid 'Close' data ({len(df_symbol)} rows) after dropna.")
                    continue

                # --- Indicator Calculation --- (Rest of loop is mostly the same)
                df_with_indicators = calculate_all_indicators(df_symbol)
                # ... (validation checks for indicators) ...
                if df_with_indicators.empty or 'Close' not in df_with_indicators.columns or len(df_with_indicators) < 2:
                     logging.warning(f"Skipping {repr(symbol)}: Indicator calculation failed or insufficient data.")
                     continue

                # --- Extract Prices & Calculate Change ---
                current_close = df_with_indicators['Close'].iloc[-1]
                prev_close = df_with_indicators['Close'].iloc[-2]
                local_current_prices[symbol] = current_close

                percent_change = ((current_close - prev_close) / prev_close) * 100 if prev_close else 0.0

                # --- Generate Trading Signal ---
                recommendation_result = generate_recommendations(symbol, df_with_indicators)
                # ... (signal/target extraction) ...
                signal = recommendation_result.get('signal', 'HOLD') if recommendation_result else "HOLD"
                target = recommendation_result.get('target') if recommendation_result else None
                if recommendation_result and signal in ['BUY', 'SELL']:
                     local_recommendations_for_trade.append(recommendation_result)

                # --- Store Combined Data ---
                stock_info = {'symbol': symbol, 'cmp': current_close, 'percent_change': percent_change, 'signal': signal, 'target': target}
                local_all_stock_data.append(stock_info)

            # --- Error Handling for the Symbol Loop ---
            except KeyError as ke:
                 logging.error(f"KeyError processing {repr(symbol)}: {ke}", exc_info=True)
                 local_error = f"Data error for {symbol} (KeyError)."
            except IndexError as idx_err:
                 logging.warning(f"IndexError processing {repr(symbol)} (likely price/indicator access): {idx_err}. Skipping symbol.")
            except Exception as e:
                logging.error(f"Unhandled error processing symbol {repr(symbol)}: {e}", exc_info=True)
                local_error = f"Unexpected error processing {symbol} (see logs)."

            finally:
                # --- Cleanup ---
                del symbol_data, df_with_indicators
                gc.collect()
        # --- End Symbol Loop ---
        logging.info(f"Finished processing symbols.")

    # --- Step 3: Prepare Data for Telegram --- (Same as before)
    if local_all_stock_data:
        try:
            dataframe_for_telegram = pd.DataFrame(local_all_stock_data)
            # ... (formatting logic for df_display) ...
            df_display = dataframe_for_telegram[['symbol', 'cmp', 'percent_change', 'signal', 'target']].copy()
            df_display.rename(columns={'symbol': 'Symbol', 'cmp': 'CMP', 'percent_change': '% Change', 'signal': 'Signal', 'target': 'Target'}, inplace=True)
            df_display['CMP'] = df_display['CMP'].map('{:,.2f}'.format)
            df_display['% Change'] = df_display['% Change'].map('{:,.2f}%'.format)
            df_display['Target'] = df_display['Target'].map(lambda x: '{:,.2f}'.format(x) if pd.notnull(x) else 'N/A')
            app_cache['dataframe_summary'] = df_display # Store formatted DF
        except Exception as df_err:
            logging.error(f"Error creating/formatting DataFrame for Telegram: {df_err}", exc_info=True)
            local_error = (local_error + " | Error preparing data for Telegram." if local_error else "Error preparing data for Telegram.")
            app_cache['dataframe_summary'] = None

    # --- Step 4: Update Paper Trading Portfolio --- (Same as before)
    if local_recommendations_for_trade:
        # ... (logic to update portfolio) ...
        valid_trade_recs = [rec for rec in local_recommendations_for_trade if rec['symbol'] in local_current_prices]
        if valid_trade_recs:
             try:
                 local_portfolio_state, local_trades_executed = update_paper_portfolio(valid_trade_recs, local_current_prices)
             except Exception as trade_err:
                  logging.error(f"Error updating paper portfolio: {trade_err}", exc_info=True)
                  local_error = (local_error + " | Error during paper trading." if local_error else "Error during paper trading.")
                  try: local_portfolio_state = load_portfolio()
                  except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
        else:
             try: local_portfolio_state = load_portfolio()
             except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
    else:
        try: local_portfolio_state = load_portfolio()
        except Exception as load_err:
             local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}

    # --- Step 5: Send Telegram Notification PHOTO --- (Same as before)
    df_summary_to_send = app_cache.get('dataframe_summary')
    if df_summary_to_send is not None and not df_summary_to_send.empty:
        logging.info("Sending Telegram notification photo...")
        notify_recommendations_photo(df_summary_to_send)
    # ... (logging for skipped photo) ...

    # --- Step 6: Calculate Portfolio Display Value --- (Same as before)
    try:
        # ... (logic to load portfolio if needed, fetch missing prices) ...
        if local_portfolio_state is None: local_portfolio_state = load_portfolio()
        if local_current_prices is None: local_current_prices = {}
        portfolio_symbols_needing_price = [ sym for sym in local_portfolio_state.get('holdings',{}).keys() if sym not in local_current_prices ]
        if portfolio_symbols_needing_price:
             data_now = fetch_stock_data(portfolio_symbols_needing_price, period="5d") # NOTE: fetch_stock_data was simplified, this call needs adjustment if used. For now, assume sequential processing covers most prices.
             # This part needs revision if portfolio prices are critical and not covered by main loop
             logging.warning("Fetching portfolio prices separately needs revised fetch_stock_data logic if multi-symbol is needed.")

        total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
        local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}
    except Exception as e:
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        local_error = (local_error + " | Error calculating portfolio value." if local_error else "Error calculating portfolio value.")
        local_portfolio_display = {'total_value': 'Error', 'cash': 'Error', 'holdings': []}
        if local_portfolio_state: local_portfolio_display['cash'] = local_portfolio_state.get('cash', 'Error')

    # --- Step 7: Run Backtesting Example --- (Same as before)
    logging.info(f"Running backtest for {BACKTEST_SYMBOL}...")
    try:
        # Use the simplified fetch_stock_data
        backtest_data = fetch_stock_data(BACKTEST_SYMBOL, period=BACKTEST_PERIOD)
        # ... (rest of backtest logic) ...
        if not backtest_data.empty:
             local_backtest_results = run_backtest(BACKTEST_SYMBOL, backtest_data.copy(), initial_capital=INITIAL_CASH)
        else:
             local_backtest_results = {"error": f"Could not fetch data for {BACKTEST_SYMBOL}."}
    except Exception as e:
        logging.error(f"Error running backtest for {BACKTEST_SYMBOL}: {e}", exc_info=True)
        local_backtest_results = {"error": f"An error occurred during backtesting: {e}"}

    # --- Step 8: Update Cache with Results --- (Same as before)
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


# --- Flask Route --- (Same as before)
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

# --- Main Execution --- (Same as before)
if __name__ == '__main__':
    logging.info("Performing initial data load on startup...")
    process_all_data()
    logging.info("Initial data load complete. Web server starting (via Gunicorn on Render)...")
    # Gunicorn runs based on Procfile in Render
    # port = int(os.environ.get('PORT', 8080))
    # app.run(host='0.0.0.0', port=port, debug=False) # For local testing only
