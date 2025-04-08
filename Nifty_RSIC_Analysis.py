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
    'next_update': None  # When the next scan will occur
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
        logger.info(f"Fetching data for {symbol}")
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
            details = {
                'symbol': symbol,
                'company': get_company_name(symbol),
                'current_price': df_5m['Close'].iloc[-1],
                'previous_close': df_5m['Close'].iloc[-2],
                'day_high': df_1d['High'].iloc[-1],
                'day_low': df_1d['Low'].iloc[-1],
                'rsi_5m': df_5m['RSI'].iloc[-1],
                'rsi_30m': df_30m['RSI'].iloc[-1],
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
        message += f"💰 Price: ₹{rec['current_price']:.2f}\n"
        message += f"📈 RSI (5m): {rec['rsi_5m']:.2f}\n"
        message += f"📈 RSI (30m): {rec['rsi_30m']:.2f}\n"
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
    <style>
        body { padding-top: 20px; }
        .stock-card { margin-bottom: 20px; transition: all 0.3s; }
        .stock-card:hover { transform: translateY(-5px); box-shadow: 0 10px 20px rgba(0,0,0,0.1); }
        .buy-badge { background-color: #28a745; }
        .header-section { background-color: #f8f9fa; padding: 20px 0; margin-bottom: 30px; }
        .chart-container { height: 300px; margin-top: 30px; }
        .market-open { color: #28a745; font-weight: bold; }
        .market-closed { color: #dc3545; font-weight: bold; }
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

        <div class="row">
            <div class="col-md-12">
                <div class="alert alert-info" id="no-stocks" style="display:none;">
                    No stocks currently match all the technical criteria. Check back soon!
                </div>
            </div>
        </div>

        <div class="row" id="recommendations-container">
            <!-- Stock cards will be inserted here -->
        </div>

        <div class="row mt-4">
            <div class="col-md-12">
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
        </div>

        <div class="row mt-4">
            <div class="col-md-12">
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
    <script>
        let historyChart = null;
        let countdownInterval = null;

        // Function to format remaining time
        function formatCountdown(targetTimeStr) {
            const now = new Date();
            const target = new Date(targetTimeStr);
            
            // Calculate difference in seconds
            let diff = Math.floor((target - now) / 1000);
            
            if (diff <= 0) return "Any moment now...";
            
            const hours = Math.floor(diff / 3600);
            diff -= hours * 3600;
            const minutes = Math.floor(diff / 60);
            const seconds = diff - minutes * 60;
            
            let result = "";
            if (hours > 0) result += `${hours}h `;
            if (minutes > 0 || hours > 0) result += `${minutes}m `;
            result += `${seconds}s`;
            
            return result;
        }

        // Update countdown timer
        function updateCountdown(nextUpdateTime) {
            if (countdownInterval) {
                clearInterval(countdownInterval);
            }
            
            function update() {
                const countdownStr = formatCountdown(nextUpdateTime);
                document.getElementById('next-update').textContent = `Next update: ${countdownStr}`;
            }
            
            update(); // Initial update
            countdownInterval = setInterval(update, 1000);
        }

        // Function to fetch data and update UI
        function fetchAndUpdateData() {
            document.getElementById('loading-indicator').style.display = 'block';
            
            fetch('/api/data')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('loading-indicator').style.display = 'none';
                    
                    // Update last update time
                    document.getElementById('last-update').textContent = `Last update: ${data.last_update || 'Not yet updated'}`;
                    
                    // Update market status
                    const marketStatusElement = document.getElementById('market-status');
                    marketStatusElement.textContent = data.market_status;
                    if (data.market_status === 'Open') {
                        marketStatusElement.className = 'market-open';
                    } else {
                        marketStatusElement.className = 'market-closed';
                    }
                    
                    // Update next update countdown
                    if (data.next_update) {
                        updateCountdown(data.next_update);
                    }
                    
                    // Clear existing cards
                    const container = document.getElementById('recommendations-container');
                    container.innerHTML = '';
                    
                    // Show/hide no stocks message
                    const noStocksAlert = document.getElementById('no-stocks');
                    if (!data.buy_recommendations || data.buy_recommendations.length === 0) {
                        noStocksAlert.style.display = 'block';
                    } else {
                        noStocksAlert.style.display = 'none';
                        
                        // Add stock cards
                        data.buy_recommendations.forEach(stock => {
                            const card = document.createElement('div');
                            card.className = 'col-md-4';
                            card.innerHTML = `
                                <div class="card stock-card">
                                    <div class="card-header d-flex justify-content-between align-items-center">
                                        <h5 class="mb-0">${stock.symbol}</h5>
                                        <span class="badge buy-badge">BUY</span>
                                    </div>
                                    <div class="card-body">
                                        <h6 class="card-subtitle mb-2 text-muted">${stock.company}</h6>
                                        <p class="card-text">Current Price: ₹${stock.current_price.toFixed(2)}</p>
                                        <div class="row">
                                            <div class="col-6">
                                                <p class="mb-1">RSI (5m): ${stock.rsi_5m.toFixed(2)}</p>
                                                <p class="mb-1">RSI (30m): ${stock.rsi_30m.toFixed(2)}</p>
                                            </div>
                                            <div class="col-6">
                                                <p class="mb-1">Day High: ₹${stock.day_high.toFixed(2)}</p>
                                                <p class="mb-1">Day Low: ₹${stock.day_low.toFixed(2)}</p>
                                            </div>
                                        </div>
                                    </div>
                                    <div class="card-footer text-muted">
                                        Signal generated: ${stock.timestamp}
                                    </div>
                                </div>
                            `;
                            container.appendChild(card);
                        });
                    }
                    
                    // Update history chart
                    updateHistoryChart(data.data_history);
                })
                .catch(error => {
                    console.error('Error fetching data:', error);
                    document.getElementById('loading-indicator').style.display = 'none';
                });
        }
        
        // Function to update history chart
        function updateHistoryChart(historyData) {
            if (!historyData || historyData.length === 0) return;
            
            const labels = historyData.map(item => {
                const date = new Date(item.timestamp);
                return date.toLocaleTimeString();
            });
            
            const counts = historyData.map(item => item.recommendations);
            
            if (historyChart) {
                historyChart.data.labels = labels;
                historyChart.data.datasets[0].data = counts;
                historyChart.update();
            } else {
                const ctx = document.getElementById('history-chart').getContext('2d');
                historyChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Number of Recommendations',
                            data: counts,
                            backgroundColor: 'rgba(54, 162, 235, 0.2)',
                            borderColor: 'rgba(54, 162, 235, 1)',
                            borderWidth: 2,
                            tension: 0.3
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {
                            y: {
                                beginAtZero: true,
                                ticks: {
                                    precision: 0
                                }
                            }
                        }
                    }
                });
            }
        }

        // Initial fetch
        fetchAndUpdateData();
        
        // Refresh every minute to show countdown and changes
        setInterval(fetchAndUpdateData, 60000);
    </script>
</body>
</html>
        """)
    
    logger.info("Created template files")

if __name__ == "__main__":
    try:
        # Create template directory and files
        create_templates()
        
        # Download NIFTY 500 stock list
        download_nifty500_list()
        
        # Run initial scan
        scan_stocks()
        
        # Start periodic scan in a separate thread
        scan_thread = threading.Thread(target=periodic_scan)
        scan_thread.daemon = True
        scan_thread.start()
        
        # Start Flask app
        port = int(os.environ.get("PORT", 5000))
        app.run(host="0.0.0.0", port=port)
        
    except Exception as e:
        logger.error(f"Error in main: {e}")
