# ChartInk Screener App - Updated to handle data extraction issues
# This application:
# 1. Scrapes data from chartink.com/screener/close-above-20-ema
# 2. Sends the results to a Telegram channel
# 3. Displays the data in a table on a web page

import requests
from bs4 import BeautifulSoup
import pandas as pd
import json
import time
import re
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
        
        # Log the page title to verify we're getting the right page
        logger.info(f"Page title: {soup.title.string if soup.title else 'No title found'}")
        
        # Find the script that contains the data - try different approaches
        scripts = soup.find_all('script')
        data_script = None
        
        # Method 1: Direct variable search
        for script in scripts:
            if script.string and 'var screener_results' in script.string:
                data_script = script.string
                logger.info("Found data using method 1: var screener_results")
                break
        
        # Method 2: Look for any JSON data that resembles stock data
        if not data_script:
            for script in scripts:
                if script.string and ('"nsecode"' in script.string or '"name"' in script.string) and ('"close"' in script.string):
                    data_script = script.string
                    logger.info("Found data using method 2: JSON pattern matching")
                    break
        
        # Method 3: Try to find the data in a script tag by pattern
        if not data_script:
            pattern = r'var\s+\w+\s*=\s*(\[.*?\]);'
            for script in scripts:
                if script.string:
                    matches = re.findall(pattern, script.string, re.DOTALL)
                    for match in matches:
                        try:
                            test_data = json.loads(match)
                            if isinstance(test_data, list) and len(test_data) > 0:
                                if isinstance(test_data[0], dict) and ('nsecode' in test_data[0] or 'name' in test_data[0]):
                                    data_script = match
                                    logger.info("Found data using method 3: regex pattern matching")
                                    break
                        except:
                            continue
                if data_script:
                    break
                    
        # Method 4: Look for data in API calls
        if not data_script:
            api_url = 'https://chartink.com/screener/process'
            condition = 'close > ema(close,20)'
            payload = {
                'scan_clause': condition
            }
            
            try:
                api_response = requests.post(api_url, headers=headers, data=payload, timeout=30)
                api_response.raise_for_status()
                data_script = api_response.text
                logger.info("Found data using method 4: API call")
            except Exception as api_e:
                logger.error(f"API call failed: {str(api_e)}")
        
        if not data_script:
            logger.error("Could not find screener results in the page")
            # Save the HTML for debugging
            with open('debug_page.html', 'w', encoding='utf-8') as f:
                f.write(response.text)
            logger.info("Saved HTML to debug_page.html for inspection")
            return None
        
        # Extract JSON data from the script
        json_data = None
        
        # Try different approaches to extract the JSON
        try:
            # Method 1: Simple var extraction
            if 'var screener_results = ' in data_script:
                json_str = data_script.split('var screener_results = ')[1].split(';')[0]
                json_data = json.loads(json_str)
            # Method 2: If it's already JSON
            elif data_script.strip().startswith('[') and data_script.strip().endswith(']'):
                json_data = json.loads(data_script)
            # Method 3: If it's in an API response format
            elif '"data"' in data_script:
                api_data = json.loads(data_script)
                if 'data' in api_data:
                    json_data = api_data['data']
            
            if not json_data:
                # Last resort - try to find any JSON array in the text
                pattern = r'\[(?:\{.*?\}(?:,|))+\]'
                matches = re.findall(pattern, data_script, re.DOTALL)
                for match in matches:
                    try:
                        test_data = json.loads(match)
                        if isinstance(test_data, list) and len(test_data) > 0:
                            if isinstance(test_data[0], dict):
                                json_data = test_data
                                break
                    except:
                        continue
            
            if not json_data:
                logger.error("Could not parse JSON data")
                return None
                
        except Exception as e:
            logger.error(f"Error parsing JSON data: {str(e)}")
            logger.error(f"Data script snippet: {data_script[:200]}...")  # Log a snippet for debugging
            return None
        
        # Process the data - adapt field names based on what we have
        stocks = []
        for item in json_data:
            stock = {}
            # Check for common field names and alternatives
            stock['nsecode'] = item.get('nsecode', item.get('symbol', item.get('ticker', '')))
            stock['name'] = item.get('name', item.get('company_name', stock['nsecode']))
            stock['close'] = float(item.get('close', item.get('last_price', item.get('ltp', 0))))
            stock['per_chg'] = float(item.get('per_chg', item.get('change_percent', item.get('percent_change', 0))))
            stock['volume'] = int(item.get('volume', item.get('volume_traded', 0)))
            stock['ema_20'] = float(item.get('ema_20', item.get('ema', stock['close'])))
            stock['market_cap'] = float(item.get('market_cap', 0))
            stock['industry'] = item.get('industry', item.get('sector', ''))
            
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

# Adding a function to generate sample data if we can't fetch real data
def generate_sample_data():
    """Generate sample data for testing when scraping fails."""
    logger.info("Generating sample data for testing")
    
    sample_data = [
        {'nsecode': 'RELIANCE', 'name': 'Reliance Industries', 'close': 2456.35, 'per_chg': 1.25, 'volume': 1456789, 'ema_20': 2400.50, 'market_cap': 1650000, 'industry': 'Oil & Gas'},
        {'nsecode': 'TCS', 'name': 'Tata Consultancy Services', 'close': 3570.80, 'per_chg': 0.75, 'volume': 876543, 'ema_20': 3520.30, 'market_cap': 1320000, 'industry': 'IT'},
        {'nsecode': 'HDFCBANK', 'name': 'HDFC Bank', 'close': 1680.45, 'per_chg': -0.50, 'volume': 2345678, 'ema_20': 1690.20, 'market_cap': 930000, 'industry': 'Banking'},
        {'nsecode': 'INFY', 'name': 'Infosys', 'close': 1740.30, 'per_chg': 0.90, 'volume': 1234567, 'ema_20': 1720.15, 'market_cap': 740000, 'industry': 'IT'},
        {'nsecode': 'ICICIBANK', 'name': 'ICICI Bank', 'close': 950.75, 'per_chg': 0.25, 'volume': 3456789, 'ema_20': 940.50, 'market_cap': 660000, 'industry': 'Banking'},
        {'nsecode': 'HINDUNILVR', 'name': 'Hindustan Unilever', 'close': 2540.60, 'per_chg': -0.30, 'volume': 567890, 'ema_20': 2550.40, 'market_cap': 598000, 'industry': 'FMCG'},
        {'nsecode': 'SBIN', 'name': 'State Bank of India', 'close': 595.25, 'per_chg': 1.75, 'volume': 5678901, 'ema_20': 580.10, 'market_cap': 531000, 'industry': 'Banking'},
        {'nsecode': 'BAJFINANCE', 'name': 'Bajaj Finance', 'close': 7120.40, 'per_chg': -1.20, 'volume': 890123, 'ema_20': 7200.30, 'market_cap': 430000, 'industry': 'Finance'},
        {'nsecode': 'BHARTIARTL', 'name': 'Bharti Airtel', 'close': 850.30, 'per_chg': 0.65, 'volume': 2345678, 'ema_20': 840.20, 'market_cap': 475000, 'industry': 'Telecom'},
        {'nsecode': 'KOTAKBANK', 'name': 'Kotak Mahindra Bank', 'close': 1920.15, 'per_chg': -0.40, 'volume': 789012, 'ema_20': 1930.25, 'market_cap': 380000, 'industry': 'Banking'},
        {'nsecode': 'ADANIPORTS', 'name': 'Adani Ports', 'close': 780.50, 'per_chg': 2.80, 'volume': 3456789, 'ema_20': 760.25, 'market_cap': 168000, 'industry': 'Infrastructure'},
        {'nsecode': 'ASIANPAINT', 'name': 'Asian Paints', 'close': 3450.75, 'per_chg': 0.35, 'volume': 456789, 'ema_20': 3435.60, 'market_cap': 331000, 'industry': 'Consumer Goods'},
        {'nsecode': 'AXISBANK', 'name': 'Axis Bank', 'close': 1040.20, 'per_chg': 1.15, 'volume': 2345678, 'ema_20': 1025.80, 'market_cap': 320000, 'industry': 'Banking'},
        {'nsecode': 'TATASTEEL', 'name': 'Tata Steel', 'close': 128.35, 'per_chg': 1.90, 'volume': 7890123, 'ema_20': 125.80, 'market_cap': 156000, 'industry': 'Metals'},
        {'nsecode': 'MARUTI', 'name': 'Maruti Suzuki', 'close': 10220.50, 'per_chg': -0.75, 'volume': 345678, 'ema_20': 10300.25, 'market_cap': 308000, 'industry': 'Automobile'},
    ]
    
    return pd.DataFrame(sample_data)

# Flask routes
@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

@app.route('/api/data')
def api_data():
    """API endpoint to get the data."""
    df = get_data()
    
    # If we couldn't fetch real data, use sample data
    if df is None:
        logger.warning("Using sample data as fallback")
        df = generate_sample_data()
        cached_data['data'] = df
        cached_data['timestamp'] = time.time()
        
        # Send update to Telegram with sample data notice
        message = "⚠️ SAMPLE DATA: Real data could not be fetched from ChartInk\n\n" + format_telegram_message(df)
        send_to_telegram(message)
    
    return jsonify({
        'data': df.to_dict('records'),
        'updated_at': datetime.fromtimestamp(cached_data['timestamp']).strftime('%Y-%m-%d %H:%M:%S') if cached_data['timestamp'] else None,
        'is_sample': df is cached_data['data'] and cached_data['data'] is not None and len(df) <= 15  # Check if we're using sample data
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
        .sample-data-notice { 
            background-color: #fff3cd; 
            color: #856404; 
            border: 1px solid #ffeeba; 
            padding: 10px; 
            border-radius: 5px; 
            margin-bottom: 15px; 
            display: none;
        }
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
                        <div id="sampleDataNotice" class="sample-data-notice">
                            <i class="bi bi-exclamation-triangle"></i> 
                            <strong>Note:</strong> Displaying sample data as ChartInk data could not be fetched. This data is for demonstration purposes only.
                        </div>
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
                        
                        // Show sample data notice if needed
                        if (response.is_sample) {
                            $('#sampleDataNotice').show();
                        } else {
                            $('#sampleDataNotice').hide();
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
        
        # If real data couldn't be fetched, use sample data
        if df is None:
            logger.warning("Using sample data for scheduled update")
            df = generate_sample_data()
            
        cached_data['data'] = df
        cached_data['timestamp'] = time.time()
        
        # Send update to Telegram
        is_sample = df is not None and len(df) <= 15  # Simple check for sample data
        prefix = "⚠️ SAMPLE DATA: " if is_sample else ""
        message = prefix + format_telegram_message(df)
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
