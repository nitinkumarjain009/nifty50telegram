import pandas as pd
import numpy as np
import os
import time
import datetime
import pytz
import requests
import talib as ta
import schedule
import threading
from flask import Flask, render_template, jsonify
import telepot
from telepot.loop import MessageLoop
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
import warnings
warnings.filterwarnings('ignore')

# Configuration
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"
TELEGRAM_GROUP = "@Stockniftybot"  # Telegram group username
CSV_FILE_PATH = "nifty50_stocks.csv"
TEMPLATES_DIR = "templates"
MARKET_OPEN_TIME = "09:15"  # IST
MARKET_CLOSE_TIME = "15:30"  # IST
CHECK_INTERVAL_MINUTES = 5  # Check every 5 minutes during market hours
IST_TIMEZONE = pytz.timezone('Asia/Kolkata')

# Initialize the Flask app
app = Flask(__name__, template_folder=TEMPLATES_DIR)
app.config['JSON_SORT_KEYS'] = False

# Initialize Telegram bot
bot = telepot.Bot(TELEGRAM_BOT_TOKEN)

# Global variables to store recommendations and analysis
current_recommendations = {}
daily_analysis = {}
last_update_time = None

def is_market_hours():
    """Check if it's currently market hours in IST"""
    now = datetime.datetime.now(IST_TIMEZONE)
    current_time = now.strftime("%H:%M")
    current_day = now.weekday()
    
    # Check if it's a weekday (0-4 is Monday to Friday)
    if current_day < 5:
        return MARKET_OPEN_TIME <= current_time <= MARKET_CLOSE_TIME
    return False

def load_stock_data():
    """Load stocks data from CSV file"""
    try:
        return pd.read_csv(CSV_FILE_PATH)
    except Exception as e:
        print(f"Error loading stock data: {e}")
        send_telegram_message(f"Error loading stock data: {e}")
        return pd.DataFrame()

def fetch_latest_data(symbol):
    """Fetch latest stock data for the given symbol"""
    try:
        # This is a placeholder. In a real implementation, you would use an API like
        # NSE India, Yahoo Finance, AlphaVantage, etc. to fetch real-time data
        # For this example, we'll simulate data
        
        # Simulation: Get historical data and add some random variation for today
        hist_data = pd.DataFrame({
            'date': pd.date_range(end=datetime.datetime.now(), periods=100, freq='D'),
            'open': np.random.normal(500, 10, 100),
            'high': np.random.normal(505, 15, 100),
            'low': np.random.normal(495, 15, 100),
            'close': np.random.normal(500, 10, 100),
            'volume': np.random.normal(1000000, 200000, 100)
        })
        
        # Make sure high is the highest and low is the lowest for each day
        for i in range(len(hist_data)):
            values = [hist_data.loc[i, 'open'], hist_data.loc[i, 'close']]
            hist_data.loc[i, 'high'] = max(values) + abs(np.random.normal(0, 2))
            hist_data.loc[i, 'low'] = min(values) - abs(np.random.normal(0, 2))
        
        hist_data['symbol'] = symbol
        return hist_data
    except Exception as e:
        print(f"Error fetching data for {symbol}: {e}")
        return pd.DataFrame()

def calculate_indicators(df):
    """Calculate technical indicators for the given dataframe"""
    try:
        # Ensure the dataframe has the required columns
        required_columns = ['close', 'high', 'low', 'volume']
        if not all(col in df.columns for col in required_columns):
            raise ValueError("Data missing required columns for technical analysis")
        
        # Convert columns to numeric if they aren't already
        for col in required_columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Calculate Moving Averages
        df['SMA_20'] = ta.SMA(df['close'].values, timeperiod=20)
        df['SMA_50'] = ta.SMA(df['close'].values, timeperiod=50)
        df['SMA_200'] = ta.SMA(df['close'].values, timeperiod=200)
        
        # Calculate EMA
        df['EMA_12'] = ta.EMA(df['close'].values, timeperiod=12)
        df['EMA_26'] = ta.EMA(df['close'].values, timeperiod=26)
        
        # Calculate MACD
        df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = ta.MACD(
            df['close'].values, fastperiod=12, slowperiod=26, signalperiod=9
        )
        
        # Calculate RSI
        df['RSI'] = ta.RSI(df['close'].values, timeperiod=14)
        
        # Calculate Bollinger Bands
        df['BB_Upper'], df['BB_Middle'], df['BB_Lower'] = ta.BBANDS(
            df['close'].values, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0
        )
        
        # Calculate Stochastic
        df['SlowK'], df['SlowD'] = ta.STOCH(
            df['high'].values, df['low'].values, df['close'].values,
            fastk_period=14, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0
        )
        
        # Calculate ADX
        df['ADX'] = ta.ADX(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
        
        # Calculate OBV (On-Balance Volume)
        df['OBV'] = ta.OBV(df['close'].values, df['volume'].values)
        
        # Calculate ATR
        df['ATR'] = ta.ATR(df['high'].values, df['low'].values, df['close'].values, timeperiod=14)
        
        # Calculate VWAP (Volume Weighted Average Price) - custom implementation
        df['VWAP'] = (df['volume'] * df['close']).cumsum() / df['volume'].cumsum()
        
        return df
    except Exception as e:
        print(f"Error calculating indicators: {e}")
        return df

def get_recommendations(df, symbol):
    """Generate trading recommendations based on technical indicators"""
    try:
        # Get the latest data point which contains all our indicators
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        
        recommendations = {}
        
        # Moving Average recommendations
        if latest['close'] > latest['SMA_20'] and prev['close'] <= prev['SMA_20']:
            recommendations['SMA_20'] = 'BUY'
        elif latest['close'] < latest['SMA_20'] and prev['close'] >= prev['SMA_20']:
            recommendations['SMA_20'] = 'SELL'
        else:
            recommendations['SMA_20'] = 'HOLD'
            
        if latest['close'] > latest['SMA_50'] and prev['close'] <= prev['SMA_50']:
            recommendations['SMA_50'] = 'BUY'
        elif latest['close'] < latest['SMA_50'] and prev['close'] >= prev['SMA_50']:
            recommendations['SMA_50'] = 'SELL'
        else:
            recommendations['SMA_50'] = 'HOLD'
            
        # Golden Cross / Death Cross
        if latest['SMA_50'] > latest['SMA_200'] and prev['SMA_50'] <= prev['SMA_200']:
            recommendations['Golden_Cross'] = 'BUY'
        elif latest['SMA_50'] < latest['SMA_200'] and prev['SMA_50'] >= prev['SMA_200']:
            recommendations['Death_Cross'] = 'SELL'
        else:
            recommendations['MA_Cross'] = 'HOLD'
        
        # MACD recommendations
        if latest['MACD'] > latest['MACD_Signal'] and prev['MACD'] <= prev['MACD_Signal']:
            recommendations['MACD'] = 'BUY'
        elif latest['MACD'] < latest['MACD_Signal'] and prev['MACD'] >= prev['MACD_Signal']:
            recommendations['MACD'] = 'SELL'
        else:
            recommendations['MACD'] = 'HOLD'
        
        # RSI recommendations
        if latest['RSI'] < 30:
            recommendations['RSI'] = 'BUY'
        elif latest['RSI'] > 70:
            recommendations['RSI'] = 'SELL'
        else:
            recommendations['RSI'] = 'HOLD'
        
        # Bollinger Bands recommendations
        if latest['close'] < latest['BB_Lower']:
            recommendations['Bollinger'] = 'BUY'
        elif latest['close'] > latest['BB_Upper']:
            recommendations['Bollinger'] = 'SELL'
        else:
            recommendations['Bollinger'] = 'HOLD'
        
        # Stochastic recommendations
        if latest['SlowK'] < 20 and latest['SlowD'] < 20 and latest['SlowK'] > latest['SlowD']:
            recommendations['Stochastic'] = 'BUY'
        elif latest['SlowK'] > 80 and latest['SlowD'] > 80 and latest['SlowK'] < latest['SlowD']:
            recommendations['Stochastic'] = 'SELL'
        else:
            recommendations['Stochastic'] = 'HOLD'
        
        # ADX recommendations (trend strength)
        if latest['ADX'] > 25:
            trend_strength = 'STRONG'
        else:
            trend_strength = 'WEAK'
        recommendations['ADX'] = trend_strength
        
        # Aggregate recommendation
        buy_count = sum(1 for rec in recommendations.values() if rec == 'BUY')
        sell_count = sum(1 for rec in recommendations.values() if rec == 'SELL')
        
        if buy_count > sell_count and buy_count >= 3:
            overall = 'BUY'
        elif sell_count > buy_count and sell_count >= 3:
            overall = 'SELL'
        else:
            overall = 'HOLD'
        
        recommendations['OVERALL'] = overall
        
        return {
            'symbol': symbol,
            'timestamp': datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'),
            'price': round(latest['close'], 2),
            'recommendations': recommendations
        }
    except Exception as e:
        print(f"Error generating recommendations for {symbol}: {e}")
        return {
            'symbol': symbol,
            'timestamp': datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'),
            'error': str(e)
        }

def create_technical_chart(df, symbol):
    """Create a technical analysis chart using Plotly"""
    try:
        # Create subplot with 3 rows
        fig = make_subplots(rows=3, cols=1, 
                            shared_xaxes=True, 
                            vertical_spacing=0.05,
                            row_heights=[0.6, 0.2, 0.2],
                            subplot_titles=(f"{symbol} Price", "Volume", "RSI"))
        
        # Add candlestick chart
        fig.add_trace(go.Candlestick(x=df['date'],
                                     open=df['open'],
                                     high=df['high'],
                                     low=df['low'],
                                     close=df['close'],
                                     name='Price'),
                      row=1, col=1)
        
        # Add Moving Averages
        fig.add_trace(go.Scatter(x=df['date'], y=df['SMA_20'], name='SMA 20', line=dict(color='blue')), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['SMA_50'], name='SMA 50', line=dict(color='orange')), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['SMA_200'], name='SMA 200', line=dict(color='red')), row=1, col=1)
        
        # Add Bollinger Bands
        fig.add_trace(go.Scatter(x=df['date'], y=df['BB_Upper'], name='BB Upper', line=dict(color='rgba(173, 204, 255, 0.7)')), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=df['BB_Lower'], name='BB Lower', line=dict(color='rgba(173, 204, 255, 0.7)')), row=1, col=1)
        
        # Add volume bar chart
        colors = ['green' if row['close'] >= row['open'] else 'red' for _, row in df.iterrows()]
        fig.add_trace(go.Bar(x=df['date'], y=df['volume'], name='Volume', marker_color=colors), row=2, col=1)
        
        # Add RSI
        fig.add_trace(go.Scatter(x=df['date'], y=df['RSI'], name='RSI', line=dict(color='purple')), row=3, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=[70] * len(df), name='RSI Overbought', line=dict(color='rgba(255,0,0,0.5)', dash='dash')), row=3, col=1)
        fig.add_trace(go.Scatter(x=df['date'], y=[30] * len(df), name='RSI Oversold', line=dict(color='rgba(0,255,0,0.5)', dash='dash')), row=3, col=1)
        
        # Update layout
        fig.update_layout(title=f'Technical Analysis for {symbol}',
                          xaxis_rangeslider_visible=False,
                          height=800,
                          width=1200,
                          showlegend=True)
        
        # Update y-axis labels
        fig.update_yaxes(title_text="Price", row=1, col=1)
        fig.update_yaxes(title_text="Volume", row=2, col=1)
        fig.update_yaxes(title_text="RSI", row=3, col=1)
        
        return fig
    except Exception as e:
        print(f"Error creating chart for {symbol}: {e}")
        return None

def send_telegram_message(message, target=None):
    """Send a message to Telegram channel or group"""
    try:
        if target is None:
            # Send to personal chat ID
            bot.sendMessage(TELEGRAM_CHAT_ID, message, parse_mode='Markdown')
            print(f"Message sent to Telegram chat: {message[:50]}...")
            
            # Also send to group
            bot.sendMessage(TELEGRAM_GROUP, message, parse_mode='Markdown')
            print(f"Message sent to Telegram group {TELEGRAM_GROUP}: {message[:50]}...")
        else:
            # Send to specified target
            bot.sendMessage(target, message, parse_mode='Markdown')
            print(f"Message sent to Telegram {target}: {message[:50]}...")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def send_telegram_photo(photo_path, caption, target=None):
    """Send a photo to Telegram channel or group"""
    try:
        if target is None:
            # Send to personal chat ID
            with open(photo_path, 'rb') as photo:
                bot.sendPhoto(TELEGRAM_CHAT_ID, photo, caption=caption)
            print(f"Photo sent to Telegram chat: {caption[:50]}...")
            
            # Also send to group
            with open(photo_path, 'rb') as photo:
                bot.sendPhoto(TELEGRAM_GROUP, photo, caption=caption)
            print(f"Photo sent to Telegram group {TELEGRAM_GROUP}: {caption[:50]}...")
        else:
            # Send to specified target
            with open(photo_path, 'rb') as photo:
                bot.sendPhoto(target, photo, caption=caption)
            print(f"Photo sent to Telegram {target}: {caption[:50]}...")
    except Exception as e:
        print(f"Error sending Telegram photo: {e}")

def format_recommendations_message(recommendations):
    """Format recommendations for Telegram message"""
    msg = f"*Stock Recommendations ({recommendations['timestamp']})*\n\n"
    msg += f"*Symbol: {recommendations['symbol']}*\n"
    msg += f"Price: ₹{recommendations['price']}\n\n"
    
    msg += "*Technical Indicators:*\n"
    for indicator, signal in recommendations['recommendations'].items():
        if indicator != 'OVERALL':
            msg += f"- {indicator}: {signal}\n"
    
    msg += f"\n*OVERALL RECOMMENDATION: {recommendations['recommendations']['OVERALL']}*"
    return msg

def run_analysis():
    """Run analysis on all stocks and send recommendations"""
    global current_recommendations, last_update_time
    
    stocks_df = load_stock_data()
    all_recommendations = []
    
    if stocks_df.empty:
        message = "❌ Error: Could not load stock data."
        send_telegram_message(message)
        return
    
    print(f"Running analysis at {datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Process each stock
    for _, row in stocks_df.iterrows():
        symbol = row['symbol']
        
        # Fetch latest data
        stock_data = fetch_latest_data(symbol)
        
        if stock_data.empty:
            print(f"Skipping {symbol} due to data fetching error")
            continue
        
        # Calculate technical indicators
        stock_data = calculate_indicators(stock_data)
        
        # Generate recommendations
        recommendations = get_recommendations(stock_data, symbol)
        all_recommendations.append(recommendations)
        
        # If it's a significant recommendation (BUY or SELL), send individual notification
        if recommendations.get('recommendations', {}).get('OVERALL') in ['BUY', 'SELL']:
            message = format_recommendations_message(recommendations)
            send_telegram_message(message)  # Will send to both personal chat and group
            
            # Create and save chart
            fig = create_technical_chart(stock_data, symbol)
            if fig:
                chart_path = f"static/{symbol}_chart.png"
                pio.write_image(fig, chart_path)
                send_telegram_photo(chart_path, f"Technical chart for {symbol}")  # Will send to both personal chat and group
    
    # Update global recommendations
    current_recommendations = all_recommendations
    last_update_time = datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    
    return all_recommendations

def generate_daily_analysis():
    """Generate daily analysis after market hours"""
    global daily_analysis
    
    stocks_df = load_stock_data()
    
    if stocks_df.empty:
        message = "❌ Error: Could not load stock data for daily analysis."
        send_telegram_message(message)
        return
    
    buy_recommendations = []
    sell_recommendations = []
    hold_recommendations = []
    
    # Run analysis for all stocks
    for _, row in stocks_df.iterrows():
        symbol = row['symbol']
        stock_data = fetch_latest_data(symbol)
        
        if stock_data.empty:
            continue
            
        stock_data = calculate_indicators(stock_data)
        recommendations = get_recommendations(stock_data, symbol)
        
        overall_rec = recommendations.get('recommendations', {}).get('OVERALL')
        if overall_rec == 'BUY':
            buy_recommendations.append({
                'symbol': symbol,
                'price': recommendations['price'],
                'strength': sum(1 for rec in recommendations['recommendations'].values() if rec == 'BUY')
            })
        elif overall_rec == 'SELL':
            sell_recommendations.append({
                'symbol': symbol,
                'price': recommendations['price'],
                'strength': sum(1 for rec in recommendations['recommendations'].values() if rec == 'SELL')
            })
        else:
            hold_recommendations.append({
                'symbol': symbol,
                'price': recommendations['price']
            })
    
    # Sort by strength
    buy_recommendations.sort(key=lambda x: x['strength'], reverse=True)
    sell_recommendations.sort(key=lambda x: x['strength'], reverse=True)
    
    # Format the daily analysis message
    now = datetime.datetime.now(IST_TIMEZONE)
    message = f"*Daily Market Analysis - {now.strftime('%Y-%m-%d')}*\n\n"
    
    message += f"*BUY Recommendations ({len(buy_recommendations)}):*\n"
    for i, rec in enumerate(buy_recommendations[:10], 1):
        message += f"{i}. {rec['symbol']} at ₹{rec['price']} (Strength: {rec['strength']})\n"
    
    message += f"\n*SELL Recommendations ({len(sell_recommendations)}):*\n"
    for i, rec in enumerate(sell_recommendations[:10], 1):
        message += f"{i}. {rec['symbol']} at ₹{rec['price']} (Strength: {rec['strength']})\n"
    
    message += f"\n*HOLD Recommendations: {len(hold_recommendations)} stocks*"
    
    # Store daily analysis
    daily_analysis = {
        'date': now.strftime('%Y-%m-%d'),
        'buy': buy_recommendations,
        'sell': sell_recommendations,
        'hold': len(hold_recommendations)
    }
    
    # Send to Telegram - both personal chat and group
    send_telegram_message(message)  # Will send to both
    
    return daily_analysis

def schedule_jobs():
    """Schedule all jobs"""
    # Schedule market hours checks every X minutes
    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(lambda: job_wrapper(run_analysis) if is_market_hours() else None)
    
    # Schedule daily analysis at market close
    schedule.every().day.at(MARKET_CLOSE_TIME).do(lambda: job_wrapper(generate_daily_analysis))
    
    # Run jobs continuously
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

def job_wrapper(job_func):
    """Wrapper for scheduled jobs to handle exceptions"""
    try:
        return job_func()
    except Exception as e:
        print(f"Error in scheduled job {job_func.__name__}: {e}")
        error_message = f"❌ Error in {job_func.__name__}: {e}"
        send_telegram_message(error_message)  # Will send to both personal chat and group

# Flask routes
@app.route('/')
def index():
    """Main page that displays recommendations"""
    return render_template('index.html')

@app.route('/api/recommendations')
def api_recommendations():
    """API endpoint to get current recommendations"""
    return jsonify({
        'last_update': last_update_time,
        'recommendations': current_recommendations
    })

@app.route('/api/daily')
def api_daily():
    """API endpoint to get daily analysis"""
    return jsonify(daily_analysis)

def run_flask():
    """Run Flask app in a separate thread"""
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

def main():
    """Main function to run the stock analyzer"""
    # Create directories if they don't exist
    os.makedirs('static', exist_ok=True)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    
    # Create basic HTML template if it doesn't exist
    index_template_path = os.path.join(TEMPLATES_DIR, 'index.html')
    if not os.path.exists(index_template_path):
        with open(index_template_path, 'w') as f:
            f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Nifty 50 Stock Analyzer</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        .recommendation-buy { color: green; font-weight: bold; }
        .recommendation-sell { color: red; font-weight: bold; }
        .recommendation-hold { color: orange; }
        .card { margin-bottom: 20px; }
        .last-update { font-style: italic; color: #666; }
    </style>
</head>
<body>
    <div class="container mt-4">
        <h1 class="mb-4">Nifty 50 Stock Analyzer</h1>
        
        <div class="row">
            <div class="col-md-12">
                <div class="card">
                    <div class="card-header">
                        <h5>Current Recommendations</h5>
                        <p class="last-update" id="lastUpdate">Last updated: Loading...</p>
                    </div>
                    <div class="card-body">
                        <div class="table-responsive">
                            <table class="table table-striped table-hover">
                                <thead>
                                    <tr>
                                        <th>Symbol</th>
                                        <th>Price</th>
                                        <th>Overall</th>
                                        <th>SMA 20</th>
                                        <th>SMA 50</th>
                                        <th>MA Cross</th>
                                        <th>MACD</th>
                                        <th>RSI</th>
                                        <th>Bollinger</th>
                                        <th>Stochastic</th>
                                        <th>ADX</th>
                                    </tr>
                                </thead>
                                <tbody id="recommendationsTable">
                                    <tr>
                                        <td colspan="11" class="text-center">Loading recommendations...</td>
                                    </tr>
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="row mt-4">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">
                        <h5>Daily Analysis</h5>
                        <p class="last-update" id="dailyAnalysisDate">Date: Loading...</p>
                    </div>
                    <div class="card-body">
                        <h6>BUY Recommendations</h6>
                        <ul id="buyList">
                            <li>Loading...</li>
                        </ul>
                        
                        <h6>SELL Recommendations</h6>
                        <ul id="sellList">
                            <li>Loading...</li>
                        </ul>
                        
                        <p id="holdCount">HOLD Recommendations: Loading...</p>
                    </div>
                </div>
            </div>
            
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">
                        <h5>Market Stats</h5>
                    </div>
                    <div class="card-body">
                        <canvas id="recommendationChart" width="400" height="300"></canvas>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let recommendationChart;
        
        // Function to update recommendations
        function updateRecommendations() {
            fetch('/api/recommendations')
                .then(response => response.json())
                .then(data => {
                    const tableBody = document.getElementById('recommendationsTable');
                    const lastUpdate = document.getElementById('lastUpdate');
                    
                    if (data.last_update) {
                        lastUpdate.textContent = `Last updated: ${data.last_update}`;
                    }
                    
                    if (data.recommendations && data.recommendations.length > 0) {
                        tableBody.innerHTML = '';
                        
                        data.recommendations.forEach(rec => {
                            const row = document.createElement('tr');
                            
                            // Add cells
                            row.innerHTML = `
                                <td>${rec.symbol}</td>
                                <td>₹${rec.price}</td>
                                <td class="recommendation-${rec.recommendations.OVERALL.toLowerCase()}">${rec.recommendations.OVERALL}</td>
                                <td class="recommendation-${rec.recommendations.SMA_20.toLowerCase()}">${rec.recommendations.SMA_20}</td>
                                <td class="recommendation-${rec.recommendations.SMA_50.toLowerCase()}">${rec.recommendations.SMA_50}</td>
                                <td class="recommendation-${(rec.recommendations.MA_Cross || rec.recommendations.Golden_Cross || rec.recommendations.Death_Cross || 'HOLD').toLowerCase()}">${rec.recommendations.MA_Cross || rec.recommendations.Golden_Cross || rec.recommendations.Death_Cross || 'HOLD'}</td>
                                <td class="recommendation-${rec.recommendations.MACD.toLowerCase()}">${rec.recommendations.MACD}</td>
                                <td class="recommendation-${rec.recommendations.RSI.toLowerCase()}">${rec.recommendations.RSI}</td>
                                <td class="recommendation-${rec.recommendations.Bollinger.toLowerCase()}">${rec.recommendations.Bollinger}</td>
                                <td class="recommendation-${rec.recommendations.Stochastic.toLowerCase()}">${rec.recommendations.Stochastic}</td>
                                <td>${rec.recommendations.ADX}</td>
                            `;
                            
                            tableBody.appendChild(row);
                        });
                    }
                })
                .catch(error => {
                    console.error('Error fetching recommendations:', error);
                });
        }
        
        // Function to update daily analysis
        function updateDailyAnalysis() {
            fetch('/api/daily')
                .then(response => response.json())
                .then(data => {
                    const buyList = document.getElementById('buyList');
                    const sellList = document.getElementById('sellList');
                    const holdCount = document.getElementById('holdCount');
                    const dailyAnalysisDate = document.getElementById('dailyAnalysisDate');
                    
                    if (data.date) {
                        dailyAnalysisDate.textContent = `Date: ${data.date}`;
                        
                        // Update BUY list
                        buyList.innerHTML = '';
                        if (data.buy && data.buy.length > 0) {
                            data.buy.slice(0, 10).forEach((item, index) => {
                                const li = document.createElement('li');
                                li.innerHTML = `<strong>${item.symbol}</strong> at ₹${item.price} (Target: ₹${item.target_price}) - Strength: ${item.strength}`;
                                buyList.appendChild(li);
                            });
                        } else {
                            buyList.innerHTML = '<li>No BUY recommendations today</li>';
                        }
                        
                        // Update SELL list
                        sellList.innerHTML = '';
                        if (data.sell && data.sell.length > 0) {
                            data.sell.slice(0, 10).forEach((item, index) => {
                                const li = document.createElement('li');
                                li.innerHTML = `<strong>${item.symbol}</strong> at ₹${item.price} (Target: ₹${item.target_price}) - Strength: ${item.strength}`;
                                sellList.appendChild(li);
                            });
                        } else {
                            sellList.innerHTML = '<li>No SELL recommendations today</li>';
                        }
                        
                        // Update HOLD count
                        holdCount.textContent = `HOLD Recommendations: ${data.hold || 0} stocks`;
                        
                        // Update chart
                        updateChart(data);
                    }
                })
                .catch(error => {
                    console.error('Error fetching daily analysis:', error);
                });
        }
        
        // Function to update chart
        function updateChart(data) {
            const ctx = document.getElementById('recommendationChart').getContext('2d');
            
            if (recommendationChart) {
                recommendationChart.destroy();
            }
            
            recommendationChart = new Chart(ctx, {
                type: 'pie',
                data: {
                    labels: ['BUY', 'SELL', 'HOLD'],
                    datasets: [{
                        label: 'Recommendations',
                        data: [
                            data.buy ? data.buy.length : 0,
                            data.sell ? data.sell.length : 0,
                            data.hold || 0
                        ],
                        backgroundColor: [
                            'rgba(75, 192, 192, 0.6)',
                            'rgba(255, 99, 132, 0.6)',
                            'rgba(255, 206, 86, 0.6)'
                        ],
                        borderColor: [
                            'rgba(75, 192, 192, 1)',
                            'rgba(255, 99, 132, 1)',
                            'rgba(255, 206, 86, 1)'
                        ],
                        borderWidth: 1
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            position: 'bottom',
                        },
                        title: {
                            display: true,
                            text: 'Recommendation Distribution'
                        }
                    }
                }
            });
        }
        
        // Initial update
        updateRecommendations();
        updateDailyAnalysis();
        
        // Update every 5 minutes
        setInterval(updateRecommendations, 300000);
        setInterval(updateDailyAnalysis, 300000);
    </script>
</body>
</html>
            ''')
    
    # Create example CSV file if it doesn't exist
    if not os.path.exists(CSV_FILE_PATH):
        with open(CSV_FILE_PATH, 'w') as f:
            f.write("symbol,name,sector\n")
            f.write("RELIANCE,Reliance Industries,Energy\n")
            f.write("TCS,Tata Consultancy Services,IT\n")
            f.write("HDFCBANK,HDFC Bank,Banking\n")
            f.write("INFY,Infosys,IT\n")
            f.write("ICICIBANK,ICICI Bank,Banking\n")
            f.write("HINDUNILVR,Hindustan Unilever,FMCG\n")
            f.write("SBIN,State Bank of India,Banking\n")
            f.write("BHARTIARTL,Bharti Airtel,Telecom\n")
            f.write("ITC,ITC,FMCG\n")
            f.write("KOTAKBANK,Kotak Mahindra Bank,Banking\n")
    
    # Send startup message
    startup_message = (
        "*📈 Nifty 50 Stock Analyzer Started 📉*\n\n"
        f"Bot started at {datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"Market hours: {MARKET_OPEN_TIME} - {MARKET_CLOSE_TIME} IST\n"
        f"Check interval: Every {CHECK_INTERVAL_MINUTES} minutes during market hours\n\n"
        "The bot will send recommendations during market hours and a daily summary after market close."
    )
    send_telegram_message(startup_message)  # Will send to both personal chat and group
    
    # Start Flask app in a separate thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    # Define Telegram bot handler
    def handle_message(msg):
        """Handle incoming Telegram messages"""
        content_type, chat_type, chat_id = telepot.glance(msg)
        
        if content_type != 'text':
            return
        
        command = msg['text'].lower()
        
        if command == '/start':
            response = (
                "*Welcome to Nifty 50 Stock Analyzer Bot!*\n\n"
                "Available commands:\n"
                "/status - Check bot status\n"
                "/recommendations - Get current recommendations\n"
                "/daily - Get daily analysis\n"
                "/analyze <symbol> - Analyze a specific stock"
            )
            bot.sendMessage(chat_id, response, parse_mode='Markdown')
        
        elif command == '/status':
            status = (
                "*Bot Status*\n\n"
                f"Current time: {datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')} IST\n"
                f"Market hours: {MARKET_OPEN_TIME} - {MARKET_CLOSE_TIME} IST\n"
                f"Market is currently {'OPEN' if is_market_hours() else 'CLOSED'}\n"
                f"Last analysis update: {last_update_time or 'Not yet run'}\n"
                f"Number of stocks being monitored: {len(load_stock_data()) if not load_stock_data().empty else 0}\n"
                f"Check interval: Every {CHECK_INTERVAL_MINUTES} minutes during market hours"
            )
            bot.sendMessage(chat_id, status, parse_mode='Markdown')
        
        elif command == '/recommendations':
            if not current_recommendations:
                bot.sendMessage(chat_id, "No recommendations available yet. Please wait for the next analysis run.", parse_mode='Markdown')
                return
                
            # Get BUY and SELL recommendations only
            buy_sell_recs = [r for r in current_recommendations if r.get('recommendations', {}).get('OVERALL') in ['BUY', 'SELL']]
            
            if not buy_sell_recs:
                bot.sendMessage(chat_id, "No BUY or SELL recommendations in the latest analysis.", parse_mode='Markdown')
                return
                
            response = f"*Latest Recommendations ({last_update_time})*\n\n"
            
            for rec in buy_sell_recs:
                response += f"*{rec['symbol']} - {rec['recommendations']['OVERALL']}*\n"
                response += f"Price: ₹{rec['price']}\n"
                response += f"Target: ₹{rec['target_price']}\n"
                response += f"Stop Loss: ₹{rec['stop_loss']}\n\n"
                
            bot.sendMessage(chat_id, response, parse_mode='Markdown')
        
        elif command == '/daily':
            if not daily_analysis:
                bot.sendMessage(chat_id, "No daily analysis available yet. It will be generated after market hours.", parse_mode='Markdown')
                return
                
            # Format daily analysis message (same as what's sent automatically)
            message = f"*Daily Market Analysis - {daily_analysis['date']}*\n\n"
            
            message += f"*BUY Recommendations ({len(daily_analysis['buy'])}):*\n"
            for i, rec in enumerate(daily_analysis['buy'][:10], 1):
                message += f"{i}. {rec['symbol']} at ₹{rec['price']} (Target: ₹{rec['target_price']}) - Strength: {rec['strength']}\n"
            
            message += f"\n*SELL Recommendations ({len(daily_analysis['sell'])}):*\n"
            for i, rec in enumerate(daily_analysis['sell'][:10], 1):
                message += f"{i}. {rec['symbol']} at ₹{rec['price']} (Target: ₹{rec['target_price']}) - Strength: {rec['strength']}\n"
            
            message += f"\n*HOLD Recommendations: {daily_analysis['hold']} stocks*"
            
            bot.sendMessage(chat_id, message, parse_mode='Markdown')
        
        elif command.startswith('/analyze '):
            symbol = command.split(' ')[1].upper()
            bot.sendMessage(chat_id, f"Analyzing {symbol}... Please wait.", parse_mode='Markdown')
            
            # Fetch and analyze the stock
            stock_data = fetch_latest_data(symbol)
            
            if stock_data.empty:
                bot.sendMessage(chat_id, f"❌ Error: Could not fetch data for {symbol}. Please verify the symbol is correct.", parse_mode='Markdown')
                return
                
            stock_data = calculate_indicators(stock_data)
            recommendations = get_recommendations(stock_data, symbol)
            
            # Format and send the message
            message = format_recommendations_message(recommendations)
            bot.sendMessage(chat_id, message, parse_mode='Markdown')
            
            # Create and send chart
            fig = create_technical_chart(stock_data, symbol)
            if fig:
                chart_path = f"static/{symbol}_analysis.png"
                pio.write_image(fig, chart_path)
                with open(chart_path, 'rb') as photo:
                    bot.sendPhoto(chat_id, photo, caption=f"Technical chart for {symbol}")
        
        else:
            bot.sendMessage(chat_id, "Unknown command. Use /start to see available commands.", parse_mode='Markdown')
    
    # Start the Telegram bot
    MessageLoop(bot, handle_message).run_as_thread()
    
    # Calculate target prices and stop loss when generating recommendations
    def calculate_price_targets(df, recommendations):
        """Calculate target price and stop loss based on ATR and current trend"""
        try:
            latest = df.iloc[-1]
            current_price = latest['close']
            atr = latest['ATR']
            
            overall_rec = recommendations['recommendations']['OVERALL']
            
            # Calculate multipliers based on trend strength (ADX)
            adx_value = latest['ADX']
            if adx_value > 40:  # Very strong trend
                multiplier = 3.0
            elif adx_value > 25:  # Strong trend
                multiplier = 2.5
            else:  # Weak trend
                multiplier = 2.0
                
            # Calculate target price and stop loss
            if overall_rec == 'BUY':
                target_price = round(current_price + (atr * multiplier), 2)
                stop_loss = round(current_price - (atr * 1.5), 2)
            elif overall_rec == 'SELL':
                target_price = round(current_price - (atr * multiplier), 2)
                stop_loss = round(current_price + (atr * 1.5), 2)
            else:
                target_price = round(current_price, 2)
                stop_loss = round(current_price, 2)
                
            recommendations['target_price'] = target_price
            recommendations['stop_loss'] = stop_loss
            
            return recommendations
        except Exception as e:
            print(f"Error calculating price targets: {e}")
            recommendations['target_price'] = recommendations['price']
            recommendations['stop_loss'] = recommendations['price']
            return recommendations
    
    # Update the get_recommendations function to include target prices
    def get_recommendations_with_targets(df, symbol):
        """Enhanced version of get_recommendations that includes target prices"""
        base_recommendations = get_recommendations(df, symbol)
        return calculate_price_targets(df, base_recommendations)
    
    # Update the run_analysis function to use the enhanced recommendations
    original_run_analysis = run_analysis
    def enhanced_run_analysis():
        """Enhanced version of run_analysis that includes target prices"""
        global current_recommendations, last_update_time
        
        stocks_df = load_stock_data()
        all_recommendations = []
        
        if stocks_df.empty:
            message = "❌ Error: Could not load stock data."
            send_telegram_message(message)
            return
        
        print(f"Running analysis at {datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Process each stock
        for _, row in stocks_df.iterrows():
            symbol = row['symbol']
            
            # Fetch latest data
            stock_data = fetch_latest_data(symbol)
            
            if stock_data.empty:
                print(f"Skipping {symbol} due to data fetching error")
                continue
            
            # Calculate technical indicators
            stock_data = calculate_indicators(stock_data)
            
            # Generate recommendations with targets
            recommendations = get_recommendations_with_targets(stock_data, symbol)
            all_recommendations.append(recommendations)
            
            # If it's a significant recommendation (BUY or SELL), send individual notification
            if recommendations.get('recommendations', {}).get('OVERALL') in ['BUY', 'SELL']:
                message = format_recommendations_message(recommendations)
                # Add target price and stop loss to the message
                message += f"\n\nTarget Price: ₹{recommendations['target_price']}"
                message += f"\nStop Loss: ₹{recommendations['stop_loss']}"
                send_telegram_message(message)
                
                # Create and save chart
                fig = create_technical_chart(stock_data, symbol)
                if fig:
                    chart_path = f"static/{symbol}_chart.png"
                    pio.write_image(fig, chart_path)
                    send_telegram_photo(chart_path, f"Technical chart for {symbol}")
        
        # Update global recommendations
        current_recommendations = all_recommendations
        last_update_time = datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
        
        return all_recommendations
    
    # Replace original function with enhanced one
    run_analysis = enhanced_run_analysis
    
    # Update the generate_daily_analysis function to include target prices
    original_generate_daily_analysis = generate_daily_analysis
    def enhanced_generate_daily_analysis():
        """Enhanced version of generate_daily_analysis that includes target prices"""
        global daily_analysis
        
        stocks_df = load_stock_data()
        
        if stocks_df.empty:
            message = "❌ Error: Could not load stock data for daily analysis."
            send_telegram_message(message)
            return
        
        buy_recommendations = []
        sell_recommendations = []
        hold_recommendations = []
        
        # Run analysis for all stocks
        for _, row in stocks_df.iterrows():
            symbol = row['symbol']
            stock_data = fetch_latest_data(symbol)
            
            if stock_data.empty:
                continue
                
            stock_data = calculate_indicators(stock_data)
            recommendations = get_recommendations_with_targets(stock_data, symbol)
            
            overall_rec = recommendations.get('recommendations', {}).get('OVERALL')
            if overall_rec == 'BUY':
                buy_recommendations.append({
                    'symbol': symbol,
                    'price': recommendations['price'],
                    'target_price': recommendations['target_price'],
                    'stop_loss': recommendations['stop_loss'],
                    'strength': sum(1 for rec in recommendations['recommendations'].values() if rec == 'BUY')
                })
            elif overall_rec == 'SELL':
                sell_recommendations.append({
                    'symbol': symbol,
                    'price': recommendations['price'],
                    'target_price': recommendations['target_price'],
                    'stop_loss': recommendations['stop_loss'],
                    'strength': sum(1 for rec in recommendations['recommendations'].values() if rec == 'SELL')
                })
            else:
                hold_recommendations.append({
                    'symbol': symbol,
                    'price': recommendations['price']
                })
        
        # Sort by strength
        buy_recommendations.sort(key=lambda x: x['strength'], reverse=True)
        sell_recommendations.sort(key=lambda x: x['strength'], reverse=True)
        
        # Format the daily analysis message
        now = datetime.datetime.now(IST_TIMEZONE)
        message = f"*Daily Market Analysis - {now.strftime('%Y-%m-%d')}*\n\n"
        
        message += f"*BUY Recommendations ({len(buy_recommendations)}):*\n"
        for i, rec in enumerate(buy_recommendations[:10], 1):
            message += f"{i}. {rec['symbol']} at ₹{rec['price']} (Target: ₹{rec['target_price']}, SL: ₹{rec['stop_loss']}) - Strength: {rec['strength']}\n"
        
        message += f"\n*SELL Recommendations ({len(sell_recommendations)}):*\n"
        for i, rec in enumerate(sell_recommendations[:10], 1):
            message += f"{i}. {rec['symbol']} at ₹{rec['price']} (Target: ₹{rec['target_price']}, SL: ₹{rec['stop_loss']}) - Strength: {rec['strength']}\n"
        
        message += f"\n*HOLD Recommendations: {len(hold_recommendations)} stocks*"
        
        # Store daily analysis
        daily_analysis = {
            'date': now.strftime('%Y-%m-%d'),
            'buy': buy_recommendations,
            'sell': sell_recommendations,
            'hold': len(hold_recommendations)
        }
        
        # Send to Telegram
        send_telegram_message(message)
        
        return daily_analysis
    
    # Replace original function with enhanced one
    generate_daily_analysis = enhanced_generate_daily_analysis
    
    # Update format_recommendations_message to include target prices
    original_format_recommendations_message = format_recommendations_message
    def enhanced_format_recommendations_message(recommendations):
        """Enhanced version of format_recommendations_message that includes target prices"""
        msg = original_format_recommendations_message(recommendations)
        
        if 'target_price' in recommendations and 'stop_loss' in recommendations:
            msg += f"\n\n*Target Price: ₹{recommendations['target_price']}*"
            msg += f"\n*Stop Loss: ₹{recommendations['stop_loss']}*"
        
        return msg
    
    # Replace original function with enhanced one
    format_recommendations_message = enhanced_format_recommendations_message
    
    # Start the scheduler
    schedule_jobs()

if __name__ == "__main__":
    main()
