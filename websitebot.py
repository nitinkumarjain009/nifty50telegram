# NSE Stock Analysis App - Technical Indicators for Buy/Sell Recommendations
# This application:
# 1. Fetches data for 500 NSE stocks
# 2. Calculates multiple technical indicators for buy/sell signals
# 3. Sends daily analysis summary to Telegram at 4 PM IST
# 4. Displays the data in a table on a web page

import requests
import pandas as pd
import numpy as np
import json
import time
import re
from datetime import datetime, timedelta
import logging
import os
import pytz
import threading
import schedule
from flask import Flask, render_template, jsonify
import talib
import yfinance as yf

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("stock_analysis.log"),
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
    'data': None,
    'buy_recommendations': None,
    'sell_recommendations': None,
    'watchlist': None
}

def fetch_nse_500_list():
    """Fetch the list of NSE 500 stocks."""
    logger.info("Fetching NSE 500 stocks list...")
    
    try:
        # Option 1: Using NSE India website
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        }
        
        # Try to get NSE 500 list from NSE website
        url = "https://www1.nseindia.com/content/indices/ind_nifty500list.csv"
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            # Parse CSV data directly from response
            nse_500_df = pd.read_csv(pd.io.common.StringIO(response.text))
            nse_500_list = nse_500_df['Symbol'].tolist()
            logger.info(f"Successfully fetched {len(nse_500_list)} stocks from NSE website")
            return nse_500_list
        
        # Option 2: Use Yahoo Finance to get Nifty 500 components
        nse_500_components = pd.read_html("https://finance.yahoo.com/quote/%5ECNX500/components/")[0]
        nse_500_list = nse_500_components['Symbol'].apply(lambda x: x.replace('.NS', '')).tolist()
        logger.info(f"Successfully fetched {len(nse_500_list)} stocks from Yahoo Finance")
        return nse_500_list
    
    except Exception as e:
        logger.error(f"Error fetching NSE 500 stocks: {str(e)}")
        # Fallback to a static list of major NSE stocks
        logger.warning("Using fallback list of major NSE stocks")
        
        # Return a subset of major NSE stocks as fallback
        fallback_list = [
            "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", 
            "SBIN", "BAJFINANCE", "BHARTIARTL", "KOTAKBANK", "ADANIPORTS", 
            "ASIANPAINT", "AXISBANK", "TATASTEEL", "MARUTI", "WIPRO", "HCLTECH", 
            "SUNPHARMA", "ITC", "ULTRACEMCO", "TITAN", "BAJAJFINSV", "ADANIENT", 
            "TECHM", "DIVISLAB", "POWERGRID", "NTPC", "INDUSINDBK", "JSWSTEEL", 
            "M&M", "TATAMOTORS", "HDFCLIFE", "ONGC", "APOLLOHOSP", "CIPLA", 
            "ADANIGREEN", "SBILIFE", "GRASIM", "NESTLEIND", "HINDALCO", "EICHERMOT", 
            "DRREDDY", "COALINDIA", "BAJAJ-AUTO", "SHREECEM", "BRITANNIA", "UPL", 
            "HEROMOTOCO", "TATACONSUM", "BPCL"
        ]
        return fallback_list

def fetch_stock_data(symbols, period="60d", interval="1d"):
    """Fetch historical stock data for analysis."""
    logger.info(f"Fetching historical data for {len(symbols)} stocks...")
    
    if not symbols:
        logger.error("No symbols provided to fetch_stock_data")
        return {}
    
    # Add .NS suffix for NSE stocks
    symbols_with_suffix = [f"{symbol}.NS" for symbol in symbols]
    
    stock_data = {}
    chunk_size = 50  # Process in chunks to avoid rate limits
    
    for i in range(0, len(symbols_with_suffix), chunk_size):
        chunk = symbols_with_suffix[i:i+chunk_size]
        logger.info(f"Fetching data for chunk {i//chunk_size + 1}/{(len(symbols_with_suffix)-1)//chunk_size + 1}")
        
        try:
            # Fetch data for this chunk
            data = yf.download(
                tickers=chunk,
                period=period,
                interval=interval,
                group_by='ticker',
                auto_adjust=True,
                threads=True
            )
            
            # Process each stock in the chunk
            if len(chunk) == 1:
                # Special case for single stock (different data structure)
                symbol = symbols[i].strip()
                if not data.empty:
                    stock_data[symbol] = data
            else:
                # Multiple stocks
                for j, symbol in enumerate(chunk):
                    original_symbol = symbols[i+j].strip()
                    try:
                        if symbol in data.columns.levels[0]:
                            stock_df = data[symbol].copy()
                            if not stock_df.empty:
                                stock_data[original_symbol] = stock_df
                    except Exception as e:
                        logger.error(f"Error processing {symbol}: {str(e)}")
            
            # Sleep to avoid rate limiting
            time.sleep(1)
            
        except Exception as e:
            logger.error(f"Error fetching data for chunk: {str(e)}")
    
    logger.info(f"Successfully fetched data for {len(stock_data)} stocks")
    return stock_data

def calculate_technical_indicators(stock_data):
    """Calculate technical indicators for each stock."""
    logger.info("Calculating technical indicators...")
    
    results = []
    
    for symbol, df in stock_data.items():
        try:
            # Skip if not enough data
            if len(df) < 30:
                logger.warning(f"Not enough data for {symbol}, skipping")
                continue
                
            # Make sure we have OHLCV data
            if not all(col in df.columns for col in ['Open', 'High', 'Low', 'Close', 'Volume']):
                logger.warning(f"Missing required price data for {symbol}, skipping")
                continue
            
            # Get the latest data
            latest = df.iloc[-1].copy()
            prev_day = df.iloc[-2].copy() if len(df) > 1 else latest.copy()
            
            # Basic price data
            stock_info = {
                'symbol': symbol,
                'name': symbol.replace('.NS', ''),
                'close': round(latest['Close'], 2),
                'prev_close': round(prev_day['Close'], 2),
                'change': round(latest['Close'] - prev_day['Close'], 2),
                'change_percent': round(((latest['Close'] / prev_day['Close']) - 1) * 100, 2),
                'volume': int(latest['Volume']),
                'avg_volume_10d': int(df['Volume'].tail(10).mean()),
                'date': latest.name.strftime('%Y-%m-%d')
            }
            
            # Calculate various technical indicators
            
            # 1. Moving Averages
            df['SMA_20'] = talib.SMA(df['Close'], timeperiod=20)
            df['SMA_50'] = talib.SMA(df['Close'], timeperiod=50)
            df['SMA_200'] = talib.SMA(df['Close'], timeperiod=200)
            df['EMA_20'] = talib.EMA(df['Close'], timeperiod=20)
            df['EMA_50'] = talib.EMA(df['Close'], timeperiod=50)
            
            # 2. RSI (Relative Strength Index)
            df['RSI'] = talib.RSI(df['Close'], timeperiod=14)
            
            # 3. MACD (Moving Average Convergence Divergence)
            df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = talib.MACD(
                df['Close'], fastperiod=12, slowperiod=26, signalperiod=9)
            
            # 4. Bollinger Bands
            df['BB_Upper'], df['BB_Middle'], df['BB_Lower'] = talib.BBANDS(
                df['Close'], timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
            
            # 5. Stochastic Oscillator
            df['SlowK'], df['SlowD'] = talib.STOCH(
                df['High'], df['Low'], df['Close'], 
                fastk_period=14, slowk_period=3, slowk_matype=0, 
                slowd_period=3, slowd_matype=0)
            
            # 6. ADX (Average Directional Index)
            df['ADX'] = talib.ADX(df['High'], df['Low'], df['Close'], timeperiod=14)
            
            # 7. OBV (On-Balance Volume)
            df['OBV'] = talib.OBV(df['Close'], df['Volume'])
            
            # 8. CCI (Commodity Channel Index)
            df['CCI'] = talib.CCI(df['High'], df['Low'], df['Close'], timeperiod=14)
            
            # 9. ATR (Average True Range)
            df['ATR'] = talib.ATR(df['High'], df['Low'], df['Close'], timeperiod=14)
            
            # 10. Williams %R
            df['WILLR'] = talib.WILLR(df['High'], df['Low'], df['Close'], timeperiod=14)
            
            # Add the latest indicator values to stock_info
            latest_indicators = df.iloc[-1]
            
            stock_info.update({
                'sma_20': round(latest_indicators['SMA_20'], 2) if not pd.isna(latest_indicators['SMA_20']) else None,
                'sma_50': round(latest_indicators['SMA_50'], 2) if not pd.isna(latest_indicators['SMA_50']) else None,
                'sma_200': round(latest_indicators['SMA_200'], 2) if not pd.isna(latest_indicators['SMA_200']) else None,
                'ema_20': round(latest_indicators['EMA_20'], 2) if not pd.isna(latest_indicators['EMA_20']) else None,
                'ema_50': round(latest_indicators['EMA_50'], 2) if not pd.isna(latest_indicators['EMA_50']) else None,
                'rsi': round(latest_indicators['RSI'], 2) if not pd.isna(latest_indicators['RSI']) else None,
                'macd': round(latest_indicators['MACD'], 3) if not pd.isna(latest_indicators['MACD']) else None,
                'macd_signal': round(latest_indicators['MACD_Signal'], 3) if not pd.isna(latest_indicators['MACD_Signal']) else None,
                'macd_hist': round(latest_indicators['MACD_Hist'], 3) if not pd.isna(latest_indicators['MACD_Hist']) else None,
                'bb_upper': round(latest_indicators['BB_Upper'], 2) if not pd.isna(latest_indicators['BB_Upper']) else None,
                'bb_middle': round(latest_indicators['BB_Middle'], 2) if not pd.isna(latest_indicators['BB_Middle']) else None,
                'bb_lower': round(latest_indicators['BB_Lower'], 2) if not pd.isna(latest_indicators['BB_Lower']) else None,
                'stoch_k': round(latest_indicators['SlowK'], 2) if not pd.isna(latest_indicators['SlowK']) else None,
                'stoch_d': round(latest_indicators['SlowD'], 2) if not pd.isna(latest_indicators['SlowD']) else None,
                'adx': round(latest_indicators['ADX'], 2) if not pd.isna(latest_indicators['ADX']) else None,
                'cci': round(latest_indicators['CCI'], 2) if not pd.isna(latest_indicators['CCI']) else None,
                'atr': round(latest_indicators['ATR'], 3) if not pd.isna(latest_indicators['ATR']) else None,
                'willr': round(latest_indicators['WILLR'], 2) if not pd.isna(latest_indicators['WILLR']) else None
            })
            
            # Calculate buy/sell signals
            signals = []
            
            # Signal 1: Price above both 20 and 50 EMAs (Bullish)
            if (stock_info['close'] > stock_info['ema_20'] and 
                stock_info['close'] > stock_info['ema_50']):
                signals.append({'type': 'buy', 'strength': 1, 'desc': 'Price > EMA20 & EMA50'})
            
            # Signal 2: Golden Cross (50-day SMA crosses above 200-day SMA) (Strong Bullish)
            if (df['SMA_50'].iloc[-1] > df['SMA_200'].iloc[-1] and 
                df['SMA_50'].iloc[-2] <= df['SMA_200'].iloc[-2]):
                signals.append({'type': 'buy', 'strength': 3, 'desc': 'Golden Cross'})
            
            # Signal 3: Death Cross (50-day SMA crosses below 200-day SMA) (Strong Bearish)
            if (df['SMA_50'].iloc[-1] < df['SMA_200'].iloc[-1] and 
                df['SMA_50'].iloc[-2] >= df['SMA_200'].iloc[-2]):
                signals.append({'type': 'sell', 'strength': 3, 'desc': 'Death Cross'})
            
            # Signal 4: RSI conditions
            if stock_info['rsi'] is not None:
                if stock_info['rsi'] < 30:
                    signals.append({'type': 'buy', 'strength': 2, 'desc': 'RSI Oversold (<30)'})
                elif stock_info['rsi'] > 70:
                    signals.append({'type': 'sell', 'strength': 2, 'desc': 'RSI Overbought (>70)'})
            
            # Signal 5: MACD crossover
            if (stock_info['macd'] is not None and stock_info['macd_signal'] is not None):
                if (df['MACD'].iloc[-1] > df['MACD_Signal'].iloc[-1] and 
                    df['MACD'].iloc[-2] <= df['MACD_Signal'].iloc[-2]):
                    signals.append({'type': 'buy', 'strength': 2, 'desc': 'MACD Bullish Crossover'})
                elif (df['MACD'].iloc[-1] < df['MACD_Signal'].iloc[-1] and 
                      df['MACD'].iloc[-2] >= df['MACD_Signal'].iloc[-2]):
                    signals.append({'type': 'sell', 'strength': 2, 'desc': 'MACD Bearish Crossover'})
            
            # Signal 6: Bollinger Bands
            if (stock_info['bb_upper'] is not None and stock_info['bb_lower'] is not None):
                if stock_info['close'] <= stock_info['bb_lower']:
                    signals.append({'type': 'buy', 'strength': 1, 'desc': 'Price at BB Lower Band'})
                elif stock_info['close'] >= stock_info['bb_upper']:
                    signals.append({'type': 'sell', 'strength': 1, 'desc': 'Price at BB Upper Band'})
            
            # Signal 7: Stochastic Oscillator
            if (stock_info['stoch_k'] is not None and stock_info['stoch_d'] is not None):
                if (stock_info['stoch_k'] < 20 and stock_info['stoch_d'] < 20):
                    signals.append({'type': 'buy', 'strength': 1, 'desc': 'Stochastic Oversold'})
                elif (stock_info['stoch_k'] > 80 and stock_info['stoch_d'] > 80):
                    signals.append({'type': 'sell', 'strength': 1, 'desc': 'Stochastic Overbought'})
                if (df['SlowK'].iloc[-1] > df['SlowD'].iloc[-1] and 
                    df['SlowK'].iloc[-2] <= df['SlowD'].iloc[-2]):
                    signals.append({'type': 'buy', 'strength': 1, 'desc': 'Stochastic Bullish Crossover'})
                elif (df['SlowK'].iloc[-1] < df['SlowD'].iloc[-1] and 
                      df['SlowK'].iloc[-2] >= df['SlowD'].iloc[-2]):
                    signals.append({'type': 'sell', 'strength': 1, 'desc': 'Stochastic Bearish Crossover'})
            
            # Signal 8: ADX trend strength
            if stock_info['adx'] is not None:
                if stock_info['adx'] > 25:
                    if stock_info['change_percent'] > 0:
                        signals.append({'type': 'buy', 'strength': 1, 'desc': 'Strong Trend (ADX>25) with Positive Price'})
                    else:
                        signals.append({'type': 'sell', 'strength': 1, 'desc': 'Strong Trend (ADX>25) with Negative Price'})
            
            # Signal 9: Volume spike
            if (stock_info['volume'] > 2 * stock_info['avg_volume_10d']):
                if stock_info['change_percent'] > 0:
                    signals.append({'type': 'buy', 'strength': 2, 'desc': 'Volume Spike with Price Increase'})
                else:
                    signals.append({'type': 'sell', 'strength': 2, 'desc': 'Volume Spike with Price Decrease'})
            
            # Signal 10: Williams %R
            if stock_info['willr'] is not None:
                if stock_info['willr'] < -80:
                    signals.append({'type': 'buy', 'strength': 1, 'desc': 'Williams %R Oversold'})
                elif stock_info['willr'] > -20:
                    signals.append({'type': 'sell', 'strength': 1, 'desc': 'Williams %R Overbought'})
            
            # Calculate overall signal
            buy_strength = sum(signal['strength'] for signal in signals if signal['type'] == 'buy')
            sell_strength = sum(signal['strength'] for signal in signals if signal['type'] == 'sell')
            
            if buy_strength > sell_strength:
                overall_signal = 'BUY'
                signal_strength = min(10, buy_strength)
            elif sell_strength > buy_strength:
                overall_signal = 'SELL'
                signal_strength = min(10, sell_strength)
            else:
                overall_signal = 'NEUTRAL'
                signal_strength = 0
            
            stock_info['signals'] = signals
            stock_info['overall_signal'] = overall_signal
            stock_info['signal_strength'] = signal_strength
            
            results.append(stock_info)
            
        except Exception as e:
            logger.error(f"Error calculating indicators for {symbol}: {str(e)}")
    
    # Convert to DataFrame
    df_results = pd.DataFrame(results)
    logger.info(f"Calculated indicators for {len(df_results)} stocks")
    
    return df_results

def get_recommendations(df):
    """Get buy and sell recommendations from the analysis."""
    if df is None or df.empty:
        return None, None, None
    
    # Sort by signal strength
    df_buy = df[df['overall_signal'] == 'BUY'].sort_values('signal_strength', ascending=False)
    df_sell = df[df['overall_signal'] == 'SELL'].sort_values('signal_strength', ascending=False)
    
    # Create a watchlist of high signal strength stocks (both buy and sell)
    df_watchlist = pd.concat([df_buy.head(10), df_sell.head(10)])
    
    return df_buy, df_sell, df_watchlist

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

def format_telegram_message(df_buy, df_sell, df_all):
    """Format data for Telegram message."""
    if df_all is None or df_all.empty:
        return "No stock data available for analysis."
    
    # Format the message
    now = datetime.now(pytz.timezone('Asia/Kolkata'))
    message = f"<b>📊 Stock Market Analysis ({now.strftime('%Y-%m-%d')})</b>\n\n"
    
    # Market overview
    positive_stocks = len(df_all[df_all['change_percent'] > 0])
    negative_stocks = len(df_all[df_all['change_percent'] < 0])
    message += f"<b>Market Overview:</b>\n"
    message += f"• Stocks analyzed: {len(df_all)}\n"
    message += f"• Stocks up: {positive_stocks} ({round(positive_stocks/len(df_all)*100, 1)}%)\n"
    message += f"• Stocks down: {negative_stocks} ({round(negative_stocks/len(df_all)*100, 1)}%)\n\n"
    
    # Top BUY recommendations
    if df_buy is not None and not df_buy.empty:
        top_buy = df_buy.head(5)
        message += f"<b>🟢 TOP BUY SIGNALS:</b>\n"
        for _, row in top_buy.iterrows():
            message += f"• {row['symbol']}: ₹{row['close']} ({row['change_percent']}%), Strength: {row['signal_strength']}/10\n"
        message += "\n"
    
    # Top SELL recommendations
    if df_sell is not None and not df_sell.empty:
        top_sell = df_sell.head(5)
        message += f"<b>🔴 TOP SELL SIGNALS:</b>\n"
        for _, row in top_sell.iterrows():
            message += f"• {row['symbol']}: ₹{row['close']} ({row['change_percent']}%), Strength: {row['signal_strength']}/10\n"
        message += "\n"
    
    # Biggest gainers and losers
    top_gainers = df_all.sort_values('change_percent', ascending=False).head(3)
    top_losers = df_all.sort_values('change_percent').head(3)
    
    message += f"<b>📈 Biggest Gainers:</b>\n"
    for _, row in top_gainers.iterrows():
        message += f"• {row['symbol']}: ₹{row['close']} (+{row['change_percent']}%)\n"
    message += "\n"
    
    message += f"<b>📉 Biggest Losers:</b>\n"
    for _, row in top_losers.iterrows():
        message += f"• {row['symbol']}: ₹{row['close']} ({row['change_percent']}%)\n"
    
    return message

def get_data():
    """Get stock data with caching."""
    current_time = time.time()
    
    # Cache data for 1 hour (3600 seconds)
    if cached_data['data'] is None or cached_data['timestamp'] is None or \
       (current_time - cached_data['timestamp'] > 3600):
        
        logger.info("Cache expired, fetching fresh data")
        
        # 1. Get NSE 500 stock list
        stock_symbols = fetch_nse_500_list()
        
        # 2. Fetch historical data
        stock_data = fetch_stock_data(stock_symbols)
        
        # 3. Calculate technical indicators
        df_results = calculate_technical_indicators(stock_data)
        
        if df_results is not None and not df_results.empty:
            # 4. Get recommendations
            df_buy, df_sell, df_watchlist = get_recommendations(df_results)
            
            # 5. Update cache
            cached_data['data'] = df_results
            cached_data['buy_recommendations'] = df_buy
            cached_data['sell_recommendations'] = df_sell 
            cached_data['watchlist'] = df_watchlist
            cached_data['timestamp'] = current_time
    else:
        logger.info("Using cached data")
    
    return cached_data['data'], cached_data['buy_recommendations'], cached_data['sell_recommendations'], cached_data['watchlist']

def run_daily_analysis():
    """Run daily analysis and send report to Telegram."""
    logger.info("Running daily analysis")
    
    # Update the data
    df_all, df_buy, df_sell, _ = get_data()
    
    # Format and send the message
    message = format_telegram_message(df_buy, df_sell, df_all)
    send_to_telegram(message)
    
    logger.info("Daily analysis completed")

# Flask routes
@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')

@app.route('/api/data')
def api_data():
    """API endpoint to get the data."""
    df_all, df_buy, df_sell, df_watchlist = get_data()
    
    # If we couldn't fetch real data
    if df_all is None or df_all.empty:
        logger.error("No data available")
        return jsonify({
            'error': 'No data available',
            'updated_at': None
        }), 500
    
    return jsonify({
        'all_stocks': df_all.to_dict('records'),
        'buy_recommendations': df_buy.to_dict('records') if df_buy is not None else [],
        'sell_recommendations': df_sell.to_dict('records') if df_sell is not None else [],
        'watchlist': df_watchlist.to_dict('records') if df_watchlist is not None else [],
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
    <title>NSE Stock Analysis - Technical Indicators</title>
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
        .nav-pills .nav-link.active { background-color: #0d6efd; }
        .signal-buy { background-color: rgba(0, 128, 0, 0.1); }
        .signal-sell { background-color: rgba(255, 0, 0, 0.1); }
        .signal-neutral { background-color: rgba(128, 128, 128, 0.1); }
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
            border-radius: 5px;
        }
        .strength-buy { background-color: green; }
        .strength-sell { background-color: red; }
        .indicator-box {
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 10px;
            background-color: #f8f9fa;
            border: 1px solid #dee2e6;
        }
        .indicator-title {
            font-weight: bold;
            margin-bottom: 5px;
        }
        .tooltip-inner {
            max-width: 300px;
            text-align: left;
        }
    </style>
</head>
<body>
    <div class="container">
              <h1 class="text-center mb-4">NSE Stock Analysis - Technical Indicators</h1>
       
        <div class="last-updated" id="lastUpdated">Last updated: Loading...</div>
        
        <div id="loading" class="loading">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="mt-2">Loading stock data and calculating indicators...</p>
        </div>
        
        <div id="content" style="display: none;">
            <ul class="nav nav-pills mb-3" id="pills-tab" role="tablist">
                <li class="nav-item" role="presentation">
                    <button class="nav-link active" id="pills-watch-tab" data-bs-toggle="pill" data-bs-target="#pills-watch" type="button" role="tab">Watchlist</button>
                </li>
                <li class="nav-item" role="presentation">
                    <button class="nav-link" id="pills-buy-tab" data-bs-toggle="pill" data-bs-target="#pills-buy" type="button" role="tab">Buy Signals</button>
                </li>
                <li class="nav-item" role="presentation">
                    <button class="nav-link" id="pills-sell-tab" data-bs-toggle="pill" data-bs-target="#pills-sell" type="button" role="tab">Sell Signals</button>
                </li>
                <li class="nav-item" role="presentation">
                    <button class="nav-link" id="pills-all-tab" data-bs-toggle="pill" data-bs-target="#pills-all" type="button" role="tab">All Stocks</button>
                </li>
            </ul>
            
            <div class="tab-content" id="pills-tabContent">
                <div class="tab-pane fade show active" id="pills-watch" role="tabpanel">
                    <div class="table-responsive">
                        <table id="watchlistTable" class="table table-striped table-hover">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>Close (₹)</th>
                                    <th>Change</th>
                                    <th>% Change</th>
                                    <th>Signal</th>
                                    <th>Strength</th>
                                    <th>Volume</th>
                                    <th>Indicators</th>
                                </tr>
                            </thead>
                            <tbody id="watchlistBody"></tbody>
                        </table>
                    </div>
                </div>
                <div class="tab-pane fade" id="pills-buy" role="tabpanel">
                    <div class="table-responsive">
                        <table id="buyTable" class="table table-striped table-hover">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>Close (₹)</th>
                                    <th>Change</th>
                                    <th>% Change</th>
                                    <th>Strength</th>
                                    <th>Volume</th>
                                    <th>Indicators</th>
                                </tr>
                            </thead>
                            <tbody id="buyBody"></tbody>
                        </table>
                    </div>
                </div>
                <div class="tab-pane fade" id="pills-sell" role="tabpanel">
                    <div class="table-responsive">
                        <table id="sellTable" class="table table-striped table-hover">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>Close (₹)</th>
                                    <th>Change</th>
                                    <th>% Change</th>
                                    <th>Strength</th>
                                    <th>Volume</th>
                                    <th>Indicators</th>
                                </tr>
                            </thead>
                            <tbody id="sellBody"></tbody>
                        </table>
                    </div>
                </div>
                <div class="tab-pane fade" id="pills-all" role="tabpanel">
                    <div class="table-responsive">
                        <table id="allStocksTable" class="table table-striped table-hover">
                            <thead>
                                <tr>
                                    <th>Symbol</th>
                                    <th>Close (₹)</th>
                                    <th>Change</th>
                                    <th>% Change</th>
                                    <th>Signal</th>
                                    <th>Volume</th>
                                    <th>RSI</th>
                                    <th>MACD</th>
                                    <th>EMA20</th>
                                    <th>EMA50</th>
                                </tr>
                            </thead>
                            <tbody id="allStocksBody"></tbody>
                        </table>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Modal for Stock Details -->
    <div class="modal fade" id="stockDetailModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="stockDetailTitle">Stock Details</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <div class="row">
                        <div class="col-md-6">
                            <div class="indicator-box">
                                <div class="indicator-title">Price & Volume</div>
                                <div id="stockPrice"></div>
                            </div>
                            <div class="indicator-box">
                                <div class="indicator-title">Moving Averages</div>
                                <div id="stockMA"></div>
                            </div>
                            <div class="indicator-box">
                                <div class="indicator-title">Oscillators</div>
                                <div id="stockOscillators"></div>
                            </div>
                        </div>
                        <div class="col-md-6">
                            <div class="indicator-box">
                                <div class="indicator-title">Bollinger Bands</div>
                                <div id="stockBB"></div>
                            </div>
                            <div class="indicator-box">
                                <div class="indicator-title">MACD</div>
                                <div id="stockMACD"></div>
                            </div>
                            <div class="indicator-box">
                                <div class="indicator-title">Other Indicators</div>
                                <div id="stockOther"></div>
                            </div>
                        </div>
                    </div>
                    <div class="row mt-3">
                        <div class="col-12">
                            <div class="indicator-box">
                                <div class="indicator-title">Buy/Sell Signals</div>
                                <div id="stockSignals"></div>
                            </div>
                        </div>
                    </div>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    </div>

    <script src="https://code.jquery.com/jquery-3.6.3.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.1/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.datatables.net/1.13.1/js/dataTables.bootstrap5.min.js"></script>
    
    <script>
        // Initialize tooltips
        const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
        const tooltipList = [...tooltipTriggerList].map(tooltipTriggerEl => new bootstrap.Tooltip(tooltipTriggerEl));
        
        // DataTables instances
        let watchlistTable, buyTable, sellTable, allStocksTable;
        
        // Initialize the page
        $(document).ready(function() {
            fetchData();
            
            // Set up auto-refresh every 15 minutes
            setInterval(fetchData, 15 * 60 * 1000);
        });
        
        function fetchData() {
            $('#loading').show();
            $('#content').hide();
            
            $.ajax({
                url: '/api/data',
                type: 'GET',
                dataType: 'json',
                success: function(data) {
                    if (data.updated_at) {
                        $('#lastUpdated').text('Last updated: ' + data.updated_at);
                    }
                    
                    populateWatchlistTable(data.watchlist);
                    populateBuyTable(data.buy_recommendations);
                    populateSellTable(data.sell_recommendations);
                    populateAllStocksTable(data.all_stocks);
                    
                    $('#loading').hide();
                    $('#content').show();
                },
                error: function(xhr, status, error) {
                    console.error("Error fetching data:", error);
                    $('#loading').html('<div class="alert alert-danger">Error loading data. Please try again later.</div>');
                }
            });
        }
        
        function populateWatchlistTable(data) {
            const tableBody = $('#watchlistBody');
            tableBody.empty();
            
            data.forEach(stock => {
                const row = $('<tr>').addClass(stock.overall_signal === 'BUY' ? 'signal-buy' : (stock.overall_signal === 'SELL' ? 'signal-sell' : 'signal-neutral'));
                
                // Add the data to the row
                row.append($('<td>').text(stock.symbol));
                row.append($('<td>').text(stock.close.toFixed(2)));
                
                const changeCell = $('<td>');
                const changeValue = stock.change.toFixed(2);
                const changeElement = $('<span>').text(changeValue);
                if (stock.change > 0) {
                    changeElement.addClass('up').prepend('▲ ');
                } else if (stock.change < 0) {
                    changeElement.addClass('down').prepend('▼ ');
                }
                changeCell.append(changeElement);
                row.append(changeCell);
                
                const changePercentCell = $('<td>');
                const changePercentValue = stock.change_percent.toFixed(2) + '%';
                const changePercentElement = $('<span>').text(changePercentValue);
                if (stock.change_percent > 0) {
                    changePercentElement.addClass('up').prepend('+');
                } else if (stock.change_percent < 0) {
                    changePercentElement.addClass('down');
                }
                changePercentCell.append(changePercentElement);
                row.append(changePercentCell);
                
                // Signal
                const signalCell = $('<td>');
                const signalBadge = $('<span>').addClass('badge').text(stock.overall_signal);
                if (stock.overall_signal === 'BUY') {
                    signalBadge.addClass('bg-success');
                } else if (stock.overall_signal === 'SELL') {
                    signalBadge.addClass('bg-danger');
                } else {
                    signalBadge.addClass('bg-secondary');
                }
                signalCell.append(signalBadge);
                row.append(signalCell);
                
                // Strength indicator
                const strengthCell = $('<td>');
                const strengthIndicator = $('<div>').addClass('strength-indicator');
                const strengthBar = $('<div>').addClass('strength-indicator-bar');
                
                if (stock.overall_signal === 'BUY') {
                    strengthBar.addClass('strength-buy');
                    strengthBar.css('width', (stock.signal_strength * 10) + '%');
                } else if (stock.overall_signal === 'SELL') {
                    strengthBar.addClass('strength-sell');
                    strengthBar.css('width', (stock.signal_strength * 10) + '%');
                }
                
                strengthIndicator.append(strengthBar);
                strengthCell.append(strengthIndicator);
                strengthCell.append($('<span>').addClass('ms-2').text(stock.signal_strength + '/10'));
                row.append(strengthCell);
                
                // Volume
                const volumeFormatted = formatNumber(stock.volume);
                row.append($('<td>').text(volumeFormatted));
                
                // Indicators (button to show modal)
                const detailsCell = $('<td>');
                const detailsButton = $('<button>').addClass('btn btn-sm btn-outline-primary')
                    .text('Details')
                    .on('click', function() { showStockDetails(stock); });
                detailsCell.append(detailsButton);
                row.append(detailsCell);
                
                tableBody.append(row);
            });
            
            // Initialize or refresh DataTable
            if (watchlistTable) {
                watchlistTable.destroy();
            }
            
            watchlistTable = $('#watchlistTable').DataTable({
                order: [[5, 'desc']], // Sort by signal strength
                pageLength: 25,
                language: {
                    search: "Filter:",
                    lengthMenu: "Show _MENU_ stocks"
                }
            });
        }
        
        function populateBuyTable(data) {
            const tableBody = $('#buyBody');
            tableBody.empty();
            
            data.forEach(stock => {
                const row = $('<tr>').addClass('signal-buy');
                
                // Add the data to the row
                row.append($('<td>').text(stock.symbol));
                row.append($('<td>').text(stock.close.toFixed(2)));
                
                const changeCell = $('<td>');
                const changeValue = stock.change.toFixed(2);
                const changeElement = $('<span>').text(changeValue);
                if (stock.change > 0) {
                    changeElement.addClass('up').prepend('▲ ');
                } else if (stock.change < 0) {
                    changeElement.addClass('down').prepend('▼ ');
                }
                changeCell.append(changeElement);
                row.append(changeCell);
                
                const changePercentCell = $('<td>');
                const changePercentValue = stock.change_percent.toFixed(2) + '%';
                const changePercentElement = $('<span>').text(changePercentValue);
                if (stock.change_percent > 0) {
                    changePercentElement.addClass('up').prepend('+');
                } else if (stock.change_percent < 0) {
                    changePercentElement.addClass('down');
                }
                changePercentCell.append(changePercentElement);
                row.append(changePercentCell);
                
                // Strength indicator
                const strengthCell = $('<td>');
                const strengthIndicator = $('<div>').addClass('strength-indicator');
                const strengthBar = $('<div>').addClass('strength-indicator-bar strength-buy');
                strengthBar.css('width', (stock.signal_strength * 10) + '%');
                strengthIndicator.append(strengthBar);
                strengthCell.append(strengthIndicator);
                strengthCell.append($('<span>').addClass('ms-2').text(stock.signal_strength + '/10'));
                row.append(strengthCell);
                
                // Volume
                const volumeFormatted = formatNumber(stock.volume);
                row.append($('<td>').text(volumeFormatted));
                
                // Indicators (button to show modal)
                const detailsCell = $('<td>');
                const detailsButton = $('<button>').addClass('btn btn-sm btn-outline-primary')
                    .text('Details')
                    .on('click', function() { showStockDetails(stock); });
                detailsCell.append(detailsButton);
                row.append(detailsCell);
                
                tableBody.append(row);
            });
            
            // Initialize or refresh DataTable
            if (buyTable) {
                buyTable.destroy();
            }
            
            buyTable = $('#buyTable').DataTable({
                order: [[4, 'desc']], // Sort by signal strength
                pageLength: 25,
                language: {
                    search: "Filter:",
                    lengthMenu: "Show _MENU_ stocks"
                }
            });
        }
        
        function populateSellTable(data) {
            const tableBody = $('#sellBody');
            tableBody.empty();
            
            data.forEach(stock => {
                const row = $('<tr>').addClass('signal-sell');
                
                // Add the data to the row
                row.append($('<td>').text(stock.symbol));
                row.append($('<td>').text(stock.close.toFixed(2)));
                
                const changeCell = $('<td>');
                const changeValue = stock.change.toFixed(2);
                const changeElement = $('<span>').text(changeValue);
                if (stock.change > 0) {
                    changeElement.addClass('up').prepend('▲ ');
                } else if (stock.change < 0) {
                    changeElement.addClass('down').prepend('▼ ');
                }
                changeCell.append(changeElement);
                row.append(changeCell);
                
                const changePercentCell = $('<td>');
                const changePercentValue = stock.change_percent.toFixed(2) + '%';
                const changePercentElement = $('<span>').text(changePercentValue);
                if (stock.change_percent > 0) {
                    changePercentElement.addClass('up').prepend('+');
                } else if (stock.change_percent < 0) {
                    changePercentElement.addClass('down');
                }
                changePercentCell.append(changePercentElement);
                row.append(changePercentCell);
                
                // Strength indicator
                const strengthCell = $('<td>');
                const strengthIndicator = $('<div>').addClass('strength-indicator');
                const strengthBar = $('<div>').addClass('strength-indicator-bar strength-sell');
                strengthBar.css('width', (stock.signal_strength * 10) + '%');
                strengthIndicator.append(strengthBar);
                strengthCell.append(strengthIndicator);
                strengthCell.append($('<span>').addClass('ms-2').text(stock.signal_strength + '/10'));
                row.append(strengthCell);
                
                // Volume
                const volumeFormatted = formatNumber(stock.volume);
                row.append($('<td>').text(volumeFormatted));
                
                // Indicators (button to show modal)
                const detailsCell = $('<td>');
                const detailsButton = $('<button>').addClass('btn btn-sm btn-outline-primary')
                    .text('Details')
                    .on('click', function() { showStockDetails(stock); });
                detailsCell.append(detailsButton);
                row.append(detailsCell);
                
                tableBody.append(row);
            });
            
            // Initialize or refresh DataTable
            if (sellTable) {
                sellTable.destroy();
            }
            
            sellTable = $('#sellTable').DataTable({
                order: [[4, 'desc']], // Sort by signal strength
                pageLength: 25,
                language: {
                    search: "Filter:",
                    lengthMenu: "Show _MENU_ stocks"
                }
            });
        }
        
        function populateAllStocksTable(data) {
            const tableBody = $('#allStocksBody');
            tableBody.empty();
            
            data.forEach(stock => {
                let rowClass = '';
                if (stock.overall_signal === 'BUY') {
                    rowClass = 'signal-buy';
                } else if (stock.overall_signal === 'SELL') {
                    rowClass = 'signal-sell';
                }
                
                const row = $('<tr>').addClass(rowClass);
                
                // Add the data to the row
                row.append($('<td>').text(stock.symbol));
                row.append($('<td>').text(stock.close.toFixed(2)));
                
                const changeCell = $('<td>');
                const changeValue = stock.change.toFixed(2);
                const changeElement = $('<span>').text(changeValue);
                if (stock.change > 0) {
                    changeElement.addClass('up').prepend('▲ ');
                } else if (stock.change < 0) {
                    changeElement.addClass('down').prepend('▼ ');
                }
                changeCell.append(changeElement);
                row.append(changeCell);
                
                const changePercentCell = $('<td>');
                const changePercentValue = stock.change_percent.toFixed(2) + '%';
                const changePercentElement = $('<span>').text(changePercentValue);
                if (stock.change_percent > 0) {
                    changePercentElement.addClass('up').prepend('+');
                } else if (stock.change_percent < 0) {
                    changePercentElement.addClass('down');
                }
                changePercentCell.append(changePercentElement);
                row.append(changePercentCell);
                
                // Signal
                const signalCell = $('<td>');
                const signalBadge = $('<span>').addClass('badge').text(stock.overall_signal);
                if (stock.overall_signal === 'BUY') {
                    signalBadge.addClass('bg-success');
                } else if (stock.overall_signal === 'SELL') {
                    signalBadge.addClass('bg-danger');
                } else {
                    signalBadge.addClass('bg-secondary');
                }
                signalCell.append(signalBadge);
                row.append(signalCell);
                
                // Volume
                const volumeFormatted = formatNumber(stock.volume);
                row.append($('<td>').text(volumeFormatted));
                
                // RSI
                const rsiCell = $('<td>');
                if (stock.rsi !== null) {
                    let rsiClass = '';
                    if (stock.rsi < 30) {
                        rsiClass = 'up'; // Oversold
                    } else if (stock.rsi > 70) {
                        rsiClass = 'down'; // Overbought
                    }
                    rsiCell.append($('<span>').addClass(rsiClass).text(stock.rsi.toFixed(2)));
                } else {
                    rsiCell.text('N/A');
                }
                row.append(rsiCell);
                
                // MACD
                const macdCell = $('<td>');
                if (stock.macd !== null && stock.macd_signal !== null) {
                    let macdClass = '';
                    if (stock.macd > stock.macd_signal) {
                        macdClass = 'up'; // Bullish
                    } else if (stock.macd < stock.macd_signal) {
                        macdClass = 'down'; // Bearish
                    }
                    macdCell.append($('<span>').addClass(macdClass).text(stock.macd.toFixed(3)));
                } else {
                    macdCell.text('N/A');
                }
                row.append(macdCell);
                
                // EMA20
                const ema20Cell = $('<td>');
                if (stock.ema_20 !== null) {
                    let emaClass = '';
                    if (stock.close > stock.ema_20) {
                        emaClass = 'up'; // Above EMA
                    } else if (stock.close < stock.ema_20) {
                        emaClass = 'down'; // Below EMA
                    }
                    ema20Cell.append($('<span>').addClass(emaClass).text(stock.ema_20.toFixed(2)));
                } else {
                    ema20Cell.text('N/A');
                }
                row.append(ema20Cell);
                
                // EMA50
                const ema50Cell = $('<td>');
                if (stock.ema_50 !== null) {
                    let emaClass = '';
                    if (stock.close > stock.ema_50) {
                        emaClass = 'up'; // Above EMA
                    } else if (stock.close < stock.ema_50) {
                        emaClass = 'down'; // Below EMA
                    }
                    ema50Cell.append($('<span>').addClass(emaClass).text(stock.ema_50.toFixed(2)));
                } else {
                    ema50Cell.text('N/A');
                }
                row.append(ema50Cell);
                
                tableBody.append(row);
            });
            
            // Initialize or refresh DataTable
            if (allStocksTable) {
                allStocksTable.destroy();
            }
            
            allStocksTable = $('#allStocksTable').DataTable({
                pageLength: 25,
                language: {
                    search: "Filter:",
                    lengthMenu: "Show _MENU_ stocks"
                }
            });
        }
        
        function showStockDetails(stock) {
            // Set the modal title
            $('#stockDetailTitle').text(stock.symbol + ' - Technical Analysis');
            
            // Price & Volume Info
            let priceHtml = `<strong>Close:</strong> ₹${stock.close.toFixed(2)}<br>`;
            priceHtml += `<strong>Change:</strong> <span class="${stock.change_percent > 0 ? 'up' : (stock.change_percent < 0 ? 'down' : '')}">
                ${stock.change_percent > 0 ? '+' : ''}${stock.change_percent.toFixed(2)}% (₹${stock.change.toFixed(2)})</span><br>`;
            priceHtml += `<strong>Volume:</strong> ${formatNumber(stock.volume)}<br>`;
            priceHtml += `<strong>Avg Volume (10d):</strong> ${formatNumber(stock.avg_volume_10d)}`;
            $('#stockPrice').html(priceHtml);
            
            // Moving Averages
            let maHtml = `<strong>SMA 20:</strong> ₹${stock.sma_20 ? stock.sma_20.toFixed(2) : 'N/A'}<br>`;
            maHtml += `<strong>SMA 50:</strong> ₹${stock.sma_50 ? stock.sma_50.toFixed(2) : 'N/A'}<br>`;
            maHtml += `<strong>SMA 200:</strong> ₹${stock.sma_200 ? stock.sma_200.toFixed(2) : 'N/A'}<br>`;
            maHtml += `<strong>EMA 20:</strong> ₹${stock.ema_20 ? stock.ema_20.toFixed(2) : 'N/A'}<br>`;
            maHtml += `<strong>EMA 50:</strong> ₹${stock.ema_50 ? stock.ema_50.toFixed(2) : 'N/A'}`;
            $('#stockMA').html(maHtml);
            
            // Oscillators
            let oscHtml = `<strong>RSI (14):</strong> ${stock.rsi ? stock.rsi.toFixed(2) : 'N/A'}<br>`;
            oscHtml += `<strong>Stochastic %K:</strong> ${stock.stoch_k ? stock.stoch_k.toFixed(2) : 'N/A'}<br>`;
            oscHtml += `<strong>Stochastic %D:</strong> ${stock.stoch_d ? stock.stoch_d.toFixed(2) : 'N/A'}<br>`;
            oscHtml += `<strong>Williams %R:</strong> ${stock.willr ? stock.willr.toFixed(2) : 'N/A'}`;
            $('#stockOscillators').html(oscHtml);
            
            // Bollinger Bands
            let bbHtml = `<strong>Upper Band:</strong> ₹${stock.bb_upper ? stock.bb_upper.toFixed(2) : 'N/A'}<br>`;
            bbHtml += `<strong>Middle Band:</strong> ₹${stock.bb_middle ? stock.bb_middle.toFixed(2) : 'N/A'}<br>`;
            bbHtml += `<strong>Lower Band:</strong> ₹${stock.bb_lower ? stock.bb_lower.toFixed(2) : 'N/A'}<br>`;
            $('#stockBB').html(bbHtml);
            
            // MACD
            let macdHtml = `<strong>MACD Line:</strong> ${stock.macd ? stock.macd.toFixed(3) : 'N/A'}<br>`;
            macdHtml += `<strong>Signal Line:</strong> ${stock.macd_signal ? stock.macd_signal.toFixed(3) : 'N/A'}<br>`;
            macdHtml += `<strong>Histogram:</strong> ${stock.macd_hist ? stock.macd_hist.toFixed(3) : 'N/A'}`;
            $('#stockMACD').html(macdHtml);
            
            // Other indicators
            let otherHtml = `<strong>ADX:</strong> ${stock.adx ? stock.adx.toFixed(2) : 'N/A'}<br>`;
            otherHtml += `<strong>CCI:</strong> ${stock.cci ? stock.cci.toFixed(2) : 'N/A'}<br>`;
            otherHtml += `<strong>ATR:</strong> ${stock.atr ? stock.atr.toFixed(3) : 'N/A'}`;
            $('#stockOther').html(otherHtml);
            
            // Buy/Sell Signals
            let signalsHtml = `<strong>Overall Signal:</strong> <span class="badge ${stock.overall_signal === 'BUY' ? 'bg-success' : (stock.overall_signal === 'SELL' ? 'bg-danger' : 'bg-secondary')}">${stock.overall_signal}</span>`;
            signalsHtml += ` (Strength: ${stock.signal_strength}/10)<br><br>`;
            
            if (stock.signals && stock.signals.length > 0) {
                signalsHtml += '<strong>Signal Details:</strong><ul>';
                stock.signals.forEach(signal => {
                    const signalClass = signal.type === 'buy' ? 'text-success' : 'text-danger';
                    signalsHtml += `<li class="${signalClass}">${signal.desc} (${signal.type.toUpperCase()}, Strength: ${signal.strength})</li>`;
                });
                signalsHtml += '</ul>';
            } else {
                signalsHtml += '<p>No specific signals detected.</p>';
            }
            
            $('#stockSignals').html(signalsHtml);
            
            // Show the modal
            const modal = new bootstrap.Modal(document.getElementById('stockDetailModal'));
            modal.show();
        }
        def format_number(num):
    """Format large numbers for display."""
    if num >= 1000000:
        return f"{(num / 1000000):.2f}M"
    elif num >= 1000:
        return f"{(num / 1000):.2f}K"
    else:
        return str(num)

# Complete the script.js content
with open('static/script.js', 'w', encoding='utf-8') as f:
    f.write('''
function formatNumber(num) {
    if (num >= 1000000) {
        return (num / 1000000).toFixed(2) + "M";
    } else if (num >= 1000) {
        return (num / 1000).toFixed(2) + "K";
    } else {
        return num.toString();
    }
}
''')

# Schedule the daily analysis to run at 4 PM IST (10:30 UTC)
def schedule_tasks():
    """Schedule daily tasks."""
    schedule.every().day.at("10:30").do(run_daily_analysis)
    
    # Run the scheduler in a separate thread
    def run_schedule():
        while True:
            schedule.run_pending()
            time.sleep(60)  # Check every minute
    
    scheduler_thread = threading.Thread(target=run_schedule, daemon=True)
    scheduler_thread.start()
    logger.info("Scheduler started")

if __name__ == "__main__":
    # Create static directory
    os.makedirs('static', exist_ok=True)
    
    # Initialize the scheduler
    schedule_tasks()
    
    # Determine port
    port = int(os.environ.get('PORT', 5000))
    
    # Log startup information
    logger.info(f"Starting NSE Stock Analysis App on port {port}")
    logger.info("App will send daily analysis to Telegram at 4 PM IST")
    
    # Start the Flask app
    app.run(host='0.0.0.0', port=port, debug=False)
