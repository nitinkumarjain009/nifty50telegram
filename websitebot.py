# ChartInk Screener App
# This application:
# 1. Scrapes data from chartink.com/screener/close-above-20-ema
# 2. Sends the results to a Telegram channel
# 3. Displays the data in a table on a web page

import requests
from bs4 import BeautifulSoup
import pandas as pd
import json
import time
from datetime import datetime
import logging
import os
from flask import Flask, render_template, jsonify

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("chartink_screener.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', 'YOUR_TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', 'YOUR_TELEGRAM_CHAT_ID')

# Flask app initialization
app = Flask(__name__)

# Cache for the data
cached_data = {
    'timestamp': None,
    'data': None
}

def fetch_chartink_data():
    """Fetch data from ChartInk screener."""
    logger.info("Fetching data from ChartInk...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://chartink.com/'
    }
    
    url = 'https://chartink.com/screener/close-above-20-ema'
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find the script that contains the data
        scripts = soup.find_all('script')
        data_script = None
        
        for script in scripts:
            if script.string and 'var screener_results' in script.string:
                data_script = script.string
                break
        
        if not data_script:
            logger.error("Could not find screener results in the page")
            return None
        
        # Extract JSON data from the script
        json_str = data_script.split('var screener_results = ')[1].split(';')[0]
        data = json.loads(json_str)
        
        # Process the data
        stocks = []
        for item in data:
            stock = {
                'nsecode': item.get('nsecode', ''),
                'name': item.get('name', ''),
                'close': item.get('close', 0),
                'per_chg': item.get('per_chg', 0),
                'volume': item.get('volume', 0),
                'ema_20': item.get('ema_20', 0),
                'market_cap': item.get('market_cap', 0),
                'industry': item.get('industry', '')
            }
            stocks.append(stock)
        
        df = pd.DataFrame(stocks)
        logger.info(f"Successfully fetched {len(df)} stocks from ChartInk")
        return df
    
    except Exception as e:
        logger.error(f"Error fetching data from ChartInk: {str(e)}")
        return None

def send_to_telegram(message):
    """Send message to Telegram channel."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == 'YOUR_TELEGRAM_BOT_TOKEN':
        logger.warning("Telegram token not set. Skipping Telegram notification.")
        return False
    
    try:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(api_url, data=payload, timeout=10)
        response.raise_for_status()
        logger.info("Message sent to Telegram successfully")
        return True
    except Exception as e:
        logger.error(f"Error sending message to Telegram: {str(e)}")
        return False

def format_telegram_message(df):
    """Format DataFrame for Telegram message."""
    if df is None or df.empty:
        return "No stocks found matching the criteria."
    
    # Format the message
    message = "<b>🔍 ChartInk Screener: Stocks Above 20 EMA</b>\n\n"
    message += f"<i>Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>\n\n"
    
    # Add top 10 stocks by percentage change
    top_stocks = df.sort_values(by='per_chg', ascending=False).head(10)
    message += "<b>Top 10 Gainers:</b>\n"
    
    for _, row in top_stocks.iterrows():
        message += f"• {row['nsecode']}: {row['close']:.2f} ({row['per_chg']:.2f}%)\n"
    
    message += "\n<a href='https://chartink.com/screener/close-above-20-ema'>View full list on ChartInk</a>"
    
    return message

def get_data():
    """Get data with caching."""
    current_time = time.time()
    
    # Cache data for 15 minutes
    if cached_data['data'] is None or cached_data['timestamp'] is None or \
       (current_time - cached_data['timestamp'] > 900):  # 15 minutes = 900 seconds
        
        logger.info("Cache expired, fetching fresh data")
        df = fetch_chartink_data()
        
        if df is not None:
            cached_data['data'] = df
            cached_data['timestamp'] = current_time
            
            # Send update to Telegram
            message = format_telegram_message(df)
            send_to_telegram(message)
    else:
        logger.info("Using cached data")
    
    return cached_data['data']

# Flask routes
@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

@app.route('/api/data')
def api_data():
    """API endpoint to get the data."""
    df = get_data()
    if df is None:
        return jsonify({'error': 'Failed to fetch data'}), 500
    
    return jsonify({
        'data': df.to_dict('records'),
        'updated_at': datetime.fromtimestamp(cached_data['timestamp']).strftime('%Y-%m-%d %H:%M:%S') if cached_data['timestamp'] else None
    })

# Create HTML templates directory and template file
os.makedirs('templates', exist_ok=True)

with open('templates/index.html', 'w') as f:
    f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ChartInk Screener - Stocks Above 20 EMA</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <link rel="stylesheet" href="https://cdn.datatables.net/1.13.1/css/dataTables.bootstrap5.min.css">
    <style>
        body { padding-top: 20px; }
        .table-responsive { margin-top: 20px; }
        .up { color: green; }
        .down { color: red; }
        .last-updated { font-style: italic; font-size: 0.9rem; margin-bottom: 15px; }
        .loading { text-align: center; padding: 40px; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="text-center mb-4">ChartInk Screener: Stocks Above 20 EMA</h1>
        
        <div class="row">
            <div class="col-md-12">
                <div class="card">
                    <div class="card-header bg-primary text-white">
                        <h5 class="card-title mb-0">Screener Results</h5>
                    </div>
                    <div class="card-body">
                        <div id="lastUpdated" class="last-updated">Loading data...</div>
                        <div id="loading" class="loading">
                            <div class="spinner-border text-primary" role="status">
                                <span class="visually-hidden">Loading...</span>
                            </div>
                            <p class="mt-2">Loading data from ChartInk...</p>
                        </div>
                        <div id="dataContainer" style="display: none;">
                            <div class="table-responsive">
                                <table id="stocksTable" class="table table-striped table-hover">
                                    <thead>
                                        <tr>
                                            <th>NSE Code</th>
                                            <th>Name</th>
                                            <th>Close</th>
                                            <th>Change %</th>
                                            <th>Volume</th>
                                            <th>20 EMA</th>
                                            <th>Market Cap (Cr)</th>
                                            <th>Industry</th>
                                        </tr>
                                    </thead>
                                    <tbody id="tableBody">
                                    </tbody>
                                </table>
                            </div>
                        </div>
                        <div id="errorMessage" class="alert alert-danger" style="display: none;"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.1/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.1/js/dataTables.bootstrap5.min.js"></script>
    
    <script>
        $(document).ready(function() {
            let dataTable = null;
            
            function loadData() {
                $.ajax({
                    url: '/api/data',
                    type: 'GET',
                    dataType: 'json',
                    success: function(response) {
                        $('#loading').hide();
                        $('#dataContainer').show();
                        
                        if (response.updated_at) {
                            $('#lastUpdated').text('Last updated: ' + response.updated_at);
                        }
                        
                        const tableBody = $('#tableBody');
                        tableBody.empty();
                        
                        response.data.forEach(stock => {
                            const changeClass = stock.per_chg >= 0 ? 'up' : 'down';
                            const changeIcon = stock.per_chg >= 0 ? 
                                '<i class="bi bi-arrow-up-circle-fill"></i>' : 
                                '<i class="bi bi-arrow-down-circle-fill"></i>';
                                
                            const row = `
                                <tr>
                                    <td>${stock.nsecode}</td>
                                    <td>${stock.name}</td>
                                    <td>${parseFloat(stock.close).toFixed(2)}</td>
                                    <td class="${changeClass}">${changeIcon} ${parseFloat(stock.per_chg).toFixed(2)}%</td>
                                    <td>${parseInt(stock.volume).toLocaleString()}</td>
                                    <td>${parseFloat(stock.ema_20).toFixed(2)}</td>
                                    <td>${parseFloat(stock.market_cap).toLocaleString()}</td>
                                    <td>${stock.industry}</td>
                                </tr>
                            `;
                            tableBody.append(row);
                        });
                        
                        // Initialize or refresh DataTable
                        if (dataTable) {
                            dataTable.destroy();
                        }
                        
                        dataTable = $('#stocksTable').DataTable({
                            order: [[3, 'desc']], // Sort by change % by default
                            pageLength: 25,
                            lengthMenu: [10, 25, 50, 100],
                            responsive: true
                        });
                    },
                    error: function(xhr, status, error) {
                        $('#loading').hide();
                        $('#errorMessage').text('Error loading data: ' + error).show();
                    }
                });
            }
            
            // Load data initially
            loadData();
            
            // Refresh data every 15 minutes
            setInterval(loadData, 15 * 60 * 1000);
        });
    </script>
</body>
</html>
    ''')

def scheduled_update():
    """Function to run scheduled updates."""
    while True:
        logger.info("Running scheduled update")
        df = fetch_chartink_data()
        if df is not None:
            cached_data['data'] = df
            cached_data['timestamp'] = time.time()
            
            # Send update to Telegram
            message = format_telegram_message(df)
            send_to_telegram(message)
        
        # Sleep for 1 hour
        time.sleep(3600)  # 1 hour = 3600 seconds

if __name__ == "__main__":
    import threading
    
    # Start the scheduled update in a separate thread
    scheduler_thread = threading.Thread(target=scheduled_update)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    # Start the Flask app
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
