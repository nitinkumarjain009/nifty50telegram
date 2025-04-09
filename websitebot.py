import requests
import pandas as pd
import numpy as np
import json
import time
import os
import pytz
import threading
import schedule
from flask import Flask, render_template, jsonify
import talib
import yfinance as yf
import logging
from datetime import datetime

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
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

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

def fetch_nifty50_list():
    """Fetch the list of Nifty 50 stocks from CSV."""
    logger.info("Fetching Nifty 50 stocks list...")
    try:
        nifty50_df = pd.read_csv('nifty50_stocks.csv')
        nifty50_list = nifty50_df['Symbol'].str.replace('.NS', '', regex=False).tolist()
        logger.info(f"Successfully fetched {len(nifty50_list)} stocks from CSV")
        return nifty50_list
    except Exception as e:
        logger.error(f"Error fetching Nifty 50 stocks: {str(e)}")
        return []

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
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram token or chat ID not set. Skipping Telegram notification.")
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
        logger.info("Message sent to Telegram successfully
