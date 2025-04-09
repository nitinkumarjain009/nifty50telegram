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

# For python-telegram-bot v20+
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# Configuration
TELEGRAM_BOT_TOKEN = "8017759392:AAEwM-W-y83lLXTjlPl8sC_aBmizuIrFXnU"
TELEGRAM_CHAT_ID = "711856868"
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

# Initialize the Bot instance
bot = Bot(TELEGRAM_BOT_TOKEN)

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

async def send_telegram_message(message, target=None):
    """Send a message to Telegram channel or group"""
    try:
        if target is None:
            # Send to personal chat ID
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='Markdown')
            print(f"Message sent to Telegram chat: {message[:50]}...")
            
            # Also send to group
            await bot.send_message(chat_id=TELEGRAM_GROUP, text=message, parse_mode='Markdown')
            print(f"Message sent to Telegram group {TELEGRAM_GROUP}: {message[:50]}...")
        else:
            # Send to specified target
            await bot.send_message(chat_id=target, text=message, parse_mode='Markdown')
            print(f"Message sent to Telegram {target}: {message[:50]}...")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

async def send_telegram_photo(photo_path, caption, target=None):
    """Send a photo to Telegram channel or group"""
    try:
        if target is None:
            # Send to personal chat ID
            with open(photo_path, 'rb') as photo:
                await bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=photo, caption=caption)
            print(f"Photo sent to Telegram chat: {caption[:50]}...")
            
            # Also send to group
            with open(photo_path, 'rb') as photo:
                await bot.send_photo(chat_id=TELEGRAM_GROUP, photo=photo, caption=caption)
            print(f"Photo sent to Telegram group {TELEGRAM_GROUP}: {caption[:50]}...")
        else:
            # Send to specified target
            with open(photo_path, 'rb') as photo:
                await bot.send_photo(chat_id=target, photo=photo, caption=caption)
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

async def run_analysis():
    """Run analysis on all stocks and send recommendations"""
    global current_recommendations, last_update_time
    
    stocks_df = load_stock_data()
    all_recommendations = []
    
    if stocks_df.empty:
        message = "❌ Error: Could not load stock data."
        await send_telegram_message(message)
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
            await send_telegram_message(message)
            
            # Create and save chart
            fig = create_technical_chart(stock_data, symbol)
            if fig:
                chart_path = f"static/{symbol}_chart.png"
                pio.write_image(fig, chart_path)
                await send_telegram_photo(chart_path, f"Technical chart for {symbol}")
    
    # Update global recommendations
    current_recommendations = all_recommendations
    last_update_time = datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')
    
    return all_recommendations

async def generate_daily_analysis():
    """Generate daily analysis after market hours"""
    global daily_analysis
    
    stocks_df = load_stock_data()
    
    if stocks_df.empty:
        message = "❌ Error: Could not load stock data for daily analysis."
        await send_telegram_message(message)
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
    await send_telegram_message(message)
    
    return daily_analysis

async def job_wrapper(job_func):
    """Wrapper for scheduled jobs to handle exceptions"""
    try:
        return await job_func()
    except Exception as e:
        print(f"Error in scheduled job {job_func.__name__}: {e}")
        error_message = f"❌ Error in {job_func.__name__}: {e}"
        await send_telegram_message(error_message)

async def run_scheduled_job():
    """Run scheduled jobs"""
    # Check if it's market hours
    if is_market_hours():
        await job_wrapper(run_analysis)
    
    # Check if it's market close time
    now = datetime.datetime.now(IST_TIMEZONE)
    current_time = now.strftime("%H:%M")
    if current_time == MARKET_CLOSE_TIME:
        await job_wrapper(generate_daily_analysis)

# Command handlers for v20+
async def handle_command_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = (
        "*Welcome to Nifty 50 Stock Analyzer Bot!*\n\n"
        "Available commands:\n"
        "/status - Check bot status\n"
        "/recommendations - Get current recommendations\n"
        "/daily - Get daily analysis\n"
        "/analyze <symbol> - Analyze a specific stock"
    )
    await update.message.reply_markdown(response)

async def handle_command_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = (
        "*Bot Status*\n\n"
        f"Current time: {datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')} IST\n"
        f"Market hours: {MARKET_OPEN_TIME} - {MARKET_CLOSE_TIME} IST\n"
        f"Market is currently {'OPEN' if is_market_hours() else 'CLOSED'}\n"
        f"Last analysis update: {last_update_time or 'Not yet run'}\n"
        f"Number of stocks being monitored: {len(load_stock_data()) if not load_stock_data().empty else 0}\n"
        f"Check interval: Every {CHECK_INTERVAL_MINUTES} minutes during market hours"
    )
    await update.message.reply_markdown(status)

async def handle_command_recommendations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not current_recommendations:
        await update.message.reply_markdown("No recommendations available yet. Please wait for the next analysis run.")
        return
        
    # Get BUY and SELL recommendations only
    buy_sell_recs = [r for r in current_recommendations if r.get('recommendations', {}).get('OVERALL') in ['BUY', 'SELL']]
    
    if not buy_sell_recs:
        await update.message.reply_markdown("No BUY or SELL recommendations in the latest analysis.")
        return
        
    response = f"*Latest Recommendations ({last_update_time})*\n\n"
    
    for rec in buy_sell_recs:
        response += f"*{rec['symbol']} - {rec['recommendations']['OVERALL']}*\n"
        response += f"Price: ₹{rec['price']}\n"
        response += f"Target: ₹{rec['target_price']}\n"
        response += f"Stop Loss: ₹{rec['stop_loss']}\n\n"
        
    await update.message.reply_markdown(response)

async def handle_command_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not daily_analysis:
        await update.message.reply_markdown("No daily analysis available yet. It will be generated after market hours.")
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
    
    await update.message.reply_markdown(message)

async def handle_command_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 1:
        await update.message.reply_text("Please provide a symbol to analyze. Example: /analyze RELIANCE")
        return
        
    # Extract the symbol from the command
    symbol = context.args[0].upper()
    
    # Check if symbol exists in our dataset
    stocks_df = load_stock_data()
    if not any(stocks_df['symbol'] == symbol):
        await update.message.reply_text(f"Symbol {symbol} not found in Nifty 50 stocks list.")
        return
        
    # Send a message that analysis is in progress
    await update.message.reply_text(f"Analyzing {symbol}. Please wait...")
    
    # Fetch and analyze the stock
    stock_data = fetch_latest_data(symbol)
    
    if stock_data.empty:
        await update.message.reply_text(f"Error fetching data for {symbol}.")
        return

    # Calculate indicators and get recommendations    
    stock_data = calculate_indicators(stock_data)
    # Add these functions after calculate_indicators but before get_recommendations

def fetch_timeframe_data(symbol, timeframe='daily'):
    """Fetch stock data for specific timeframe (daily, weekly, monthly)"""
    try:
        # Simulating data for different timeframes
        # In a real implementation, you would fetch actual data from an API
        
        if timeframe == 'weekly':
            periods = 52  # One year of weekly data
            freq = 'W'
        elif timeframe == 'monthly':
            periods = 24  # Two years of monthly data
            freq = 'M'
        else:  # daily
            periods = 100
            freq = 'D'
        
        # Create sample data with the appropriate frequency
        hist_data = pd.DataFrame({
            'date': pd.date_range(end=datetime.datetime.now(), periods=periods, freq=freq),
            'open': np.random.normal(500, 10, periods),
            'high': np.random.normal(505, 15, periods),
            'low': np.random.normal(495, 15, periods),
            'close': np.random.normal(500, 10, periods),
            'volume': np.random.normal(1000000, 200000, periods)
        })
        
        # Make sure high is the highest and low is the lowest for each period
        for i in range(len(hist_data)):
            values = [hist_data.loc[i, 'open'], hist_data.loc[i, 'close']]
            hist_data.loc[i, 'high'] = max(values) + abs(np.random.normal(0, 2))
            hist_data.loc[i, 'low'] = min(values) - abs(np.random.normal(0, 2))
        
        hist_data['symbol'] = symbol
        return hist_data
    except Exception as e:
        print(f"Error fetching {timeframe} data for {symbol}: {e}")
        return pd.DataFrame()

def generate_timeframe_analysis(timeframe):
    """Generate analysis for weekly or monthly timeframes"""
    stocks_df = load_stock_data()
    
    if stocks_df.empty:
        return {}
    
    # Categories for RSI-based classification
    rsi_categories = {
        'oversold': {'min': 0, 'max': 30},
        'neutral_low': {'min': 30, 'max': 45},
        'neutral': {'min': 45, 'max': 55},
        'neutral_high': {'min': 55, 'max': 70},
        'overbought': {'min': 70, 'max': 100}
    }
    
    categorized_stocks = {category: [] for category in rsi_categories}
    
    # Process each stock
    for _, row in stocks_df.iterrows():
        symbol = row['symbol']
        
        # Fetch data for the specified timeframe
        stock_data = fetch_timeframe_data(symbol, timeframe)
        
        if stock_data.empty:
            continue
            
        # Calculate indicators
        stock_data = calculate_indicators(stock_data)
        
        # Get latest data point
        latest = stock_data.iloc[-1]
        
        # Determine RSI category
        rsi_value = latest['RSI']
        rsi_category = None
        
        for category, range_val in rsi_categories.items():
            if range_val['min'] <= rsi_value < range_val['max']:
                rsi_category = category
                break
        
        if rsi_category is None:
            continue
        
        # Get overall recommendation
        recommendations = get_recommendations(stock_data, symbol)
        overall_rec = recommendations.get('recommendations', {}).get('OVERALL', 'HOLD')
        
        # Add to appropriate category
        categorized_stocks[rsi_category].append({
            'symbol': symbol,
            'price': round(latest['close'], 2),
            'rsi': round(rsi_value, 2),
            'adx': round(latest['ADX'], 2),
            'recommendation': overall_rec
        })
    
    # Sort stocks within each category by RSI
    for category in categorized_stocks:
        categorized_stocks[category].sort(key=lambda x: x['rsi'])
    
    return {
        'timeframe': timeframe,
        'timestamp': datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'),
        'categories': categorized_stocks
    }

# Add these Flask routes after the existing ones

@app.route('/weekly')
def weekly_analysis():
    """Page for weekly timeframe analysis"""
    return render_template('timeframe_analysis.html', 
                          timeframe='Weekly',
                          market_status=is_market_hours(),
                          last_update=last_update_time)

@app.route('/monthly')
def monthly_analysis():
    """Page for monthly timeframe analysis"""
    return render_template('timeframe_analysis.html', 
                          timeframe='Monthly',
                          market_status=is_market_hours(),
                          last_update=last_update_time)

@app.route('/api/weekly')
def api_weekly():
    """API endpoint for weekly analysis"""
    weekly_data = generate_timeframe_analysis('weekly')
    return jsonify(weekly_data)

@app.route('/api/monthly')
def api_monthly():
    """API endpoint for monthly analysis"""
    monthly_data = generate_timeframe_analysis('monthly')
    return jsonify(monthly_data)

# Now add this to the ensure_directories function to create the new template

def ensure_directories():
    """Ensure all required directories exist"""
    os.makedirs('static', exist_ok=True)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    
    # Create timeframe analysis template
    if not os.path.exists(f"{TEMPLATES_DIR}/timeframe_analysis.html"):
        with open(f"{TEMPLATES_DIR}/timeframe_analysis.html", "w") as f:
            f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>{{ timeframe }} Analysis - Nifty 50 Stock Analyzer</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .card { margin-bottom: 20px; }
        .oversold { border-left: 5px solid green; }
        .neutral_low { border-left: 5px solid lightgreen; }
        .neutral { border-left: 5px solid gray; }
        .neutral_high { border-left: 5px solid orange; }
        .overbought { border-left: 5px solid red; }
        .buy-rec { color: green; font-weight: bold; }
        .sell-rec { color: red; font-weight: bold; }
        .hold-rec { color: gray; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="text-center mb-4">{{ timeframe }} Analysis - Nifty 50 Stocks</h1>
        
        <div class="row mb-4">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">
                        <h5>Market Status</h5>
                    </div>
                    <div class="card-body">
                        <p><strong>Market is:</strong> <span id="market-status" class="badge bg-success">Loading...</span></p>
                        <p><strong>Last Update:</strong> <span id="last-update">Loading...</span></p>
                    </div>
                </div>
            </div>
            
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">
                        <h5>Navigation</h5>
                    </div>
                    <div class="card-body">
                        <a href="/" class="btn btn-primary">Daily Analysis</a>
                        <a href="/weekly" class="btn btn-secondary">Weekly Analysis</a>
                        <a href="/monthly" class="btn btn-info">Monthly Analysis</a>
                    </div>
                </div>
            </div>
        </div>
        
        <div id="loading">
            <div class="d-flex justify-content-center">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
            </div>
            <p class="text-center">Loading {{ timeframe }} analysis data...</p>
        </div>
        
        <div id="analysis-content" style="display: none;">
            <h3>RSI-Based Stock Categories</h3>
            <p class="text-muted">Last updated: <span id="timestamp">Loading...</span></p>
            
            <div class="accordion" id="accordionRSI">
                <!-- RSI Categories will be dynamically inserted here -->
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Update market status on page load
        document.addEventListener('DOMContentLoaded', function() {
            // Set market status
            const marketStatus = {{ 'true' if market_status else 'false' }};
            const statusEl = document.getElementById('market-status');
            
            if (marketStatus) {
                statusEl.textContent = 'OPEN';
                statusEl.className = 'badge bg-success';
            } else {
                statusEl.textContent = 'CLOSED';
                statusEl.className = 'badge bg-danger';
            }
            
            // Set last update time
            const lastUpdate = "{{ last_update or 'Not available' }}";
            document.getElementById('last-update').textContent = lastUpdate;
            
            // Load timeframe analysis
            fetchTimeframeData('{{ timeframe.lower() }}');
        });
        
        function fetchTimeframeData(timeframe) {
            fetch(`/api/${timeframe}`)
                .then(response => response.json())
                .then(data => {
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('analysis-content').style.display = 'block';
                    
                    document.getElementById('timestamp').textContent = data.timestamp;
                    
                    const accordion = document.getElementById('accordionRSI');
                    accordion.innerHTML = '';
                    
                    // Category labels and descriptions
                    const categoryLabels = {
                        'oversold': {
                            'title': 'Oversold (RSI below 30)',
                            'description': 'Potentially undervalued stocks with RSI below 30. These may present buying opportunities.',
                            'expanded': true
                        },
                        'neutral_low': {
                            'title': 'Neutral-Low (RSI 30-45)',
                            'description': 'Stocks with RSI between 30-45. These are moving from oversold conditions toward neutral.',
                            'expanded': false
                        },
                        'neutral': {
                            'title': 'Neutral (RSI 45-55)',
                            'description': 'Stocks with RSI between 45-55. These are in a balanced state without strong momentum either way.',
                            'expanded': false
                        },
                        'neutral_high': {
                            'title': 'Neutral-High (RSI 55-70)',
                            'description': 'Stocks with RSI between 55-70. These show upward momentum but aren\'t yet overbought.',
                            'expanded': false
                        },
                        'overbought': {
                            'title': 'Overbought (RSI above 70)',
                            'description': 'Potentially overvalued stocks with RSI above 70. These may present selling opportunities.',
                            'expanded': true
                        }
                    };
                    
                    // Process each RSI category
                    Object.entries(data.categories).forEach(([category, stocks], index) => {
                        if (!stocks.length) return;
                        
                        const categoryInfo = categoryLabels[category] || {
                            'title': `${category} RSI`,
                            'description': `Stocks classified in the ${category} RSI range.`,
                            'expanded': false
                        };
                        
                        const accordionItem = document.createElement('div');
                        accordionItem.className = 'accordion-item';
                        
                        const headerId = `heading${category}`;
                        const collapseId = `collapse${category}`;
                        
                        accordionItem.innerHTML = `
                            <h2 class="accordion-header" id="${headerId}">
                                <button class="accordion-button ${categoryInfo.expanded ? '' : 'collapsed'}" type="button" 
                                    data-bs-toggle="collapse" data-bs-target="#${collapseId}" 
                                    aria-expanded="${categoryInfo.expanded ? 'true' : 'false'}" aria-controls="${collapseId}">
                                    ${categoryInfo.title} (${stocks.length} stocks)
                                </button>
                            </h2>
                            <div id="${collapseId}" class="accordion-collapse collapse ${categoryInfo.expanded ? 'show' : ''}" 
                                aria-labelledby="${headerId}" data-bs-parent="#accordionRSI">
                                <div class="accordion-body">
                                    <p class="mb-3">${categoryInfo.description}</p>
                                    <div class="table-responsive">
                                        <table class="table table-striped table-hover">
                                            <thead>
                                                <tr>
                                                    <th>Symbol</th>
                                                    <th>CMP (₹)</th>
                                                    <th>RSI</th>
                                                    <th>ADX</th>
                                                    <th>Recommendation</th>
                                                </tr>
                                            </thead>
                                            <tbody id="tbody-${category}">
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                        `;
                        
                        accordion.appendChild(accordionItem);
                        
                        // Add stocks to the table
                        const tbody = document.getElementById(`tbody-${category}`);
                        stocks.forEach(stock => {
                            const tr = document.createElement('tr');
                            
                            // Add recommendation class
                            let recClass = 'hold-rec';
                            if (stock.recommendation === 'BUY') recClass = 'buy-rec';
                            else if (stock.recommendation === 'SELL') recClass = 'sell-rec';
                            
                            tr.innerHTML = `
                                <td>${stock.symbol}</td>
                                <td>₹${stock.price}</td>
                                <td>${stock.rsi}</td>
                                <td>${stock.adx}</td>
                                <td class="${recClass}">${stock.recommendation}</td>
                            `;
                            
                            tbody.appendChild(tr);
                        });
                    });
                })
                .catch(error => {
                    console.error(`Error fetching ${timeframe} data:`, error);
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('analysis-content').style.display = 'block';
                    document.getElementById('accordionRSI').innerHTML = 
                        `<div class="alert alert-danger">Error loading ${timeframe} analysis data. Please try again later.</div>`;
                });
        }
    </script>
</body>
</html>
            """)
    recommendations = get_recommendations_with_targets(stock_data, symbol)
    
# Put your code inside an async function
async def send_analysis(update, message, recommendations, stock_data, symbol):
    # Format and send the analysis
    message = format_recommendations_message(recommendations)
    await update.message.reply_markdown(message)
    
    # Create and send chart
    try:
        fig = create_technical_chart(stock_data, symbol)
        if fig:
            chart_path = f"static/{symbol}_chart.png"
            pio.write_image(fig, chart_path)
            
            # Send the chart as photo
            with open(chart_path, 'rb') as photo:
                await update.message.reply_photo(photo=photo, caption=f"Technical chart for {symbol}")
    except Exception as e:
        await update.message.reply_text(f"Error generating chart: {e}")

# Then call this function where needed, for example in your handle_command_analyze function:
# await send_analysis(update, message, recommendations, stock_data, symbol)
    
async def handle_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "*Nifty 50 Stock Analyzer Bot Help*\n\n"
        "This bot analyzes Nifty 50 stocks using technical indicators and provides trading recommendations.\n\n"
        "*Available Commands:*\n"
        "/start - Start interacting with the bot\n"
        "/status - Check bot status and market hours\n"
        "/recommendations - Get latest stock recommendations\n"
        "/daily - Get daily market analysis\n"
        "/analyze <symbol> - Analyze a specific stock (e.g., /analyze RELIANCE)\n"
        "/help - Show this help message\n\n"
        "The bot runs analysis every {CHECK_INTERVAL_MINUTES} minutes during market hours and sends notifications for BUY/SELL recommendations."
    )
    await update.message.reply_markdown(help_text)
    
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle regular text messages - interpret as symbols to analyze"""
    text = update.message.text.strip().upper()
    
    # Check if it looks like a stock symbol (no spaces, alphanumeric)
    if ' ' not in text and text.isalnum():
        # Treat as a command to analyze the stock
        context.args = [text]
        await handle_command_analyze(update, context)
    else:
        await update.message.reply_text(
            "Please use specific commands or enter a valid stock symbol. Use /help for more information."
        )

# Flask routes with optimized structure
@app.route('/')
def home():
    """Home page with dashboard"""
    return render_template('index.html', 
                          market_status=is_market_hours(),
                          last_update=last_update_time)

@app.route('/api/recommendations')
def api_recommendations():
    """API endpoint to get current recommendations"""
    return jsonify(current_recommendations)

@app.route('/api/daily')
def api_daily():
    """API endpoint to get daily analysis"""
    return jsonify(daily_analysis)

@app.route('/api/analyze/<symbol>')
def api_analyze(symbol):
    """API endpoint to analyze a specific stock"""
    symbol = symbol.upper()
    
    # Check if symbol exists
    stocks_df = load_stock_data()
    if not any(stocks_df['symbol'] == symbol):
        return jsonify({"error": f"Symbol {symbol} not found in Nifty 50 stocks list."})
    
    # Fetch and analyze the stock
    stock_data = fetch_latest_data(symbol)
    
    if stock_data.empty:
        return jsonify({"error": f"Error fetching data for {symbol}."})
    
    # Calculate indicators and get recommendations
    stock_data = calculate_indicators(stock_data)
    recommendations = get_recommendations_with_targets(stock_data, symbol)
    
    return jsonify(recommendations)

# Combined endpoint for all timeframe analysis
@app.route('/api/timeframe/<timeframe>')
def api_timeframe(timeframe):
    """
    API endpoint for timeframe analysis (daily, weekly, monthly)
    Accepts timeframe parameter: 'daily', 'weekly', 'monthly'
    """
    # Validate timeframe parameter
    valid_timeframes = ['daily', 'weekly', 'monthly']
    if timeframe not in valid_timeframes:
        return jsonify({
            "error": f"Invalid timeframe. Please use one of: {', '.join(valid_timeframes)}"
        })
    
    # For daily timeframe, use the existing daily analysis data
    if timeframe == 'daily':
        return jsonify(daily_analysis)
    
    # For weekly and monthly, generate the appropriate analysis
    analysis_data = generate_timeframe_analysis(timeframe)
    return jsonify(analysis_data)

# Combined route for timeframe pages
@app.route('/<timeframe>')
def timeframe_analysis(timeframe):
    """
    Page for timeframe analysis (daily, weekly, monthly)
    Accepts timeframe parameter: 'daily', 'weekly', 'monthly'
    """
    # Validate timeframe parameter
    valid_timeframes = ['weekly', 'monthly']
    
    # Redirect to home for daily or invalid timeframes
    if timeframe not in valid_timeframes:
        return redirect('/')
    
    return render_template('timeframe_analysis.html', 
                          timeframe=timeframe.capitalize(),
                          market_status=is_market_hours(),
                          last_update=last_update_time)

def generate_timeframe_analysis(timeframe):
    """Generate analysis for different timeframes (weekly or monthly)"""
    stocks_df = load_stock_data()
    
    if stocks_df.empty:
        return {}
    
    # Categories for RSI-based classification
    rsi_categories = {
        'oversold': {'min': 0, 'max': 30},
        'neutral_low': {'min': 30, 'max': 45},
        'neutral': {'min': 45, 'max': 55},
        'neutral_high': {'min': 55, 'max': 70},
        'overbought': {'min': 70, 'max': 100}
    }
    
    categorized_stocks = {category: [] for category in rsi_categories}
    
    # Process each stock
    for _, row in stocks_df.iterrows():
        symbol = row['symbol']
        
        # Fetch data for the specified timeframe
        stock_data = fetch_timeframe_data(symbol, timeframe)
        
        if stock_data.empty:
            continue
            
        # Calculate indicators
        stock_data = calculate_indicators(stock_data)
        
        # Get latest data point
        latest = stock_data.iloc[-1]
        
        # Determine RSI category
        rsi_value = latest['RSI']
        rsi_category = None
        
        for category, range_val in rsi_categories.items():
            if range_val['min'] <= rsi_value < range_val['max']:
                rsi_category = category
                break
        
        if rsi_category is None:
            continue
        
        # Get overall recommendation
        recommendations = get_recommendations(stock_data, symbol)
        overall_rec = recommendations.get('recommendations', {}).get('OVERALL', 'HOLD')
        
        # Add to appropriate category
        categorized_stocks[rsi_category].append({
            'symbol': symbol,
            'price': round(latest['close'], 2),
            'rsi': round(rsi_value, 2),
            'adx': round(latest['ADX'], 2),
            'recommendation': overall_rec
        })
    
    # Sort stocks within each category by RSI
    for category in categorized_stocks:
        categorized_stocks[category].sort(key=lambda x: x['rsi'])
    
    return {
        'timeframe': timeframe,
        'timestamp': datetime.datetime.now(IST_TIMEZONE).strftime('%Y-%m-%d %H:%M:%S'),
        'categories': categorized_stocks
    }

def fetch_timeframe_data(symbol, timeframe='daily'):
    """Fetch stock data for specific timeframe (daily, weekly, monthly)"""
    try:
        # Simulating data for different timeframes
        # In a real implementation, you would fetch actual data from an API
        
        if timeframe == 'weekly':
            periods = 52  # One year of weekly data
            freq = 'W'
        elif timeframe == 'monthly':
            periods = 24  # Two years of monthly data
            freq = 'M'
        else:  # daily
            periods = 100
            freq = 'D'
        
        # Create sample data with the appropriate frequency
        hist_data = pd.DataFrame({
            'date': pd.date_range(end=datetime.datetime.now(), periods=periods, freq=freq),
            'open': np.random.normal(500, 10, periods),
            'high': np.random.normal(505, 15, periods),
            'low': np.random.normal(495, 15, periods),
            'close': np.random.normal(500, 10, periods),
            'volume': np.random.normal(1000000, 200000, periods)
        })
        
        # Make sure high is the highest and low is the lowest for each period
        for i in range(len(hist_data)):
            values = [hist_data.loc[i, 'open'], hist_data.loc[i, 'close']]
            hist_data.loc[i, 'high'] = max(values) + abs(np.random.normal(0, 2))
            hist_data.loc[i, 'low'] = min(values) - abs(np.random.normal(0, 2))
        
        hist_data['symbol'] = symbol
        return hist_data
    except Exception as e:
        print(f"Error fetching {timeframe} data for {symbol}: {e}")
        return pd.DataFrame()

def ensure_directories():
    """Ensure all required directories exist"""
    os.makedirs('static', exist_ok=True)
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    
    # Create timeframe analysis template
    if not os.path.exists(f"{TEMPLATES_DIR}/timeframe_analysis.html"):
        with open(f"{TEMPLATES_DIR}/timeframe_analysis.html", "w") as f:
            f.write("""
<!DOCTYPE html>
<html>
<head>
    <title>{{ timeframe }} Analysis - Nifty 50 Stock Analyzer</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .card { margin-bottom: 20px; }
        .oversold { border-left: 5px solid green; }
        .neutral_low { border-left: 5px solid lightgreen; }
        .neutral { border-left: 5px solid gray; }
        .neutral_high { border-left: 5px solid orange; }
        .overbought { border-left: 5px solid red; }
        .buy-rec { color: green; font-weight: bold; }
        .sell-rec { color: red; font-weight: bold; }
        .hold-rec { color: gray; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="text-center mb-4">{{ timeframe }} Analysis - Nifty 50 Stocks</h1>
        
        <div class="row mb-4">
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">
                        <h5>Market Status</h5>
                    </div>
                    <div class="card-body">
                        <p><strong>Market is:</strong> <span id="market-status" class="badge bg-success">Loading...</span></p>
                        <p><strong>Last Update:</strong> <span id="last-update">Loading...</span></p>
                    </div>
                </div>
            </div>
            
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header">
                        <h5>Navigation</h5>
                    </div>
                    <div class="card-body">
                        <a href="/" class="btn btn-primary">Daily Analysis</a>
                        <a href="/weekly" class="btn btn-secondary">Weekly Analysis</a>
                        <a href="/monthly" class="btn btn-info">Monthly Analysis</a>
                    </div>
                </div>
            </div>
        </div>
        
        <div id="loading">
            <div class="d-flex justify-content-center">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
            </div>
            <p class="text-center">Loading {{ timeframe }} analysis data...</p>
        </div>
        
        <div id="analysis-content" style="display: none;">
            <h3>RSI-Based Stock Categories</h3>
            <p class="text-muted">Last updated: <span id="timestamp">Loading...</span></p>
            
            <div class="accordion" id="accordionRSI">
                <!-- RSI Categories will be dynamically inserted here -->
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Update market status on page load
        document.addEventListener('DOMContentLoaded', function() {
            // Set market status
            const marketStatus = {{ 'true' if market_status else 'false' }};
            const statusEl = document.getElementById('market-status');
            
            if (marketStatus) {
                statusEl.textContent = 'OPEN';
                statusEl.className = 'badge bg-success';
            } else {
                statusEl.textContent = 'CLOSED';
                statusEl.className = 'badge bg-danger';
            }
            
            // Set last update time
            const lastUpdate = "{{ last_update or 'Not available' }}";
            document.getElementById('last-update').textContent = lastUpdate;
            
            // Load timeframe analysis
            const timeframe = '{{ timeframe.lower() }}';
            fetchTimeframeData(timeframe);
        });
        
        function fetchTimeframeData(timeframe) {
            fetch(`/api/timeframe/${timeframe}`)
                .then(response => response.json())
                .then(data => {
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('analysis-content').style.display = 'block';
                    
                    document.getElementById('timestamp').textContent = data.timestamp;
                    
                    const accordion = document.getElementById('accordionRSI');
                    accordion.innerHTML = '';
                    
                    // Category labels and descriptions
                    const categoryLabels = {
                        'oversold': {
                            'title': 'Oversold (RSI below 30)',
                            'description': 'Potentially undervalued stocks with RSI below 30. These may present buying opportunities.',
                            'expanded': true
                        },
                        'neutral_low': {
                            'title': 'Neutral-Low (RSI 30-45)',
                            'description': 'Stocks with RSI between 30-45. These are moving from oversold conditions toward neutral.',
                            'expanded': false
                        },
                        'neutral': {
                            'title': 'Neutral (RSI 45-55)',
                            'description': 'Stocks with RSI between 45-55. These are in a balanced state without strong momentum either way.',
                            'expanded': false
                        },
                        'neutral_high': {
                            'title': 'Neutral-High (RSI 55-70)',
                            'description': 'Stocks with RSI between 55-70. These show upward momentum but aren\'t yet overbought.',
                            'expanded': false
                        },
                        'overbought': {
                            'title': 'Overbought (RSI above 70)',
                            'description': 'Potentially overvalued stocks with RSI above 70. These may present selling opportunities.',
                            'expanded': true
                        }
                    };
                    
                    // Process each RSI category
                    Object.entries(data.categories).forEach(([category, stocks], index) => {
                        if (!stocks.length) return;
                        
                        const categoryInfo = categoryLabels[category] || {
                            'title': `${category} RSI`,
                            'description': `Stocks classified in the ${category} RSI range.`,
                            'expanded': false
                        };
                        
                        const accordionItem = document.createElement('div');
                        accordionItem.className = 'accordion-item';
                        
                        const headerId = `heading${category}`;
                        const collapseId = `collapse${category}`;
                        
                        accordionItem.innerHTML = `
                            <h2 class="accordion-header" id="${headerId}">
                                <button class="accordion-button ${categoryInfo.expanded ? '' : 'collapsed'}" type="button" 
                                    data-bs-toggle="collapse" data-bs-target="#${collapseId}" 
                                    aria-expanded="${categoryInfo.expanded ? 'true' : 'false'}" aria-controls="${collapseId}">
                                    ${categoryInfo.title} (${stocks.length} stocks)
                                </button>
                            </h2>
                            <div id="${collapseId}" class="accordion-collapse collapse ${categoryInfo.expanded ? 'show' : ''}" 
                                aria-labelledby="${headerId}" data-bs-parent="#accordionRSI">
                                <div class="accordion-body">
                                    <p class="mb-3">${categoryInfo.description}</p>
                                    <div class="table-responsive">
                                        <table class="table table-striped table-hover">
                                            <thead>
                                                <tr>
                                                    <th>Symbol</th>
                                                    <th>CMP (₹)</th>
                                                    <th>RSI</th>
                                                    <th>ADX</th>
                                                    <th>Recommendation</th>
                                                </tr>
                                            </thead>
                                            <tbody id="tbody-${category}">
                                            </tbody>
                                        </table>
                                    </div>
                                </div>
                            </div>
                        `;
                        
                        accordion.appendChild(accordionItem);
                        
                        // Add stocks to the table
                        const tbody = document.getElementById(`tbody-${category}`);
                        stocks.forEach(stock => {
                            const tr = document.createElement('tr');
                            
                            // Add recommendation class
                            let recClass = 'hold-rec';
                            if (stock.recommendation === 'BUY') recClass = 'buy-rec';
                            else if (stock.recommendation === 'SELL') recClass = 'sell-rec';
                            
                            tr.innerHTML = `
                                <td>${stock.symbol}</td>
                                <td>₹${stock.price}</td>
                                <td>${stock.rsi}</td>
                                <td>${stock.adx}</td>
                                <td class="${recClass}">${stock.recommendation}</td>
                            `;
                            
                            tbody.appendChild(tr);
                        });
                    });
                })
                .catch(error => {
                    console.error(`Error fetching ${timeframe} data:`, error);
                    document.getElementById('loading').style.display = 'none';
                    document.getElementById('analysis-content').style.display = 'block';
                    document.getElementById('accordionRSI').innerHTML = 
                        `<div class="alert alert-danger">Error loading ${timeframe} analysis data. Please try again later.</div>`;
                });
        }
    </script>
</body>
</html>
            """)
    
    # Update the index.html JavaScript to use the new API endpoint
    if os.path.exists(f"{TEMPLATES_DIR}/index.html"):
        with open(f"{TEMPLATES_DIR}/index.html", "r") as f:
            content = f.read()
        
        # Update the fetchDailyAnalysis function to use the new API endpoint
        if "fetch('/api/daily')" in content:
            content = content.replace(
                "fetch('/api/daily')",
                "fetch('/api/timeframe/daily')"
            )
            
            with open(f"{TEMPLATES_DIR}/index.html", "w") as f:
                f.write(content)

def run_scheduler():
    """Run the scheduler in a separate thread"""
    async def scheduler_job():
        while True:
            # Run the job
            await run_scheduled_job()
            # Sleep for the interval
            await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)

    # Create and run the event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(scheduler_job())

async def main():
    """Main function to start the bot and web server"""
    # Ensure directories exist
    ensure_directories()
    
    # Rest of the function remains the same...
    # Initialize the Telegram application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Add command handlers
    application.add_handler(CommandHandler("start", handle_command_start))
    application.add_handler(CommandHandler("help", handle_help_command))
    application.add_handler(CommandHandler("status", handle_command_status))
    application.add_handler(CommandHandler("recommendations", handle_command_recommendations))
    application.add_handler(CommandHandler("daily", handle_command_daily))
    application.add_handler(CommandHandler("analyze", handle_command_analyze))
    
    # Add message handler for text
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    
    # Start the scheduler in a separate thread
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Start the Telegram bot
    print("Starting Telegram bot...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    
    # Start the Flask web server
    print("Starting Flask web server...")
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000)
    
    # Keep the main thread running
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    try:
        import asyncio
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot stopped!")
