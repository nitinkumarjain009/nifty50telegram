#!/usr/bin/env python3
"""
Enhanced Stock Recommendations Bot
- Fetches buy recommendations from nifty500-trading-bot.onrender.com
- Sends recommendations to Telegram
- Hosts a webpage displaying the recommendations with additional details
"""

import requests
import json
import time
import logging
import sys
from datetime import datetime
from flask import Flask, render_template, jsonify
import threading
import os

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

# Store data for the web server
latest_data = {
    "recommendations": [],
    "last_updated": None
}

# Initialize Flask app
app = Flask(__name__)

def calculate_target_price(stock_data):
    """Calculate a simple target price based on RSI and current price
    
    For demonstration purposes:
    - RSI < 30 (oversold): Target = Current price + 5%
    - RSI > 70 (overbought): Target = Current price + 2%
    - Otherwise: Target = Current price + 3%
    """
    price = stock_data.get('close', 0)
    rsi = stock_data.get('rsi', 50)  # Default to 50 if not available
    
    if rsi < 30:
        # More oversold = higher potential upside
        target = price * 1.05
    elif rsi > 70:
        # Overbought = lower potential upside
        target = price * 1.02
    else:
        # Moderate RSI
        target = price * 1.03
    
    return round(target, 2)

def fetch_stock_data():
    """Fetch stock data from the API"""
    try:
        logger.info("Fetching stock data from API...")
        response = requests.get(STOCK_API_URL, timeout=30)
        response.raise_for_status()  # Raise exception for 4XX/5XX responses
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching stock data: {e}")
        return None

def format_buy_recommendations(data):
    """Format buy recommendations into a readable message for Telegram"""
    if not data or 'buy_recommendations' not in data:
        return "No buy recommendations available."
    
    recommendations = data['buy_recommendations']
    if not recommendations:
        return "No buy recommendations for today."
    
    # Sort by signal strength (highest first)
    recommendations.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
    
    message_parts = ["🔔 *STOCK BUY RECOMMENDATIONS* 🔔\n"]
    
    # Add timestamp
    updated_at = data.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    message_parts.append(f"*Updated at:* {updated_at}\n")
    
    # Add recommendations
    for i, stock in enumerate(recommendations[:10], 1):  # Limit to top 10
        signal_strength = stock.get('signal_strength', 0)
        change_percent = stock.get('change_percent', 0)
        change_symbol = "🟢" if change_percent > 0 else "🔴" if change_percent < 0 else "⚪"
        target_price = calculate_target_price(stock)
        rsi = stock.get('rsi', 'N/A')
        
        message_parts.append(
            f"{i}. *{stock['symbol']}* - ₹{stock.get('close', 0):.2f} {change_symbol}\n"
            f"   Signal Strength: {signal_strength}/10\n"
            f"   Change: {change_percent:.2f}%\n"
            f"   RSI: {rsi}\n"
            f"   Target: ₹{target_price}\n"
        )
    
    message_parts.append("\n_Use these recommendations at your own risk. Always do your own research._")
    
    return "\n".join(message_parts)

def process_recommendations_for_web(data):
    """Process buy recommendations for web display"""
    if not data or 'buy_recommendations' not in data:
        return []
    
    recommendations = data['buy_recommendations']
    if not recommendations:
        return []
    
    # Sort by signal strength (highest first)
    recommendations.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
    
    processed_recommendations = []
    for stock in recommendations:
        processed_recommendations.append({
            'symbol': stock.get('symbol', 'N/A'),
            'close': stock.get('close', 0),
            'change_percent': stock.get('change_percent', 0),
            'signal_strength': stock.get('signal_strength', 0),
            'rsi': stock.get('rsi', 'N/A'),
            'target_price': calculate_target_price(stock),
            'volume': stock.get('volume', 'N/A')
        })
    
    return processed_recommendations

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
            # Fetch data
            stock_data = fetch_stock_data()
            
            if stock_data:
                # Update web data
                global latest_data
                latest_data["recommendations"] = process_recommendations_for_web(stock_data)
                latest_data["last_updated"] = stock_data.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                
                # Format message
                message = format_buy_recommendations(stock_data)
                
                # Send to Telegram
                if send_to_telegram(message):
                    logger.info("Successfully sent recommendations to Telegram")
                else:
                    logger.error("Failed to send recommendations to any Telegram recipient")
            
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
                          recommendations=latest_data["recommendations"],
                          last_updated=latest_data["last_updated"])

@app.route('/api/recommendations')
def api_recommendations():
    """API endpoint to get the latest recommendations as JSON"""
    return jsonify({
        'recommendations': latest_data["recommendations"],
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
    <title>Stock Buy Recommendations</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .table-responsive { margin-top: 20px; }
        .up { color: green; }
        .down { color: red; }
        .last-updated { font-style: italic; font-size: 0.9rem; margin-bottom: 15px; }
        .overbought { background-color: rgba(255, 0, 0, 0.1); }
        .oversold { background-color: rgba(0, 128, 0, 0.1); }
        .strength-indicator {
            display: inline-block;
            width: 100px;
            height: 10px;
            background-color: #e9ecef;
            border-radius: 5px;
            overflow: hidden;
        }
        .strength-indicator-bar {
            height: 100%;
            background-color: green;
            border-radius: 5px;
        }
        .refresh-button {
            margin-bottom: 15px;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="text-center mb-4">Stock Buy Recommendations</h1>
        
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
        
        {% if recommendations %}
            <div class="table-responsive">
                <table class="table table-striped table-hover">
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>CMP (₹)</th>
                            <th>Change (%)</th>
                            <th>RSI</th>
                            <th>Target Price (₹)</th>
                            <th>Signal Strength</th>
                        </tr>
                    </thead>
                    <tbody>
                        {% for stock in recommendations %}
                            <tr>
                                <td>{{ stock.symbol }}</td>
                                <td>{{ "%.2f"|format(stock.close) }}</td>
                                <td class="{% if stock.change_percent > 0 %}up{% elif stock.change_percent < 0 %}down{% endif %}">
                                    {{ "%.2f"|format(stock.change_percent) }}%
                                </td>
                                <td class="{% if stock.rsi < 30 %}oversold{% elif stock.rsi > 70 %}overbought{% endif %}">
                                    {{ stock.rsi }}
                                </td>
                                <td>{{ "%.2f"|format(stock.target_price) }}</td>
                                <td>
                                    <div class="strength-indicator">
                                        <div class="strength-indicator-bar" style="width: {{ stock.signal_strength*10 }}%;"></div>
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
        
        <div class="mt-4">
            <div class="card">
                <div class="card-header">
                    <h5 class="mb-0">About This Tool</h5>
                </div>
                <div class="card-body">
                    <p>This tool fetches technical analysis-based buy recommendations for NSE stocks. The data is updated hourly.</p>
                    <p>The recommendations are based on various technical indicators including RSI, MACD, EMA, and others.</p>
                    <p><strong>Disclaimer:</strong> These recommendations are generated purely based on technical indicators and should not be considered as financial advice. Always do your own research before making investment decisions.</p>
                </div>
            </div>
        </div>
    </div>

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
    logger.info("Starting Stock Recommendations Bot with Web Server")
    
    # Start the data update thread
    data_thread = threading.Thread(target=update_stock_data, daemon=True)
    data_thread.start()
    
    # Run the web server
    logger.info(f"Starting web server on port {WEB_PORT}")
    app.run(host='0.0.0.0', port=WEB_PORT)

if __name__ == "__main__":
    main()
