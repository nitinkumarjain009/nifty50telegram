# indicators.py
import pandas as pd

def add_sma(df, window):
    """Adds Simple Moving Average"""
    df[f'SMA_{window}'] = df['Close'].rolling(window=window, min_periods=1).mean()
    return df

def add_ema(df, span):
    """Adds Exponential Moving Average"""
    df[f'EMA_{span}'] = df['Close'].ewm(span=span, adjust=False, min_periods=1).mean()
    return df

def add_rsi(df, window=14):
    """Adds Relative Strength Index (RSI)"""
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=window, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=window, min_periods=1).mean()

    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    # Fill initial NaNs if any method results in them early on
    df['RSI'] = df['RSI'].fillna(50) # Fill NaNs with neutral 50
    return df

def add_macd(df, fast_period=12, slow_period=26, signal_period=9):
    """Adds Moving Average Convergence Divergence (MACD)"""
    df['EMA_Fast'] = df['Close'].ewm(span=fast_period, adjust=False).mean()
    df['EMA_Slow'] = df['Close'].ewm(span=slow_period, adjust=False).mean()
    df['MACD'] = df['EMA_Fast'] - df['EMA_Slow']
    df['MACD_Signal'] = df['MACD'].ewm(span=signal_period, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    return df

def add_bollinger_bands(df, window=20, num_std_dev=2):
    """Adds Bollinger Bands"""
    df[f'SMA_{window}'] = df['Close'].rolling(window=window, min_periods=1).mean() # Ensure middle band exists
    rolling_std = df['Close'].rolling(window=window, min_periods=1).std()
    df['Bollinger_Upper'] = df[f'SMA_{window}'] + (rolling_std * num_std_dev)
    df['Bollinger_Lower'] = df[f'SMA_{window}'] - (rolling_std * num_std_dev)
    return df

def calculate_all_indicators(df):
    """Calculates a standard set of indicators."""
    df = add_sma(df, 20)
    df = add_sma(df, 50)
    # df = add_ema(df, 12) # Included in MACD
    # df = add_ema(df, 26) # Included in MACD
    df = add_rsi(df, 14)
    df = add_macd(df, 12, 26, 9)
    df = add_bollinger_bands(df, 20, 2)
    return df
