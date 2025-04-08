import pandas as pd
import numpy as np
import yfinance as yf
import requests
from ta.momentum import RSIIndicator
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

# Configuration
NIFTY_INDEX = "^NSEI"  # Nifty 50 index
NIFTY_STOCKS_FILE = "nifty50_stocks.csv"  # You'll need to create this CSV with stock symbols
RSI_PERIOD = 14
OVERSOLD_THRESHOLD = 30
OVERBOUGHT_THRESHOLD = 70

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
    """Analyze stocks for RSI conditions."""
    stocks = get_nifty_stocks()
    daily_oversold = []
    weekly_overbought = []
    monthly_overbought = []
    
    for stock in stocks:
        # Ensure proper Yahoo Finance ticker format
        if not stock.endswith(".NS") and not stock.endswith(".BO"):
            stock_symbol = f"{stock}.NS"
        else:
            stock_symbol = stock
            
        # Calculate RSI for different timeframes
        daily_rsi, daily_price = calculate_rsi(stock_symbol, RSI_PERIOD, "daily")
        weekly_rsi, weekly_price = calculate_rsi(stock_symbol, RSI_PERIOD, "weekly")
        monthly_rsi, monthly_price = calculate_rsi(stock_symbol, RSI_PERIOD, "monthly")
        
        stock_name = stock.replace(".NS", "").replace(".BO", "")
        
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
    
    return daily_oversold, weekly_overbought + monthly_overbought

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

def format_message(oversold_stocks, overbought_stocks):
    """Format the analysis results as a Telegram message."""
    current_date = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%d-%b-%Y %H:%M:%S")
    message = f"<b>🔍 Nifty RSI Analysis ({current_date})</b>\n\n"
    
    # Format oversold stocks
    if oversold_stocks:
        message += "<b>📉 Stocks with Daily RSI below 30 (Potentially Oversold):</b>\n\n"
        for stock in oversold_stocks:
            message += f"• <b>{stock['symbol']}</b>: RSI = {stock['rsi']} | Price = ₹{stock['price']}\n"
        message += "\n"
    
    # Format overbought stocks
    if overbought_stocks:
        message += "<b>📈 Stocks with Weekly/Monthly RSI above 70 (Potentially Overbought):</b>\n\n"
        for stock in overbought_stocks:
            message += f"• <b>{stock['symbol']}</b> ({stock['timeframe']}): RSI = {stock['rsi']} | Price = ₹{stock['price']}\n"
    
    # If no stocks found
    if not oversold_stocks and not overbought_stocks:
        message += "No stocks meeting RSI criteria found in this scan."
    
    message += "\n<i>Technical indicators should be used alongside other analysis methods. Trade with proper risk management.</i>"
    return message

@app.route('/')
def home():
    """Render the web page with analysis results."""
    current_date = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%d-%b-%Y %H:%M:%S")
    
    html_template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Nifty RSI Analysis</title>
        <meta http-equiv="refresh" content="1800"> <!-- Refresh every 30 minutes -->
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 0;
                padding: 20px;
                line-height: 1.6;
                color: #333;
                max-width: 800px;
                margin: 0 auto;
            }
            h1 {
                color: #2c3e50;
                border-bottom: 2px solid #3498db;
                padding-bottom: 10px;
            }
            h2 {
                color: #2c3e50;
                margin-top: 30px;
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
            .stock-symbol {
                font-weight: bold;
                font-size: 18px;
                color: #2980b9;
            }
            .timeframe {
                color: #7f8c8d;
                font-size: 14px;
                margin-left: 10px;
            }
            .stock-details {
                display: flex;
                justify-content: space-between;
                margin-top: 10px;
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
            @media (max-width: 600px) {
                body {
                    padding: 10px;
                }
                .stock-details {
                    flex-direction: column;
                }
            }
        </style>
    </head>
    <body>
        <h1>Nifty Stocks RSI Analysis</h1>
        <p class="last-updated">Last updated: {{ date }}</p>
        
        <!-- Oversold Stocks Section -->
        <h2>📉 Stocks with Daily RSI below 30 (Potentially Oversold)</h2>
        {% if oversold %}
            {% for stock in oversold %}
                <div class="stock-card oversold">
                    <div class="stock-symbol">{{ stock.symbol }}
                        <span class="timeframe">({{ stock.timeframe }})</span>
                    </div>
                    <div class="stock-details">
                        <span>RSI: <strong>{{ stock.rsi }}</strong></span>
                        <span>Price: <strong>₹{{ stock.price }}</strong></span>
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <div class="no-stocks">
                <p>No oversold stocks found in this scan.</p>
            </div>
        {% endif %}
        
        <!-- Overbought Stocks Section -->
        <h2>📈 Stocks with Weekly/Monthly RSI above 70 (Potentially Overbought)</h2>
        {% if overbought %}
            {% for stock in overbought %}
                <div class="stock-card overbought">
                    <div class="stock-symbol">{{ stock.symbol }}
                        <span class="timeframe">({{ stock.timeframe }})</span>
                    </div>
                    <div class="stock-details">
                        <span>RSI: <strong>{{ stock.rsi }}</strong></span>
                        <span>Price: <strong>₹{{ stock.price }}</strong></span>
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <div class="no-stocks">
                <p>No overbought stocks found in this scan.</p>
            </div>
        {% endif %}
        
        <div class="disclaimer">
            <p>Disclaimer: This information is for educational purposes only and should not be considered as financial advice. 
            Always do your own research before making investment decisions.</p>
        </div>
    </body>
    </html>
    '''
    
    return render_template_string(html_template, oversold=oversold_stocks, overbought=overbought_stocks, date=current_date)

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
    global oversold_stocks, overbought_stocks
    
    current_time = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%H:%M:%S")
    logger.info(f"Running RSI analysis for Nifty stocks at {current_time}...")
    
    # Run analysis
    daily_oversold, combined_overbought = analyze_stocks()
    
    # Update global variables for web display
    oversold_stocks = daily_oversold
    overbought_stocks = combined_overbought
    
    # Send to Telegram
    if daily_oversold or combined_overbought:
        message = format_message(daily_oversold, combined_overbought)
        send_telegram_message(message)
        logger.info(f"Analysis complete. Found {len(daily_oversold)} oversold and {len(combined_overbought)} overbought stocks.")
    else:
        logger.info("Analysis complete. No stocks meeting RSI criteria found.")

def start_scheduler():
    """Start the scheduler to run the analysis during and after market hours."""
    scheduler = BlockingScheduler(timezone=pytz.timezone('Asia/Kolkata'))
    
    # Run every 30 minutes during market hours on weekdays
    scheduler.add_job(
        run_analysis, 
        'cron', 
        day_of_week='mon-fri', 
        hour='9-15', 
        minute='15,45'
    )
    
    # Run after market hours on weekdays at 4:00 PM
    scheduler.add_job(
        run_analysis, 
        'cron', 
        day_of_week='mon-fri', 
        hour=16, 
        minute=0
    )
    
    logger.info("Scheduler started. Will run analysis every 30 minutes during market hours and at 4:00 PM IST on weekdays.")
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
