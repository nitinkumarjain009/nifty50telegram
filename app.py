#app.py
import logging
import gc
import time
import os
import pandas as pd
import yfinance as yf
import requests
from datetime import datetime, timezone
from flask import Flask, render_template
from apscheduler.schedulers.background import BackgroundScheduler
import pytz

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

# Telegram configuration
TELEGRAM_BOT_TOKEN = "8017759392:AAEwM-W-y83lLXTjlPl8sC_aBmizuIrFXnU"
TELEGRAM_CHAT_ID = "711856868"
TELEGRAM_GROUP_CHANNEL = "@Stockniftybot"

# Market timing configuration
MARKET_OPEN_HOUR = 9  # 9 AM
MARKET_CLOSE_HOUR = 15  # 3 PM
SCAN_INTERVAL_MINUTES = 10
INDIA_TIMEZONE = pytz.timezone('Asia/Kolkata')

# Flask app initialization
app = Flask(__name__)

# Cache for storing processed data
app_cache = {
    'all_stock_data': [],
    'portfolio_display': None,
    'dataframe_summary': None,
    'backtest_results': None,
    'trades_executed': [],
    'sent_buy_signals': set(),  # Track which buy signals have been sent
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

def extract_close_data(symbol_data, symbol):
    """Extract close price data from potentially complex DataFrame structures."""
    try:
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
                return df_symbol
            else:
                logging.warning(f"'Close' not found in column levels for {symbol}.")
                return pd.DataFrame()
        
        # Standard DataFrame with direct columns
        elif 'Close' in symbol_data.columns:
            df_symbol = symbol_data[['Close']].copy()
            logging.debug(f"Direct Close column found for {symbol}")
            return df_symbol
        else:
            logging.warning(f"No 'Close' column found for {symbol}. Available columns: {symbol_data.columns}")
            return pd.DataFrame()
    except Exception as e:
        logging.error(f"Error extracting Close data for {symbol}: {e}", exc_info=True)
        return pd.DataFrame()

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

def run_backtest(symbol, backtest_data, initial_capital=100000):
    """Run a simple backtest on historical data."""
    if backtest_data.empty:
        return {"error": "No backtest data available"}
    
    # Extract Close data
    df = extract_close_data(backtest_data, symbol)
    if df.empty:
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

def send_telegram_message(message):
    """Send a text message to Telegram."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            logging.info(f"Telegram message sent successfully")
            return True
        else:
            logging.error(f"Failed to send Telegram message: {response.text}")
            return False
    except Exception as e:
        logging.error(f"Error sending Telegram message: {e}", exc_info=True)
        return False

def send_buy_signal_notification(symbol, price, target, change_pct):
    """Send an immediate notification for a new BUY signal."""
    global app_cache
    
    # Generate a unique identifier for this buy signal
    signal_id = f"{symbol}_{price:.2f}_{datetime.now(INDIA_TIMEZONE).strftime('%Y%m%d')}"
    
    # Check if we've already sent this signal
    if signal_id in app_cache['sent_buy_signals']:
        logging.debug(f"Buy signal for {symbol} already sent today, skipping notification")
        return False
    
    # Format the message
    message = f"<b>🔔 NEW BUY SIGNAL 🔔</b>\n\n"
    message += f"<b>{symbol}</b>\n"
    message += f"Current Price: ₹{price:.2f}\n"
    message += f"Target: ₹{target:.2f}\n"
    message += f"Change: {change_pct:.2f}%\n"
    message += f"Reason: MACD bullish crossover with RSI confirmation\n"
    message += "\n<i>Trade with caution. This is an automated alert.</i>"
    
    # Send the message
    success = send_telegram_message(message)
    
    if success:
        # Mark this signal as sent
        app_cache['sent_buy_signals'].add(signal_id)
        logging.info(f"Sent immediate buy signal notification for {symbol}")
        return True
    
    return False

def notify_recommendations_summary(df_summary):
    """Send a Telegram notification with all current buy recommendations."""
    try:
        # Filter only BUY signals
        buy_signals = df_summary[df_summary['Signal'] == 'BUY']
        
        if buy_signals.empty:
            logging.info("No BUY signals to send in summary")
            return True
        
        # Format message for Telegram
        message = "<b>🔔 CURRENT BUY SIGNALS SUMMARY 🔔</b>\n\n"
        for _, row in buy_signals.iterrows():
            symbol = row['Symbol']
            price = row['CMP'].replace(',', '')  # Remove formatting
            try:
                price = float(price)
            except:
                price = "N/A"
            
            message += f"<b>{symbol}</b>\n"
            message += f"Current Price: {row['CMP']}\n"
            message += f"Target: {row['Target']}\n"
            message += f"Change: {row['% Change']}\n"
            message += "---------------\n"
        
        # Add timestamp
        now = datetime.now(INDIA_TIMEZONE)
        message += f"\n<i>Generated at {now.strftime('%Y-%m-%d %H:%M:%S')}</i>"
        
        # Send message
        send_telegram_message(message)
        logging.info(f"Telegram summary notification sent with {len(buy_signals)} buy recommendations")
        return True
    except Exception as e:
        logging.error(f"Error sending Telegram summary notification: {e}", exc_info=True)
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
    new_buy_signals = []

    # --- Process Symbols ---
    symbols = NIFTY_50_SYMBOLS
    logging.info(f"Processing {len(symbols)} Nifty 50 symbols.")

    if not symbols:
        local_error = "Symbol list is empty. Cannot process."
    else:
        # --- Symbol Loop ---
        for symbol in symbols:
            try:
                # Fetch data
                symbol_data = fetch_stock_data(symbol, period=DATA_FETCH_PERIOD)
                if symbol_data.empty or len(symbol_data) < 2:
                    logging.warning(f"Skipping {symbol}: Insufficient data.")
                    continue

                # Extract Close data
                df_symbol = extract_close_data(symbol_data, symbol)
                if df_symbol.empty or len(df_symbol) < 2:
                    logging.warning(f"Skipping {symbol}: Failed to extract Close data.")
                    continue
                
                # Drop NaNs
                df_symbol = df_symbol.dropna(subset=['Close'])
                if df_symbol.empty or len(df_symbol) < 2:
                    logging.warning(f"Skipping {symbol}: No valid price data after dropna.")
                    continue

                # Calculate indicators
                df_with_indicators = calculate_all_indicators(df_symbol)
                if df_with_indicators.empty:
                    logging.warning(f"Skipping {symbol}: Failed to calculate indicators.")
                    continue

                # Extract Prices & Calculate Change
                current_close = df_with_indicators['Close'].iloc[-1]
                prev_close = df_with_indicators['Close'].iloc[-2]
                local_current_prices[symbol] = current_close
                percent_change = ((current_close - prev_close) / prev_close) * 100 if prev_close else 0.0

                # Generate Trading Signal
                recommendation_result = generate_recommendations(symbol, df_with_indicators)
                signal = recommendation_result.get('signal', 'HOLD') if recommendation_result else "HOLD"
                target = recommendation_result.get('target') if recommendation_result else None
                
                # Track recommendations for trading and immediate notifications
                if recommendation_result and signal == 'BUY':
                    local_recommendations_for_trade.append(recommendation_result)
                    
                    # Check if this is a new buy signal to send immediate notification
                    signal_id = f"{symbol}_{current_close:.2f}_{datetime.now(INDIA_TIMEZONE).strftime('%Y%m%d')}"
                    if signal_id not in app_cache['sent_buy_signals']:
                        new_buy_signals.append({
                            'symbol': symbol,
                            'price': current_close,
                            'target': target,
                            'change_pct': percent_change
                        })
                
                # Store Combined Data
                stock_info = {
                    'symbol': symbol, 
                    'cmp': current_close, 
                    'percent_change': percent_change, 
                    'signal': signal, 
                    'target': target
                }
                local_all_stock_data.append(stock_info)

            except Exception as e:
                logging.error(f"Error processing {symbol}: {e}", exc_info=True)
                continue
            finally:
                # Cleanup
                gc.collect()
    
    # --- Send Immediate Notifications for New Buy Signals ---
    for signal in new_buy_signals:
        send_buy_signal_notification(
            signal['symbol'],
            signal['price'],
            signal['target'],
            signal['change_pct']
        )

    # --- Prepare DataFrame Summary ---
    if local_all_stock_data:
        try:
            dataframe_for_display = pd.DataFrame(local_all_stock_data)
            df_display = dataframe_for_display[['symbol', 'cmp', 'percent_change', 'signal', 'target']].copy()
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
            logging.error(f"Error creating DataFrame summary: {df_err}", exc_info=True)
            local_error = "Error preparing data summary."
            app_cache['dataframe_summary'] = None

    # --- Update Paper Trading Portfolio ---
    try:
        if local_recommendations_for_trade:
            local_portfolio_state, local_trades_executed = update_paper_portfolio(
                local_recommendations_for_trade, local_current_prices
            )
        else:
            local_portfolio_state = load_portfolio()
            
        # Calculate portfolio display value
        total_value, cash, holdings_details = get_portfolio_value(local_portfolio_state, local_current_prices)
        local_portfolio_display = {'total_value': total_value, 'cash': cash, 'holdings': holdings_details}
        
    except Exception as e:
        logging.error(f"Error updating portfolio: {e}", exc_info=True)
        local_error = (local_error + " | Portfolio update error." if local_error else "Portfolio update error.")
        local_portfolio_state = {'cash': INITIAL_CASH, 'holdings': {}}
        local_portfolio_display = {'total_value': INITIAL_CASH, 'cash': INITIAL_CASH, 'holdings': []}

    # --- Run Backtest ---
    try:
        backtest_data = fetch_stock_data(BACKTEST_SYMBOL, period=BACKTEST_PERIOD)
        if not backtest_data.empty:
            local_backtest_results = run_backtest(BACKTEST_SYMBOL, backtest_data.copy(), initial_capital=INITIAL_CASH)
        else:
            local_backtest_results = {"error": f"Could not fetch data for {BACKTEST_SYMBOL}."}
    except Exception as e:
        logging.error(f"Error running backtest: {e}", exc_info=True)
        local_backtest_results = {"error": f"Backtest error: {str(e)[:100]}"}

    # --- Update Cache ---
    app_cache['all_stock_data'] = local_all_stock_data
    app_cache['portfolio_display'] = local_portfolio_display
    app_cache['backtest_results'] = local_backtest_results
    app_cache['trades_executed'] = local_trades_executed
    app_cache['last_update_time'] = datetime.now(timezone.utc)
    app_cache['processing_error'] = local_error

    # Send periodic summary if enough time has passed
    notify_recommendations_summary(app_cache['dataframe_summary'])

    end_process_time = time.time()
    logging.info(f"--- Data Processing Finished in {end_process_time - start_process_time:.2f} seconds ---")

# --- Scheduled Task Function ---
def scheduled_data_update():
    """Function to be called by the scheduler."""
    try:
        if is_market_open():
            logging.info("Scheduled update: Market is open, processing data...")
            process_all_data()
        else:
            logging.info("Scheduled update: Market is closed, skipping processing.")
    except Exception as e:
        logging.error(f"Error in scheduled update: {e}", exc_info=True)

# --- Flask Route ---
@app.route('/')
def index():
    now = datetime.now(timezone.utc)
    cache_needs_update = False
    
    if app_cache['last_update_time'] is None: 
        cache_needs_update = True
        logging.info("Cache empty, processing data.")
    else:
        time_since_update = now - app_cache['last_update_time']
        if time_since_update.total_seconds() > CACHE_DURATION_SECONDS: 
            cache_needs_update = True
            logging.info("Cache expired, processing data.")
        else: 
            logging.info("Serving from cache.")
            
    if cache_needs_update:
        try: 
            process_all_data()
        except Exception as e:
            logging.error(f"Critical error in data processing: {e}", exc_info=True)
            app_cache['processing_error'] = f"Data processing error: {str(e)[:100]}"
            if app_cache['last_update_time'] is None: 
                return render_template('index.html', error=f"Initial processing failed: {str(e)[:100]}", last_updated="Never")
                
    last_updated_str = app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC') if app_cache['last_update_time'] else "Processing..."
    display_error = app_cache.get('processing_error')
    
    return render_template('index.html',
        all_stock_data=app_cache.get('all_stock_data', []), 
        paper_portfolio=app_cache.get('portfolio_display'),
        initial_capital=INITIAL_CASH, 
        trades_executed=app_cache.get('trades_executed', []),
        backtest_results=app_cache.get('backtest_results'),
        dataframe_summary=app_cache.get('dataframe_summary'),
        last_updated=last_updated_str,
        error=display_error,
        market_status="OPEN" if is_market_open() else "CLOSED"
    )

@app.route('/refresh')
def refresh_data():
    """Force a data refresh regardless of cache time."""
    try:
        process_all_data()
        return {'status': 'success', 'timestamp': app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC')}
    except Exception as e:
        logging.error(f"Error in manual refresh: {e}", exc_info=True)
        return {'status': 'error', 'message': str(e)}

@app.route('/status')
def api_status():
    """Return API status information."""
    return {
        'status': 'online',
        'market_status': "OPEN" if is_market_open() else "CLOSED",
        'last_update': app_cache['last_update_time'].strftime('%Y-%m-%d %H:%M:%S UTC') if app_cache['last_update_time'] else None,
        'symbols_count': len(app_cache.get('all_stock_data', [])),
        'cache_ttl': CACHE_DURATION_SECONDS,
        'server_time': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    }

def create_scheduler():
    """Initialize and start the background scheduler."""
    try:
        scheduler = BackgroundScheduler(timezone=INDIA_TIMEZONE)
        
        # Add job to run during market hours on weekdays
        scheduler.add_job(
            scheduled_data_update,
            'cron',
            day_of_week='mon-fri',
            hour=f"{MARKET_OPEN_HOUR}-{MARKET_CLOSE_HOUR}",
            minute=f"*/{SCAN_INTERVAL_MINUTES}",
            misfire_grace_time=60
        )
        
        # Add job to run at market open for initial data
        scheduler.add_job(
            scheduled_data_update,
            'cron',
            day_of_week='mon-fri',
            hour=MARKET_OPEN_HOUR,
            minute=15
        )
        
        logging.info("Starting background scheduler...")
        scheduler.start()
        return scheduler
    except Exception as e:
        logging.error(f"Failed to create scheduler: {e}", exc_info=True)
        return None

# --- Create templates directory if it doesn't exist ---
def ensure_template_directory():
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    if not os.path.exists(template_dir):
        try:
            os.makedirs(template_dir)
            logging.info(f"Created template directory: {template_dir}")
        except Exception as e:
            logging.error(f"Failed to create template directory: {e}", exc_info=True)
            return False
    
    # Create a basic index.html template if it doesn't exist
    index_template_path = os.path.join(template_dir, 'index.html')
    if not os.path.exists(index_template_path):
        try:
            with open(index_template_path, 'w') as f:
                f.write("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nifty 50 Stock Scanner</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        .card { margin-bottom: 20px; }
        .up { color: green; }
        .down { color: red; }
        .signal-BUY { background-color: #d4edda; }
        .signal-SELL { background-color: #f8d7da; }
        th { position: sticky; top: 0; background-color: #fff; }
    </style>
</head>
<body>
    <div class="container mt-4">
        <div class="row">
            <div class="col-12">
                <div class="d-flex justify-content-between align-items-center mb-4">
                    <h1>Nifty 50 Stock Scanner</h1>
                    <div>
                        <span class="badge bg-{{ 'success' if market_status == 'OPEN' else 'danger' }}">Market {{ market_status }}</span>
                        <button class="btn btn-sm btn-primary ms-2" id="refreshBtn">Refresh Data</button>
                    </div>
                </div>
                
                {% if error %}
                <div class="alert alert-danger">{{ error }}</div>
                {% endif %}
                
                <div class="card">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <h5 class="mb-0">Stock Recommendations</h5>
                        <small>Last Updated: {{ last_updated }}</small>
                    </div>
                    <div class="card-body">
                        {% if dataframe_summary is not none %}
                        <div class="table-responsive" style="max-height: 400px; overflow-y: auto;">
                            <table class="table table-striped table-hover">
                                <thead>
                                    <tr>
                                        <th>Symbol</th>
                                        <th>Current Price</th>
                                        <th>Change</th>
                                        <th>Signal</th>
                                        <th>Target</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for _, row in dataframe_summary.iterrows() %}
                                    <tr class="signal-{{ row['Signal'] }}">
                                        <td>{{ row['Symbol'] }}</td>
                                        <td>₹{{ row['CMP'] }}</td>
                                        <td class="{{ 'up' if not row['% Change'].startswith('-') else 'down' }}">{{ row['% Change'] }}</td>
                                        <td>{{ row['Signal'] }}</td>
                                        <td>{{ '₹' + row['Target'] if row['Target'] != 'N/A' else 'N/A' }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <p>No stock data available.</p>
                        {% endif %}
                    </div>
                </div>
                
                <div class="row">
                    <!-- Paper Trading Portfolio -->
                    <div class="col-md-6">
                        <div class="card">
                            <div class="card-header">
                                <h5 class="mb-0">Paper Trading Portfolio</h5>
                            </div>
                            <div class="card-body">
                                {% if paper_portfolio %}
                                <div class="mb-3">
                                    <h6>Summary</h6>
                                    <table class="table table-sm">
                                        <tr>
                                            <td>Total Value:</td>
                                            <td>₹{{ "{:,.2f}".format(paper_portfolio.total_value) }}</td>
                                        </tr>
                                        <tr>
                                            <td>Cash:</td>
                                            <td>₹{{ "{:,.2f}".format(paper_portfolio.cash) }}</td>
                                        </tr>
                                        <tr>
                                            <td>Returns:</td>
                                            <td class="{{ 'up' if paper_portfolio.total_value > initial_capital else 'down' }}">
                                                {{ "{:,.2f}".format((paper_portfolio.total_value - initial_capital) / initial_capital * 100) }}%
                                            </td>
                                        </tr>
                                    </table>
                                </div>
                                
                                {% if paper_portfolio.holdings %}
                                <h6>Holdings</h6>
                                <div class="table-responsive">
                                    <table class="table table-sm table-striped">
                                        <thead>
                                            <tr>
                                                <th>Symbol</th>
                                                <th>Shares</th>
                                                <th>Avg Cost</th>
                                                <th>Current</th>
                                                <th>P&L</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for holding in paper_portfolio.holdings %}
                                            <tr>
                                                <td>{{ holding.symbol }}</td>
                                                <td>{{ holding.shares }}</td>
                                                <td>₹{{ "{:,.2f}".format(holding.avg_price) }}</td>
                                                <td>₹{{ "{:,.2f}".format(holding.current_price) }}</td>
                                                <td class="{{ 'up' if holding.pnl >= 0 else 'down' }}">
                                                    {{ "{:,.2f}".format(holding.pnl_pct) }}%
                                                </td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                                {% else %}
                                <p>No current holdings.</p>
                                {% endif %}
                                {% else %}
                                <p>Portfolio data not available.</p>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                    
                    <!-- Backtest Results -->
                    <div class="col-md-6">
                        <div class="card">
                            <div class="card-header">
                                <h5 class="mb-0">Backtest Results</h5>
                            </div>
                            <div class="card-body">
                                {% if backtest_results and not backtest_results.get('error') %}
                                <div class="mb-3">
                                    <h6>{{ backtest_results.symbol }}</h6>
                                    <table class="table table-sm">
                                        <tr>
                                            <td>Period:</td>
                                            <td>{{ backtest_results.start_date.strftime('%Y-%m-%d') }} to {{ backtest_results.end_date.strftime('%Y-%m-%d') }}</td>
                                        </tr>
                                        <tr>
                                            <td>Strategy Return:</td>
                                            <td class="{{ 'up' if backtest_results.return > 0 else 'down' }}">
                                                {{ "{:,.2f}".format(backtest_results.return) }}%
                                            </td>
                                        </tr>
                                        <tr>
                                            <td>Buy & Hold Return:</td>
                                            <td class="{{ 'up' if backtest_results.buy_and_hold_return > 0 else 'down' }}">
                                                {{ "{:,.2f}".format(backtest_results.buy_and_hold_return) }}%
                                            </td>
                                        </tr>
                                    </table>
                                </div>
                                
                                <h6>Recent Trades</h6>
                                <div class="table-responsive" style="max-height: 200px; overflow-y: auto;">
                                    <table class="table table-sm table-striped">
                                        <thead>
                                            <tr>
                                                <th>Date</th>
                                                <th>Action</th>
                                                <th>Price</th>
                                                <th>Shares</th>
                                                <th>Value</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for trade in backtest_results.trades[-10:] %}
                                            <tr>
                                                <td>{{ trade.date.strftime('%Y-%m-%d') }}</td>
                                                <td class="{{ 'up' if trade.action == 'BUY' else 'down' }}">{{ trade.action }}</td>
                                                <td>₹{{ "{:,.2f}".format(trade.price) }}</td>
                                                <td>{{ trade.shares }}</td>
                                                <td>₹{{ "{:,.2f}".format(trade.value) }}</td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                                {% else %}
                                <p>{{ backtest_results.get('error', 'Backtest data not available.') }}</p>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Recent Trades -->
                <div class="card">
                    <div class="card-header">
                        <h5 class="mb-0">Recent Trades (Paper Portfolio)</h5>
                    </div>
                    <div class="card-body">
                        {% if trades_executed %}
                        <div class="table-responsive">
                            <table class="table table-striped">
                                <thead>
                                    <tr>
                                        <th>Time</th>
                                        <th>Symbol</th>
                                        <th>Action</th>
                                        <th>Shares</th>
                                        <th>Price</th>
                                        <th>Total</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for trade in trades_executed %}
                                    <tr>
                                        <td>{{ trade.timestamp.strftime('%Y-%m-%d %H:%M') }}</td>
                                        <td>{{ trade.symbol }}</td>
                                        <td class="{{ 'up' if trade.action == 'BUY' else 'down' }}">{{ trade.action }}</td>
                                        <td>{{ trade.shares }}</td>
                                        <td>₹{{ "{:,.2f}".format(trade.price) }}</td>
                                        <td>₹{{ "{:,.2f}".format(trade.total) }}</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                        {% else %}
                        <p>No recent trades.</p>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.getElementById('refreshBtn').addEventListener('click', function() {
            this.disabled = true;
            this.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Refreshing...';
            
            fetch('/refresh')
                .then(response => response.json())
                .then(data => {
                    if(data.status === 'success') {
                        window.location.reload();
                    } else {
                        alert('Error refreshing data: ' + data.message);
                        this.disabled = false;
                        this.innerHTML = 'Refresh Data';
                    }
                })
                .catch(error => {
                    alert('Network error: ' + error);
                    this.disabled = false;
                    this.innerHTML = 'Refresh Data';
                });
        });
    </script>
</body>
</html>""")
            logging.info(f"Created basic index.html template")
        except Exception as e:
            logging.error(f"Failed to create index.html template: {e}", exc_info=True)
            return False
    
    return True

# --- Entry Point ---
if __name__ == '__main__':
    try:
        # Ensure templates directory and files exist
        if not ensure_template_directory():
            logging.error("Failed to set up templates. Exiting.")
            exit(1)
        
        # Initial data processing
        process_all_data()
        
        # Start the background scheduler
        scheduler = create_scheduler()
        if not scheduler:
            logging.warning("Scheduler initialization failed, continuing without automatic updates.")
        
        # Start Flask app
        port = int(os.environ.get('PORT', 5000))
        logging.info(f"Starting Flask app on port {port}")
        app.run(host='0.0.0.0', port=port, debug=False)
    except Exception as e:
        logging.critical(f"Fatal error during application startup: {e}", exc_info=True)
