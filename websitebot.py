#!/usr/bin/env python3
"""
Enhanced Stock Recommendations Bot
- Reads stock data from nifty50_stocks.csv
- Generates buy/sell recommendations based on ADX and RSI
- Fetches additional data from nifty500-trading-bot.onrender.com
- Sends recommendations to Telegram
- Hosts a webpage displaying the recommendations with additional details
"""

import requests
import json
import time
import logging
import sys
import pandas as pd
import os
from datetime import datetime
from flask import Flask, render_template, jsonify
import threading
import csv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("stock_bot.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("StockBot")

# Configuration
STOCK_API_URL = "https://nifty500-trading-bot.onrender.com/api/data"
TELEGRAM_BOT_TOKEN = "8017759392:AAEwM-W-y83lLXTjlPl8sC_aBmizuIrFXnU"
TELEGRAM_CHAT_ID = "711856868"
TELEGRAM_CHANNEL = "@Stockniftybot"
CHECK_INTERVAL = 3600  # Check every hour (in seconds)
WEB_PORT = int(os.environ.get("PORT", 5000))  # Use environment PORT or default to 5000
CSV_FILE_PATH = "nifty50_stocks.csv"  # Path to the CSV file

# Store data for the web server
latest_data = {
    "buy_recommendations": [],
    "sell_recommendations": [],
    "last_updated": None
}

# Initialize Flask app
app = Flask(__name__)

def create_csv_if_not_exists():
    """Create the CSV file with headers if it doesn't exist"""
    if not os.path.exists(CSV_FILE_PATH):
        logger.info(f"CSV file not found. Creating {CSV_FILE_PATH} with headers")
        with open(CSV_FILE_PATH, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['symbol', 'close', 'rsi', 'adx', 'volume', 'change_percent'])
        logger.info(f"Created {CSV_FILE_PATH}. Please populate it with stock data")

def read_stock_data_from_csv():
    """Read stock data from the CSV file"""
    try:
        create_csv_if_not_exists()
        
        if os.path.getsize(CSV_FILE_PATH) == 0:
            logger.warning(f"CSV file {CSV_FILE_PATH} is empty")
            return []
            
        df = pd.read_csv(CSV_FILE_PATH)
        logger.info(f"Successfully read {len(df)} stocks from CSV file")
        return df.to_dict('records')
    except Exception as e:
        logger.error(f"Error reading CSV file: {e}")
        return []

def calculate_target_price(stock_data, recommendation_type):
    """Calculate target price based on RSI, ADX and current price
    
    For buy recommendations:
    - RSI < 30 (oversold): Target = Current price + 5%
    - ADX > 25 (strong trend): Target = Current price + 4%
    - Otherwise: Target = Current price + 3%
    
    For sell recommendations:
    - RSI > 70 (overbought): Target = Current price - 5%
    - ADX > 25 (strong trend): Target = Current price - 4%
    - Otherwise: Target = Current price - 3%
    """
    price = stock_data.get('close', 0)
    rsi = stock_data.get('rsi', 50)  # Default to 50 if not available
    adx = stock_data.get('adx', 20)  # Default to 20 if not available
    
    if recommendation_type == 'buy':
        if rsi < 30:
            # More oversold = higher potential upside
            target = price * 1.05
        elif adx > 25:
            # Strong trend = good potential upside
            target = price * 1.04
        else:
            # Moderate conditions
            target = price * 1.03
    else:  # sell recommendation
        if rsi > 70:
            # More overbought = higher potential downside
            target = price * 0.95
        elif adx > 25:
            # Strong trend = good potential downside
            target = price * 0.96
        else:
            # Moderate conditions
            target = price * 0.97
    
    return round(target, 2)

def analyze_stocks(stocks):
    """
    Analyze stocks and separate into buy and sell recommendations based on ADX and RSI
    
    Buy criteria:
    - RSI < 40 (approaching oversold)
    - ADX > 20 (trending)
    
    Sell criteria:
    - RSI > 60 (approaching overbought)
    - ADX > 20 (trending)
    """
    buy_recommendations = []
    sell_recommendations = []
    
    for stock in stocks:
        rsi = stock.get('rsi', 50)
        adx = stock.get('adx', 15)
        
        # Calculate signal strength on a scale of 1-10 based on RSI and ADX
        if rsi < 40 and adx > 20:
            # Buy signal
            # Calculate stronger signal when RSI lower (more oversold) and ADX higher (stronger trend)
            signal_strength = round(((40 - rsi) / 10) * 0.6 + (min(adx, 50) / 50) * 0.4 * 10)
            signal_strength = max(1, min(10, signal_strength))  # Ensure between 1-10
            
            stock['signal_strength'] = signal_strength
            stock['target_price'] = calculate_target_price(stock, 'buy')
            buy_recommendations.append(stock)
            
        elif rsi > 60 and adx > 20:
            # Sell signal
            # Calculate stronger signal when RSI higher (more overbought) and ADX higher (stronger trend)
            signal_strength = round(((rsi - 60) / 10) * 0.6 + (min(adx, 50) / 50) * 0.4 * 10)
            signal_strength = max(1, min(10, signal_strength))  # Ensure between 1-10
            
            stock['signal_strength'] = signal_strength
            stock['target_price'] = calculate_target_price(stock, 'sell')
            sell_recommendations.append(stock)
    
    # Sort by signal strength (highest first)
    buy_recommendations.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
    sell_recommendations.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
    
    return {
        'buy_recommendations': buy_recommendations,
        'sell_recommendations': sell_recommendations,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    }

def fetch_additional_data():
    """Fetch additional stock data from the API"""
    try:
        logger.info("Fetching additional stock data from API...")
        response = requests.get(STOCK_API_URL, timeout=30)
        response.raise_for_status()  # Raise exception for 4XX/5XX responses
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching additional stock data: {e}")
        return None

def merge_stock_data(csv_data, api_data):
    """Merge stock data from CSV and API"""
    if not api_data or 'buy_recommendations' not in api_data:
        return csv_data
    
    # Create a dictionary of symbols from API data for easy lookup
    api_stocks = {}
    for stock in api_data.get('buy_recommendations', []):
        api_stocks[stock.get('symbol')] = stock
    
    # Enhance CSV data with any additional fields from API data
    for stock in csv_data.get('buy_recommendations', []) + csv_data.get('sell_recommendations', []):
        symbol = stock.get('symbol')
        if symbol in api_stocks:
            # Copy any missing fields from API data
            for key, value in api_stocks[symbol].items():
                if key not in stock or stock[key] is None:
                    stock[key] = value
    
    return csv_data

def format_recommendations(data, recommendation_type):
    """Format recommendations into a readable message for Telegram"""
    if not data:
        return f"No {recommendation_type} recommendations available."
    
    if recommendation_type == 'buy':
        recommendations = data.get('buy_recommendations', [])
        emoji = "🟢" 
        title = "STOCK BUY RECOMMENDATIONS"
    else:
        recommendations = data.get('sell_recommendations', [])
        emoji = "🔴"
        title = "STOCK SELL RECOMMENDATIONS"
    
    if not recommendations:
        return f"No {recommendation_type} recommendations for today."
    
    message_parts = [f"{emoji} *{title}* {emoji}\n"]
    
    # Add timestamp
    updated_at = data.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    message_parts.append(f"*Updated at:* {updated_at}\n")
    
    # Add recommendations
    for i, stock in enumerate(recommendations[:10], 1):  # Limit to top 10
        signal_strength = stock.get('signal_strength', 0)
        change_percent = stock.get('change_percent', 0)
        change_symbol = "🟢" if change_percent > 0 else "🔴" if change_percent < 0 else "⚪"
        target_price = stock.get('target_price', calculate_target_price(stock, recommendation_type))
        rsi = stock.get('rsi', 'N/A')
        adx = stock.get('adx', 'N/A')
        
        message_parts.append(
            f"{i}. *{stock['symbol']}* - ₹{stock.get('close', 0):.2f} {change_symbol}\n"
            f"   Signal Strength: {signal_strength}/10\n"
            f"   Change: {change_percent:.2f}%\n"
            f"   RSI: {rsi} | ADX: {adx}\n"
            f"   Target: ₹{target_price}\n"
        )
    
    message_parts.append("\n_Use these recommendations at your own risk. Always do your own research._")
    
    return "\n".join(message_parts)

def send_to_telegram(message):
    """Send message to Telegram"""
    # Try to send to both the chat ID and the channel
    recipients = [TELEGRAM_CHAT_ID, TELEGRAM_CHANNEL]
    success = False
    
    for recipient in recipients:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": recipient,
                "text": message,
                "parse_mode": "Markdown"
            }
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Message sent successfully to {recipient}")
            success = True
        except Exception as e:
            logger.error(f"Failed to send message to {recipient}: {e}")
    
    return success

def update_stock_data():
    """Fetch data and update both Telegram and the web server data"""
    while True:
        try:
            # Read stock data from CSV
            csv_stocks = read_stock_data_from_csv()
            
            if csv_stocks:
                # Analyze stocks based on ADX and RSI
                analyzed_data = analyze_stocks(csv_stocks)
                
                # Fetch additional data from API
                api_data = fetch_additional_data()
                
                # Merge data from both sources
                final_data = merge_stock_data(analyzed_data, api_data)
                
                # Update web data
                global latest_data
                latest_data["buy_recommendations"] = final_data.get('buy_recommendations', [])
                latest_data["sell_recommendations"] = final_data.get('sell_recommendations', [])
                latest_data["last_updated"] = final_data.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                
                # Format and send buy recommendations
                buy_message = format_recommendations(final_data, 'buy')
                if send_to_telegram(buy_message):
                    logger.info("Successfully sent buy recommendations to Telegram")
                else:
                    logger.error("Failed to send buy recommendations to Telegram")
                
                # Format and send sell recommendations
                sell_message = format_recommendations(final_data, 'sell')
                if send_to_telegram(sell_message):
                    logger.info("Successfully sent sell recommendations to Telegram")
                else:
                    logger.error("Failed to send sell recommendations to Telegram")
            else:
                logger.warning("No stock data available from CSV")
            
            # Wait for next check
            logger.info(f"Waiting {CHECK_INTERVAL} seconds until next check")
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            logger.info("Data update thread stopped")
            break
        except Exception as e:
            logger.error(f"Unexpected error in data update thread: {e}")
            # Wait a bit before retrying to avoid tight loop if persistent error
            time.sleep(60)

# Flask routes
@app.route('/')
def home():
    """Render the home page with recommendations"""
    return render_template('index.html', 
                          buy_recommendations=latest_data["buy_recommendations"],
                          sell_recommendations=latest_data["sell_recommendations"],
                          last_updated=latest_data["last_updated"])

@app.route('/api/recommendations')
def api_recommendations():
    """API endpoint to get the latest recommendations as JSON"""
    return jsonify({
        'buy_recommendations': latest_data["buy_recommendations"],
        'sell_recommendations': latest_data["sell_recommendations"],
        'last_updated': latest_data["last_updated"]
    })

# Create HTML templates directory
os.makedirs('templates', exist_ok=True)

# Create index.html template
with open('templates/index.html', 'w') as f:
    f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stock Trading Recommendations</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; padding-bottom: 30px; }
        .table-responsive { margin-top: 20px; }
        .up { color: green; }
        .down { color: red; }
        .last-updated { font-style: italic; font-size: 0.9rem; margin-bottom: 15px; }
        .overbought { background-color: rgba(255, 0, 0, 0.1); }
        .oversold { background-color: rgba(0, 128, 0, 0.1); }
        .high-adx { background-color: rgba(0, 0, 255, 0.1); }
        .strength-indicator {
            display: inline-block;
            width: 100px;
            height: 10px;
            background-color: #e9ecef;
            border-radius: 5px;
            overflow: hidden;
        }
        .strength-indicator-bar-buy {
            height: 100%;
            background-color: green;
            border-radius: 5px;
        }
        .strength-indicator-bar-sell {
            height: 100%;
            background-color: red;
            border-radius: 5px;
        }
        .refresh-button {
            margin-bottom: 15px;
        }
        .tab-content {
            padding-top: 20px;
        }
        .nav-pills .nav-link.active {
            background-color: #0d6efd;
        }
        .nav-pills .nav-link {
            color: #0d6efd;
        }
        .recommendation-badge {
            font-size: 0.8rem;
            padding: 3px 8px;
            margin-left: 10px;
            vertical-align: middle;
            border-radius: 10px;
        }
        .buy-badge {
            background-color: rgba(0, 128, 0, 0.2);
            color: green;
        }
        .sell-badge { 
            background-color: rgba(255, 0, 0, 0.2);
            color: red;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="text-center mb-4">Stock Trading Recommendations</h1>
        
        <div class="row">
            <div class="col-md-6">
                <div class="last-updated" id="lastUpdated">
                    {% if last_updated %}
                        Last updated: {{ last_updated }}
                    {% else %}
                        Last updated: Not available
                    {% endif %}
                </div>
            </div>
            <div class="col-md-6 text-end">
                <button id="refreshBtn" class="btn btn-primary refresh-button">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" class="bi bi-arrow-clockwise" viewBox="0 0 16 16">
                        <path fill-rule="evenodd" d="M8 3a5 5 0 1 0 4.546 2.914.5.5 0 0 1 .908-.417A6 6 0 1 1 8 2z"/>
                        <path d="M8 4.466V.534a.25.25 0 0 1 .41-.192l2.36 1.966c.12.1.12.284 0 .384L8.41 4.658A.25.25 0 0 1 8 4.466"/>
                    </svg>
                    Refresh
                </button>
            </div>
        </div>
        
        <ul class="nav nav-pills mb-3" id="pills-tab" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="pills-buy-tab" data-bs-toggle="pill" data-bs-target="#pills-buy" type="button" role="tab" aria-controls="pills-buy" aria-selected="true">Buy Recommendations</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-sell-tab" data-bs-toggle="pill" data-bs-target="#pills-sell" type="button" role="tab" aria-controls="pills-sell" aria-selected="false">Sell Recommendations</button>
            </li>
        </ul>
        
        <div class="tab-content" id="pills-tabContent">
            <!-- Buy Recommendations Tab -->
            <div class="tab-pane fade show active" id="pills-buy" role="tabpanel" aria-labelledby="pills-buy-tab">
                {% if buy_recommendations %}
                    <div class="table-responsive">
                        <table class="table table-striped table-hover">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>CMP (₹)</th>
                                    <th>Change (%)</th>
                                    <th>RSI</th>
                                    <th>ADX</th>
                                    <th>Target Price (₹)</th>
                                    <th>Signal Strength</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for stock in buy_recommendations %}
                                    <tr>
                                        <td>{{ stock.symbol }} <span class="recommendation-badge buy-badge">BUY</span></td>
                                        <td>{{ "%.2f"|format(stock.close) }}</td>
                                        <td class="{% if stock.change_percent > 0 %}up{% elif stock.change_percent < 0 %}down{% endif %}">
                                            {{ "%.2f"|format(stock.change_percent) }}%
                                        </td>
                                        <td class="{% if stock.rsi < 30 %}oversold{% elif stock.rsi > 70 %}overbought{% endif %}">
                                            {{ stock.rsi }}
                                        </td>
                                        <td class="{% if stock.adx > 25 %}high-adx{% endif %}">
                                            {{ stock.adx }}
                                        </td>
                                        <td>{{ "%.2f"|format(stock.target_price) }}</td>
                                        <td>
                                            <div class="strength-indicator">
                                                <div class="strength-indicator-bar-buy" style="width: {{ stock.signal_strength*10 }}%;"></div>
                                            </div>
                                            <span class="ms-2">{{ stock.signal_strength }}/10</span>
                                        </td>
                                    </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                {% else %}
                    <div class="alert alert-info">
                        No buy recommendations available at this time.
                    </div>
                {% endif %}
            </div>
            
            <!-- Sell Recommendations Tab -->
            <div class="tab-pane fade" id="pills-sell" role="tabpanel" aria-labelledby="pills-sell-tab">
                {% if sell_recommendations %}
                    <div class="table-responsive">
                        <table class="table table-striped table-hover">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>CMP (₹)</th>
                                    <th>Change (%)</th>
                                    <th>RSI</th>
                                    <th>ADX</th>
                                    <th>Target Price (₹)</th>
                                    <th>Signal Strength</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for stock in sell_recommendations %}
                                    <tr>
                                        <td>{{ stock.symbol }} <span class="recommendation-badge sell-badge">SELL</span></td>
                                        <td>{{ "%.2f"|format(stock.close) }}</td>
                                        <td class="{% if stock.change_percent > 0 %}up{% elif stock.change_percent < 0 %}down{% endif %}">
                                            {{ "%.2f"|format(stock.change_percent) }}%
                                        </td>
                                        <td class="{% if stock.rsi < 30 %}oversold{% elif stock.rsi > 70 %}overbought{% endif %}">
                                            {{ stock.rsi }}
                                        </td>
                                        <td class="{% if stock.adx > 25 %}high-adx{% endif %}">
                                            {{ stock.adx }}
                                        </td>
                                        <td>{{ "%.2f"|format(stock.target_price) }}</td>
                                        <td>
                                            <div class="strength-indicator">
                                                <div class="strength-indicator-bar-sell" style="width: {{ stock.signal_strength*10 }}%;"></div>
                                            </div>
                                            <span class="ms-2">{{ stock.signal_strength }}/10</span>
                                        </td>
                                    </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                {% else %}
                    <div class="alert alert-info">
                        No sell recommendations available at this time.
                    </div>
                {% endif %}
            </div>
        </div>
        
        <div class="mt-4">
            <div class="card">
                <div class="card-header">
                    <h5 class="mb-0">About This Tool</h5>
                </div>
                <div class="card-body">
                    <p>This tool provides buy and sell recommendations for Nifty 50 stocks based on technical analysis.</p>
                    
                    <h6>Key Indicators Used:</h6>
                    <ul>
                        <li><strong>RSI (Relative Strength Index):</strong> Measures momentum and identifies overbought or oversold conditions.
                            <ul>
                                <li>RSI < 40: Potential buy signal (approaching oversold)</li>
                                <li>RSI > 60: Potential sell signal (approaching overbought)</li>
                            </ul>
                        </li>
                        <li><strong>ADX (Average Directional Index):</strong> Measures trend strength.
                            <ul>
                                <li>ADX > 20: Indicates the presence of a trend</li>
                                <li>ADX > 25: Indicates a strong trend</li>
                            </ul>
                        </li>
                    </ul>
                    
                    <p><strong>How to Use:</strong> The signal strength (1-10) combines the strength of both indicators, with 10 being the strongest recommendation.</p>
                    
                    <p><strong>Data Source:</strong> Stock data is loaded from nifty50_stocks.csv and updated hourly. Additional data may be fetched from online sources when available.</p>
                    
                    <p><strong>Disclaimer:</strong> These recommendations are generated purely based on technical indicators and should not be considered as financial advice. Always do your own research before making investment decisions.</p>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.getElementById('refreshBtn').addEventListener('click', function() {
            location.reload();
        });
    </script>
</body>
</html>
''')

def main():
    """Main function to run the bot and web server"""
    logger.info("Starting Enhanced Stock Recommendations Bot with Web Server")
    
    # Create sample CSV file if it doesn't exist
    create_csv_if_not_exists()
    
    # Start the data update thread
    data_thread = threading.Thread(target=update_stock_data, daemon=True)
    data_thread.start()
    
    # Run the web server
    logger.info(f"Starting web server on port {WEB_PORT}")
    app.run(host='0.0.0.0', port=WEB_PORT)

if __name__ == "__main__":
    main()
