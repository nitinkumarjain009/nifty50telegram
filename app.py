# app.py
from flask import Flask, render_template
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta, timezone # Added timezone
import os
import logging
import time
import gc

# Import local modules... (same as before)
from indicators import calculate_all_indicators
from trading_logic import (
    generate_recommendations, update_paper_portfolio, get_portfolio_value,
    run_backtest, load_portfolio, INITIAL_CASH
)
from telegram_sender import notify_recommendations

# Configure logging... (same as before)

app = Flask(__name__)

# --- Constants --- (same as before)
STOCK_LIST_FILE = 'nifty50_stocks.csv'
DATA_FETCH_PERIOD = "6mo"
BACKTEST_SYMBOL = "RELIANCE.NS"
BACKTEST_PERIOD = "6mo"
CACHE_DURATION_SECONDS = 3600 # Cache results for 1 hour (3600 seconds)

# --- Simple In-Memory Cache ---
# Warning: This cache is lost if the application restarts.
# For persistence across restarts on Render, you'd need an external cache
# like Redis or store results in a database/disk (disk not ideal on free tier).
app_cache = {
    "last_update_time": None,
    "recommendations": [],
    "portfolio_display": None,
    "backtest_results": None,
    "trades_executed": [],
    "processing_error": None # Store any error during processing
}
# -----------------------------

# --- Helper Functions (get_stock_symbols, fetch_stock_data) ---
# ... (Keep your existing helper functions) ...

# --- Background Data Processing Function ---
def process_all_data():
    """Fetches data, calculates indicators/recommendations, updates portfolio, runs backtest."""
    global app_cache # Allow modification of the global cache
    logging.info("--- Starting Background Data Processing ---")
    start_process_time = time.time()

    local_recommendations = []
    local_current_prices = {}
    local_trades_executed = []
    local_portfolio_state = None
    local_backtest_results = None
    local_error = None

    symbols = get_stock_symbols()
    if not symbols:
        local_error = f"Could not load stock symbols from {STOCK_LIST_FILE} or file is empty."
        logging.error(local_error)
        paper_portfolio_state = load_portfolio() # Load portfolio even if symbols fail
    else:
        logging.info(f"Processing {len(symbols)} symbols sequentially...")
        # --- Symbol Loop (Copied from original index route) ---
        for symbol in symbols:
            symbol_data = pd.DataFrame()
            df_with_indicators = pd.DataFrame()
            try:
                logging.info(f"--- Processing symbol: {symbol} ---")
                symbol_data = fetch_stock_data([symbol], period=DATA_FETCH_PERIOD)
                if symbol_data.empty: continue # Skip if no data

                df_symbol = symbol_data.copy()
                df_symbol = df_symbol.dropna(subset=['Close'])
                if df_symbol.empty: continue # Skip if no valid data

                df_with_indicators = calculate_all_indicators(df_symbol)
                if not df_with_indicators.empty and 'Close' in df_with_indicators.columns:
                    recommendation = generate_recommendations(symbol, df_with_indicators)
                    if recommendation:
                        local_recommendations.append(recommendation)
                    local_current_prices[symbol] = df_with_indicators['Close'].iloc[-1]
                # ... (Removed some inner logging for brevity during background run) ...
            except Exception as e:
                logging.error(f"Error processing symbol {symbol}: {e}", exc_info=True)
                local_error = "Error during symbol processing." # Set a general error flag
            finally:
                del symbol_data, df_with_indicators
                gc.collect()
        # --- End Symbol Loop ---

        # --- Paper Trading Update ---
        if local_recommendations:
            logging.info("Updating paper trading portfolio...")
            valid_recs = [rec for rec in local_recommendations if rec['symbol'] in local_current_prices]
            if valid_recs:
                 local_portfolio_state, local_trades_executed = update_paper_portfolio(valid_recs, local_current_prices)
                 # Send Telegram Notification ONLY when recommendations are generated/updated
                 logging.info("Sending Telegram notifications...")
                 notify_recommendations(local_recommendations)
            else:
                 local_portfolio_state = load_portfolio() # Load if no valid recs
        else:
            logging.info("No recommendations generated.")
            local_portfolio_state = load_portfolio() # Load if no recommendations

    # --- Get Portfolio Display Data ---
    local_portfolio_display = None
    try:
         if local_portfolio_state is None: local_portfolio_state = load_portfolio()
         # Fetch missing prices if needed (logic similar to original index route)
         portfolio_symbols_needing_price = [
             sym for sym in local_portfolio_state.get('holdings',{}).keys() if sym not in local_current_prices
         ]
         if portfolio_symbols_needing_price:
             data_now = fetch_stock_data(portfolio_symbols_needing_price, period="5d")
             if not data_now.empty:
                 # ... (Logic to extract prices from data_now - single/multi) ...
                 # (Simplified for brevity - copy logic from previous app.py if needed)
                 if len(portfolio_symbols_needing_price) == 1 and isinstance(data_now.columns, pd.Index):
                     sym = portfolio_symbols_needing_price[0]
                     try: local_current_prices[sym] = data_now['Close'].iloc[-1]
                     except: pass # Ignore price fetch error here
                 elif isinstance(data_now.columns, pd.MultiIndex):
                     for sym in portfolio_symbols_needing_price:
                         if sym in data_now.columns.levels[0]:
                             try: local_current_prices[sym] = data_now[(sym, 'Close')].iloc[-1]
                             except: pass # Ignore price fetch error here

         total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
         local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}
    except Exception as e:
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        local_error = (local_error + " | Error calculating portfolio value." if local_error else
                       "Error calculating portfolio value.")
        local_portfolio_display = {'total_value': 0, 'cash': 0, 'holdings': []}
        if local_portfolio_state: local_portfolio_display['cash'] = local_portfolio_state.get('cash', 0)

    # --- Run Backtesting Example ---
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
    app_cache['recommendations'] = local_recommendations
    app_cache['portfolio_display'] = local_portfolio_display
    app_cache['backtest_results'] = local_backtest_results
    app_cache['trades_executed'] = local_trades_executed # Store trades from THIS run
    app_cache['last_update_time'] = datetime.now(timezone.utc)
    app_cache['processing_error'] = local_error # Store any error encountered

    end_process_time = time.time()
    logging.info(f"--- Background Data Processing Finished ({end_process_time - start_process_time:.2f} seconds) ---")
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
            logging.info(f"Cache expired ({time_since_update.total_seconds():.0f}s > {CACHE_DURATION_SECONDS}s). Triggering data processing.")
            cache_needs_update = True
        else:
            logging.info("Serving data from cache.")

    if cache_needs_update:
        try:
            # *** Run the processing ***
            process_all_data()
            # Note: For a real web app, this should ideally run in a background thread
            #       or task queue (like Celery) so it doesn't block the request.
            #       But for Render free tier & simplicity, we run it directly here.
            #       This means the *first* request after cache expiry will be slow.
        except Exception as e:
             logging.error(f"Critical error during scheduled data processing: {e}", exc_info=True)
             app_cache['processing_error'] = f"Failed to update data: {e}"
             # Keep serving old cache data if available
             if app_cache['last_update_time'] is None:
                  # If initial processing fails, return error immediately
                   return render_template('index.html', error=app_cache['processing_error'], last_updated="Never")


    # --- Render Template using data from app_cache ---
    # Determine update time string safely
    last_updated_str = app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC') if app_cache['last_update_time'] else "Processing..."

    return render_template(
        'index.html',
        recommendations=app_cache['recommendations'],
        paper_portfolio=app_cache['portfolio_display'],
        initial_capital=INITIAL_CASH,
        trades_executed=app_cache['trades_executed'], # Show trades from the last successful update
        backtest_results=app_cache['backtest_results'],
        last_updated=last_updated_str,
        error=app_cache['processing_error'] # Display any processing error stored in cache
    )

# --- Main Execution ---
if __name__ == '__main__':
    # Perform an initial data load on startup *before* starting the web server
    # This helps ensure data is available immediately and might help with health checks.
    logging.info("Performing initial data load on startup...")
    process_all_data()
    logging.info("Initial data load complete. Starting web server...")

    port = int(os.environ.get('PORT', 8080))
    # Use Gunicorn in production via Procfile, app.run is mainly for local dev
    # However, Render uses the Procfile, so this __main__ block might not be
    # the primary entry point on Render *unless* Gunicorn fails completely.
    # For local testing: app.run(host='0.0.0.0', port=port, debug=False)
    # On Render, Gunicorn takes over based on Procfile.
