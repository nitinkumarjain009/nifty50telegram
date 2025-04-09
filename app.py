# Inside process_all_data function in app.py

    # --- Symbol Loop ---
    for symbol in symbols:
        symbol_data = pd.DataFrame()
        df_with_indicators = pd.DataFrame()
        logging.debug(f"--- Processing symbol: {repr(symbol)} ---")
        try:
            # Fetch data for the single symbol (pass as list)
            symbol_data = fetch_stock_data([symbol], period=DATA_FETCH_PERIOD)

            # Robust checks after fetching
            if symbol_data.empty:
                logging.warning(f"Skipping {repr(symbol)}: No data fetched.")
                continue
            if len(symbol_data) < 2:
                logging.warning(f"Skipping {repr(symbol)}: Insufficient data rows fetched ({len(symbol_data)}).")
                continue

            df_symbol = symbol_data.copy()

            # *** ADD CHECK FOR 'Close' COLUMN EXISTENCE ***
            if 'Close' not in df_symbol.columns:
                 logging.warning(f"Skipping {repr(symbol)}: 'Close' column MISSING in fetched data. Columns available: {df_symbol.columns.tolist()}")
                 continue # Skip this symbol if 'Close' column is not present

            # Now it's safe to use dropna with subset=['Close']
            df_symbol = df_symbol.dropna(subset=['Close'])

            # Proceed with subsequent checks and calculations...
            if df_symbol.empty:
                logging.warning(f"Skipping {repr(symbol)}: DataFrame empty after dropna for 'Close'.")
                continue
            if len(df_symbol) < 2:
                logging.warning(f"Skipping {repr(symbol)}: Insufficient valid 'Close' data ({len(df_symbol)} rows) after dropna.")
                continue

            # Calculate indicators
            df_with_indicators = calculate_all_indicators(df_symbol)
            # ... (rest of the checks and logic for indicators, prices, etc.) ...
            # ... (ensure similar checks before iloc access later) ...
            if df_with_indicators.empty or len(df_with_indicators) < 2 or 'Close' not in df_with_indicators.columns:
                 logging.warning(f"Skipping {repr(symbol)}: Indicator calculation failed or insufficient data.")
                 continue

            # Extract CMP and Previous Close safely
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

        except KeyError as ke: # Catch KeyError specifically if needed, though general Exception should cover it
             logging.error(f"KeyError processing {repr(symbol)}: {ke}", exc_info=True)
             local_error = f"Error processing {symbol} (see logs)."
        except IndexError as idx_err:
             logging.warning(f"IndexError processing {repr(symbol)} (likely price access): {idx_err}. Skipping symbol.")
        except Exception as e:
            logging.error(f"Unhandled error processing symbol {repr(symbol)}: {e}", exc_info=True)
            local_error = f"Error processing {symbol} (see logs)."

        finally:
            del symbol_data, df_with_indicators
            gc.collect()
    # --- End Symbol Loop ---
    # ... (rest of process_all_data) ...
