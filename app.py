# app.py
# ... (Keep all imports and constants, NIFTY_50_SYMBOLS list, cache definition, fetch_stock_data) ...

# --- Helper Functions --- (fetch_stock_data remains the same, simplified version)
def fetch_stock_data(symbol, period="6mo"):
    # ... (simplified fetch_stock_data as in previous version) ...
    if not symbol or not isinstance(symbol, str):
        logging.warning(f"fetch_stock_data called with invalid symbol: {symbol}")
        return pd.DataFrame()
    try:
        logging.debug(f"Fetching {period} data for symbol: {symbol}...")
        start_time = time.time()
        data = yf.download(symbol, period=period, auto_adjust=True, progress=False)
        end_time = time.time()
        logging.debug(f"Data fetch for {symbol} completed in {end_time - start_time:.2f} seconds.")
        if data.empty:
            logging.warning(f"No data returned by yfinance for symbol: {symbol}")
            return pd.DataFrame()
        return data
    except Exception as e:
        logging.error(f"Error during yfinance download for {symbol}: {e}", exc_info=True)
        return pd.DataFrame()

# --- Background Data Processing Function ---
def process_all_data():
    """Fetches data, calculates all required values, updates portfolio, runs backtest."""
    global app_cache
    logging.info("--- Starting Background Data Processing ---")
    start_process_time = time.time()

    # ... (Initialization of local variables as before) ...
    local_all_stock_data = []
    local_recommendations_for_trade = []
    local_current_prices = {}
    local_trades_executed = []
    local_portfolio_state = None
    local_backtest_results = None
    local_error = None
    dataframe_for_telegram = pd.DataFrame()

    # --- Use Hardcoded Symbol List ---
    symbols = NIFTY_50_SYMBOLS
    logging.info(f"Using hardcoded list of {len(symbols)} Nifty 50 symbols.")

    if not symbols:
        local_error = "Symbol list is empty. Cannot process."
        # ... (error handling and loading portfolio state) ...
    else:
        logging.info(f"Processing {len(symbols)} symbols sequentially...")
        # --- Symbol Loop ---
        for symbol in symbols:
            symbol_data = pd.DataFrame()
            df_with_indicators = pd.DataFrame()
            logging.debug(f"--- Processing symbol: {repr(symbol)} ---")
            try:
                symbol_data = fetch_stock_data(symbol, period=DATA_FETCH_PERIOD)

                # --- Data Validation Checks ---
                if symbol_data.empty or len(symbol_data) < 2:
                    # Log and skip if insufficient data
                    if symbol_data.empty: logging.warning(f"Skipping {repr(symbol)}: No data fetched.")
                    else: logging.warning(f"Skipping {repr(symbol)}: Insufficient data rows fetched ({len(symbol_data)}).")
                    continue

                # *** REVISED 'Close' COLUMN HANDLING ***
                close_col_found = False
                df_symbol = None # Initialize df_symbol here

                try:
                    if isinstance(symbol_data.columns, pd.MultiIndex):
                        logging.warning(f"Received MultiIndex for single symbol {repr(symbol)}. Columns: {symbol_data.columns}. Attempting extraction.")
                        # Check if 'Close' exists at the top level
                        if 'Close' in symbol_data.columns.get_level_values(0):
                            # Attempt direct selection, which often works for top-level access
                            df_extracted = symbol_data['Close'].copy()
                            logging.debug(f"After direct selection ['Close'] for {repr(symbol)}, type: {type(df_extracted)}")

                            # Check if the result is a Series (expected if single ticker)
                            if isinstance(df_extracted, pd.Series):
                                df_symbol = df_extracted.to_frame(name='Close')
                                logging.debug(f"Converted Series to DataFrame for {repr(symbol)}. Columns: {df_symbol.columns}")
                            elif isinstance(df_extracted, pd.DataFrame):
                                # If it's already a DataFrame, check if it needs renaming
                                if len(df_extracted.columns) == 1:
                                     # If single column, rename it to 'Close' just in case
                                     df_extracted.columns = ['Close']
                                     df_symbol = df_extracted
                                     logging.debug(f"Used extracted DataFrame, ensured 'Close' column name for {repr(symbol)}.")
                                else:
                                     # If multiple columns remain after selecting 'Close', something is wrong
                                     logging.error(f"Unexpected DataFrame structure after selecting 'Close' from MultiIndex for {repr(symbol)}. Columns: {df_extracted.columns}")
                                     continue # Skip symbol

                            else:
                                logging.error(f"Unexpected type after selecting 'Close' from MultiIndex for {repr(symbol)}: {type(df_extracted)}")
                                continue # Skip symbol

                            # Final check if df_symbol is now valid with a 'Close' column
                            if df_symbol is not None and 'Close' in df_symbol.columns:
                                close_col_found = True
                                logging.debug(f"'Close' column successfully prepared from MultiIndex for {repr(symbol)}.")
                            else:
                                logging.error(f"FAILED to prepare 'Close' column from MultiIndex for {repr(symbol)} after extraction/conversion.")
                                continue # Skip symbol

                        else: # 'Close' not found in top level of MultiIndex
                             logging.warning(f"Skipping {repr(symbol)}: 'Close' column MISSING in fetched MultiIndex data top level. Levels: {symbol_data.columns.levels}")
                             continue # Skip symbol

                    elif 'Close' in symbol_data.columns:
                        # Standard case: 'Close' is a direct column
                        df_symbol = symbol_data[['Close']].copy() # Select as DataFrame
                        close_col_found = True
                        logging.debug(f"Found 'Close' as direct column for {repr(symbol)}.")
                    else:
                         # 'Close' column is missing entirely
                         logging.warning(f"Skipping {repr(symbol)}: 'Close' column MISSING in fetched data. Columns available: {symbol_data.columns.tolist()}")
                         continue # Skip symbol

                except Exception as extraction_err:
                    logging.error(f"Error during 'Close' column extraction/preparation for {repr(symbol)}: {extraction_err}", exc_info=True)
                    continue # Skip symbol on extraction error

                # --- Proceed only if 'Close' was prepared successfully ---
                if not close_col_found or df_symbol is None:
                    logging.error(f"Skipping {repr(symbol)} because 'Close' column preparation failed.")
                    continue

                # --- Drop NaNs from the prepared 'Close' column ---
                df_symbol = df_symbol.dropna(subset=['Close']) # Now this should be safe

                # Check again after dropping NaNs
                if df_symbol.empty or len(df_symbol) < 2:
                     if df_symbol.empty: logging.warning(f"Skipping {repr(symbol)}: DataFrame empty after dropna for 'Close'.")
                     else: logging.warning(f"Skipping {repr(symbol)}: Insufficient valid 'Close' data ({len(df_symbol)} rows) after dropna.")
                     continue

                # --- Indicator Calculation --- (Rest of loop remains the same)
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
    # ... (Create and format df_display, store in app_cache['dataframe_summary']) ...
    if local_all_stock_data:
        try:
            dataframe_for_telegram = pd.DataFrame(local_all_stock_data)
            df_display = dataframe_for_telegram[['symbol', 'cmp', 'percent_change', 'signal', 'target']].copy()
            df_display.rename(columns={'symbol': 'Symbol', 'cmp': 'CMP', 'percent_change': '% Change', 'signal': 'Signal', 'target': 'Target'}, inplace=True)
            df_display['CMP'] = df_display['CMP'].map('{:,.2f}'.format)
            df_display['% Change'] = df_display['% Change'].map('{:,.2f}%'.format)
            df_display['Target'] = df_display['Target'].map(lambda x: '{:,.2f}'.format(x) if pd.notnull(x) else 'N/A')
            app_cache['dataframe_summary'] = df_display
        except Exception as df_err:
            logging.error(f"Error creating/formatting DataFrame for Telegram: {df_err}", exc_info=True)
            local_error = (local_error + " | Error preparing data for Telegram." if local_error else "Error preparing data for Telegram.")
            app_cache['dataframe_summary'] = None


    # --- Step 4: Update Paper Trading Portfolio --- (Same as before)
    # ... (Load state, update based on local_recommendations_for_trade) ...
    if local_recommendations_for_trade:
        valid_trade_recs = [rec for rec in local_recommendations_for_trade if rec['symbol'] in local_current_prices]
        if valid_trade_recs:
             try:
                 local_portfolio_state, local_trades_executed = update_paper_portfolio(valid_trade_recs, local_current_prices)
             except Exception as trade_err:
                  logging.error(f"Error updating paper portfolio: {trade_err}", exc_info=True); local_error = (local_error + " | Error during paper trading." if local_error else "Error during paper trading.")
                  try: local_portfolio_state = load_portfolio()
                  except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
        else:
             try: local_portfolio_state = load_portfolio()
             except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
    else:
        try: local_portfolio_state = load_portfolio()
        except Exception as load_err:
             local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}; logging.error(f"Failed load portfolio: {load_err}")


    # --- Step 5: Send Telegram Notification PHOTO --- (Same as before)
    # ... (Check cache['dataframe_summary'] and call notify_recommendations_photo) ...
    df_summary_to_send = app_cache.get('dataframe_summary')
    if df_summary_to_send is not None and not df_summary_to_send.empty:
        logging.info("Sending Telegram notification photo...")
        notify_recommendations_photo(df_summary_to_send)
    elif not local_all_stock_data: logging.warning("Skipping Telegram photo: No stock data processed.")
    else: logging.warning("Skipping Telegram photo: Summary DataFrame could not be generated.")


    # --- Step 6: Calculate Portfolio Display Value --- (Same as before)
    # ... (Load state if needed, fetch missing prices, call get_portfolio_value) ...
    try:
        if local_portfolio_state is None:
             try: local_portfolio_state = load_portfolio()
             except: local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
        if local_current_prices is None: local_current_prices = {}
        # NOTE: Fetching missing portfolio prices logic needs review if fetch_stock_data only handles single symbols now.
        # Consider only using prices obtained during the main loop for simplicity on free tier.
        total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
        local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}
    except Exception as e:
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        local_error = (local_error + " | Error calculating portfolio value." if local_error else "Error calculating portfolio value.")
        local_portfolio_display = {'total_value': 'Error', 'cash': 'Error', 'holdings': []}
        if local_portfolio_state: local_portfolio_display['cash'] = local_portfolio_state.get('cash', 'Error')


    # --- Step 7: Run Backtesting Example --- (Same as before)
    # ... (Fetch data for BACKTEST_SYMBOL, call run_backtest) ...
    logging.info(f"Running backtest for {BACKTEST_SYMBOL}...")
    try:
        backtest_data = fetch_stock_data(BACKTEST_SYMBOL, period=BACKTEST_PERIOD)
        if not backtest_data.empty:
             local_backtest_results = run_backtest(BACKTEST_SYMBOL, backtest_data.copy(), initial_capital=INITIAL_CASH)
        else:
             local_backtest_results = {"error": f"Could not fetch data for {BACKTEST_SYMBOL}."}
    except Exception as e:
        logging.error(f"Error running backtest for {BACKTEST_SYMBOL}: {e}", exc_info=True)
        local_backtest_results = {"error": f"An error occurred during backtesting: {e}"}


    # --- Step 8: Update Cache with Results --- (Same as before)
    # ... (Assign local results to app_cache keys) ...
    app_cache['all_stock_data'] = local_all_stock_data
    app_cache['portfolio_display'] = local_portfolio_display
    app_cache['backtest_results'] = local_backtest_results
    app_cache['trades_executed'] = local_trades_executed
    app_cache['last_update_time'] = datetime.now(timezone.utc)
    app_cache['processing_error'] = local_error

    end_process_time = time.time()
    logging.info(f"--- Background Data Processing Finished ({end_process_time - start_process_time:.2f} seconds) ---")
    if local_error: logging.error(f"Processing finished with error(s): {local_error}")
    else: logging.info("Processing finished successfully.")
# --- End Background Data Processing Function ---


# --- Flask Route --- (Same as before)
# ... (Checks cache, calls process_all_data if needed, renders template) ...
@app.route('/')
def index():
    now = datetime.now(timezone.utc)
    cache_needs_update = False
    if app_cache['last_update_time'] is None: cache_needs_update = True; logging.info("Cache empty, processing.")
    else:
        time_since_update = now - app_cache['last_update_time']
        if time_since_update.total_seconds() > CACHE_DURATION_SECONDS: cache_needs_update = True; logging.info("Cache expired, processing.")
        else: logging.info("Serving from cache.")
    if cache_needs_update:
        try: process_all_data()
        except Exception as e:
             logging.error(f"Critical error calling process_all_data: {e}", exc_info=True); app_cache['processing_error'] = f"Failed update: {e}"
             if app_cache['last_update_time'] is None: return render_template('index.html', error=f"Initial processing failed: {e}", last_updated="Never")
    last_updated_str = app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC') if app_cache['last_update_time'] else "Processing..."
    display_error = app_cache.get('processing_error')
    return render_template('index.html',
        all_stock_data=app_cache.get('all_stock_data', []), paper_portfolio=app_cache.get('portfolio_display'),
        initial_capital=INITIAL_CASH, trades_executed=app_cache.get('trades_executed', []),
        backtest_results=app_cache.get('backtest_results'), last_updated=last_updated_str, error=display_error)

# --- Main Execution --- (Same as before)
# ... (Calls process_all_data on startup) ...
if __name__ == '__main__':
    logging.info("Performing initial data load on startup...")
    process_all_data()
    logging.info("Initial data load complete. Web server starting (via Gunicorn on Render)...")
