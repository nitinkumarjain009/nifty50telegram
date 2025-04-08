import pandas as pd
import numpy as np
import yfinance as yf
import requests
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
import pytz
from datetime import datetime, time, timedelta
import logging
from apscheduler.schedulers.blocking import BlockingScheduler
import os
from flask import Flask, render_template_string
import threading

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Telegram configuration
TELEGRAM_TOKEN = '8017759392:AAEwM-W-y83lLXTjlPl8sC_aBmizuIrFXnU'
TELEGRAM_CHAT_ID = '@Stockniftybot'

# Flask app for web display
app = Flask(__name__)
oversold_stocks = []
overbought_stocks = []
chandelier_signals = []

# Configuration
NIFTY_INDEX = "^NSEI"  # Nifty 50 index
NIFTY_STOCKS_FILE = "nifty50_stocks.csv"  # You'll need to create this CSV with stock symbols
RSI_PERIOD = 14
OVERSOLD_THRESHOLD = 30
OVERBOUGHT_THRESHOLD = 60

# Chandelier Exit configuration
ATR_PERIOD = 22
CHANDELIER_MULTIPLIER = 3

def calculate_rsi(ticker_symbol, period=14, timeframe="daily"):
    """Calculate RSI for a given stock ticker and timeframe."""
    try:
        # Set interval and period based on timeframe
        if timeframe == "daily":
            interval = "1d"
            hist_period = "30d"
        elif timeframe == "weekly":
            interval = "1wk"
            hist_period = "200d"  # Need more historical data for weekly
        elif timeframe == "monthly":
            interval = "1mo"
            hist_period = "600d"  # Need more historical data for monthly
        else:
            logger.error(f"Invalid timeframe: {timeframe}")
            return None, None
        
        # Get data
        data = yf.download(ticker_symbol, period=hist_period, interval=interval, progress=False)
        
        if data.empty or len(data) < period + 1:
            logger.warning(f"Not enough {timeframe} data for {ticker_symbol}")
            return None, None
        
        # Make sure we're working with a Series, not a DataFrame or ndarray
        close_series = data['Close']
        if isinstance(close_series, pd.DataFrame):  # If it's still a DataFrame somehow
            close_series = close_series.iloc[:, 0]  # Take the first column as a Series
        elif isinstance(close_series.values, np.ndarray) and close_series.values.ndim > 1:
            # If it's a Series with a multi-dimensional ndarray inside
            close_series = pd.Series(close_series.values.flatten())
            
        # Calculate RSI with the properly formatted Series
        rsi_indicator = RSIIndicator(close=close_series, window=period)
        rsi_values = rsi_indicator.rsi()
        
        # Get the latest values
        latest_rsi = rsi_values.iloc[-1]
        latest_price = close_series.iloc[-1]
        
        return latest_rsi, latest_price
    
    except Exception as e:
        logger.error(f"Error calculating {timeframe} RSI for {ticker_symbol}: {e}")
        return None, None

def calculate_chandelier_exit(ticker_symbol, atr_period=22, multiplier=3):
    """Calculate Chandelier Exit for a given stock ticker."""
    try:
        # Get data for the past 60 days to have enough history
        data = yf.download(ticker_symbol, period="60d", interval="1d", progress=False)
        
        if data.empty or len(data) < atr_period + 1:
            logger.warning(f"Not enough data for chandelier exit on {ticker_symbol}")
            return None, None, None
        
        # Calculate ATR
        atr_indicator = AverageTrueRange(high=data['High'], low=data['Low'], close=data['Close'], window=atr_period)
        atr = atr_indicator.average_true_range()
        
        # Calculate highest high for long exit
        data['highest_high'] = data['High'].rolling(window=atr_period).max()
        
        # Calculate lowest low for short exit
        data['lowest_low'] = data['Low'].rolling(window=atr_period).min()
        
        # Calculate Chandelier Exit Long (for sell signals)
        data['chandelier_long'] = data['highest_high'] - (multiplier * atr)
        
        # Calculate Chandelier Exit Short (for buy signals)
        data['chandelier_short'] = data['lowest_low'] + (multiplier * atr)
        
        # Determine signal
        current_close = data['Close'].iloc[-1]
        previous_close = data['Close'].iloc[-2]
        chandelier_long = data['chandelier_long'].iloc[-1]
        chandelier_short = data['chandelier_short'].iloc[-1]
        
        # Buy signal: Price crosses above the Chandelier Exit Short
        buy_signal = previous_close <= data['chandelier_short'].iloc[-2] and current_close > chandelier_short
        
        # Sell signal: Price crosses below the Chandelier Exit Long
        sell_signal = previous_close >= data['chandelier_long'].iloc[-2] and current_close < chandelier_long
        
        signal = None
        if buy_signal:
            signal = "BUY"
        elif sell_signal:
            signal = "SELL"
        
        return signal, round(chandelier_long, 2), round(chandelier_short, 2)
    
    except Exception as e:
        logger.error(f"Error calculating Chandelier Exit for {ticker_symbol}: {e}")
        return None, None, None

def get_nifty_stocks():
    """Get the list of Nifty stocks from CSV file or fallback to a sample list."""
    try:
        if os.path.exists(NIFTY_STOCKS_FILE):
            df = pd.read_csv(NIFTY_STOCKS_FILE)
            return df['Symbol'].tolist()
        else:
            # Fallback to a sample of Nifty 50 stocks
            logger.warning(f"{NIFTY_STOCKS_FILE} not found. Using sample stock list.")
            return ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS", 
                    "HDFC.NS", "ITC.NS", "KOTAKBANK.NS", "LT.NS", "HINDUNILVR.NS"]
    except Exception as e:
        logger.error(f"Error loading stock list: {e}")
        return []

def analyze_stocks():
    """Analyze stocks for RSI conditions and Chandelier Exit signals."""
    stocks = get_nifty_stocks()
    daily_oversold = []
    weekly_overbought = []
    monthly_overbought = []
    chandelier_signals_list = []
    
    for stock in stocks:
        # Ensure proper Yahoo Finance ticker format
        if not stock.endswith(".NS") and not stock.endswith(".BO"):
            stock_symbol = f"{stock}.NS"
        else:
            stock_symbol = stock
            
        stock_name = stock.replace(".NS", "").replace(".BO", "")
        
        # Calculate RSI for different timeframes
        daily_rsi, daily_price = calculate_rsi(stock_symbol, RSI_PERIOD, "daily")
        weekly_rsi, weekly_price = calculate_rsi(stock_symbol, RSI_PERIOD, "weekly")
        monthly_rsi, monthly_price = calculate_rsi(stock_symbol, RSI_PERIOD, "monthly")
        
        # Check daily oversold condition
        if daily_rsi is not None and daily_rsi < OVERSOLD_THRESHOLD:
            daily_oversold.append({
                "symbol": stock_name,
                "rsi": round(daily_rsi, 2),
                "price": round(daily_price, 2),
                "timeframe": "daily"
            })
            logger.info(f"Found daily oversold stock: {stock_name} with RSI: {daily_rsi:.2f}")
        
        # Check weekly overbought condition
        if weekly_rsi is not None and weekly_rsi > OVERBOUGHT_THRESHOLD:
            weekly_overbought.append({
                "symbol": stock_name,
                "rsi": round(weekly_rsi, 2),
                "price": round(weekly_price, 2),
                "timeframe": "weekly"
            })
            logger.info(f"Found weekly overbought stock: {stock_name} with RSI: {weekly_rsi:.2f}")
            
        # Check monthly overbought condition
        if monthly_rsi is not None and monthly_rsi > OVERBOUGHT_THRESHOLD:
            monthly_overbought.append({
                "symbol": stock_name,
                "rsi": round(monthly_rsi, 2),
                "price": round(monthly_price, 2),
                "timeframe": "monthly"
            })
            logger.info(f"Found monthly overbought stock: {stock_name} with RSI: {monthly_rsi:.2f}")
        
        # Calculate Chandelier Exit signals
        signal, long_exit, short_exit = calculate_chandelier_exit(stock_symbol, ATR_PERIOD, CHANDELIER_MULTIPLIER)
        
        if signal:
            # Get price data for the signal
            data = yf.download(stock_symbol, period="1d", interval="1d", progress=False)
            current_price = round(data['Close'].iloc[-1], 2)
            
            chandelier_signals_list.append({
                "symbol": stock_name,
                "signal": signal,
                "price": current_price,
                "long_exit": long_exit,
                "short_exit": short_exit,
                "daily_rsi": round(daily_rsi, 2) if daily_rsi is not None else None
            })
            logger.info(f"Found {signal} signal for {stock_name} at price: {current_price}")
    
    return daily_oversold, weekly_overbought + monthly_overbought, chandelier_signals_list

def send_telegram_message(message):
    """Send message to Telegram channel."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            logger.info("Telegram message sent successfully")
        else:
            logger.error(f"Failed to send Telegram message: {response.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram message: {e}")

def format_message(oversold_stocks, overbought_stocks, chandelier_signals):
    """Format the analysis results as a Telegram message."""
    current_date = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%d-%b-%Y %H:%M:%S")
    message = f"<b>🔍 Nifty Technical Analysis ({current_date})</b>\n\n"
    
    # Format Chandelier Exit signals (prioritize these)
    if chandelier_signals:
        message += "<b>🔔 Chandelier Exit Signals:</b>\n\n"
        for stock in chandelier_signals:
            signal_emoji = "🟢" if stock['signal'] == "BUY" else "🔴"
            rsi_info = f" | RSI = {stock['daily_rsi']}" if stock['daily_rsi'] is not None else ""
            message += f"{signal_emoji} <b>{stock['symbol']}</b>: {stock['signal']} @ ₹{stock['price']}{rsi_info}\n"
        message += "\n"
    
    # Format oversold stocks
    if oversold_stocks:
        message += "<b>📉 Stocks with Daily RSI below 30 (Potentially Oversold):</b>\n\n"
        for stock in oversold_stocks:
            message += f"• <b>{stock['symbol']}</b>: RSI = {stock['rsi']} | Price = ₹{stock['price']}\n"
        message += "\n"
    
    # Format overbought stocks
    if overbought_stocks:
        message += "<b>📈 Stocks with Weekly/Monthly RSI above 60 (Potentially Overbought):</b>\n\n"
        for stock in overbought_stocks:
            message += f"• <b>{stock['symbol']}</b> ({stock['timeframe']}): RSI = {stock['rsi']} | Price = ₹{stock['price']}\n"
    
    # If no signals found
    if not oversold_stocks and not overbought_stocks and not chandelier_signals:
        message += "No significant technical signals found in this scan."
    
    message += "\n<i>Technical indicators should be used alongside other analysis methods. Trade with proper risk management.</i>"
    return message

@app.route('/')
def home():
    """Render the web page with analysis results."""
    current_date = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%d-%b-%Y %H:%M:%S")
    is_market_open = is_market_hours()
    
    html_template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Nifty Technical Analysis</title>
        <meta http-equiv="refresh" content="600"> <!-- Refresh every 10 minutes -->
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                line-height: 1.6;
                color: #333;
                max-width: 1000px;
                margin: 0 auto;
                background-color: #f5f5f5;
            }
            .header {
                text-align: center;
                margin-bottom: 20px;
                background-color: #2c3e50;
                color: white;
                padding: 20px;
                border-radius: 5px;
            }
            .market-status {
                display: flex;
                justify-content: center;
                align-items: center;
                margin-bottom: 20px;
            }
            .status-indicator {
                width: 12px;
                height: 12px;
                border-radius: 50%;
                margin-right: 8px;
            }
            .open {
                background-color: #2ecc71;
            }
            .closed {
                background-color: #e74c3c;
            }
            .section {
                background-color: white;
                border-radius: 5px;
                padding: 20px;
                margin-bottom: 20px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            h1 {
                color: white;
                margin: 0;
            }
            h2 {
                color: #2c3e50;
                margin-top: 0;
                border-bottom: 2px solid #3498db;
                padding-bottom: 10px;
            }
            .stock-card {
                background-color: #f9f9f9;
                border-radius: 5px;
                padding: 15px;
                margin-bottom: 10px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .oversold {
                border-left: 4px solid #e74c3c;
            }
            .overbought {
                border-left: 4px solid #2ecc71;
            }
            .buy-signal {
                border-left: 4px solid #27ae60;
                background-color: #eafaf1;
            }
            .sell-signal {
                border-left: 4px solid #c0392b;
                background-color: #fdedec;
            }
            .stock-symbol {
                font-weight: bold;
                font-size: 18px;
                color: #2980b9;
            }
            .signal-badge {
                display: inline-block;
                padding: 3px 8px;
                border-radius: 3px;
                color: white;
                font-weight: bold;
                margin-left: 10px;
            }
            .buy {
                background-color: #27ae60;
            }
            .sell {
                background-color: #c0392b;
            }
            .timeframe {
                color: #7f8c8d;
                font-size: 14px;
                margin-left: 10px;
            }
            .stock-details {
                display: flex;
                flex-wrap: wrap;
                justify-content: space-between;
                margin-top: 10px;
            }
            .detail-item {
                flex: 1;
                min-width: 150px;
                margin: 5px;
            }
            .disclaimer {
                margin-top: 30px;
                font-style: italic;
                color: #7f8c8d;
                border-top: 1px solid #eee;
                padding-top: 15px;
            }
            .last-updated {
                text-align: right;
                font-size: 14px;
                color: #95a5a6;
            }
            .no-stocks {
                padding: 20px;
                background-color: #f8f9fa;
                text-align: center;
                border-radius: 5px;
            }
            .grid-container {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                gap: 15px;
            }
            @media (max-width: 600px) {
                body {
                    padding: 10px;
                }
                .grid-container {
                    grid-template-columns: 1fr;
                }
                .stock-details {
                    flex-direction: column;
                }
                .detail-item {
                    margin: 3px 0;
                }
            }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Nifty Stocks Technical Analysis</h1>
            <p class="last-updated">Last updated: {{ date }}</p>
        </div>
        
        <div class="market-status">
            <div class="status-indicator {{ 'open' if market_open else 'closed' }}"></div>
            <p>Market is currently <strong>{{ 'OPEN' if market_open else 'CLOSED' }}</strong></p>
        </div>
        
        <!-- Chandelier Exit Signals Section -->
        <div class="section">
            <h2>🔔 Chandelier Exit Signals</h2>
            {% if chandelier %}
                <div class="grid-container">
                    {% for stock in chandelier %}
                        <div class="stock-card {{ 'buy-signal' if stock.signal == 'BUY' else 'sell-signal' }}">
                            <div class="stock-symbol">{{ stock.symbol }}
                                <span class="signal-badge {{ 'buy' if stock.signal == 'BUY' else 'sell' }}">{{ stock.signal }}</span>
                            </div>
                            <div class="stock-details">
                                <div class="detail-item">
                                    <span>Price: <strong>₹{{ stock.price }}</strong></span>
                                </div>
                                {% if stock.daily_rsi %}
                                <div class="detail-item">
                                    <span>RSI: <strong>{{ stock.daily_rsi }}</strong></span>
                                </div>
                                {% endif %}
                                <div class="detail-item">
                                    <span>Long Exit: <strong>₹{{ stock.long_exit }}</strong></span>
                                </div>
                                <div class="detail-item">
                                    <span>Short Exit: <strong>₹{{ stock.short_exit }}</strong></span>
                                </div>
                            </div>
                        </div>
                    {% endfor %}
                </div>
            {% else %}
                <div class="no-stocks">
                    <p>No Chandelier Exit signals found in this scan.</p>
                </div>
            {% endif %}
        </div>
        
        <!-- Oversold Stocks Section -->
        <div class="section">
            <h2>📉 Stocks with Daily RSI below 30 (Potentially Oversold)</h2>
            {% if oversold %}
                <div class="grid-container">
                    {% for stock in oversold %}
                        <div class="stock-card oversold">
                            <div class="stock-symbol">{{ stock.symbol }}
                                <span class="timeframe">({{ stock.timeframe }})</span>
                            </div>
                            <div class="stock-details">
                                <div class="detail-item">
                                    <span>RSI: <strong>{{ stock.rsi }}</strong></span>
                                </div>
                                <div class="detail-item">
                                    <span>Price: <strong>₹{{ stock.price }}</strong></span>
                                </div>
                            </div>
                        </div>
                    {% endfor %}
                </div>
            {% else %}
                <div class="no-stocks">
                    <p>No oversold stocks found in this scan.</p>
                </div>
            {% endif %}
        </div>
        
        <!-- Overbought Stocks Section -->
        <div class="section">
            <h2>📈 Stocks with Weekly/Monthly RSI above 60 (Potentially Overbought)</h2>
            {% if overbought %}
                <div class="grid-container">
                    {% for stock in overbought %}
                        <div class="stock-card overbought">
                            <div class="stock-symbol">{{ stock.symbol }}
                                <span class="timeframe">({{ stock.timeframe }})</span>
                            </div>
                            <div class="stock-details">
                                <div class="detail-item">
                                    <span>RSI: <strong>{{ stock.rsi }}</strong></span>
                                </div>
                                <div class="detail-item">
                                    <span>Price: <strong>₹{{ stock.price }}</strong></span>
                                </div>
                            </div>
                        </div>
                    {% endfor %}
                </div>
            {% else %}
                <div class="no-stocks">
                    <p>No overbought stocks found in this scan.</p>
                </div>
            {% endif %}
        </div>
        
        <div class="disclaimer">
            <p>Disclaimer: This information is for educational purposes only and should not be considered as financial advice. 
            Always do your own research before making investment decisions.</p>
            <p>Technical indicators should be used alongside other analysis methods. Trade with proper risk management.</p>
        </div>
    </body>
    </html>
    '''
    
    return render_template_string(html_template, oversold=oversold_stocks, overbought=overbought_stocks, 
                                 chandelier=chandelier_signals, date=current_date, market_open=is_market_open)

def run_flask_app():
    """Run the Flask app on a separate thread."""
    app.run(host='0.0.0.0', port=5000)

def is_market_hours():
    """Check if it's currently market hours in India (9:15 AM to 3:30 PM IST on weekdays)."""
    ist_now = datetime.now(pytz.timezone('Asia/Kolkata'))
    weekday = ist_now.weekday()
    current_time = ist_now.time()
    
    # Check if it's a weekday (0-4 represents Monday to Friday)
    if weekday < 5:
        # Check if it's between 9:15 AM and 3:30 PM
        market_open = time(9, 15)
        market_close = time(15, 30)
        return market_open <= current_time <= market_close
    
    return False

def run_analysis():
    """Run the analysis, send to Telegram and update webpage."""
    global oversold_stocks, overbought_stocks, chandelier_signals
    
    current_time = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%H:%M:%S")
    logger.info(f"Running technical analysis for Nifty stocks at {current_time}...")
    
    # Run analysis
    daily_oversold, combined_overbought, new_chandelier_signals = analyze_stocks()
    
    # Update global variables for web display
    oversold_stocks = daily_oversold
    overbought_stocks = combined_overbought
    chandelier_signals = new_chandelier_signals
    
    # Send to Telegram only if there are signals to report
    if daily_oversold or combined_overbought or new_chandelier_signals:
        message = format_message(daily_oversold, combined_overbought, new_chandelier_signals)
        send_telegram_message(message)
        
        # Log summary
        logger.info(f"Analysis complete. Found {len(daily_oversold)} oversold, {len(combined_overbought)} overbought stocks, and {len(new_chandelier_signals)} chandelier signals.")
    else:
        logger.info("Analysis complete. No significant technical signals found.")

def start_scheduler():
    """Start the scheduler to run the analysis with different intervals during/after market hours."""
    scheduler = BlockingScheduler(timezone=pytz.timezone('Asia/Kolkata'))
    
    # Run every 10 minutes during market hours on weekdays
    scheduler.add_job(
        run_analysis, 
        'cron', 
        day_of_week='mon-fri', 
        hour='9-15', 
        minute='*/10'
    )
    
    # Run after market hours on weekdays at 4:00 PM for daily analysis
    scheduler.add_job(
        run_analysis, 
        'cron', 
        day_of_week='mon-fri', 
        hour=16, 
        minute=0
    )
    
    logger.info("Scheduler started. Will run analysis every 10 minutes during market hours and at 4:00 PM IST on weekdays.")
    scheduler.start()

if __name__ == "__main__":
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Run initial analysis
    run_analysis()
    
    # Start scheduler for scheduled runs
    start_scheduler()
