#!/usr/bin/env python3
"""
Enhanced Stock Recommendations Bot
- Reads stock data from nifty50_stocks.csv with multiple timeframes (daily, weekly, monthly)
- Generates buy/sell recommendations based on ADX and RSI across timeframes
- Fetches additional data from nifty500-trading-bot.onrender.com
- Sends comprehensive table-based recommendations to Telegram
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
import tabulate  # For creating tables in Telegram messages

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
    "all_stocks": [],  # Store all stocks regardless of recommendation
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
            writer.writerow([
                'symbol', 'close', 'change_percent', 'volume',
                'daily_rsi', 'daily_adx', 
                'weekly_rsi', 'weekly_adx',
                'monthly_rsi', 'monthly_adx'
            ])
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

def get_recommendation(stock_data):
    """
    Determine buy/sell recommendation based on multiple timeframes
    
    Rules:
    - Strong Buy: Daily and Weekly both show buy signals
    - Buy: Daily shows buy signal, Weekly is neutral or buy
    - Strong Sell: Daily and Weekly both show sell signals
    - Sell: Daily shows sell signal, Weekly is neutral or sell
    - Neutral: Mixed or no clear signals
    """
    daily_rsi = stock_data.get('daily_rsi', 50)
    daily_adx = stock_data.get('daily_adx', 15)
    weekly_rsi = stock_data.get('weekly_rsi', 50)
    weekly_adx = stock_data.get('weekly_adx', 15)
    monthly_rsi = stock_data.get('monthly_rsi', 50)
    monthly_adx = stock_data.get('monthly_adx', 15)
    
    # Check for buy signals
    daily_buy = daily_rsi < 40 and daily_adx > 20
    weekly_buy = weekly_rsi < 40 and weekly_adx > 20
    monthly_buy = monthly_rsi < 40 and monthly_adx > 20
    
    # Check for sell signals
    daily_sell = daily_rsi > 60 and daily_adx > 20
    weekly_sell = weekly_rsi > 60 and weekly_adx > 20
    monthly_sell = monthly_rsi > 60 and monthly_adx > 20
    
    # Calculate signal strength (1-10)
    buy_strength = 0
    sell_strength = 0
    
    # Daily timeframe (most weight): up to 5 points
    if daily_buy:
        buy_strength += 2.5 + ((40 - daily_rsi) / 40) * 2.5
    if daily_sell:
        sell_strength += 2.5 + ((daily_rsi - 60) / 40) * 2.5
    
    # Weekly timeframe: up to 3 points
    if weekly_buy:
        buy_strength += 1.5 + ((40 - weekly_rsi) / 40) * 1.5
    if weekly_sell:
        sell_strength += 1.5 + ((weekly_rsi - 60) / 40) * 1.5
    
    # Monthly timeframe: up to 2 points
    if monthly_buy:
        buy_strength += 1 + ((40 - monthly_rsi) / 40) * 1
    if monthly_sell:
        sell_strength += 1 + ((monthly_rsi - 60) / 40) * 1
    
    # Cap strengths between 1-10
    buy_strength = min(10, max(1, buy_strength if buy_strength > 0 else 0))
    sell_strength = min(10, max(1, sell_strength if sell_strength > 0 else 0))
    
    # Determine recommendation
    if buy_strength > sell_strength:
        if daily_buy and weekly_buy:
            recommendation = "STRONG BUY"
        else:
            recommendation = "BUY"
        signal_strength = buy_strength
    elif sell_strength > buy_strength:
        if daily_sell and weekly_sell:
            recommendation = "STRONG SELL"
        else:
            recommendation = "SELL"
        signal_strength = sell_strength
    else:
        recommendation = "NEUTRAL"
        signal_strength = max(buy_strength, sell_strength, 1)  # At least 1
    
    return {
        'recommendation': recommendation,
        'signal_strength': round(signal_strength, 1)  # Round to 1 decimal place
    }

def calculate_target_price(stock_data):
    """Calculate target price based on recommendation, RSI and ADX across timeframes"""
    price = stock_data.get('close', 0)
    recommendation = stock_data.get('recommendation', 'NEUTRAL')
    
    # Factor in signal strength
    signal_strength = stock_data.get('signal_strength', 5)
    
    # Calculate target based on recommendation and signal strength
    if recommendation == "STRONG BUY":
        target = price * (1 + 0.03 + (signal_strength / 100))
    elif recommendation == "BUY":
        target = price * (1 + 0.02 + (signal_strength / 150))
    elif recommendation == "STRONG SELL":
        target = price * (1 - 0.03 - (signal_strength / 100))
    elif recommendation == "SELL":
        target = price * (1 - 0.02 - (signal_strength / 150))
    else:  # NEUTRAL
        target = price * (1 + 0.01)  # Slight upward bias
    
    return round(target, 2)

def analyze_stocks(stocks):
    """Analyze stocks using multiple timeframes and determine recommendations"""
    buy_recommendations = []
    sell_recommendations = []
    all_stocks = []
    
    for stock in stocks:
        # Get recommendation based on all timeframes
        recommendation_data = get_recommendation(stock)
        
        # Add recommendation and signal strength to stock data
        stock['recommendation'] = recommendation_data['recommendation'] 
        stock['signal_strength'] = recommendation_data['signal_strength']
        
        # Calculate target price
        stock['target_price'] = calculate_target_price(stock)
        
        # Store in appropriate lists
        all_stocks.append(stock)
        
        if "BUY" in stock['recommendation']:
            buy_recommendations.append(stock)
        elif "SELL" in stock['recommendation']:
            sell_recommendations.append(stock)
    
    # Sort by signal strength (highest first)
    buy_recommendations.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
    sell_recommendations.sort(key=lambda x: x.get('signal_strength', 0), reverse=True)
    all_stocks.sort(key=lambda x: x.get('symbol', ''))  # Sort alphabetically
    
    return {
        'buy_recommendations': buy_recommendations,
        'sell_recommendations': sell_recommendations,
        'all_stocks': all_stocks,
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
    for stock in csv_data.get('all_stocks', []):
        symbol = stock.get('symbol')
        if symbol in api_stocks:
            # Copy any missing fields from API data
            for key, value in api_stocks[symbol].items():
                if key not in stock or stock[key] is None:
                    stock[key] = value
    
    return csv_data

def format_recommendations_text(data, recommendation_type=None):
    """Format recommendations into a text message for Telegram"""
    recommendations = []
    
    if recommendation_type == 'buy':
        recommendations = data.get('buy_recommendations', [])
        emoji = "🟢" 
        title = "STOCK BUY RECOMMENDATIONS"
    elif recommendation_type == 'sell':
        recommendations = data.get('sell_recommendations', [])
        emoji = "🔴"
        title = "STOCK SELL RECOMMENDATIONS"
    else:
        recommendations = data.get('all_stocks', [])
        emoji = "📊"
        title = "ALL STOCKS ANALYSIS"
    
    if not recommendations:
        return f"No {recommendation_type if recommendation_type else 'stock'} data available."
    
    message_parts = [f"{emoji} *{title}* {emoji}\n"]
    
    # Add timestamp
    updated_at = data.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    message_parts.append(f"*Updated at:* {updated_at}\n")
    
    # Add recommendations
    for i, stock in enumerate(recommendations[:10], 1):  # Limit to top 10
        signal_strength = stock.get('signal_strength', 0)
        recommendation = stock.get('recommendation', 'NEUTRAL')
        change_percent = stock.get('change_percent', 0)
        change_symbol = "🟢" if change_percent > 0 else "🔴" if change_percent < 0 else "⚪"
        
        message_parts.append(
            f"{i}. *{stock['symbol']}* - ₹{stock.get('close', 0):.2f} {change_symbol}\n"
            f"   *{recommendation}* | Strength: {signal_strength}/10\n"
            f"   Change: {change_percent:.2f}%\n"
            f"   RSI (D/W/M): {stock.get('daily_rsi', 'N/A')}/{stock.get('weekly_rsi', 'N/A')}/{stock.get('monthly_rsi', 'N/A')}\n"
            f"   ADX (D/W/M): {stock.get('daily_adx', 'N/A')}/{stock.get('weekly_adx', 'N/A')}/{stock.get('monthly_adx', 'N/A')}\n"
            f"   Target: ₹{stock.get('target_price', 0):.2f}\n"
        )
    
    message_parts.append("\n_Use these recommendations at your own risk. Always do your own research._")
    
    return "\n".join(message_parts)

def create_markdown_table(data, max_rows=20):
    """Create a markdown-formatted table for Telegram"""
    # Prepare header
    headers = ["Symbol", "CMP", "Rec", "Signal", "D-RSI", "D-ADX", "W-RSI", "W-ADX", "M-RSI", "M-ADX", "Target"]
    
    # Prepare rows
    rows = []
    for stock in data[:max_rows]:  # Limit to max_rows
        recommendation = stock.get('recommendation', 'NEUTRAL')
        # Short form of recommendation
        rec_short = recommendation.replace("STRONG ", "S_")
        
        rows.append([
            stock.get('symbol', 'N/A'),
            f"₹{stock.get('close', 0):.1f}",
            rec_short,
            f"{stock.get('signal_strength', 0):.1f}",
            stock.get('daily_rsi', 'N/A'),
            stock.get('daily_adx', 'N/A'),
            stock.get('weekly_rsi', 'N/A'),
            stock.get('weekly_adx', 'N/A'),
            stock.get('monthly_rsi', 'N/A'),
            stock.get('monthly_adx', 'N/A'),
            f"₹{stock.get('target_price', 0):.1f}"
        ])
    
    # Generate the markdown table
    table = tabulate.tabulate(rows, headers, tablefmt="pipe")
    
    return f"```\n{table}\n```"

def send_table_to_telegram(data, recommendation_type=None):
    """Send stock data as a formatted table to Telegram"""
    # Get relevant data
    if recommendation_type == 'buy':
        stocks = data.get('buy_recommendations', [])
        title = "🟢 STOCK BUY RECOMMENDATIONS 🟢"
    elif recommendation_type == 'sell':
        stocks = data.get('sell_recommendations', [])
        title = "🔴 STOCK SELL RECOMMENDATIONS 🔴"
    else:
        stocks = data.get('all_stocks', [])
        title = "📊 ALL STOCKS ANALYSIS 📊"
    
    # Create intro message
    updated_at = data.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    intro = f"*{title}*\n*Updated at:* {updated_at}\n\n"
    
    # Create table if we have data
    if not stocks:
        message = f"{intro}No {recommendation_type if recommendation_type else 'stock'} data available."
    else:
        # Generate markdown table
        table = create_markdown_table(stocks)
        message = f"{intro}{table}\n\n_Use these recommendations at your own risk. Always do your own research._"
    
    # Send message
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
            logger.info(f"Table message sent successfully to {recipient}")
            success = True
        except Exception as e:
            logger.error(f"Failed to send table message to {recipient}: {e}")
    
    return success

def update_stock_data():
    """Fetch data and update both Telegram and the web server data"""
    while True:
        try:
            # Read stock data from CSV
            csv_stocks = read_stock_data_from_csv()
            
            if csv_stocks:
                # Analyze stocks based on multiple timeframes
                analyzed_data = analyze_stocks(csv_stocks)
                
                # Fetch additional data from API
                api_data = fetch_additional_data()
                
                # Merge data from both sources
                final_data = merge_stock_data(analyzed_data, api_data)
                
                # Update web data
                global latest_data
                latest_data["buy_recommendations"] = final_data.get('buy_recommendations', [])
                latest_data["sell_recommendations"] = final_data.get('sell_recommendations', [])
                latest_data["all_stocks"] = final_data.get('all_stocks', [])
                latest_data["last_updated"] = final_data.get('updated_at', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                
                # Send all stocks table to Telegram
                if send_table_to_telegram(final_data):
                    logger.info("Successfully sent all stocks table to Telegram")
                else:
                    logger.error("Failed to send all stocks table to Telegram")
                
                # Send buy recommendations table to Telegram
                if send_table_to_telegram(final_data, 'buy'):
                    logger.info("Successfully sent buy recommendations table to Telegram")
                else:
                    logger.error("Failed to send buy recommendations table to Telegram")
                
                # Send sell recommendations table to Telegram
                if send_table_to_telegram(final_data, 'sell'):
                    logger.info("Successfully sent sell recommendations table to Telegram")
                else:
                    logger.error("Failed to send sell recommendations table to Telegram")
                
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
                          all_stocks=latest_data["all_stocks"],
                          last_updated=latest_data["last_updated"])

@app.route('/api/recommendations')
def api_recommendations():
    """API endpoint to get the latest recommendations as JSON"""
    return jsonify({
        'buy_recommendations': latest_data["buy_recommendations"],
        'sell_recommendations': latest_data["sell_recommendations"],
        'all_stocks': latest_data["all_stocks"],
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
    <title>Multi-Timeframe Stock Analysis</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css">
    <style>
        body { padding-top: 20px; padding-bottom: 30px; }
        .table-responsive { margin-top: 20px; overflow-x: auto; }
        .up { color: green; }
        .down { color: red; }
        .neutral { color: #6c757d; }
        .last-updated { font-style: italic; font-size: 0.9rem; margin-bottom: 15px; }
        .overbought { background-color: rgba(255, 0, 0, 0.1); }
        .oversold { background-color: rgba(0, 128, 0, 0.1); }
        .high-adx { background-color: rgba(0, 0, 255, 0.1); }
        .strength-indicator {
            display: inline-block;
            width: 70px;
            height: 8px;
            background-color: #e9ecef;
            border-radius: 4px;
            overflow: hidden;
        }
        .strength-indicator-bar-buy {
            height: 100%;
            background-color: green;
            border-radius: 4px;
        }
        .strength-indicator-bar-sell {
            height: 100%;
            background-color: red;
            border-radius: 4px;
        }
        .strength-indicator-bar-neutral {
            height: 100%;
            background-color: #6c757d;
            border-radius: 4px;
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
            font-size: 0.75rem;
            padding: 2px 6px;
            margin-left: 5px;
            vertical-align: middle;
            border-radius: 10px;
            white-space: nowrap;
        }
        .buy-badge {
            background-color: rgba(0, 128, 0, 0.2);
            color: green;
        }
        .strong-buy-badge {
            background-color: rgba(0, 128, 0, 0.4);
            color: green;
        }
        .sell-badge { 
            background-color: rgba(255, 0, 0, 0.2);
            color: red;
        }
        .strong-sell-badge { 
            background-color: rgba(255, 0, 0, 0.4);
            color: red;
        }
        .neutral-badge { 
            background-color: rgba(108, 117, 125, 0.2);
            color: #6c757d;
        }
        .timeframe-header {
            background-color: #f8f9fa;
            font-size: 0.8rem;
            text-align: center;
        }
        .table th {
            position: sticky;
            top: 0;
            background-color: #fff;
            z-index: 1;
        }
        .search-container {
            margin-bottom: 15px;
        }
        .table td, .table th {
            font-size: 0.85rem;
            padding: 0.5rem;
        }
        .sub-header {
            background-color: #e9ecef;
            font-weight: normal;
            font-size: 0.75rem;
            text-align: center;
        }
        .legend {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 15px;
            font-size: 0.8rem;
        }
        .legend-item {
            display: flex;
            align-items: center;
            margin-right: 10px;
        }
        .legend-color {
            width: 15px;
            height: 15px;
            margin-right: 5px;
            border-radius: 3px;
        }
        .scrollable-table {
            max-height: 600px;
            overflow-y: auto;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="text-center mb-3">Multi-Timeframe Stock Analysis</h1>
        
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
                    <i class="bi bi-arrow-clockwise"></i> Refresh
                </button>
            </div>
        </div>
        
        <ul class="nav nav-pills mb-3" id="pills-tab" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="pills-all-tab" data-bs-toggle="pill" data-bs-target="#pills-all" type="button" role="tab" aria-controls="pills-all" aria-selected="true">All Stocks</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-buy-tab" data-bs-toggle="pill" data-bs-target="#pills-buy" type="button" role="tab" aria-controls="pills-buy" aria-selected="false">Buy Recommendations</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="pills-sell-tab" data-bs-toggle="pill" data-bs-target="#pills-sell" type="button" role="tab" aria-controls="pills-sell" aria-selected="false">Sell Recommendations</button>
            </li>
        </ul>
        
        <div class="tab-content" id="pills-tabContent">
            <!-- All Stocks Tab -->
            <div class="tab-pane fade show active" id="pills-all" role="tabpanel" aria-labelledby="pills-all-tab">
                <div class="search-container">
                    <input type="text" id="searchAllStocks" class="form-control" placeholder="Search by symbol...">
                </div>
                
                {% if all_stocks %}
                    <div class="scrollable-table">
                        <div class="table-responsive">
                            <table class="table table-striped table-hover table-sm" id="allStocksTable">
                                <thead>
                                    <tr>
                                        <th rowspan="2">Symbol</th>
                                        <th rowspan="2">CMP (₹)</th>
                                        <th rowspan="2">Change (%)</th>
                                        <th rowspan="2">Recommendation</th>
                                        <th rowspan="2">Signal</th>
                                        <th rowspan="2">Target (₹)</th>
                                        <th colspan="3" class="timeframe-header">RSI</th>
                                        <th colspan="3" class="timeframe-header">ADX</th>
                                    </tr>
                                    <tr class="sub-header">
                                        <th>Daily</th>
                                        <th>Weekly</th>
                                        <th>Monthly</th>
                                        <th>Daily</th>
                                        <th>Weekly</th>
                                        <th>Monthly</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for stock in all_stocks %}
                                        <tr>
                                            <td>{{ stock.symbol }}</td>
                                            <td>{{ "%.2f"|format(stock.close) }}</td>
                                            <td class="{% if stock.change_percent > 0 %}up{% elif stock.change_percent < 0 %}down{% else %}neutral{% endif %}">
                                                {{ "%.2f"|format(stock.change_percent) }}%
                                            </td>
                                            <td>
                                                {% if stock.recommendation == 'STRONG BUY' %}
                                                    <span class="recommendation-badge strong-buy-badge">STRONG BUY</span>
                                                {% elif stock.recommendation == 'BUY' %}
                                                    <span class="recommendation-badge buy-badge">BUY</span>
                                                {% elif stock.recommendation == 'STRONG SELL' %}
                                                    <span class="recommendation-badge strong-sell-badge">STRONG SELL</span>
                                                {% elif stock.recommendation == 'SELL' %}
                                                    <span class="recommendation-badge sell-badge">SELL</span>
                                                {% else %}
                                                    <span class="recommendation-badge neutral-badge">NEUTRAL</span>
                                                {% endif %}
                                            </td>
                                            <td>
                                                {% if 'BUY' in stock.recommendation %}
                                                    <div class="strength-indicator">
                                                        <div class="strength-indicator-bar-buy" style="width: {{ stock.signal_strength*10 }}%;"></div>
                                                    </div>
                                                {% elif 'SELL' in stock.recommendation %}
                                                    <div class="strength-indicator">
                                                        <div class="strength-indicator-bar-sell" style="width: {{ stock.signal_strength*10 }}%;"></div>
                                                    </div>
                                                {% else %}
                                                    <div class="strength-indicator">
                                                        <div class="strength-indicator-bar-neutral" style="width: {{ stock.signal_strength*10 }}%;"></div>
                                                    </div>
                                                {% endif %}
                                                <span class="ms-1">{{ stock.signal_strength }}</span>
                                            </td>
                                            <td>{{ "%.2f"|format(stock.target_price) }}</td>
                                            <td class="{% if stock.daily_rsi < 30 %}oversold{% elif stock.daily_rsi > 70 %}overbought{% endif %}">
                                                {{ stock.daily_rsi }}
                                            </td>
                                            <!-- Completion of templates/index.html -->
                            <td class="{% if stock.daily_rsi < 30 %}oversold{% elif stock.daily_rsi > 70 %}overbought{% endif %}">
                                {{ stock.daily_rsi }}
                            </td>
                            <td class="{% if stock.weekly_rsi < 30 %}oversold{% elif stock.weekly_rsi > 70 %}overbought{% endif %}">
                                {{ stock.weekly_rsi }}
                            </td>
                            <td class="{% if stock.monthly_rsi < 30 %}oversold{% elif stock.monthly_rsi > 70 %}overbought{% endif %}">
                                {{ stock.monthly_rsi }}
                            </td>
                            <td class="{% if stock.daily_adx > 25 %}high-adx{% endif %}">
                                {{ stock.daily_adx }}
                            </td>
                            <td class="{% if stock.weekly_adx > 25 %}high-adx{% endif %}">
                                {{ stock.weekly_adx }}
                            </td>
                            <td class="{% if stock.monthly_adx > 25 %}high-adx{% endif %}">
                                {{ stock.monthly_adx }}
                            </td>
                        </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
{% else %}
    <div class="alert alert-warning">
        No stock data available. Please make sure the CSV file is populated.
    </div>
{% endif %}

<div class="legend">
    <div class="legend-item">
        <div class="legend-color" style="background-color: rgba(0, 128, 0, 0.1);"></div>
        <span>Oversold (RSI < 30)</span>
    </div>
    <div class="legend-item">
        <div class="legend-color" style="background-color: rgba(255, 0, 0, 0.1);"></div>
        <span>Overbought (RSI > 70)</span>
    </div>
    <div class="legend-item">
        <div class="legend-color" style="background-color: rgba(0, 0, 255, 0.1);"></div>
        <span>Strong Trend (ADX > 25)</span>
    </div>
</div>
</div>

<!-- Buy Recommendations Tab -->
<div class="tab-pane fade" id="pills-buy" role="tabpanel" aria-labelledby="pills-buy-tab">
<div class="search-container">
    <input type="text" id="searchBuyRecs" class="form-control" placeholder="Search by symbol...">
</div>

{% if buy_recommendations %}
    <div class="scrollable-table">
        <div class="table-responsive">
            <table class="table table-striped table-hover table-sm" id="buyRecsTable">
                <thead>
                    <tr>
                        <th rowspan="2">Symbol</th>
                        <th rowspan="2">CMP (₹)</th>
                        <th rowspan="2">Change (%)</th>
                        <th rowspan="2">Recommendation</th>
                        <th rowspan="2">Signal</th>
                        <th rowspan="2">Target (₹)</th>
                        <th colspan="3" class="timeframe-header">RSI</th>
                        <th colspan="3" class="timeframe-header">ADX</th>
                    </tr>
                    <tr class="sub-header">
                        <th>Daily</th>
                        <th>Weekly</th>
                        <th>Monthly</th>
                        <th>Daily</th>
                        <th>Weekly</th>
                        <th>Monthly</th>
                    </tr>
                </thead>
                <tbody>
                    {% for stock in buy_recommendations %}
                        <tr>
                            <td>{{ stock.symbol }}</td>
                            <td>{{ "%.2f"|format(stock.close) }}</td>
                            <td class="{% if stock.change_percent > 0 %}up{% elif stock.change_percent < 0 %}down{% else %}neutral{% endif %}">
                                {{ "%.2f"|format(stock.change_percent) }}%
                            </td>
                            <td>
                                {% if stock.recommendation == 'STRONG BUY' %}
                                    <span class="recommendation-badge strong-buy-badge">STRONG BUY</span>
                                {% elif stock.recommendation == 'BUY' %}
                                    <span class="recommendation-badge buy-badge">BUY</span>
                                {% endif %}
                            </td>
                            <td>
                                <div class="strength-indicator">
                                    <div class="strength-indicator-bar-buy" style="width: {{ stock.signal_strength*10 }}%;"></div>
                                </div>
                                <span class="ms-1">{{ stock.signal_strength }}</span>
                            </td>
                            <td>{{ "%.2f"|format(stock.target_price) }}</td>
                            <td class="{% if stock.daily_rsi < 30 %}oversold{% elif stock.daily_rsi > 70 %}overbought{% endif %}">
                                {{ stock.daily_rsi }}
                            </td>
                            <td class="{% if stock.weekly_rsi < 30 %}oversold{% elif stock.weekly_rsi > 70 %}overbought{% endif %}">
                                {{ stock.weekly_rsi }}
                            </td>
                            <td class="{% if stock.monthly_rsi < 30 %}oversold{% elif stock.monthly_rsi > 70 %}overbought{% endif %}">
                                {{ stock.monthly_rsi }}
                            </td>
                            <td class="{% if stock.daily_adx > 25 %}high-adx{% endif %}">
                                {{ stock.daily_adx }}
                            </td>
                            <td class="{% if stock.weekly_adx > 25 %}high-adx{% endif %}">
                                {{ stock.weekly_adx }}
                            </td>
                            <td class="{% if stock.monthly_adx > 25 %}high-adx{% endif %}">
                                {{ stock.monthly_adx }}
                            </td>
                        </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
{% else %}
    <div class="alert alert-info">
        No buy recommendations available at this time.
    </div>
{% endif %}
</div>

<!-- Sell Recommendations Tab -->
<div class="tab-pane fade" id="pills-sell" role="tabpanel" aria-labelledby="pills-sell-tab">
<div class="search-container">
    <input type="text" id="searchSellRecs" class="form-control" placeholder="Search by symbol...">
</div>

{% if sell_recommendations %}
    <div class="scrollable-table">
        <div class="table-responsive">
            <table class="table table-striped table-hover table-sm" id="sellRecsTable">
                <thead>
                    <tr>
                        <th rowspan="2">Symbol</th>
                        <th rowspan="2">CMP (₹)</th>
                        <th rowspan="2">Change (%)</th>
                        <th rowspan="2">Recommendation</th>
                        <th rowspan="2">Signal</th>
                        <th rowspan="2">Target (₹)</th>
                        <th colspan="3" class="timeframe-header">RSI</th>
                        <th colspan="3" class="timeframe-header">ADX</th>
                    </tr>
                    <tr class="sub-header">
                        <th>Daily</th>
                        <th>Weekly</th>
                        <th>Monthly</th>
                        <th>Daily</th>
                        <th>Weekly</th>
                        <th>Monthly</th>
                    </tr>
                </thead>
                <tbody>
                    {% for stock in sell_recommendations %}
                        <tr>
                            <td>{{ stock.symbol }}</td>
                            <td>{{ "%.2f"|format(stock.close) }}</td>
                            <td class="{% if stock.change_percent > 0 %}up{% elif stock.change_percent < 0 %}down{% else %}neutral{% endif %}">
                                {{ "%.2f"|format(stock.change_percent) }}%
                            </td>
                            <td>
                                {% if stock.recommendation == 'STRONG SELL' %}
                                    <span class="recommendation-badge strong-sell-badge">STRONG SELL</span>
                                {% elif stock.recommendation == 'SELL' %}
                                    <span class="recommendation-badge sell-badge">SELL</span>
                                {% endif %}
                            </td>
                            <td>
                                <div class="strength-indicator">
                                    <div class="strength-indicator-bar-sell" style="width: {{ stock.signal_strength*10 }}%;"></div>
                                </div>
                                <span class="ms-1">{{ stock.signal_strength }}</span>
                            </td>
                            <td>{{ "%.2f"|format(stock.target_price) }}</td>
                            <td class="{% if stock.daily_rsi < 30 %}oversold{% elif stock.daily_rsi > 70 %}overbought{% endif %}">
                                {{ stock.daily_rsi }}
                            </td>
                            <td class="{% if stock.weekly_rsi < 30 %}oversold{% elif stock.weekly_rsi > 70 %}overbought{% endif %}">
                                {{ stock.weekly_rsi }}
                            </td>
                            <td class="{% if stock.monthly_rsi < 30 %}oversold{% elif stock.monthly_rsi > 70 %}overbought{% endif %}">
                                {{ stock.monthly_rsi }}
                            </td>
                            <td class="{% if stock.daily_adx > 25 %}high-adx{% endif %}">
                                {{ stock.daily_adx }}
                            </td>
                            <td class="{% if stock.weekly_adx > 25 %}high-adx{% endif %}">
                                {{ stock.weekly_adx }}
                            </td>
                            <td class="{% if stock.monthly_adx > 25 %}high-adx{% endif %}">
                                {{ stock.monthly_adx }}
                            </td>
                        </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
{% else %}
    <div class="alert alert-info">
        No sell recommendations available at this time.
    </div>
{% endif %}
</div>
</div>

<footer class="text-center mt-4">
<p class="text-muted">
    &copy; 2025 Stock Analysis Bot. This analysis is for informational purposes only. Not financial advice.
</p>
</footer>

</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
<script>
document.addEventListener('DOMContentLoaded', function() {
    // Refresh button
    document.getElementById('refreshBtn').addEventListener('click', function() {
        location.reload();
    });
    
    // Search functionality for All Stocks table
    document.getElementById('searchAllStocks').addEventListener('input', function() {
        filterTable('allStocksTable', this.value);
    });
    
    // Search functionality for Buy Recommendations table
    document.getElementById('searchBuyRecs').addEventListener('input', function() {
        filterTable('buyRecsTable', this.value);
    });
    
    // Search functionality for Sell Recommendations table
    document.getElementById('searchSellRecs').addEventListener('input', function() {
        filterTable('sellRecsTable', this.value);
    });
    
    function filterTable(tableId, query) {
        query = query.toLowerCase();
        const table = document.getElementById(tableId);
        if (!table) return;
        
        const rows = table.getElementsByTagName('tbody')[0].getElementsByTagName('tr');
        
        for (let i = 0; i < rows.length; i++) {
            const symbol = rows[i].getElementsByTagName('td')[0].textContent.toLowerCase();
            if (symbol.includes(query)) {
                rows[i].style.display = '';
            } else {
                rows[i].style.display = 'none';
            }
        }
    }
});
</script>
</body>
</html>
'''
    )

# Main function to start the application
def main():
    """Start the web server and data update thread"""
    logger.info("Starting Stock Trading Bot with Multiple Timeframes")
    
    # Create CSV if not exists
    create_csv_if_not_exists()
    
    # Start data update thread
    update_thread = threading.Thread(target=update_stock_data, daemon=True)
    update_thread.start()
    
    # Start web server
    logger.info(f"Starting web server on port {WEB_PORT}")
    app.run(host='0.0.0.0', port=WEB_PORT)

if __name__ == "__main__":
    main()
