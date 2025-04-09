# telegram_sender.py
import requests
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Get secrets from environment variables ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_GROUP_CHANNEL = os.environ.get('TELEGRAM_GROUP_CHANNEL') # e.g., @Stockniftybot
# ---------------------------------------------

# Ensure secrets are set
if not TELEGRAM_TOKEN:
    logging.warning("TELEGRAM_TOKEN environment variable not set. Telegram notifications disabled.")
if not TELEGRAM_CHAT_ID:
    logging.warning("TELEGRAM_CHAT_ID environment variable not set. Direct Telegram notifications disabled.")
if not TELEGRAM_GROUP_CHANNEL:
    logging.warning("TELEGRAM_GROUP_CHANNEL environment variable not set. Group Telegram notifications disabled.")

def send_message(text, chat_id):
    """Sends a message to a specific Telegram chat ID."""
    if not TELEGRAM_TOKEN or not chat_id:
        logging.warning(f"Skipping Telegram message due to missing token or chat_id for: {chat_id}")
        return False

    send_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'  # Allows basic HTML formatting like <b>, <i>
    }
    try:
        response = requests.post(send_url, data=payload, timeout=10) # Added timeout
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        logging.info(f"Successfully sent message to chat_id: {chat_id}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Telegram message to {chat_id}: {e}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred sending Telegram message to {chat_id}: {e}")
        return False

def notify_recommendations(recommendations):
    """Formats and sends recommendation summary to configured chats."""
    if not recommendations:
        message = "No strong Buy/Sell signals generated today based on the strategy."
    else:
        buy_signals = [f"<b>BUY {rec['symbol']}</b> @ {rec['price']:.2f} (Target: {rec['target']:.2f})"
                       for rec in recommendations if rec['signal'] == 'BUY']
        sell_signals = [f"<b>SELL {rec['symbol']}</b> @ {rec['price']:.2f}"
                        for rec in recommendations if rec['signal'] == 'SELL'] # Assuming SELL means exit long

        message = "<b>Stock Recommendations:</b>\n\n"
        if buy_signals:
            message += "--- BUYS ---\n" + "\n".join(buy_signals) + "\n\n"
        if sell_signals:
            message += "--- SELLS ---\n" + "\n".join(sell_signals) + "\n\n"
        if not buy_signals and not sell_signals:
             message = "No strong Buy/Sell signals generated today based on the strategy."

    # Send to direct Chat ID
    if TELEGRAM_CHAT_ID:
        send_message(message, TELEGRAM_CHAT_ID)

    # Send to Group Channel
    if TELEGRAM_GROUP_CHANNEL:
        send_message(message, TELEGRAM_GROUP_CHANNEL) # Use group username like @YourGroupName
