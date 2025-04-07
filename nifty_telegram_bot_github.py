#!/usr/bin/env python3
import sys
import os
import logging
import datetime
import numpy as np
import subprocess
import importlib.util

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Check and install required packages
required_packages = ['requests', 'pandas', 'beautifulsoup4', 'pandas_ta', 'pywhatkit', 'pywhatsapp']

for package in required_packages:
    try:
        if package in ['pandas_ta', 'pywhatkit', 'pywhatsapp']:
            # Check if specific packages are installed
            if importlib.util.find_spec(package) is None:
                logger.info(f"Installing {package}...")
                subprocess.check_call([sys.executable, "-m", "pip", "install", package])
                logger.info(f"{package} installed successfully.")
            else:
                logger.info(f"{package} is already installed.")
        else:
            # For other packages, just import to check
            __import__(package)
    except (ImportError, subprocess.CalledProcessError) as e:
        logger.error(f"Error installing {package}: {str(e)}")
        logger.info(f"Attempting to install {package}...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
            logger.info(f"{package} installed successfully.")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install {package}: {str(e)}")
            sys.exit(1)

# Import packages (after ensuring they're installed)
import requests
import pandas as pd
from bs4 import BeautifulSoup
import pandas_ta as ta
import pywhatkit

# Telegram configuration - using environment variables for security with fallback to hardcoded values
# WARNING: Hardcoded fallback API key is a security risk. Use environment variables.
API_KEY = os.environ.get("TELEGRAM_API_KEY", "8017759392:AAEwM-W-y83lLXTjlPl8sC_aBmizuIrFXnU")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "@stockniftybot")  # Using channel username
BASE_URL = f"https://api.telegram.org/bot{API_KEY}"

# WhatsApp configuration
WHATSAPP_GROUP = "Moneymine"
WHATSAPP_ADMIN = "+918376906697"

# Function to debug Telegram connection
def debug_telegram_connection():
    try:
        # Get bot info to check if token is valid
        test_url = f"{BASE_URL}/getMe"
        response = requests.get(test_url)
        if response.status_code == 200:
            bot_info = response.json()
            logger.info(f"Bot connection successful. Bot name: {bot_info['result']['first_name']}")
        else:
            logger.error(f"Bot connection failed: {response.text}")

        # Test channel posting permission
        test_message = "Testing bot permissions in this channel."
        send_telegram_message(test_message)

    except Exception as e:
        logger.error(f"Debug test failed: {str(e)}")

# Function to send message to Telegram channel
def send_telegram_message(text):
    url = f"{BASE_URL}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,  # Using channel username: @stockniftybot
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, data=payload)
        if response.status_code == 200:
            logger.info(f"Message sent successfully to {CHAT_ID}")
        else:
            error_info = response.json() if response.text else "No error details"
            logger.error(f"Failed to send message: {error_info}")
    except Exception as e:
        logger.error(f"Error sending message: {str(e)}")

# Function to send message to WhatsApp group
def send_whatsapp_message(text):
    try:
        # First connect with the admin
        current_time = datetime.datetime.now()
        # Add 2 minutes to allow pywhatkit to set up
        send_time = current_time + datetime.timedelta(minutes=2)
        
        # Send message to WhatsApp group admin
        pywhatkit.sendwhatmsg(
            WHATSAPP_ADMIN, 
            f"Nifty Bot Alert for {WHATSAPP_GROUP}:\n\n{text}", 
            send_time.hour, 
            send_time.minute,
            wait_time=30  # Seconds to wait before sending message
        )
        logger.info(f"WhatsApp message scheduled to {WHATSAPP_GROUP} via admin at {send_time.strftime('%H:%M')}")
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {str(e)}")

# Function to get historical price data for technical indicators calculation (SIMULATED DATA)
def get_historical_data(symbol, timeframe='1M', periods=100):
    try:
        # In real implementation, this would fetch data from an API
        # For now, generating sample data
        end_date = datetime.datetime.now()

        # Determine date range based on timeframe
        if timeframe == '1M':
            # Monthly data
            start_date = end_date - datetime.timedelta(days=periods*30)
        elif timeframe == '1W':
            # Weekly data
            start_date = end_date - datetime.timedelta(days=periods*7)
        elif timeframe == '1D':
            # Daily data
            start_date = end_date - datetime.timedelta(days=periods)
        else:
            # Default to daily
            start_date = end_date - datetime.timedelta(days=periods)

        date_range = pd.date_range(start=start_date, end=end_date, periods=periods)

        # Generate sample data based on symbol hash for consistency
        np.random.seed(sum(ord(c) for c in symbol))
        
        # Generate price data with a trend and volatility
        base_price = 1000 + (sum(ord(c) for c in symbol) % 5000)
        trend = 0.001 * (sum(ord(c) for c in symbol) % 10 - 5)  # Between -0.005 and 0.005
        volatility = 0.01 + 0.005 * (sum(ord(c) for c in symbol) % 5)  # Between 0.01 and 0.035
        
        # Generate price series with random walk
        prices = [base_price]
        volumes = []
        
        for i in range(1, periods):
            new_price = prices[-1] * (1 + trend + np.random.normal(0, volatility))
            prices.append(new_price)
            volumes.append(int(np.random.normal(1000000, 300000)))

        # Create DataFrame
        df = pd.DataFrame({
            'Date': date_range,
            'Open': prices,
            'High': [p * (1 + np.random.uniform(0, 0.02)) for p in prices],
            'Low': [p * (1 - np.random.uniform(0, 0.02)) for p in prices],
            'Close': prices,
            'Volume': volumes
        })
        
        df.set_index('Date', inplace=True)
        return df
        
    except Exception as e:
        logger.error(f"Error generating historical data: {str(e)}")
        return pd.DataFrame()

# Calculate all technical indicators
def calculate_indicators(df):
    if df.empty:
        return df
    
    try:
        # RSI (Relative Strength Index)
        df['RSI'] = ta.rsi(df['Close'], length=14)
        
        # MACD (Moving Average Convergence Divergence)
        macd = ta.macd(df['Close'])
        df = pd.concat([df, macd], axis=1)
        
        # Bollinger Bands
        bbands = ta.bbands(df['Close'])
        df = pd.concat([df, bbands], axis=1)
        
        # Moving Averages
        df['SMA_20'] = ta.sma(df['Close'], length=20)
        df['SMA_50'] = ta.sma(df['Close'], length=50)
        df['SMA_200'] = ta.sma(df['Close'], length=200)
        df['EMA_12'] = ta.ema(df['Close'], length=12)
        df['EMA_26'] = ta.ema(df['Close'], length=26)
        
        # Stochastic Oscillator
        stoch = ta.stoch(df['High'], df['Low'], df['Close'])
        df = pd.concat([df, stoch], axis=1)
        
        # ATR (Average True Range) - Volatility indicator
        df['ATR'] = ta.atr(df['High'], df['Low'], df['Close'])
        
        # ADX (Average Directional Index) - Trend strength
        adx = ta.adx(df['High'], df['Low'], df['Close'])
        df = pd.concat([df, adx], axis=1)
        
        # CCI (Commodity Channel Index)
        df['CCI'] = ta.cci(df['High'], df['Low'], df['Close'])
        
        # OBV (On-Balance Volume)
        df['OBV'] = ta.obv(df['Close'], df['Volume'])
        
        # Percentage Change (last column - most important as per requirements)
        df['Pct_Change'] = df['Close'].pct_change() * 100
        
        return df
    
    except Exception as e:
        logger.error(f"Error calculating indicators: {str(e)}")
        return df

# Generate signals based on indicators
def generate_signals(df):
    if df.empty:
        return {}
    
    try:
        latest = df.iloc[-1]
        signals = {}
        
        # RSI signals
        if latest['RSI'] < 30:
            signals['RSI'] = "Oversold (Buy Signal)"
        elif latest['RSI'] > 70:
            signals['RSI'] = "Overbought (Sell Signal)"
        else:
            signals['RSI'] = "Neutral"
            
        # MACD signals
        if latest['MACD_12_26_9'] > latest['MACDs_12_26_9'] and df.iloc[-2]['MACD_12_26_9'] <= df.iloc[-2]['MACDs_12_26_9']:
            signals['MACD'] = "Bullish Crossover (Buy Signal)"
        elif latest['MACD_12_26_9'] < latest['MACDs_12_26_9'] and df.iloc[-2]['MACD_12_26_9'] >= df.iloc[-2]['MACDs_12_26_9']:
            signals['MACD'] = "Bearish Crossover (Sell Signal)"
        else:
            signals['MACD'] = "No Crossover"
            
        # Moving Average signals
        if latest['Close'] > latest['SMA_200']:
            signals['Trend'] = "Bullish (Above 200 SMA)"
        else:
            signals['Trend'] = "Bearish (Below 200 SMA)"
            
        # Golden/Death Cross
        if latest['SMA_50'] > latest['SMA_200'] and df.iloc[-2]['SMA_50'] <= df.iloc[-2]['SMA_200']:
            signals['Cross'] = "Golden Cross (Major Buy Signal)"
        elif latest['SMA_50'] < latest['SMA_200'] and df.iloc[-2]['SMA_50'] >= df.iloc[-2]['SMA_200']:
            signals['Cross'] = "Death Cross (Major Sell Signal)"
        else:
            signals['Cross'] = "No Cross"
            
        # Bollinger Band signals
        if latest['Close'] < latest['BBL_5_2.0']:
            signals['Bollinger'] = "Below Lower Band (Potential Buy)"
        elif latest['Close'] > latest['BBU_5_2.0']:
            signals['Bollinger'] = "Above Upper Band (Potential Sell)"
        else:
            signals['Bollinger'] = "Within Bands"
            
        # Percentage change
        signals['Pct_Change'] = f"{latest['Pct_Change']:.2f}%"
        
        return signals
    
    except Exception as e:
        logger.error(f"Error generating signals: {str(e)}")
        return {"Error": str(e)}

# Main function to analyze stocks and send alerts
def analyze_stocks(symbols):
    results = []
    
    for symbol in symbols:
        try:
            # Get historical data
            df = get_historical_data(symbol, timeframe='1D', periods=200)
            
            # Calculate indicators
            df = calculate_indicators(df)
            
            # Generate signals
            signals = generate_signals(df)
            
            # Add to results
            result = {
                'Symbol': symbol,
                'Pct_Change': signals.get('Pct_Change', 'N/A'),
                'RSI': signals.get('RSI', 'N/A'),
                'MACD': signals.get('MACD', 'N/A'),
                'Trend': signals.get('Trend', 'N/A'),
                'Bollinger': signals.get('Bollinger', 'N/A')
            }
            results.append(result)
            
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {str(e)}")
            results.append({
                'Symbol': symbol,
                'Pct_Change': 'Error',
                'RSI': 'N/A',
                'MACD': 'N/A',
                'Trend': 'N/A',
                'Bollinger': 'N/A'
            })
    
    return results

# Create formatted message from results
def format_results_message(results):
    if not results:
        return "No results available."
    
    message = "📊 *Stock Analysis Report* 📊\n\n"
    message += "```\n"
    message += f"{'Symbol':<10}{'% Change':<10}{'Signal':<15}\n"
    message += "-" * 35 + "\n"
    
    for result in results:
        symbol = result['Symbol']
        pct_change = result['Pct_Change']
        
        # Determine primary signal based on multiple indicators
        signal = "NEUTRAL"
        if "Buy" in result['RSI'] and "Buy" in result['MACD']:
            signal = "STRONG BUY"
        elif "Sell" in result['RSI'] and "Sell" in result['MACD']:
            signal = "STRONG SELL"
        elif "Buy" in result['RSI'] or "Buy" in result['MACD']:
            signal = "BUY"
        elif "Sell" in result['RSI'] or "Sell" in result['MACD']:
            signal = "SELL"
        
        message += f"{symbol:<10}{pct_change:<10}{signal:<15}\n"
    
    message += "```\n\n"
    message += "💡 *Technical Indicators*\n"
    
    # Add detailed analysis for important stocks (e.g., NIFTY index)
    for result in results[:1]:  # Only for the first stock (assumed to be main index)
        message += f"\n*{result['Symbol']}*:\n"
        message += f"• RSI: {result['RSI']}\n"
        message += f"• MACD: {result['MACD']}\n"
        message += f"• Trend: {result['Trend']}\n"
        message += f"• Bollinger: {result['Bollinger']}\n"
    
    message += "\n⚠️ This is automated analysis for informational purposes only. Not financial advice."
    
    return message

# Main execution
if __name__ == "__main__":
    try:
        logger.info("Starting Nifty Bot analysis...")
        
        # List of symbols to analyze
        symbols = ["NIFTY50", "BANKNIFTY", "RELIANCE", "TCS", "HDFC", "INFY"]
        
        # Analyze stocks
        results = analyze_stocks(symbols)
        
        # Format message
        message = format_results_message(results)
        
        # Send to Telegram
        send_telegram_message(message)
        
        # Send to WhatsApp
        send_whatsapp_message(message)
        
        logger.info("Analysis complete and messages sent.")
        
    except Exception as e:
        logger.error(f"Critical error in main execution: {str(e)}")
        error_message = f"⚠️ Nifty Bot encountered a critical error: {str(e)}"
        try:
            send_telegram_message(error_message)
        except:
            logger.critical("Failed to send error notification to Telegram")
