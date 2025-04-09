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
# *** Import the specific function for sending photos ***
from telegram_sender import notify_recommendations_photo # Renamed for clarity

# Configure logging... (same as before)

app = Flask(__name__)

# --- Constants --- (same as before)
STOCK_LIST_FILE = 'nifty50_stocks.csv'
DATA_FETCH_PERIOD = "6mo"
BACKTEST_SYMBOL = "RELIANCE.NS"
BACKTEST_PERIOD = "6mo"
CACHE_DURATION_SECONDS = 3600

# --- Simple In-Memory Cache ---
app_cache = {
    "last_update_time": None,
    # "recommendations": [], # We'll store combined data now
    "all_stock_data": [], # Store data for HTML table
    "portfolio_display": None,
    "backtest_results": None,
    "trades_executed": [],
    "processing_error": None,
    "dataframe_summary": None # Store df for telegram photo
}
# -----------------------------

# --- Helper Functions (get_stock_symbols, fetch_stock_data) ---
# ... (Keep the existing helper functions from the previous corrected version) ...
def get_stock_symbols():
    """Reads stock symbols from the CSV file."""
    try:
        if not os.path.exists(STOCK_LIST_FILE):
             logging.error(f"Error: Stock list file '{STOCK_LIST_FILE}' not found at CWD: {os.getcwd()}")
             return []
        df = pd.read_csv(STOCK_LIST_FILE)
        if 'Symbol' not in df.columns:
             logging.error(f"'Symbol' column not found in {STOCK_LIST_FILE}")
             return []
        symbols = df['Symbol'].dropna().unique().tolist()
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
    """Fetches historical data for symbols using yfinance."""
    if not symbols:
        logging.warning("fetch_stock_data called with empty symbols list.")
        return pd.DataFrame()
    if isinstance(symbols, str): symbols = [symbols]
    symbols = [s for s in symbols if isinstance(s, str) and s.strip()]
    if not symbols:
        logging.warning("fetch_stock_data called with empty/invalid symbols after filtering.")
        return pd.DataFrame()
    try:
        #logging.info(f"Fetching {period} data for symbols: {symbols}...")
        start_time = time.time()
        if len(symbols) == 1:
            ticker_str = symbols[0]
            data = yf.download(ticker_str, period=period, auto_adjust=True, progress=False)
        else:
            data = yf.download(symbols, period=period, group_by='ticker', auto_adjust=True, progress=False)
        end_time = time.time()
        #logging.info(f"Data fetch for {symbols} completed in {end_time - start_time:.2f} seconds.")
        if data.empty:
            logging.warning(f"No data returned by yfinance for symbols: {symbols}")
            return pd.DataFrame()
        return data
    except Exception as e:
        logging.error(f"Error during yfinance download/processing for {symbols}: {e}", exc_info=True)
        return pd.DataFrame()
# --- End Helper Functions ---


# --- Background Data Processing Function ---
def process_all_data():
    """Fetches data, calculates all required values, updates portfolio, runs backtest."""
    global app_cache
    logging.info("--- Starting Background Data Processing ---")
    start_process_time = time.time()

    # Reset state for this run
    local_all_stock_data = [] # Store dicts for HTML table
    local_recommendations_for_trade = [] # Store dicts for paper trading
    local_current_prices = {}
    local_trades_executed = []
    local_portfolio_state = None
    local_backtest_results = None
    local_error = None
    dataframe_for_telegram = pd.DataFrame() # Initialize DF

    symbols = get_stock_symbols()

    if not symbols:
        local_error = f"Could not load stock symbols from {STOCK_LIST_FILE} or file is empty/invalid."
        logging.error(local_error)
        try:
            local_portfolio_state = load_portfolio()
        except Exception as load_err:
             logging.error(f"Failed to load portfolio: {load_err}")
             local_error += f" | Failed to load portfolio: {load_err}"
             local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
    else:
        logging.info(f"Processing {len(symbols)} symbols sequentially...")
        # --- Symbol Loop ---
        for symbol in symbols:
            symbol_data = pd.DataFrame()
            df_with_indicators = pd.DataFrame()
            try:
                symbol_data = fetch_stock_data([symbol], period=DATA_FETCH_PERIOD)
                if symbol_data.empty or len(symbol_data) < 2: # Need at least 2 rows for % change
                    logging.warning(f"Skipping {symbol}: Not enough data ({len(symbol_data)} rows).")
                    continue

                df_symbol = symbol_data.copy()
                df_symbol = df_symbol.dropna(subset=['Close'])
                if df_symbol.empty or len(df_symbol) < 2:
                    logging.warning(f"Skipping {symbol}: Not enough valid 'Close' data ({len(df_symbol)} rows).")
                    continue

                df_with_indicators = calculate_all_indicators(df_symbol)
                if df_with_indicators.empty or len(df_with_indicators) < 2:
                     logging.warning(f"Skipping {symbol}: Indicator calculation failed or insufficient data.")
                     continue

                # --- Extract CMP and Previous Close ---
                current_close = df_with_indicators['Close'].iloc[-1]
                prev_close = df_with_indicators['Close'].iloc[-2]
                local_current_prices[symbol] = current_close # Store for portfolio valuation

                # --- Calculate % Change ---
                percent_change = 0.0
                if prev_close is not None and prev_close != 0:
                    percent_change = ((current_close - prev_close) / prev_close) * 100
                else:
                     logging.warning(f"Could not calculate % change for {symbol} (prev_close={prev_close})")

                # --- Get Recommendation ---
                recommendation_result = generate_recommendations(symbol, df_with_indicators)
                signal = "HOLD"
                target = None
                if recommendation_result:
                    signal = recommendation_result.get('signal', 'HOLD')
                    target = recommendation_result.get('target')
                    # Add recommendation to list for paper trading if it's BUY/SELL
                    if signal in ['BUY', 'SELL']:
                         local_recommendations_for_trade.append(recommendation_result)

                # --- Store data for HTML table and eventually DataFrame ---
                stock_info = {
                    'symbol': symbol,
                    'cmp': current_close,
                    'percent_change': percent_change,
                    'signal': signal,
                    'target': target
                }
                local_all_stock_data.append(stock_info)

            except IndexError:
                 logging.warning(f"IndexError likely processing indicators/prices for {symbol}. Skipping symbol.", exc_info=False)
            except Exception as e:
                logging.error(f"Error processing symbol {symbol}: {e}", exc_info=True)
                local_error = "Error during symbol processing (see logs for details)."
            finally:
                del symbol_data, df_with_indicators
                gc.collect()
        # --- End Symbol Loop ---
        logging.info(f"Finished processing {len(symbols)} symbols.")

        # --- Create DataFrame for Telegram ---
        if local_all_stock_data:
             dataframe_for_telegram = pd.DataFrame(local_all_stock_data)
             # Select and rename columns for the image
             df_display = dataframe_for_telegram[['symbol', 'cmp', 'percent_change', 'signal', 'target']].copy()
             df_display.rename(columns={
                 'symbol': 'Symbol', 'cmp': 'CMP', 'percent_change': '% Change',
                 'signal': 'Signal', 'target': 'Target'
                 }, inplace=True)
             # Formatting for display
             df_display['CMP'] = df_display['CMP'].map('{:,.2f}'.format)
             df_display['% Change'] = df_display['% Change'].map('{:,.2f}%'.format)
             df_display['Target'] = df_display['Target'].map(lambda x: '{:,.2f}'.format(x) if pd.notnull(x) else 'N/A')
             # Store the formatted DataFrame for caching
             app_cache['dataframe_summary'] = df_display # Store the display-ready DF

        # --- Paper Trading Update ---
        if local_recommendations_for_trade: # Use the filtered list
            logging.info("Updating paper trading portfolio...")
            # Ensure current prices are available for recommended stocks
            valid_trade_recs = [rec for rec in local_recommendations_for_trade if rec['symbol'] in local_current_prices]
            if valid_trade_recs:
                 try:
                     local_portfolio_state, local_trades_executed = update_paper_portfolio(valid_trade_recs, local_current_prices)
                 except Exception as trade_err:
                      logging.error(f"Error updating paper portfolio: {trade_err}", exc_info=True)
                      local_error = "Error during paper trading update."
                      local_portfolio_state = load_portfolio() # Load previous state
            else:
                 logging.warning("No valid recommendations with current prices found for trading.")
                 local_portfolio_state = load_portfolio()
        else:
            logging.info("No BUY/SELL recommendations generated for paper trading.")
            try:
                local_portfolio_state = load_portfolio()
            except Exception as load_err:
                 logging.error(f"Failed to load portfolio: {load_err}")
                 local_error = (local_error + f" | Failed to load portfolio: {load_err}" if local_error else
                                f"Failed to load portfolio: {load_err}")
                 local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}

        # --- Send Telegram Notification PHOTO ---
        # Send only if the dataframe was successfully created
        if not dataframe_for_telegram.empty:
             logging.info("Sending Telegram notification photo...")
             # Use the NOT display-formatted DF for potential internal use,
             # but send the DISPLAY df_display generated above
             notify_recommendations_photo(app_cache['dataframe_summary']) # Pass the display DF
        else:
             logging.warning("Skipping Telegram photo: DataFrame is empty.")


    # --- Get Portfolio Display Data --- (Same logic as before)
    local_portfolio_display = None
    try:
         if local_portfolio_state is None: local_portfolio_state = load_portfolio()
         if local_current_prices is None: local_current_prices = {}
         portfolio_symbols_needing_price = [
             sym for sym in local_portfolio_state.get('holdings',{}).keys() if sym not in local_current_prices
         ]
         if portfolio_symbols_needing_price:
             data_now = fetch_stock_data(portfolio_symbols_needing_price, period="5d")
             if not data_now.empty:
                 if len(portfolio_symbols_needing_price) == 1 and isinstance(data_now.columns, pd.Index):
                     sym = portfolio_symbols_needing_price[0]
                     try:
                         if 'Close' in data_now.columns and not data_now['Close'].empty:
                              local_current_prices[sym] = data_now['Close'].iloc[-1]
                     except: pass # Ignore price fetch error here
                 elif isinstance(data_now.columns, pd.MultiIndex):
                     for sym in portfolio_symbols_needing_price:
                         if sym in data_now.columns.levels[0]:
                             try:
                                 close_col = data_now[(sym, 'Close')]
                                 if not close_col.empty: local_current_prices[sym] = close_col.iloc[-1]
                             except: pass # Ignore price fetch error here
         total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
         local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}
    except Exception as e:
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        local_error = (local_error + " | Error calculating portfolio value." if local_error else
                       "Error calculating portfolio value.")
        local_portfolio_display = {'total_value': 'Error', 'cash': 'Error', 'holdings': []}
        if local_portfolio_state: local_portfolio_display['cash'] = local_portfolio_state.get('cash', 'Error')


    # --- Run Backtesting Example --- (Same logic as before)
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
    app_cache['all_stock_data'] = local_all_stock_data # For HTML table
    app_cache['portfolio_display'] = local_portfolio_display
    app_cache['backtest_results'] = local_backtest_results
    app_cache['trades_executed'] = local_trades_executed
    app_cache['last_update_time'] = datetime.now(timezone.utc)
    app_cache['processing_error'] = local_error
    # dataframe_summary was updated earlier if successful

    end_process_time = time.time()
    logging.info(f"--- Background Data Processing Finished ({end_process_time - start_process_time:.2f} seconds) ---")
    if local_error: logging.error(f"Processing finished with error: {local_error}")
    else: logging.info("Processing finished successfully.")
# --- End Background Data Processing Function ---


# --- Flask Route ---
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
        # Pass the combined data for the HTML table
        all_stock_data=app_cache.get('all_stock_data', []),
        paper_portfolio=app_cache.get('portfolio_display'),
        initial_capital=INITIAL_CASH,
        trades_executed=app_cache.get('trades_executed', []),
        backtest_results=app_cache.get('backtest_results'),
        last_updated=last_updated_str,
        error=display_error
    )

# --- Main Execution ---
if __name__ == '__main__':
    logging.info("Performing initial data load on startup...")
    process_all_data()
    logging.info("Initial data load complete. Starting web server (via Gunicorn usually)...")
    # Gunicorn runs based on Procfile in Render
    # port = int(os.environ.get('PORT', 8080))
    # app.run(host='0.0.0.0', port=port, debug=False) # For local testing only
