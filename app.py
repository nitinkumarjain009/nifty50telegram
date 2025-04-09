# app.py
import logging
import gc
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from flask import Flask, render_template

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(levelname)s - %(message)s')

# App constants
NIFTY_50_SYMBOLS = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "BSE.NS", 
    "TCS.NS", "ITC.NS", "KOTAKBANK.NS", "LT.NS", "HINDUNILVR.NS", 
    "SBIN.NS", "BHARTIARTL.NS", "BAJFINANCE.NS", "AXISBANK.NS", "ASIANPAINT.NS", 
    "MARUTI.NS", "HCLTECH.NS", "SUNPHARMA.NS", "TITAN.NS", "NTPC.NS", 
    "BAJAJFINSV.NS", "TATASTEEL.NS", "ULTRACEMCO.NS", "NESTLEIND.NS", "TATAMOTORS.NS", 
    "M&M.NS", "POWERGRID.NS", "TECHM.NS", "HDFCLIFE.NS", "ADANIPORTS.NS", 
    "ONGC.NS", "WIPRO.NS", "JSWSTEEL.NS", "BAJAJ-AUTO.NS", "GRASIM.NS", 
    "SBILIFE.NS", "HINDALCO.NS", "DIVISLAB.NS", "DRREDDY.NS", "COALINDIA.NS", 
    "INDUSINDBK.NS", "EICHERMOT.NS", "BPCL.NS", "TATACONSUM.NS", "CIPLA.NS", 
    "BRITANNIA.NS", "HEROMOTOCO.NS", "UPL.NS", "SHREECEM.NS", "IOC.NS"
]

DATA_FETCH_PERIOD = "6mo"
CACHE_DURATION_SECONDS = 3600  # 1 hour
INITIAL_CASH = 100000
BACKTEST_SYMBOL = "RELIANCE.NS"
BACKTEST_PERIOD = "1y"

# Flask app initialization
app = Flask(__name__)

# Cache for storing processed data
app_cache = {
    'all_stock_data': [],
    'portfolio_display': None,
    'dataframe_summary': None,
    'backtest_results': None,
    'trades_executed': [],
    'last_update_time': None,
    'processing_error': None
}

# --- Helper Functions ---
def fetch_stock_data(symbol, period="6mo"):
    """Fetch stock data for a given symbol using yfinance."""
    if not symbol or not isinstance(symbol, str):
        logging.warning(f"fetch_stock_data called with invalid symbol: {symbol}")
        return pd.DataFrame()
    try:
        logging.debug(f"Fetching {period} data for symbol: {symbol}...")
        start_time = time.time()
        data = yf.download(symbol, period=period, auto_adjust=True, progress=False)
        end_time = time.time()
        logging.debug(f"Data fetch for {symbol} completed in {end_time - start_time:.2f} seconds.")
        
        # Additional debug logging to understand structure
        if not data.empty:
            logging.debug(f"Downloaded data for {symbol} - Column structure: {type(data.columns)}")
            if isinstance(data.columns, pd.MultiIndex):
                logging.debug(f"MultiIndex levels: {data.columns.levels}")
        
        if data.empty:
            logging.warning(f"No data returned by yfinance for symbol: {symbol}")
            return pd.DataFrame()
        return data
    except Exception as e:
        logging.error(f"Error during yfinance download for {symbol}: {e}", exc_info=True)
        return pd.DataFrame()

def calculate_all_indicators(df):
    """Calculate technical indicators for the given DataFrame."""
    if df.empty or 'Close' not in df.columns:
        return pd.DataFrame()
    
    # Create a copy to avoid modifying the original
    result = df.copy()
    
    # Simple Moving Averages
    result['SMA20'] = result['Close'].rolling(window=20).mean()
    result['SMA50'] = result['Close'].rolling(window=50).mean()
    
    # Exponential Moving Averages
    result['EMA12'] = result['Close'].ewm(span=12, adjust=False).mean()
    result['EMA26'] = result['Close'].ewm(span=26, adjust=False).mean()
    
    # MACD
    result['MACD'] = result['EMA12'] - result['EMA26']
    result['Signal_Line'] = result['MACD'].ewm(span=9, adjust=False).mean()
    
    # RSI (14-period)
    delta = result['Close'].diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    result['RSI'] = 100 - (100 / (1 + rs))
    
    return result

def generate_recommendations(symbol, df_with_indicators):
    """Generate trading recommendations based on indicators."""
    if df_with_indicators.empty or len(df_with_indicators) < 30:
        return None
    
    # Get the latest values
    latest = df_with_indicators.iloc[-1]
    prev = df_with_indicators.iloc[-2]
    
    close_price = latest['Close']
    signal = "HOLD"  # Default
    target_price = None
    reason = ""
    
    # MACD Crossover
    macd_current = latest['MACD']
    signal_current = latest['Signal_Line']
    macd_prev = prev['MACD']
    signal_prev = prev['Signal_Line']
    
    # RSI Conditions
    rsi = latest['RSI']
    
    # Simple strategy based on MACD crossover and RSI confirmation
    if macd_prev < signal_prev and macd_current > signal_current:
        if rsi < 70:  # Not overbought
            signal = "BUY"
            target_price = close_price * 1.05  # 5% target
            reason = "MACD bullish crossover with RSI confirmation"
    elif macd_prev > signal_prev and macd_current < signal_current:
        if rsi > 30:  # Not oversold
            signal = "SELL"
            target_price = close_price * 0.95  # 5% target
            reason = "MACD bearish crossover with RSI confirmation"
    
    return {
        'symbol': symbol,
        'signal': signal,
        'price': close_price,
        'target': target_price,
        'reason': reason,
        'timestamp': datetime.now(timezone.utc)
    }

def load_portfolio():
    """Load the paper trading portfolio state."""
    try:
        # In a real app, this would load from a database or file
        # For this example, we'll use a default portfolio
        return {'cash': INITIAL_CASH, 'holdings': {}}
    except Exception as e:
        logging.error(f"Error loading portfolio: {e}", exc_info=True)
        return {'cash': INITIAL_CASH, 'holdings': {}}

def save_portfolio(portfolio_state):
    """Save the paper trading portfolio state."""
    try:
        # In a real app, this would save to a database or file
        # For this example, we'll just log it
        logging.info(f"Portfolio saved: Cash={portfolio_state['cash']}, Holdings={portfolio_state['holdings']}")
        return True
    except Exception as e:
        logging.error(f"Error saving portfolio: {e}", exc_info=True)
        return False

def update_paper_portfolio(recommendations, current_prices):
    """Update the paper trading portfolio based on recommendations."""
    portfolio = load_portfolio()
    trades_executed = []
    
    for rec in recommendations:
        symbol = rec['symbol']
        signal = rec['signal']
        price = current_prices.get(symbol, rec.get('price'))
        
        if not price:
            continue
            
        if signal == 'BUY' and portfolio['cash'] >= price * 10:
            # Buy 10 shares if we have enough cash
            shares_to_buy = int(min(10, portfolio['cash'] / price))
            cost = shares_to_buy * price
            
            # Update portfolio
            if symbol in portfolio['holdings']:
                portfolio['holdings'][symbol]['shares'] += shares_to_buy
                portfolio['holdings'][symbol]['avg_price'] = (
                    (portfolio['holdings'][symbol]['avg_price'] * (portfolio['holdings'][symbol]['shares'] - shares_to_buy) +
                     cost) / portfolio['holdings'][symbol]['shares']
                )
            else:
                portfolio['holdings'][symbol] = {
                    'shares': shares_to_buy,
                    'avg_price': price
                }
            
            portfolio['cash'] -= cost
            
            # Record the trade
            trades_executed.append({
                'symbol': symbol,
                'action': 'BUY',
                'shares': shares_to_buy,
                'price': price,
                'total': cost,
                'timestamp': datetime.now(timezone.utc)
            })
            
        elif signal == 'SELL' and symbol in portfolio['holdings'] and portfolio['holdings'][symbol]['shares'] > 0:
            # Sell all shares
            shares_to_sell = portfolio['holdings'][symbol]['shares']
            proceeds = shares_to_sell * price
            
            # Update portfolio
            portfolio['cash'] += proceeds
            portfolio['holdings'][symbol]['shares'] = 0
            
            # Record the trade
            trades_executed.append({
                'symbol': symbol,
                'action': 'SELL',
                'shares': shares_to_sell,
                'price': price,
                'total': proceeds,
                'timestamp': datetime.now(timezone.utc)
            })
    
    # Save the updated portfolio
    save_portfolio(portfolio)
    
    return portfolio, trades_executed

def get_portfolio_value(portfolio, current_prices):
    """Calculate the current value of the portfolio."""
    holdings_value = 0
    holdings_details = []
    
    for symbol, holding in portfolio['holdings'].items():
        if holding['shares'] > 0:
            current_price = current_prices.get(symbol, holding['avg_price'])
            value = holding['shares'] * current_price
            cost_basis = holding['shares'] * holding['avg_price']
            pnl = value - cost_basis
            pnl_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
            
            holdings_value += value
            holdings_details.append({
                'symbol': symbol,
                'shares': holding['shares'],
                'avg_price': holding['avg_price'],
                'current_price': current_price,
                'value': value,
                'pnl': pnl,
                'pnl_pct': pnl_pct
            })
    
    total_value = portfolio['cash'] + holdings_value
    
    return total_value, portfolio['cash'], holdings_details

# In the run_backtest function, replace the problematic code with this:

def run_backtest(symbol, backtest_data, initial_capital=100000):
    """Run a simple backtest on historical data."""
    if backtest_data.empty:
        return {"error": "No backtest data available"}
    
    # Ensure we have a dataframe with the right columns
    if isinstance(backtest_data.columns, pd.MultiIndex):
        if 'Close' in backtest_data.columns.get_level_values(0):
            # Handle MultiIndex DataFrame properly
            close_data = backtest_data['Close']
            
            # Check if it's a Series (single-level) or DataFrame (multi-level)
            if isinstance(close_data, pd.Series):
                df = close_data.to_frame(name='Close')
            else:
                # It's already a DataFrame, rename column
                df = close_data.copy()
                df.columns = ['Close']
        else:
            return {"error": "Required 'Close' column not found in backtest data"}
    elif 'Close' in backtest_data.columns:
        df = backtest_data[['Close']].copy()
    else:
        return {"error": "Required 'Close' column not found in backtest data"}
    
    # Calculate indicators
    df = calculate_all_indicators(df)
    
    # Initialize variables
    cash = initial_capital
    shares = 0
    trades = []
    
    # Loop through the data (starting from where indicators are available)
    for i in range(50, len(df)):
        date = df.index[i]
        close = df['Close'].iloc[i]
        
        # MACD crossover
        macd_current = df['MACD'].iloc[i]
        signal_current = df['Signal_Line'].iloc[i]
        macd_prev = df['MACD'].iloc[i-1]
        signal_prev = df['Signal_Line'].iloc[i-1]
        
        # Current RSI
        rsi = df['RSI'].iloc[i]
        
        # Buy signal
        if macd_prev < signal_prev and macd_current > signal_current and rsi < 70:
            if cash > 0:
                shares_to_buy = int(cash / close)
                if shares_to_buy > 0:
                    cost = shares_to_buy * close
                    cash -= cost
                    shares += shares_to_buy
                    trades.append({
                        'date': date,
                        'action': 'BUY',
                        'price': close,
                        'shares': shares_to_buy,
                        'value': cost
                    })
        
        # Sell signal
        elif macd_prev > signal_prev and macd_current < signal_current and rsi > 30:
            if shares > 0:
                proceeds = shares * close
                cash += proceeds
                trades.append({
                    'date': date,
                    'action': 'SELL',
                    'price': close,
                    'shares': shares,
                    'value': proceeds
                })
                shares = 0
    
    # Final portfolio value
    final_value = cash + (shares * df['Close'].iloc[-1])
    
    # Performance metrics
    start_date = df.index[50]
    end_date = df.index[-1]
    buy_and_hold_return = (df['Close'].iloc[-1] / df['Close'].iloc[50]) - 1
    strategy_return = (final_value / initial_capital) - 1
    
    return {
        'symbol': symbol,
        'start_date': start_date,
        'end_date': end_date,
        'initial_capital': initial_capital,
        'final_value': final_value,
        'return': strategy_return * 100,  # Convert to percentage
        'buy_and_hold_return': buy_and_hold_return * 100,  # Convert to percentage
        'trades': trades
    }

# Now, let's update the app to scan every 10 minutes and send Telegram notifications

# Add these imports at the top
import requests
from apscheduler.schedulers.background import BackgroundScheduler
import threading
import pytz

# Add these constants
TELEGRAM_BOT_TOKEN = "8017759392:AAEwM-W-y83lLXTjlPl8sC_aBmizuIrFXnU
"  # Replace with your actual bot token
TELEGRAM_CHAT_ID = "711856868"  # Replace with your channel or chat ID
TELEGRAM_GROUP_CHANNEL = "@Stockniftybot"

MARKET_OPEN_HOUR = 9  # 9 AM
MARKET_CLOSE_HOUR = 15  # 3 PM
SCAN_INTERVAL_MINUTES = 10
INDIA_TIMEZONE = pytz.timezone('Asia/Kolkata')

# Update the function to send Telegram notifications
def send_telegram_message(message):
    """Send a text message to Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            logging.info(f"Telegram message sent successfully")
            return True
        else:
            logging.error(f"Failed to send Telegram message: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Error sending Telegram message: {e}", exc_info=True)
        return False

def notify_recommendations_photo(df_summary):
    """Send a Telegram notification with the recommendations."""
    try:
        # Filter only BUY signals
        buy_signals = df_summary[df_summary['Signal'] == 'BUY']
        
        if buy_signals.empty:
            logging.info("No BUY signals to send")
            return True
        
        # Format message for Telegram
        message = "<b>🔔 BUY SIGNALS 🔔</b>\n\n"
        for _, row in buy_signals.iterrows():
            message += f"<b>{row['Symbol']}</b>\n"
            message += f"Current Price: ₹{row['CMP']}\n"
            message += f"Target: ₹{row['Target']}\n"
            message += f"Change: {row['% Change']}\n"
            message += "---------------\n"
        
        # Add timestamp
        now = datetime.now(INDIA_TIMEZONE)
        message += f"\n<i>Generated at {now.strftime('%Y-%m-%d %H:%M:%S')}</i>"
        
        # Send message
        send_telegram_message(message)
        logging.info(f"Telegram notification sent with {len(buy_signals)} buy recommendations")
        return True
    except Exception as e:
        logging.error(f"Error sending Telegram notification: {e}", exc_info=True)
        return False

# Function to check if market is open
def is_market_open():
    """Check if the market is currently open."""
    now = datetime.now(INDIA_TIMEZONE)
    
    # Check if it's a weekday (Monday = 0, Sunday = 6)
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    
    # Check if it's within market hours (9:15 AM to 3:30 PM)
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return market_start <= now <= market_end

# This is the new scheduled task function
def scheduled_data_update():
    """Function to be called by the scheduler."""
    try:
        if is_market_open():
            logging.info("Scheduled update: Market is open, processing data...")
            process_all_data()
        else:
            logging.info("Scheduled update: Market is closed, skipping data processing.")
    except Exception as e:
        logging.error(f"Error in scheduled data update: {e}", exc_info=True)

# Initialize the scheduler and background thread
scheduler = BackgroundScheduler()
scheduler.add_job(
    scheduled_data_update, 
    'interval', 
    minutes=SCAN_INTERVAL_MINUTES,
    id='market_data_update'
)

# Update the main execution part
if __name__ == '__main__':
    logging.info("Performing initial data load on startup...")
    process_all_data()
    
    # Start the scheduler
    scheduler.start()
    logging.info(f"Scheduler started. Running every {SCAN_INTERVAL_MINUTES} minutes during market hours.")
    
    try:
        # Run the Flask app
        logging.info("Initial data load complete. Web server starting...")
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))
    except (KeyboardInterrupt, SystemExit):
        # Shut down the scheduler when exiting
        scheduler.shutdown()
        logging.info("Application shutting down...")

def notify_recommendations_photo(df_summary):
    """Send a Telegram notification with the recommendations."""
    try:
        # In a real app, this would send to Telegram
        # For this example, we'll just log it
        logging.info(f"Telegram notification sent with {len(df_summary)} recommendations")
        return True
    except Exception as e:
        logging.error(f"Error sending Telegram notification: {e}", exc_info=True)
        return False

# --- Background Data Processing Function ---
def process_all_data():
    """Fetches data, calculates all required values, updates portfolio, runs backtest."""
    global app_cache
    logging.info("--- Starting Background Data Processing ---")
    start_process_time = time.time()

    # Initialize local variables
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
                    if symbol_data.empty: 
                        logging.warning(f"Skipping {repr(symbol)}: No data fetched.")
                    else: 
                        logging.warning(f"Skipping {repr(symbol)}: Insufficient data rows fetched ({len(symbol_data)}).")
                    continue

                # *** IMPROVED 'Close' COLUMN HANDLING ***
                logging.debug(f"Data for {symbol} - columns type: {type(symbol_data.columns)}")
                logging.debug(f"Data for {symbol} - columns: {symbol_data.columns}")
                
                # Check if we have MultiIndex columns (which happens even with single symbols sometimes)
                if isinstance(symbol_data.columns, pd.MultiIndex):
                    logging.debug(f"MultiIndex detected for {symbol}. Levels: {symbol_data.columns}")
                    
                    # MultiIndex format: first level is usually the data type (Open, High, Low, Close, etc.)
                    if 'Close' in symbol_data.columns.get_level_values(0):
                        # Handle both cases - when it's a Series or DataFrame
                        close_data = symbol_data['Close']
                        
                        # Convert to DataFrame with 'Close' column if it's a Series
                        if isinstance(close_data, pd.Series):
                            df_symbol = close_data.to_frame(name='Close')
                        else:
                            # It's already a DataFrame, ensure column is named 'Close'
                            df_symbol = close_data.copy()
                            df_symbol.columns = ['Close']
                        
                        logging.debug(f"Successfully extracted Close data for {symbol}, shape: {df_symbol.shape}")
                    else:
                        logging.warning(f"'Close' not found in column levels for {symbol}. Available levels: {symbol_data.columns.get_level_values(0).unique()}")
                        continue
                
                # Standard DataFrame with direct columns
                elif 'Close' in symbol_data.columns:
                    df_symbol = symbol_data[['Close']].copy()
                    logging.debug(f"Direct Close column found for {symbol}")
                else:
                    logging.warning(f"No 'Close' column found for {symbol}. Available columns: {symbol_data.columns}")
                    continue
                
                # Drop NaNs from the prepared 'Close' column
                df_symbol = df_symbol.dropna(subset=['Close'])
                
                # Check if we have enough data after cleanup
                if df_symbol.empty or len(df_symbol) < 2:
                    if df_symbol.empty: 
                        logging.warning(f"Skipping {repr(symbol)}: DataFrame empty after dropna for 'Close'.")
                    else: 
                        logging.warning(f"Skipping {repr(symbol)}: Insufficient valid 'Close' data ({len(df_symbol)} rows) after dropna.")
                    continue

                # --- Indicator Calculation ---
                df_with_indicators = calculate_all_indicators(df_symbol)
                
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
                signal = recommendation_result.get('signal', 'HOLD') if recommendation_result else "HOLD"
                target = recommendation_result.get('target') if recommendation_result else None
                
                if recommendation_result and signal in ['BUY', 'SELL']:
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

    # --- Step 3: Prepare Data for Telegram ---
    if local_all_stock_data:
        try:
            dataframe_for_telegram = pd.DataFrame(local_all_stock_data)
            df_display = dataframe_for_telegram[['symbol', 'cmp', 'percent_change', 'signal', 'target']].copy()
            df_display.rename(columns={
                'symbol': 'Symbol', 
                'cmp': 'CMP', 
                'percent_change': '% Change', 
                'signal': 'Signal', 
                'target': 'Target'
            }, inplace=True)
            
            df_display['CMP'] = df_display['CMP'].map('{:,.2f}'.format)
            df_display['% Change'] = df_display['% Change'].map('{:,.2f}%'.format)
            df_display['Target'] = df_display['Target'].map(lambda x: '{:,.2f}'.format(x) if pd.notnull(x) else 'N/A')
            app_cache['dataframe_summary'] = df_display
        except Exception as df_err:
            logging.error(f"Error creating/formatting DataFrame for Telegram: {df_err}", exc_info=True)
            local_error = (local_error + " | Error preparing data for Telegram." if local_error else "Error preparing data for Telegram.")
            app_cache['dataframe_summary'] = None

    # --- Step 4: Update Paper Trading Portfolio ---
    if local_recommendations_for_trade:
        valid_trade_recs = [rec for rec in local_recommendations_for_trade if rec['symbol'] in local_current_prices]
        if valid_trade_recs:
            try:
                local_portfolio_state, local_trades_executed = update_paper_portfolio(valid_trade_recs, local_current_prices)
            except Exception as trade_err:
                logging.error(f"Error updating paper portfolio: {trade_err}", exc_info=True)
                local_error = (local_error + " | Error during paper trading." if local_error else "Error during paper trading.")
                try: 
                    local_portfolio_state = load_portfolio()
                except: 
                    local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
        else:
            try: 
                local_portfolio_state = load_portfolio()
            except: 
                local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
    else:
        try: 
            local_portfolio_state = load_portfolio()
        except Exception as load_err:
            local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
            logging.error(f"Failed load portfolio: {load_err}")

    # --- Step 5: Send Telegram Notification PHOTO ---
    df_summary_to_send = app_cache.get('dataframe_summary')
    if df_summary_to_send is not None and not df_summary_to_send.empty:
        logging.info("Sending Telegram notification photo...")
        notify_recommendations_photo(df_summary_to_send)
    elif not local_all_stock_data: 
        logging.warning("Skipping Telegram photo: No stock data processed.")
    else: 
        logging.warning("Skipping Telegram photo: Summary DataFrame could not be generated.")

    # --- Step 6: Calculate Portfolio Display Value ---
    try:
        if local_portfolio_state is None:
            try: 
                local_portfolio_state = load_portfolio()
            except: 
                local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
        
        if local_current_prices is None: 
            local_current_prices = {}
            
        total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
        local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}
    except Exception as e:
        logging.error(f"Error calculating portfolio display value: {e}", exc_info=True)
        local_error = (local_error + " | Error calculating portfolio value." if local_error else "Error calculating portfolio value.")
        local_portfolio_display = {'total_value': 'Error', 'cash': 'Error', 'holdings': []}
        if local_portfolio_state: 
            local_portfolio_display['cash'] = local_portfolio_state.get('cash', 'Error')

    # --- Step 7: Run Backtesting Example ---
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

    # --- Step 8: Update Cache with Results ---
    app_cache['all_stock_data'] = local_all_stock_data
    app_cache['portfolio_display'] = local_portfolio_display
    app_cache['backtest_results'] = local_backtest_results
    app_cache['trades_executed'] = local_trades_executed
    app_cache['last_update_time'] = datetime.now(timezone.utc)
    app_cache['processing_error'] = local_error

    end_process_time = time.time()
    logging.info(f"--- Background Data Processing Finished ({end_process_time - start_process_time:.2f} seconds) ---")
    if local_error: 
        logging.error(f"Processing finished with error(s): {local_error}")
    else: 
        logging.info("Processing finished successfully.")

# --- Flask Route ---
@app.route('/')
def index():
    now = datetime.now(timezone.utc)
    cache_needs_update = False
    
    if app_cache['last_update_time'] is None: 
        cache_needs_update = True
        logging.info("Cache empty, processing.")
    else:
        time_since_update = now - app_cache['last_update_time']
        if time_since_update.total_seconds() > CACHE_DURATION_SECONDS: 
            cache_needs_update = True
            logging.info("Cache expired, processing.")
        else: 
            logging.info("Serving from cache.")
            
    if cache_needs_update:
        try: 
            process_all_data()
        except Exception as e:
            logging.error(f"Critical error calling process_all_data: {e}", exc_info=True)
            app_cache['processing_error'] = f"Failed update: {e}"
            if app_cache['last_update_time'] is None: 
                return render_template('index.html', error=f"Initial processing failed: {e}", last_updated="Never")
                
    last_updated_str = app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC') if app_cache['last_update_time'] else "Processing..."
    display_error = app_cache.get('processing_error')
    
    return render_template('index.html',
        all_stock_data=app_cache.get('all_stock_data', []), 
        paper_portfolio=app_cache.get('portfolio_display'),
        initial_capital=INITIAL_CASH, 
        trades_executed=app_cache.get('trades_executed', []),
        backtest_results=app_cache.get('backtest_results'), 
        last_updated=last_updated_str, 
        error=display_error)

# --- Main Execution ---
if __name__ == '__main__':
    logging.info("Performing initial data load on startup...")
    process_all_data()
    logging.info("Initial data load complete. Web server starting (via Gunicorn on Render)...")
