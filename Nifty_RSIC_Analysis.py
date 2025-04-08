import pandas as pd
import numpy as np
import yfinance as yf
import datetime
import time
import requests
import os
from flask import Flask, render_template, jsonify
import logging
import pandas_ta as ta  # Using pandas-ta instead of TA-Lib
import threading
import pytz

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
REFRESH_INTERVAL = 15 * 60  # 15 minutes in seconds
NIFTY500_CSV_PATH = "nifty500_symbols.csv"

# Market hours in IST
MARKET_OPEN_HOUR = 9  # 9:00 AM IST
MARKET_OPEN_MINUTE = 15  # 9:15 AM IST
MARKET_CLOSE_HOUR = 15  # 3:00 PM IST
MARKET_CLOSE_MINUTE = 30  # 3:30 PM IST

# Initialize Flask app
app = Flask(__name__, template_folder='templates')

# Store results that will be displayed on the web page
results = {
    'last_update': None,
    'buy_recommendations': [],
    'data_history': [],  # To store historical recommendations
    'market_status': 'Closed',  # Current market status
    'next_update': None,  # When the next scan will occur
    'all_stocks_data': []  # All stocks data for the table
}

def is_market_open():
    """Check if the market is currently open based on IST timezone"""
    now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    
    # Check if it's a weekday (0 = Monday, 6 = Sunday)
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    
    # Check if current time is within market hours
    market_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    
    return market_open <= now <= market_close

def time_until_next_market_open():
    """Calculate time in seconds until the next market opening"""
    now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    
    # Start with today's date for the market open time
    next_open = now.replace(hour=MARKET_OPEN_HOUR, minute=MARKET_OPEN_MINUTE, second=0, microsecond=0)
    
    # If we're past today's market open time, go to the next business day
    if now >= next_open:
        next_open = next_open + datetime.timedelta(days=1)
    
    # Skip to Monday if it's Friday after market hours or weekend
    while next_open.weekday() >= 5:  # Saturday or Sunday
        next_open = next_open + datetime.timedelta(days=1)
    
    # Calculate time difference in seconds
    delta = next_open - now
    return delta.total_seconds()

def time_until_market_close():
    """Calculate time in seconds until the market closes"""
    now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
    market_close = now.replace(hour=MARKET_CLOSE_HOUR, minute=MARKET_CLOSE_MINUTE, second=0, microsecond=0)
    
    delta = market_close - now
    return delta.total_seconds()

def download_nifty500_list():
    """Download and save the list of NIFTY 500 stocks if not already available"""
    if not os.path.exists(NIFTY500_CSV_PATH):
        logger.info("Downloading NIFTY 500 stock list...")
        # In a real scenario, you'd download this from NSE or use a data provider
        # For demonstration, creating a sample list
        df = pd.DataFrame({
            'Symbol': ['RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS'],
            'Company': ['Reliance Industries', 'Tata Consultancy Services', 'HDFC Bank', 'Infosys', 'ICICI Bank']
        })
        df.to_csv(NIFTY500_CSV_PATH, index=False)
        logger.info(f"Created sample NIFTY 500 list with {len(df)} stocks")
    else:
        logger.info(f"Using existing NIFTY 500 list from {NIFTY500_CSV_PATH}")

def get_stock_data(symbol, interval='5m', period='5d'):
    """Get stock data for the given symbol using yfinance"""
    try:
        logger.info(f"Fetching data for {symbol} ({interval}, {period})")
        stock = yf.Ticker(symbol)
        df = stock.history(period=period, interval=interval)
        if df.empty:
            logger.warning(f"No data available for {symbol}")
            return None
        return df
    except Exception as e:
        logger.error(f"Error fetching data for {symbol}: {e}")
        return None

def calculate_rsi(data, window=14):
    """Calculate RSI for the given data using pandas-ta"""
    if len(data) < window + 1:
        return None
    # Calculate RSI using pandas-ta
    data.ta.rsi(close='Close', length=window, append=True)
    return data[f'RSI_{window}']  # The column name format used by pandas-ta

def update_all_stocks_data():
    """Update data for all stocks including price, % change, weekly RSI, monthly RSI"""
    try:
        logger.info("Updating all stocks data for table display...")
        
        # Read NIFTY 500 stock list
        df = pd.read_csv(NIFTY500_CSV_PATH)
        symbols = df['Symbol'].tolist()
        
        all_stocks = []
        
        for symbol in symbols:
            try:
                # Get daily data for percentage change calculation
                df_daily = get_stock_data(symbol, interval='1d', period='5d')
                if df_daily is None or len(df_daily) < 2:
                    continue
                
                # Get weekly data for weekly RSI
                df_weekly = get_stock_data(symbol, interval='1wk', period='20wk')
                if df_weekly is None or len(df_weekly) < 14:
                    df_weekly = None  # We'll handle this case
                
                # Get monthly data for monthly RSI
                df_monthly = get_stock_data(symbol, interval='1mo', period='24mo')
                if df_monthly is None or len(df_monthly) < 14:
                    df_monthly = None  # We'll handle this case
                
                # Calculate current price and percentage change
                current_price = df_daily['Close'].iloc[-1]
                prev_day_close = df_daily['Close'].iloc[-2]
                pct_change = ((current_price - prev_day_close) / prev_day_close) * 100
                
                # Calculate RSIs
                weekly_rsi = None
                monthly_rsi = None
                
                if df_weekly is not None:
                    df_weekly['RSI'] = calculate_rsi(df_weekly)
                    if 'RSI' in df_weekly.columns and not df_weekly['RSI'].isna().all():
                        weekly_rsi = df_weekly['RSI'].iloc[-1]
                
                if df_monthly is not None:
                    df_monthly['RSI'] = calculate_rsi(df_monthly)
                    if 'RSI' in df_monthly.columns and not df_monthly['RSI'].isna().all():
                        monthly_rsi = df_monthly['RSI'].iloc[-1]
                
                # Get company name
                company_name = df[df['Symbol'] == symbol]['Company'].iloc[0]
                
                # Create stock data object
                stock_data = {
                    'symbol': symbol,
                    'company': company_name,
                    'current_price': current_price,
                    'pct_change': pct_change,
                    'weekly_rsi': weekly_rsi,
                    'monthly_rsi': monthly_rsi,
                    'timestamp': datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')
                }
                
                all_stocks.append(stock_data)
                
            except Exception as e:
                logger.error(f"Error processing {symbol} for all stocks table: {e}")
        
        # Sort stocks by percentage change (descending)
        all_stocks.sort(key=lambda x: x['pct_change'], reverse=True)
        
        # Update results
        results['all_stocks_data'] = all_stocks
        logger.info(f"Updated data for {len(all_stocks)} stocks")
        
    except Exception as e:
        logger.error(f"Error in update_all_stocks_data: {e}")

def check_technical_conditions(symbol):
    """Check if the stock passes all technical conditions"""
    try:
        # Get 5-minute data
        df_5m = get_stock_data(symbol, interval='5m', period='5d')
        if df_5m is None or len(df_5m) < 5:
            return False, "Insufficient 5-minute data"
        
        # Get 30-minute data
        df_30m = get_stock_data(symbol, interval='30m', period='5d')
        if df_30m is None or len(df_30m) < 3:
            return False, "Insufficient 30-minute data"
        
        # Get daily data
        df_1d = get_stock_data(symbol, interval='1d', period='5d')
        if df_1d is None or len(df_1d) < 2:
            return False, "Insufficient daily data"
        
        # Calculate RSI for 5-minute data
        df_5m['RSI'] = calculate_rsi(df_5m)
        
        # Calculate RSI for 30-minute data
        df_30m['RSI'] = calculate_rsi(df_30m)
        
        # Check conditions:
        # [=2] 5 minute close > [=1] 5 minute low
        cond1 = df_5m['Close'].iloc[-2] > df_5m['Low'].iloc[-1]
        
        # [=3] 5 minute close > [=1] 5 minute low
        cond2 = df_5m['Close'].iloc[-3] > df_5m['Low'].iloc[-1]
        
        # [=4] 5 minute close > [=1] 5 minute low
        cond3 = df_5m['Close'].iloc[-4] > df_5m['Low'].iloc[-1]
        
        # [=2] 5 minute close < [=1] 5 minute high
        cond4 = df_5m['Close'].iloc[-2] < df_5m['High'].iloc[-1]
        
        # [=3] 5 minute close < [=1] 5 minute high
        cond5 = df_5m['Close'].iloc[-3] < df_5m['High'].iloc[-1]
        
        # [=4] 5 minute close < [=1] 5 minute high
        cond6 = df_5m['Close'].iloc[-4] < df_5m['High'].iloc[-1]
        
        # [=1] 5 minute RSI(14) > 60
        cond7 = df_5m['RSI'].iloc[-1] > 60
        
        # [ =-1 ] 5 minute RSI(14) <= 60
        cond8 = df_5m['RSI'].iloc[-2] <= 60
        
        # [=1] 30 minute RSI(14) > 60
        cond9 = df_30m['RSI'].iloc[-1] > 60
        
        # [=1] 5 minute close > 1 day ago high
        cond10 = df_5m['Close'].iloc[-1] > df_1d['High'].iloc[-2]
        
        # [=1] 5 minute open > 1 day ago high
        cond11 = df_5m['Open'].iloc[-1] > df_1d['High'].iloc[-2]
        
        all_conditions = [cond1, cond2, cond3, cond4, cond5, cond6, cond7, cond8, cond9, cond10, cond11]
        conditions_met = all(all_conditions)
        
        if conditions_met:
            # Get weekly RSI
            df_weekly = get_stock_data(symbol, interval='1wk', period='20wk')
            weekly_rsi = None
            if df_weekly is not None and len(df_weekly) >= 14:
                df_weekly['RSI'] = calculate_rsi(df_weekly)
                weekly_rsi = df_weekly['RSI'].iloc[-1]

            # Get monthly RSI
            df_monthly = get_stock_data(symbol, interval='1mo', period='24mo')
            monthly_rsi = None
            if df_monthly is not None and len(df_monthly) >= 14:
                df_monthly['RSI'] = calculate_rsi(df_monthly)
                monthly_rsi = df_monthly['RSI'].iloc[-1]
            
            # Calculate percentage change
            prev_close = df_1d['Close'].iloc[-2]
            curr_close = df_1d['Close'].iloc[-1]
            pct_change = ((curr_close - prev_close) / prev_close) * 100
            
            details = {
                'symbol': symbol,
                'company': get_company_name(symbol),
                'current_price': df_5m['Close'].iloc[-1],
                'previous_close': df_5m['Close'].iloc[-2],
                'pct_change': pct_change,
                'day_high': df_1d['High'].iloc[-1],
                'day_low': df_1d['Low'].iloc[-1],
                'rsi_5m': df_5m['RSI'].iloc[-1],
                'rsi_30m': df_30m['RSI'].iloc[-1],
                'weekly_rsi': weekly_rsi,
                'monthly_rsi': monthly_rsi,
                'timestamp': datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')
            }
            return True, details
        
        return False, "Not all conditions met"
    
    except Exception as e:
        logger.error(f"Error checking conditions for {symbol}: {e}")
        return False, f"Error: {str(e)}"

def get_company_name(symbol):
    """Get company name from the CSV file"""
    try:
        df = pd.read_csv(NIFTY500_CSV_PATH)
        company = df[df['Symbol'] == symbol]['Company'].iloc[0]
        return company
    except:
        return symbol

def send_telegram_message(message):
    """Send a message to Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set. Message not sent.")
        return
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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

def format_telegram_message(recommendations):
    """Format recommendations for Telegram message"""
    if not recommendations:
        return "📊 No stocks matching the criteria at this time."
    
    message = "🚀 <b>NIFTY 500 Technical Buy Recommendations</b> 🚀\n\n"
    
    for rec in recommendations:
        message += f"<b>{rec['symbol']} ({rec['company']})</b>\n"
        message += f"💰 Price: ₹{rec['current_price']:.2f} ({rec['pct_change']:.2f}%)\n"
        message += f"📈 RSI (5m): {rec['rsi_5m']:.2f} | RSI (30m): {rec['rsi_30m']:.2f}\n"
        message += f"📊 Weekly RSI: {rec['weekly_rsi']:.2f if rec['weekly_rsi'] else 'N/A'} | Monthly RSI: {rec['monthly_rsi']:.2f if rec['monthly_rsi'] else 'N/A'}\n"
        message += f"❗ <b>BUY RECOMMENDATION</b>\n\n"
    
    message += f"🕒 Updated at: {datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%Y-%m-%d %H:%M:%S')}"
    
    return message

def scan_stocks():
    """Main function to scan stocks based on technical criteria"""
    try:
        logger.info("Starting stock scan...")
        
        # Read NIFTY 500 stock list
        df = pd.read_csv(NIFTY500_CSV_PATH)
        symbols = df['Symbol'].tolist()
        
        buy_recommendations = []
        
        for symbol in symbols:
            passes, details = check_technical_conditions(symbol)
            if passes:
                logger.info(f"🔔 BUY signal for {symbol}")
                buy_recommendations.append(details)
        
        # Update results
        ist_now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
        results['last_update'] = ist_now.strftime('%Y-%m-%d %H:%M:%S')
        results['buy_recommendations'] = buy_recommendations
        
        # Store in history (keep last 10 scans)
        results['data_history'].append({
            'timestamp': results['last_update'],
            'recommendations': len(buy_recommendations),
            'symbols': [rec['symbol'] for rec in buy_recommendations]
        })
        results['data_history'] = results['data_history'][-10:]
        
        # Update market status
        if is_market_open():
            results['market_status'] = 'Open'
            next_scan_time = ist_now + datetime.timedelta(seconds=REFRESH_INTERVAL)
        else:
            results['market_status'] = 'Closed'
            seconds_to_open = time_until_next_market_open()
            next_scan_time = ist_now + datetime.timedelta(seconds=seconds_to_open)
        
        results['next_update'] = next_scan_time.strftime('%Y-%m-%d %H:%M:%S')
        
        # Update all stocks data table
        update_all_stocks_data()
        
        # Send Telegram notification only if market is open or it's the after-hours summary
        if buy_recommendations and (is_market_open() or 
                                   (ist_now.hour == MARKET_CLOSE_HOUR and 
                                    ist_now.minute >= MARKET_CLOSE_MINUTE and 
                                    ist_now.minute < MARKET_CLOSE_MINUTE + 15)):
            message = format_telegram_message(buy_recommendations)
            if not is_market_open():
                message = "📈 <b>END OF DAY SUMMARY</b> 📉\n\n" + message
            send_telegram_message(message)
            logger.info(f"Found {len(buy_recommendations)} stocks matching criteria")
        else:
            logger.info("No stocks match the criteria in this scan")
        
        return buy_recommendations
    
    except Exception as e:
        logger.error(f"Error in scan_stocks: {e}")
        return []

def periodic_scan():
    """Function to run the scan periodically based on market hours"""
    while True:
        try:
            # Run the scan
            scan_stocks()
            
            # Determine when to run the next scan
            if is_market_open():
                # During market hours, scan every REFRESH_INTERVAL
                logger.info(f"Market is open. Next scan in {REFRESH_INTERVAL/60} minutes")
                time.sleep(REFRESH_INTERVAL)
            else:
                # If market just closed, do one final scan
                ist_now = datetime.datetime.now(pytz.timezone('Asia/Kolkata'))
                if (ist_now.hour == MARKET_CLOSE_HOUR and 
                    ist_now.minute >= MARKET_CLOSE_MINUTE and 
                    ist_now.minute < MARKET_CLOSE_MINUTE + 15):
                    logger.info("Market just closed. Running final scan for the day")
                    time.sleep(60)  # Wait a minute before final scan
                    scan_stocks()
                
                # Calculate time until next market open
                seconds_to_open = time_until_next_market_open()
                hours_to_open = seconds_to_open / 3600
                
                logger.info(f"Market is closed. Next scan in {hours_to_open:.2f} hours")
                
                # Sleep until 5 minutes before market opens
                if seconds_to_open > 300:  # More than 5 minutes
                    time.sleep(seconds_to_open - 300)
                else:
                    time.sleep(60)  # Just wait a minute and check again
        
        except Exception as e:
            logger.error(f"Error in periodic scan: {e}")
            time.sleep(60)  # Wait a minute and try again

# Flask routes
@app.route('/')
def index():
    """Render the main page"""
    return render_template('index.html')

@app.route('/api/data')
def get_data():
    """API endpoint to get current recommendations"""
    return jsonify(results)

# Create HTML template directory and files
def create_templates():
    """Create templates directory and HTML files"""
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Create index.html
    with open('templates/index.html', 'w') as f:
        f.write("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>NIFTY 500 Technical Scanner</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.0/font/bootstrap-icons.css">
    <link rel="stylesheet" href="https://cdn.datatables.net/1.13.6/css/dataTables.bootstrap5.min.css">
    <style>
        body { padding-top: 20px; }
        .stock-card { margin-bottom: 20px; transition: all 0.3s; }
        .stock-card:hover { transform: translateY(-5px); box-shadow: 0 10px 20px rgba(0,0,0,0.1); }
        .buy-badge { background-color: #28a745; }
        .header-section { background-color: #f8f9fa; padding: 20px 0; margin-bottom: 30px; }
        .chart-container { height: 300px; margin-top: 30px; }
        .market-open { color: #28a745; font-weight: bold; }
        .market-closed { color: #dc3545; font-weight: bold; }
        .positive-change { color: #28a745; }
        .negative-change { color: #dc3545; }
        .tab-content { padding-top: 20px; }
        .nav-tabs { margin-bottom: 0; }
        .dataTables_wrapper { margin-top: 15px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header-section text-center">
            <h1 class="display-4">NIFTY 500 Technical Scanner</h1>
            <p class="lead">Real-time technical analysis and buy recommendations</p>
            <div class="row justify-content-center">
                <div class="col-md-6">
                    <div class="card mb-3">
                        <div class="card-body">
                            <p>Market Status: <span id="market-status" class="market-closed">Loading...</span></p>
                            <p id="last-update" class="mb-0">Last update: Loading...</p>
                            <p id="next-update" class="mb-0">Next update: Calculating...</p>
                        </div>
                    </div>
                </div>
            </div>
            <div class="d-flex justify-content-center">
                <div class="spinner-border text-primary" id="loading-indicator" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
            </div>
        </div>

        <ul class="nav nav-tabs" id="myTab" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="recommendations-tab" data-bs-toggle="tab" data-bs-target="#recommendations" type="button" role="tab" aria-controls="recommendations" aria-selected="true">Buy Recommendations</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="all-stocks-tab" data-bs-toggle="tab" data-bs-target="#all-stocks" type="button" role="tab" aria-controls="all-stocks" aria-selected="false">All Stocks</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="history-tab" data-bs-toggle="tab" data-bs-target="#history" type="button" role="tab" aria-controls="history" aria-selected="false">History</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="criteria-tab" data-bs-toggle="tab" data-bs-target="#criteria" type="button" role="tab" aria-controls="criteria" aria-selected="false">Criteria</button>
            </li>
        </ul>

        <div class="tab-content" id="myTabContent">
            <!-- BUY RECOMMENDATIONS TAB -->
            <div class="tab-pane fade show active" id="recommendations" role="tabpanel" aria-labelledby="recommendations-tab">
                <div class="alert alert-info" id="no-stocks" style="display:none;">
                    No stocks currently match all the technical criteria. Check back soon!
                </div>
                <div class="row" id="recommendations-container">
                    <!-- Stock cards will be inserted here -->
                </div>
            </div>

            <!-- ALL STOCKS TAB -->
            <div class="tab-pane fade" id="all-stocks" role="tabpanel" aria-labelledby="all-stocks-tab">
                <div class="card">
                    <div class="card-header bg-dark text-white">
                        <h5 class="mb-0">All NIFTY 500 Stocks</h5>
                    </div>
                    <div class="card-body">
                        <div class="table-responsive">
                            <table id="all-stocks-table" class="table table-striped table-hover" style="width:100%">
                                <thead>
                                    <tr>
                                        <th>Symbol</th>
                                        <th>Company</th>
                                        <th>Price (₹)</th>
                                        <th>Change (%)</th>
                                        <th>Weekly RSI</th>
                                        <th>Monthly RSI</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    <!-- Stock data will be inserted here -->
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <!-- HISTORY TAB -->
            <div class="tab-pane fade" id="history" role="tabpanel" aria-labelledby="history-tab">
                <div class="card">
                    <div class="card-header bg-dark text-white">
                        <h5 class="mb-0">Scan History</h5>
                    </div>
                    <div class="card-body">
                        <div class="chart-container">
                            <canvas id="history-chart"></canvas>
                        </div>
                    </div>
                </div>
            </div>

            <!-- CRITERIA TAB -->
            <div class="tab-pane fade" id="criteria" role="tabpanel" aria-labelledby="criteria-tab">
                <div class="card">
                    <div class="card-header bg-dark text-white">
                        <h5 class="mb-0">Technical Criteria</h5>
                    </div>
                    <div class="card-body">
                        <ul>
                            <li>[=2] 5 minute close > [=1] 5 minute low</li>
                            <li>[=3] 5 minute close > [=1] 5 minute low</li>
                            <li>[=4] 5 minute close > [=1] 5 minute low</li>
                            <li>[=2] 5 minute close < [=1] 5 minute high</li>
                            <li>[=3] 5 minute close < [=1] 5 minute high</li>
                            <li>[=4] 5 minute close < [=1] 5 minute high</li>
                            <li>[=1] 5 minute RSI(14) > 60</li>
                            <li>[ =-1 ] 5 minute RSI(14) <= 60</li>
                            <li>[=1] 30 minute RSI(14) > 60</li>
                            <li>[=1] 5 minute close > 1 day ago high</li>
                            <li>[=1] 5 minute open > 1 day ago high</li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>

        <footer class="mt-5 mb-3 text-center text-muted">
            <p>&copy; 2025 NIFTY Technical Analyzer | Data refreshes every 15 minutes during market hours (9:15 AM - 3:30 PM IST)</p>
        </footer>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://code.jquery.com/jquery-3.7.0.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/jquery.dataTables.min.js"></script>
<script src="https://cdn.datatables.net/1.13.6/js/dataTables.bootstrap5.min.js"></script>
<script>
    // Initialize DataTable
    let allStocksTable;
    
    // Function to format percentage change with color
    function formatPercentChange(value) {
        if (value === null || isNaN(value)) return 'N/A';
        const formattedValue = value.toFixed(2) + '%';
        if (value > 0) {
            return `<span class="positive-change">+${formattedValue}</span>`;
        } else if (value < 0) {
            return `<span class="negative-change">${formattedValue}</span>`;
        } else {
            return formattedValue;
        }
    }
    
    // Function to format RSI with color
    function formatRSI(value) {
        if (value === null || isNaN(value)) return 'N/A';
        const formattedValue = value.toFixed(2);
        if (value > 70) {
            return `<span class="text-danger">${formattedValue}</span>`;
        } else if (value < 30) {
            return `<span class="text-success">${formattedValue}</span>`;
        } else {
            return formattedValue;
        }
    }
    
    // Function to update the UI with latest data
    function updateUI(data) {
        // Update market status and timestamps
        $('#market-status').text(data.market_status)
            .removeClass('market-open market-closed')
            .addClass(data.market_status === 'Open' ? 'market-open' : 'market-closed');
        
        $('#last-update').text('Last update: ' + data.last_update);
        $('#next-update').text('Next update: ' + data.next_update);
        
        // Hide loading indicator
        $('#loading-indicator').hide();
        
        // Update recommendations
        const recommendationsContainer = $('#recommendations-container');
        recommendationsContainer.empty();
        
        if (data.buy_recommendations.length === 0) {
            $('#no-stocks').show();
        } else {
            $('#no-stocks').hide();
            
            data.buy_recommendations.forEach(stock => {
                const card = `
                    <div class="col-md-4">
                        <div class="card stock-card">
                            <div class="card-header bg-dark text-white d-flex justify-content-between align-items-center">
                                <h5 class="mb-0">${stock.symbol}</h5>
                                <span class="badge buy-badge">BUY</span>
                            </div>
                            <div class="card-body">
                                <h6 class="card-subtitle mb-2 text-muted">${stock.company}</h6>
                                <p class="card-text">Current Price: ₹${stock.current_price.toFixed(2)}</p>
                                <p class="card-text">Change: ${formatPercentChange(stock.pct_change)}</p>
                                <p class="card-text">Day Range: ₹${stock.day_low.toFixed(2)} - ₹${stock.day_high.toFixed(2)}</p>
                                <hr>
                                <div class="table-responsive">
                                    <table class="table table-sm table-borderless">
                                        <thead>
                                            <tr>
                                                <th>RSI Type</th>
                                                <th>Value</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            <tr>
                                                <td>5 Min RSI</td>
                                                <td>${formatRSI(stock.rsi_5m)}</td>
                                            </tr>
                                            <tr>
                                                <td>30 Min RSI</td>
                                                <td>${formatRSI(stock.rsi_30m)}</td>
                                            </tr>
                                            <tr>
                                                <td>Weekly RSI</td>
                                                <td>${formatRSI(stock.weekly_rsi)}</td>
                                            </tr>
                                            <tr>
                                                <td>Monthly RSI</td>
                                                <td>${formatRSI(stock.monthly_rsi)}</td>
                                            </tr>
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                            <div class="card-footer text-muted">
                                <small>Detected at: ${stock.timestamp}</small>
                            </div>
                        </div>
                    </div>
                `;
                recommendationsContainer.append(card);
            });
        }
        
        // Update all stocks table
        if (allStocksTable) {
            allStocksTable.clear();
            
            data.all_stocks_data.forEach(stock => {
                allStocksTable.row.add([
                    stock.symbol,
                    stock.company,
                    '₹' + stock.current_price.toFixed(2),
                    formatPercentChange(stock.pct_change),
                    formatRSI(stock.weekly_rsi),
                    formatRSI(stock.monthly_rsi)
                ]);
            });
            
            allStocksTable.draw();
        }
        
        // Update history chart
        updateHistoryChart(data.data_history);
    }
    
    // Function to initialize and update the history chart
    function updateHistoryChart(historyData) {
        const ctx = document.getElementById('history-chart').getContext('2d');
        
        // Extract data for chart
        const labels = historyData.map(item => {
            // Format timestamp to show only time
            const date = new Date(item.timestamp);
            return date.toLocaleTimeString();
        });
        
        const counts = historyData.map(item => item.recommendations);
        
        // If chart already exists, destroy it
        if (window.historyChart) {
            window.historyChart.destroy();
        }
        
        // Create new chart
        window.historyChart = new Chart(ctx, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Number of Buy Recommendations',
                    data: counts,
                    backgroundColor: 'rgba(75, 192, 192, 0.6)',
                    borderColor: 'rgba(75, 192, 192, 1)',
                    borderWidth: 1
                }]
            },
            options: {
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            precision: 0
                        }
                    }
                },
                responsive: true,
                maintainAspectRatio: false
            }
        });
    }
    
    // Function to fetch data from the server
    function fetchData() {
        $.ajax({
            url: '/api/data',
            type: 'GET',
            dataType: 'json',
            success: function(data) {
                updateUI(data);
            },
            error: function(err) {
                console.error('Error fetching data:', err);
                $('#loading-indicator').hide();
                alert('Error fetching data. Please check the console for details.');
            }
        });
    }
    
    // Document ready function
    $(document).ready(function() {
        // Initialize DataTable
        allStocksTable = $('#all-stocks-table').DataTable({
            pageLength: 25,
            order: [[3, 'desc']], // Sort by percentage change by default
            responsive: true,
            language: {
                search: "Filter records:",
                lengthMenu: "Show _MENU_ stocks per page",
                info: "Showing _START_ to _END_ of _TOTAL_ stocks"
            }
        });
        
        // Initial data fetch
        fetchData();
        
        // Set up periodic refresh - every 60 seconds
        setInterval(fetchData, 60000);
        
        // Add click event to refresh button
        $('#refresh-button').on('click', function() {
            $('#loading-indicator').show();
            fetchData();
        });
    });
</script>
