import pandas as pd
import numpy as np
import os
import time
import datetime
import pytz
import requests
import ta  # Using ta instead of talib
import schedule
import threading
from flask import Flask, render_template, jsonify
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
import warnings
warnings.filterwarnings('ignore')
from telegram import Bot, Update


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
bot = Bot(TELEGRAM_BOT_TOKEN)
updater = Updater(TELEGRAM_BOT_TOKEN)
dispatcher = updater.dispatcher

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
    """Calculate technical indicators for the given dataframe using ta library"""
    try:
        # Ensure the dataframe has the required columns
        required_columns = ['close', 'high', 'low', 'volume']
        if not all(col in df.columns for col in required_columns):
            raise ValueError("Data missing required columns for technical analysis")
        
        # Convert columns to numeric if they aren't already
        for col in required_columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Calculate Moving Averages
        df['SMA_20'] = ta.trend.sma_indicator(df['close'], window=20)
        df['SMA_50'] = ta.trend.sma_indicator(df['close'], window=50)
        df['SMA_200'] = ta.trend.sma_indicator(df['close'], window=200)
        
        # Calculate EMA
        df['EMA_12'] = ta.trend.ema_indicator(df['close'], window=12)
        df['EMA_26'] = ta.trend.ema_indicator(df['close'], window=26)
        
        # Calculate MACD
        macd = ta.trend.MACD(df['close'], window_slow=26, window_fast=12, window_sign=9)
        df['MACD'] = macd.macd()
        df['MACD_Signal'] = macd.macd_signal()
        df['MACD_Hist'] = macd.macd_diff()
        
        # Calculate RSI
        df['RSI'] = ta.momentum.RSIIndicator(df['close'], window=14).rsi()
        
        # Calculate Bollinger Bands
        bollinger = ta.volatility.BollingerBands(df['close'], window=20, window_dev=2)
        df['BB_Upper'] = bollinger.bollinger_hband()
        df['BB_Middle'] = bollinger.bollinger_mavg()
        df['BB_Lower'] = bollinger.bollinger_lband()
        
        # Calculate Stochastic
        stoch = ta.momentum.StochasticOscillator(df['high'], df['low'], df['close'], window=14, smooth_window=3)
        df['SlowK'] = stoch.stoch()
        df['SlowD'] = stoch.stoch_signal()
        
        # Calculate ADX
        df['ADX'] = ta.trend.ADXIndicator(df['high'], df['low'], df['close'], window=14).adx()
        
        # Calculate OBV (On-Balance Volume)
        df['OBV'] = ta.volume.OnBalanceVolumeIndicator(df['close'], df['volume']).on_balance_volume()
        
        # Calculate ATR
        df['ATR'] = ta.volatility.AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range()
        
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

def get_recommendations_with_targets(df, symbol):
    """Enhanced version of get_recommendations that includes target prices"""
    base_recommendations = get_recommendations(df, symbol)
    return calculate_price_targets(df, base_recommendations)

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
            bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
            print(f"Message sent to Telegram chat: {message[:50]}...")
            
            # Also send to group
            bot.send_message(chat_id=TELEGRAM_GROUP, text=message, parse_mode='Markdown')
            print(f"Message sent to Telegram group {TELEGRAM_GROUP}: {message[:50]}...")
        else:
            # Send to specified target
            bot.send_message(chat_id=target, text=message, parse_mode='Markdown')
            print(f"Message sent to Telegram {target}: {message[:50]}...")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def send_telegram_photo(photo_path, caption, target=None):
    """Send a photo to Telegram channel or group"""
    try:
        if target is None:
            # Send to personal chat ID
            with open(photo_path, 'rb') as photo:
                bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=photo, caption=caption)
            print(f"Photo sent to Telegram chat: {caption[:50]}...")
            
            # Also send to group
            with open(photo_path, 'rb') as photo:
                bot.send_photo(chat_id=TELEGRAM_GROUP, photo=photo, caption=caption)
            print(f"Photo sent to Telegram group {TELEGRAM_GROUP}: {caption[:50]}...")
        else:
            # Send to specified target
            with open(photo_path, 'rb') as photo:
                bot.send_photo(chat_id=target, photo=photo, caption=caption)
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
    
    if 'target_price' in recommendations and 'stop_loss' in recommendations:
        msg += f"\n\n*Target Price: ₹{recommendations['target_price']}*"
        msg += f"\n*Stop Loss: ₹{recommendations['stop_loss']}*"
    
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
        
        # Generate recommendations with targets
        recommendations = get_recommendations_with_targets(stock_data, symbol)
        all_recommendations.append(recommendations)
        
        # If it's a significant recommendation (BUY or SELL), send individual notification
        if recommendations.get('recommendations', {}).get('OVERALL') in ['BUY', 'SELL']:
            message = format_recommendations_message(recommendations)
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
    
    # Send to Telegram - both personal chat and group
    send_telegram_message(message)
    
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
        send_telegram_message(error_message)

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
def handle_command_start(update: Update, context: CallbackContext):
    response = (
        "*Welcome to Nifty 50 Stock Analyzer Bot!*\n\n"
        "Available commands:\n"
        "/status - Check bot status\n"
        "/recommendations - Get current recommendations\n"
        "/daily - Get daily analysis\n"
        "/analyze <symbol> - Analyze a specific stock"
    )
    update.message.reply_markdown(response)

def handle_command_status(update: Update, context: CallbackContext):
    status = (
        "*Bot Status*\n\n"
        f"Current time: {datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')} IST\n"
        f"Market hours: {MARKET_OPEN_TIME} - {MARKET_CLOSE_TIME} IST\n"
        f"Market is currently {'OPEN' if is_market_hours() else 'CLOSED'}\n"
        f"Last analysis update: {last_update_time or 'Not yet run'}\n"
        f"Number of stocks being monitored: {len(load_stock_data()) if not load_stock_data().empty else 0}\n"
        f"Check interval: Every {CHECK_INTERVAL_MINUTES} minutes during market hours"
    )
    update.message.reply_markdown(status)

def handle_command_recommendations(update: Update, context: CallbackContext):
    if not current_recommendations:
        update.message.reply_markdown("No recommendations available yet. Please wait for the next analysis run.")
        return
        
    # Get BUY and SELL recommendations only
    buy_sell_recs = [r for r in current_recommendations if r.get('recommendations', {}).get('OVERALL') in ['BUY', 'SELL']]
    
    if not buy_sell_recs:
        update.message.reply_markdown("No BUY or SELL recommendations in the latest analysis.")
        return
        
    response = f"*Latest Recommendations ({last_update_time})*\n\n"
    
    for rec in buy_sell_recs:
        response += f"*{rec['symbol']} - {rec['recommendations']['OVERALL']}*\n"
        response += f"Price: ₹{rec['price']}\n"
        response += f"Target: ₹{rec['target_price']}\n"
        response += f"Stop Loss: ₹{rec['stop_loss']}\n\n"
        
    update.message.reply_markdown(response)

def handle_command_daily(update: Update, context: CallbackContext):
    if not daily_analysis:
        update.message.reply_markdown("No daily analysis available yet. It will be generated after market hours.")
        return
        
    # Format daily analysis message
    message = f"*Daily Market Analysis - {daily_analysis['date']}*\n\n"
    
    message += f"*BUY Recommendations ({len(daily_analysis['buy'])}):*\n"
    for i, rec in enumerate(daily_analysis['buy'][:10], 1):
        message += f"{i}. {rec['symbol']} at ₹{rec['price']} (Target: ₹{rec['target_price']}, SL: ₹{rec['stop_loss']}) - Strength: {rec['strength']}\n"
    
    message += f"\n*SELL Recommendations ({len(daily_analysis['sell'])}):*\n"
    for i, rec in enumerate(daily_analysis['sell'][:10], 1):
       message += f"{i}. {rec['symbol']} at ₹{rec['price']} (Target: ₹{rec['target_price']}, SL: ₹{rec['stop_loss']}) - Strength: {rec['strength']}\n"
    
    message += f"\n*HOLD Recommendations: {daily_analysis['hold']} stocks*"
    
    update.message.reply_markdown(message)

def handle_command_analyze(update: Update, context: CallbackContext):
    if not context.args or len(context.args) < 1:
        update.message.reply_text("Please provide a symbol to analyze. Example: /analyze RELIANCE")
        return
        
    # Extract the symbol from the command
    symbol = context.args[0].upper().strip()
    
    # Check if symbol exists in our data
    stocks_df = load_stock_data()
    if stocks_df.empty or symbol not in stocks_df['symbol'].values:
        update.message.reply_markdown(f"Symbol {symbol} not found in the database.")
        return
    
    update.message.reply_markdown(f"Analyzing {symbol}... Please wait.")
    
    try:
        # Fetch data for the symbol
        stock_data = fetch_latest_data(symbol)
        
        if stock_data.empty:
            update.message.reply_markdown(f"Could not fetch data for {symbol}.")
            return
            
        # Calculate indicators and recommendations
        stock_data = calculate_indicators(stock_data)
        recommendations = get_recommendations_with_targets(stock_data, symbol)
        
        # Format and send recommendation message
        message = format_recommendations_message(recommendations)
        update.message.reply_markdown(message)
        
        # Create and send chart
        fig = create_technical_chart(stock_data, symbol)
        if fig:
            chart_path = f"static/{symbol}_chart.png"
            pio.write_image(fig, chart_path)
            
            with open(chart_path, 'rb') as photo:
                update.message.reply_photo(photo, caption=f"Technical chart for {symbol}")
                
    except Exception as e:
        error_message = f"Error analyzing {symbol}: {e}"
        print(error_message)
        update.message.reply_markdown(f"❌ {error_message}")

def handle_unknown(update: Update, context: CallbackContext):
    response = (
        "I don't understand that command. Available commands:\n"
        "/start - Show help message\n"
        "/status - Check bot status\n"
        "/recommendations - Get current recommendations\n"
        "/daily - Get daily analysis\n"
        "/analyze <symbol> - Analyze a specific stock"
    )
    update.message.reply_text(response)

def create_directories():
    """Create necessary directories if they don't exist"""
    os.makedirs('static', exist_ok=True)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    
    # Create basic index.html template if it doesn't exist
    index_path = os.path.join(TEMPLATES_DIR, 'index.html')
    if not os.path.exists(index_path):
        with open(index_path, 'w') as f:
            f.write("""<!DOCTYPE html>
<html>
<head>
    <title>Nifty 50 Stock Analyzer</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0,0,0,0.1);
        }
        h1, h2 {
            color: #333;
        }
        .recommendation {
            margin-bottom: 15px;
            padding: 15px;
            border-radius: 5px;
            background-color: #f9f9f9;
        }
        .buy {
            border-left: 5px solid green;
        }
        .sell {
            border-left: 5px solid red;
        }
        .hold {
            border-left: 5px solid orange;
        }
        .last-update {
            font-style: italic;
            color: #777;
            margin-bottom: 20px;
        }
        .tabs {
            margin-bottom: 20px;
        }
        .tab {
            display: inline-block;
            padding: 10px 20px;
            cursor: pointer;
            background-color: #eee;
            border-radius: 5px 5px 0 0;
        }
        .tab.active {
            background-color: #007bff;
            color: white;
        }
        .tab-content {
            display: none;
        }
        .tab-content.active {
            display: block;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Nifty 50 Stock Analyzer</h1>
        
        <div class="tabs">
            <div class="tab active" data-tab="recommendations">Recommendations</div>
            <div class="tab" data-tab="daily">Daily Analysis</div>
        </div>
        
        <div id="recommendations-tab" class="tab-content active">
            <div class="last-update">Last update: <span id="last-update">Loading...</span></div>
            
            <h2>Current Recommendations</h2>
            <div id="recommendations-container">
                <p>Loading recommendations...</p>
            </div>
        </div>
        
        <div id="daily-tab" class="tab-content">
            <h2>Daily Market Analysis</h2>
            <div id="daily-container">
                <p>Loading daily analysis...</p>
            </div>
        </div>
    </div>
    
    <script>
        // Tab functionality
        document.querySelectorAll('.tab').forEach(tab => {
            tab.addEventListener('click', () => {
                // Remove active class from all tabs and content
                document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                
                // Add active class to clicked tab
                tab.classList.add('active');
                
                // Show corresponding content
                const tabName = tab.getAttribute('data-tab');
                document.getElementById(`${tabName}-tab`).classList.add('active');
            });
        });
        
        // Fetch recommendations
        function fetchRecommendations() {
            fetch('/api/recommendations')
                .then(response => response.json())
                .then(data => {
                    // Update last update time
                    document.getElementById('last-update').textContent = data.last_update || 'Not available';
                    
                    // Update recommendations
                    const container = document.getElementById('recommendations-container');
                    
                    if (!data.recommendations || data.recommendations.length === 0) {
                        container.innerHTML = '<p>No recommendations available.</p>';
                        return;
                    }
                    
                    // Generate HTML for recommendations
                    let html = '';
                    data.recommendations.forEach(rec => {
                        const overallRec = rec.recommendations?.OVERALL || 'UNKNOWN';
                        html += `
                            <div class="recommendation ${overallRec.toLowerCase()}">
                                <h3>${rec.symbol} - ${overallRec}</h3>
                                <p>Current Price: ₹${rec.price}</p>
                                <p>Target Price: ₹${rec.target_price}</p>
                                <p>Stop Loss: ₹${rec.stop_loss}</p>
                                <h4>Technical Indicators:</h4>
                                <ul>
                        `;
                        
                        // Add each indicator
                        for (const [indicator, signal] of Object.entries(rec.recommendations || {})) {
                            if (indicator !== 'OVERALL') {
                                html += `<li>${indicator}: ${signal}</li>`;
                            }
                        }
                        
                        html += `
                                </ul>
                            </div>
                        `;
                    });
                    
                    container.innerHTML = html;
                })
                .catch(error => {
                    console.error('Error fetching recommendations:', error);
                    document.getElementById('recommendations-container').innerHTML = 
                        '<p>Error loading recommendations. Please try again later.</p>';
                });
        }
        
        // Fetch daily analysis
        function fetchDailyAnalysis() {
            fetch('/api/daily')
                .then(response => response.json())
                .then(data => {
                    const container = document.getElementById('daily-container');
                    
                    if (!data || !data.date) {
                        container.innerHTML = '<p>No daily analysis available yet.</p>';
                        return;
                    }
                    
                    // Generate HTML for daily analysis
                    let html = `<h3>Analysis for ${data.date}</h3>`;
                    
                    // BUY recommendations
                    html += `<h4>BUY Recommendations (${data.buy.length})</h4>`;
                    if (data.buy.length > 0) {
                        html += '<table border="1" cellpadding="5" style="border-collapse: collapse; width: 100%;">';
                        html += '<tr><th>Symbol</th><th>Price</th><th>Target</th><th>Stop Loss</th><th>Strength</th></tr>';
                        
                        data.buy.slice(0, 10).forEach(rec => {
                            html += `
                                <tr>
                                    <td>${rec.symbol}</td>
                                    <td>₹${rec.price}</td>
                                    <td>₹${rec.target_price}</td>
                                    <td>₹${rec.stop_loss}</td>
                                    <td>${rec.strength}</td>
                                </tr>
                            `;
                        });
                        
                        html += '</table>';
                    } else {
                        html += '<p>No BUY recommendations today.</p>';
                    }
                    
                    // SELL recommendations
                    html += `<h4>SELL Recommendations (${data.sell.length})</h4>`;
                    if (data.sell.length > 0) {
                        html += '<table border="1" cellpadding="5" style="border-collapse: collapse; width: 100%;">';
                        html += '<tr><th>Symbol</th><th>Price</th><th>Target</th><th>Stop Loss</th><th>Strength</th></tr>';
                        
                        data.sell.slice(0, 10).forEach(rec => {
                            html += `
                                <tr>
                                    <td>${rec.symbol}</td>
                                    <td>₹${rec.price}</td>
                                    <td>₹${rec.target_price}</td>
                                    <td>₹${rec.stop_loss}</td>
                                    <td>${rec.strength}</td>
                                </tr>
                            `;
                        });
                        
                        html += '</table>';
                    } else {
                        html += '<p>No SELL recommendations today.</p>';
                    }
                    
                    // HOLD recommendations
                    html += `<h4>HOLD Recommendations</h4>`;
                    html += `<p>${data.hold} stocks are recommended to HOLD.</p>`;
                    
                    container.innerHTML = html;
                })
                .catch(error => {
                    console.error('Error fetching daily analysis:', error);
                    document.getElementById('daily-container').innerHTML = 
                        '<p>Error loading daily analysis. Please try again later.</p>';
                });
        }
        
        // Initial fetch
        fetchRecommendations();
        fetchDailyAnalysis();
        
        // Refresh data every 5 minutes
        setInterval(() => {
            fetchRecommendations();
            fetchDailyAnalysis();
        }, 5 * 60 * 1000);
    </script>
</body>
</html>""")

def initialize_example_csv():
    """Create example CSV file if it doesn't exist"""
    if not os.path.exists(CSV_FILE_PATH):
        print(f"Creating example CSV file at {CSV_FILE_PATH}")
        
        # Create sample data with top Nifty 50 stocks
        example_stocks = [
            {"symbol": "RELIANCE", "name": "Reliance Industries Ltd."},
            {"symbol": "TCS", "name": "Tata Consultancy Services Ltd."},
            {"symbol": "HDFC", "name": "HDFC Bank Ltd."},
            {"symbol": "INFY", "name": "Infosys Ltd."},
            {"symbol": "ICICIBANK", "name": "ICICI Bank Ltd."},
            {"symbol": "HDFCBANK", "name": "HDFC Bank Ltd."},
            {"symbol": "KOTAKBANK", "name": "Kotak Mahindra Bank Ltd."},
            {"symbol": "LT", "name": "Larsen & Toubro Ltd."},
            {"symbol": "SBIN", "name": "State Bank of India"},
            {"symbol": "BAJFINANCE", "name": "Bajaj Finance Ltd."}
        ]
        
        df = pd.DataFrame(example_stocks)
        df.to_csv(CSV_FILE_PATH, index=False)

def main():
    """Main function to start all components"""
    try:
        # Create required directories and files
        create_directories()
        initialize_example_csv()
        
        # Start Telegram message handler
        # Register command handlers
        dispatcher.add_handler(CommandHandler("start", handle_command_start))
        dispatcher.add_handler(CommandHandler("status", handle_command_status))
        dispatcher.add_handler(CommandHandler("recommendations", handle_command_recommendations))
        dispatcher.add_handler(CommandHandler("daily", handle_command_daily))
        dispatcher.add_handler(CommandHandler("analyze", handle_command_analyze))
        dispatcher.add_handler(MessageHandler(Filters.text & ~Filters.command, handle_unknown))

        # Start the bot
        updater.start_polling()
        print(f"Telegram bot started and listening at {datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Welcome message on bot startup
        startup_message = (
            "*Nifty 50 Stock Analyzer Bot Started!*\n\n"
            f"Started at: {datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')} IST\n"
            f"Market hours: {MARKET_OPEN_TIME} - {MARKET_CLOSE_TIME} IST\n"
            f"Check interval: Every {CHECK_INTERVAL_MINUTES} minutes during market hours\n\n"
            "Bot will automatically analyze stocks and send recommendations during market hours."
        )
        
        send_telegram_message(startup_message)
        
        # Start Flask server in a separate thread
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.daemon = True
        flask_thread.start()
        print(f"Flask server started at http://0.0.0.0:{int(os.environ.get('PORT', 8080))}")
        
        # Run initial analysis if during market hours
        if is_market_hours():
            print("Running initial analysis...")
            run_analysis()
        
        # Start the scheduler for recurring jobs
        schedule_jobs()
        
    except Exception as e:
        error_message = f"❌ Critical error in main: {e}"
        print(error_message)
        try:
            send_telegram_message(error_message)
        except:
            pass
        raise

if __name__ == "__main__":
    main()
