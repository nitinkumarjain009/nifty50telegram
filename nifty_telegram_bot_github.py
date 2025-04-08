import pandas as pd
import numpy as np
import yfinance as yf
import requests
from ta.momentum import RSIIndicator
import pytz
from datetime import datetime, time
import logging
from apscheduler.schedulers.blocking import BlockingScheduler
import os
from flask import Flask, render_template_string
import threading

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Telegram configuration
TELEGRAM_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN'
TELEGRAM_CHAT_ID = 'YOUR_TELEGRAM_CHAT_ID'

# Flask app for web display
app = Flask(__name__)
oversold_stocks = []

# Configuration
NIFTY_INDEX = "^NSEI"  # Nifty 50 index
NIFTY_STOCKS_FILE = "nifty50_stocks.csv"  # You'll need to create this CSV with stock symbols
RSI_PERIOD = 14
RSI_THRESHOLD = 30

def calculate_rsi(ticker_symbol, period=14):
    """Calculate RSI for a given stock ticker."""
    try:
        # Get data for the last 30 days to have enough data points for RSI calculation
        data = yf.download(ticker_symbol, period="30d", interval="1d", progress=False)
        
        if data.empty or len(data) < period:
            logger.warning(f"Not enough data for {ticker_symbol}")
            return None, None
        
        # Calculate RSI
        rsi_indicator = RSIIndicator(close=data['Close'], window=period)
        data['RSI'] = rsi_indicator.rsi()
        
        # Get the latest values
        latest_rsi = data['RSI'].iloc[-1]
        latest_price = data['Close'].iloc[-1]
        
        return latest_rsi, latest_price
    
    except Exception as e:
        logger.error(f"Error calculating RSI for {ticker_symbol}: {e}")
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
    """Analyze stocks for RSI < 30 condition."""
    stocks = get_nifty_stocks()
    results = []
    
    for stock in stocks:
        # Ensure proper Yahoo Finance ticker format
        if not stock.endswith(".NS") and not stock.endswith(".BO"):
            stock_symbol = f"{stock}.NS"
        else:
            stock_symbol = stock
            
        rsi, price = calculate_rsi(stock_symbol, RSI_PERIOD)
        
        if rsi is not None and rsi < RSI_THRESHOLD:
            stock_name = stock.replace(".NS", "").replace(".BO", "")
            results.append({
                "symbol": stock_name,
                "rsi": round(rsi, 2),
                "price": round(price, 2)
            })
            logger.info(f"Found oversold stock: {stock_name} with RSI: {rsi:.2f}")
    
    return results

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

def format_message(results):
    """Format the analysis results as a Telegram message."""
    if not results:
        return "No Nifty stocks with RSI below 30 found today."
    
    current_date = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%d-%b-%Y")
    
    message = f"<b>🔍 Nifty Oversold Stocks Analysis ({current_date})</b>\n\n"
    message += "<b>Stocks with RSI below 30:</b>\n\n"
    
    for stock in results:
        message += f"• <b>{stock['symbol']}</b>: RSI = {stock['rsi']} | Price = ₹{stock['price']}\n"
    
    message += "\n<i>These stocks may be technically oversold. Consider for potential entry with proper risk management.</i>"
    return message

@app.route('/')
def home():
    """Render the web page with analysis results."""
    current_date = datetime.now(pytz.timezone('Asia/Kolkata')).strftime("%d-%b-%Y")
    
    html_template = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Nifty RSI Analysis</title>
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
            .stock-card {
                background-color: #f9f9f9;
                border-radius: 5px;
                padding: 15px;
                margin-bottom: 10px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            }
            .stock-symbol {
                font-weight: bold;
                font-size: 18px;
                color: #2980b9;
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
        </style>
    </head>
    <body>
        <h1>Nifty Stocks RSI Analysis</h1>
        <p class="last-updated">Last updated: {{ date }}</p>
        
        {% if stocks %}
            <h2>Stocks with RSI below 30 (Potentially Oversold)</h2>
            {% for stock in stocks %}
                <div class="stock-card">
                    <div class="stock-symbol">{{ stock.symbol }}</div>
                    <div class="stock-details">
                        <span>RSI: <strong>{{ stock.rsi }}</strong></span>
                        <span>Price: <strong>₹{{ stock.price }}</strong></span>
                    </div>
                </div>
            {% endfor %}
        {% else %}
            <div class="no-stocks">
                <p>No Nifty stocks with RSI below 30 found today.</p>
            </div>
        {% endif %}
        
        <div class="disclaimer">
            <p>Disclaimer: This information is for educational purposes only and should not be considered as financial advice. 
            Always do your own research before making investment decisions.</p>
        </div>
    </body>
    </html>
    '''
    
    return render_template_string(html_template, stocks=oversold_stocks, date=current_date)

def run_flask_app():
    """Run the Flask app on a separate thread."""
    app.run(host='0.0.0.0', port=5000)

def run_daily_analysis():
    """Run the analysis, send to Telegram and update webpage."""
    global oversold_stocks
    
    logger.info("Starting daily RSI analysis for Nifty stocks...")
    
    # Check if market is closed (after 3:30 PM IST)
    ist_now = datetime.now(pytz.timezone('Asia/Kolkata'))
    market_closed = ist_now.time() > time(15, 30)
    
    if not market_closed:
        logger.info("Market still open. Waiting until after market hours.")
        return
    
    # Run analysis
    results = analyze_stocks()
    oversold_stocks = results  # Update global variable for web display
    
    # Send to Telegram
    if results:
        message = format_message(results)
        send_telegram_message(message)
        logger.info(f"Analysis complete. Found {len(results)} oversold stocks.")
    else:
        logger.info("Analysis complete. No oversold stocks found.")

def start_scheduler():
    """Start the scheduler to run the analysis daily after market hours."""
    scheduler = BlockingScheduler(timezone=pytz.timezone('Asia/Kolkata'))
    
    # Run every weekday (Monday to Friday) at 4:00 PM IST
    scheduler.add_job(
        run_daily_analysis, 
        'cron', 
        day_of_week='mon-fri', 
        hour=16, 
        minute=0
    )
    
    logger.info("Scheduler started. Will run analysis at 4:00 PM IST on weekdays.")
    scheduler.start()

if __name__ == "__main__":
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask_app)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Run initial analysis
    run_daily_analysis()
    
    # Start scheduler for daily runs
    start_scheduler()
