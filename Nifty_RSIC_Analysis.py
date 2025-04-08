import pandas as pd
import pandas_ta as ta 
import numpy as np
import yfinance as yf
import datetime
import time
import requests
import os
from flask import Flask, render_template, jsonify
import logging
import threading

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
REFRESH_INTERVAL = 15 * 60  # 15 minutes in seconds
NIFTY500_CSV_PATH = "nifty500_symbols.csv"

# Initialize Flask app
app = Flask(__name__, template_folder='templates')

# Store results that will be displayed on the web page
results = {
    'last_update': None,
    'buy_recommendations': [],
    'data_history': []  # To store historical recommendations
}

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
                'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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
    
    message += f"🕒 Updated at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    
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
        results['last_update'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        results['buy_recommendations'] = buy_recommendations
        
        # Store in history (keep last 10 scans)
        results['data_history'].append({
            'timestamp': results['last_update'],
            'recommendations': len(buy_recommendations),
            'symbols': [rec['symbol'] for rec in buy_recommendations]
        })
        results['data_history'] = results['data_history'][-10:]
        
        # Send Telegram notification
        if buy_recommendations:
            message = format_telegram_message(buy_recommendations)
            send_telegram_message(message)
            logger.info(f"Found {len(buy_recommendations)} stocks matching criteria")
        else:
            logger.info("No stocks match the criteria in this scan")
        
        return buy_recommendations
    
    except Exception as e:
        logger.error(f"Error in scan_stocks: {e}")
        return []

def periodic_scan():
    """Function to run the scan periodically"""
    while True:
        try:
            scan_stocks()
            logger.info(f"Sleeping for {REFRESH_INTERVAL/60} minutes before next scan")
            time.sleep(REFRESH_INTERVAL)
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header-section text-center">
            <h1 class="display-4">NIFTY 500 Technical Scanner</h1>
            <p class="lead">Real-time technical analysis and buy recommendations</p>
            <p id="last-update" class="text-muted">Last update: Loading...</p>
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
            <p>&copy; 2025 NIFTY Technical Analyzer | Data refreshes every 15 minutes</p>
        </footer>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        let historyChart = null;

        // Function to fetch data and update UI
        function fetchAndUpdateData() {
            document.getElementById('loading-indicator').style.display = 'block';
            
            fetch('/api/data')
                .then(response => response.json())
                .then(data => {
                    document.getElementById('loading-indicator').style.display = 'none';
                    
                    // Update last update time
                    document.getElementById('last-update').textContent = `Last update: ${data.last_update || 'Not yet updated'}`;
                    
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
        
        // Refresh every minute to show countdown
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
