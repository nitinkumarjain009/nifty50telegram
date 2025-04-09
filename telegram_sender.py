# telegram_sender.py
import requests
import os
import logging
import pandas as pd # Add pandas import
import dataframe_image as dfi # Add dataframe_image import

# Configure logging... (same as before)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Get secrets ---
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
TELEGRAM_GROUP_CHANNEL = os.environ.get('TELEGRAM_GROUP_CHANNEL')

# --- Check secrets --- (same as before)
if not TELEGRAM_TOKEN: logging.warning("TELEGRAM_TOKEN missing.")
if not TELEGRAM_CHAT_ID: logging.warning("TELEGRAM_CHAT_ID missing.")
if not TELEGRAM_GROUP_CHANNEL: logging.warning("TELEGRAM_GROUP_CHANNEL missing.")


def send_text_message(text, chat_id):
    """Sends a text message to a specific Telegram chat ID."""
    # This is your original send_message function, renamed for clarity
    if not TELEGRAM_TOKEN or not chat_id:
        logging.warning(f"Skipping Telegram text message: Missing token or chat_id for {chat_id}")
        return False

    send_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML'
    }
    try:
        response = requests.post(send_url, data=payload, timeout=15) # Increased timeout slightly
        response.raise_for_status()
        logging.info(f"Successfully sent text message to chat_id: {chat_id}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Telegram text message to {chat_id}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error sending Telegram text message to {chat_id}: {e}")
        return False

def send_photo_message(image_path, caption, chat_id):
    """Sends a photo with a caption to a specific Telegram chat ID."""
    if not TELEGRAM_TOKEN or not chat_id:
        logging.warning(f"Skipping Telegram photo message: Missing token or chat_id for {chat_id}")
        return False
    if not os.path.exists(image_path):
        logging.error(f"Skipping Telegram photo message: Image file not found at {image_path}")
        return False

    send_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {'photo': open(image_path, 'rb')}
    payload = {'chat_id': chat_id, 'caption': caption, 'parse_mode': 'HTML'}

    try:
        response = requests.post(send_url, data=payload, files=files, timeout=30) # Longer timeout for upload
        response.raise_for_status()
        logging.info(f"Successfully sent photo message to chat_id: {chat_id}")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send Telegram photo message to {chat_id}: {e}")
        # Attempt to send error as text message
        send_text_message(f"Error: Failed to send recommendation image to {chat_id}.\nReason: {e}", chat_id)
        return False
    except Exception as e:
        logging.error(f"Unexpected error sending Telegram photo message to {chat_id}: {e}")
        send_text_message(f"Error: Unexpected error sending recommendation image to {chat_id}.\nReason: {e}", chat_id)
        return False
    finally:
        # Ensure the file stream is closed if it was opened
        if 'photo' in files and files['photo']:
            files['photo'].close()

def notify_recommendations_photo(df_display):
    """Formats summary, creates image from DataFrame, and sends as photo."""
    if df_display is None or df_display.empty:
        message = "No recommendation data available to generate image."
        logging.warning(message)
        # Send text message if dataframe is empty
        if TELEGRAM_CHAT_ID: send_text_message(message, TELEGRAM_CHAT_ID)
        if TELEGRAM_GROUP_CHANNEL: send_text_message(message, TELEGRAM_GROUP_CHANNEL)
        return

    image_path = 'stock_summary.png' # Temporary image file name

    try:
        logging.info(f"Generating image from DataFrame to {image_path}...")
        # Use dataframe_image to export the DataFrame to a PNG file
        # Using matplotlib backend is often more reliable in headless environments
        dfi.export(df_display, image_path, table_conversion='matplotlib')
        logging.info(f"Image successfully generated: {image_path}")

        # Prepare caption
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        caption = f"<b>Stock Summary & Recommendations</b>\n{timestamp}\n(CMP based on last close)"

        # Send to direct Chat ID
        if TELEGRAM_CHAT_ID:
            send_photo_message(image_path, caption, TELEGRAM_CHAT_ID)

        # Send to Group Channel
        if TELEGRAM_GROUP_CHANNEL:
            send_photo_message(image_path, caption, TELEGRAM_GROUP_CHANNEL)

    except ImportError:
         logging.error("dataframe_image or matplotlib not installed properly. Cannot generate image.")
         send_text_message("Error: Failed to generate recommendation image (missing libraries).", TELEGRAM_CHAT_ID)
         send_text_message("Error: Failed to generate recommendation image (missing libraries).", TELEGRAM_GROUP_CHANNEL)
    except Exception as e:
        logging.error(f"Failed to generate or send Telegram photo: {e}", exc_info=True)
        # Send error as text message
        err_msg = f"Error: Failed to generate/send recommendation image.\nReason: {e}"
        if TELEGRAM_CHAT_ID: send_text_message(err_msg, TELEGRAM_CHAT_ID)
        if TELEGRAM_GROUP_CHANNEL: send_text_message(err_msg, TELEGRAM_GROUP_CHANNEL)
    finally:
        # --- Clean up the generated image file ---
        if os.path.exists(image_path):
            try:
                os.remove(image_path)
                logging.info(f"Removed temporary image file: {image_path}")
            except OSError as e:
                logging.error(f"Error removing temporary image file {image_path}: {e}")

# Note: The old `notify_recommendations` function sending text is replaced
#       by `notify_recommendations_photo`. If you need both, keep the old
#       one and call it separately if needed.
