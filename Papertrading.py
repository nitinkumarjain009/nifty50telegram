import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
import datetime
import random
import logging
from telegram import Bot
import asyncio
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("trading_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Telegram configuration
TELEGRAM_TOKEN = "8017759392:AAEwM-W-y83lLXTjlPl8sC_aBmizuIrFXnU"
TELEGRAM_CHANNEL = "@Stockniftybot"
TELEGRAM_CHAT_ID = 711856868

# URLs to scrape
URLS = [
    "https://nifty500-trading-bot.onrender.com/",
    "https://nifty500-trading-bot.onrender.com/"  # You mentioned the same URL twice
]

class PaperTradingAccount:
    def __init__(self, initial_balance=100000):
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.positions = {}  # symbol: {'quantity': qty, 'price': price, 'action': 'BUY'/'SELL'}
        self.trade_history = []
        
    def execute_trade(self, symbol, action, price, quantity=1):
        timestamp = datetime.datetime.now()
        trade_value = price * quantity
        
        if action == "BUY":
            if trade_value > self.current_balance:
                logger.warning(f"Insufficient funds to buy {quantity} of {symbol} at {price}")
                return False
            
            # Execute buy
            self.current_balance -= trade_value
            
            if symbol in self.positions:
                # Average down calculation
                total_quantity = self.positions[symbol]['quantity'] + quantity
                total_cost = (self.positions[symbol]['quantity'] * self.positions[symbol]['price']) + (quantity * price)
                avg_price = total_cost / total_quantity
                self.positions[symbol] = {'quantity': total_quantity, 'price': avg_price, 'action': 'BUY'}
            else:
                self.positions[symbol] = {'quantity': quantity, 'price': price, 'action': 'BUY'}
                
        elif action == "SELL":
            if symbol not in self.positions or self.positions[symbol]['quantity'] < quantity:
                logger.warning(f"Insufficient {symbol} to sell {quantity}")
                return False
                
            # Execute sell
            buy_price = self.positions[symbol]['price']
            profit_loss = (price - buy_price) * quantity
            
            self.current_balance += trade_value
            self.positions[symbol]['quantity'] -= quantity
            
            if self.positions[symbol]['quantity'] == 0:
                del self.positions[symbol]
                
        # Record the trade
        trade_record = {
            'timestamp': timestamp,
            'symbol': symbol,
            'action': action,
            'price': price,
            'quantity': quantity,
            'value': trade_value,
            'balance_after': self.current_balance
        }
        
        if action == "SELL":
            trade_record['profit_loss'] = profit_loss
            trade_record['profit_loss_percent'] = (profit_loss / (buy_price * quantity)) * 100
            
        self.trade_history.append(trade_record)
        logger.info(f"Executed {action} trade: {symbol} x{quantity} @ {price}")
        return True
        
    def get_portfolio_value(self):
        portfolio_value = self.current_balance
        for symbol, data in self.positions.items():
            # In a real scenario, we would fetch the current market price
            # For paper trading, we'll estimate using a random fluctuation
            current_price = data['price'] * (1 + random.uniform(-0.02, 0.02))
            portfolio_value += current_price * data['quantity']
        return portfolio_value
        
    def generate_summary(self):
        portfolio_value = self.get_portfolio_value()
        total_profit_loss = portfolio_value - self.initial_balance
        total_profit_loss_percent = (total_profit_loss / self.initial_balance) * 100
        
        active_positions = len(self.positions)
        total_trades = len(self.trade_history)
        
        # Calculate P/L from closed positions
        closed_trades_pl = sum(trade.get('profit_loss', 0) for trade in self.trade_history if 'profit_loss' in trade)
        
        summary = f"""
📊 *Trading Summary* 📊
⏱️ *Date*: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

💰 *Account Overview*:
- Initial Balance: ₹{self.initial_balance:,.2f}
- Current Cash: ₹{self.current_balance:,.2f}
- Portfolio Value: ₹{portfolio_value:,.2f}
- Total P/L: ₹{total_profit_loss:,.2f} ({total_profit_loss_percent:.2f}%)

🔄 *Trading Activity*:
- Total Trades: {total_trades}
- Active Positions: {active_positions}
- Realized P/L: ₹{closed_trades_pl:,.2f}

🔍 *Active Positions*:
"""
        if self.positions:
            for symbol, data in self.positions.items():
                current_price = data['price'] * (1 + random.uniform(-0.02, 0.02))
                position_value = current_price * data['quantity']
                unrealized_pl = (current_price - data['price']) * data['quantity']
                unrealized_pl_percent = (unrealized_pl / (data['price'] * data['quantity'])) * 100
                
                summary += f"- {symbol}: {data['quantity']} @ ₹{data['price']:.2f} | Current: ₹{current_price:.2f} | P/L: ₹{unrealized_pl:.2f} ({unrealized_pl_percent:.2f}%)\n"
        else:
            summary += "- No active positions\n"
            
        summary += "\n🤖 Generated by Automated Trading Bot"
        return summary

class RecommendationScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
    
    def fetch_page(self, url):
        try:
            response = requests.get(url, headers=self.headers)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
    
    def parse_recommendations(self, html_content):
        if not html_content:
            return []
            
        soup = BeautifulSoup(html_content, 'html.parser')
        recommendations = []
        
        # This is a placeholder parsing logic since I don't know the exact structure of the page
        # You'll need to update this based on the actual HTML structure
        try:
            # Look for tables, divs, or other elements containing stock recommendations
            recommendation_elements = soup.find_all('div', class_='recommendation-item')
            
            if not recommendation_elements:
                # Try alternative selectors if the above doesn't work
                recommendation_elements = soup.find_all('tr', class_='stock-row')
            
            for element in recommendation_elements:
                try:
                    symbol = element.find('span', class_='symbol').text.strip()
                    action = element.find('span', class_='action').text.strip()  # BUY or SELL
                    price = float(element.find('span', class_='price').text.strip().replace('₹', '').replace(',', ''))
                    
                    # Extract target price if available
                    target_element = element.find('span', class_='target')
                    target_price = None
                    if target_element:
                        target_text = target_element.text.strip()
                        # Extract numbers from text like "Target: ₹1,250"
                        target_match = re.search(r'[\d,]+\.\d+|\d+', target_text)
                        if target_match:
                            target_price = float(target_match.group().replace(',', ''))
                    
                    recommendations.append({
                        'symbol': symbol,
                        'action': action.upper(),  # Normalize to uppercase
                        'price': price,
                        'target_price': target_price
                    })
                except (AttributeError, ValueError) as e:
                    logger.warning(f"Error parsing recommendation element: {e}")
                    continue
        except Exception as e:
            logger.error(f"Error during parsing: {e}")
        
        # Fallback method with regex if structured parsing fails
        if not recommendations:
            logger.info("Using regex fallback for parsing recommendations")
            # Look for patterns like "BUY RELIANCE at ₹2,750 target ₹2,900"
            patterns = [
                r'(BUY|SELL)\s+([A-Z]+)\s+at\s+₹?([\d,]+\.?\d*)\s+target\s+₹?([\d,]+\.?\d*)',
                r'([A-Z]+)\s+(BUY|SELL)\s+at\s+₹?([\d,]+\.?\d*)\s+target\s+₹?([\d,]+\.?\d*)'
            ]
            
            for pattern in patterns:
                matches = re.findall(pattern, html_content)
                for match in matches:
                    if len(match) == 4:
                        if match[0].upper() in ['BUY', 'SELL']:
                            action, symbol, price, target = match
                        else:
                            symbol, action, price, target = match
                            
                        recommendations.append({
                            'symbol': symbol,
                            'action': action.upper(),
                            'price': float(price.replace(',', '')),
                            'target_price': float(target.replace(',', ''))
                        })
        
        return recommendations
    
    def estimate_target_price(self, recommendation):
        """Estimate target price if not provided"""
        if recommendation['target_price'] is not None:
            return recommendation['target_price']
            
        # Simple estimation logic
        price = recommendation['price']
        if recommendation['action'] == 'BUY':
            # For buy recommendations, estimate 5-10% upside
            target_percent = random.uniform(5, 10)
            return round(price * (1 + target_percent/100), 2)
        else:  # SELL
            # For sell recommendations, estimate 5-10% downside
            target_percent = random.uniform(5, 10)
            return round(price * (1 - target_percent/100), 2)

class TelegramNotifier:
    def __init__(self, token):
        self.token = token
        self.bot = Bot(token=token)
    
    async def send_message(self, chat_id, message):
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='Markdown'
            )
            logger.info(f"Message sent to {chat_id}")
            return True
        except Exception as e:
            logger.error(f"Error sending message to {chat_id}: {e}")
            return False
            
    def send_notification(self, chat_id, message):
        """Synchronous wrapper for send_message"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(self.send_message(chat_id, message))
        loop.close()
        return result

class TradingBot:
    def __init__(self, urls, telegram_token, telegram_channel, telegram_chat_id):
        self.urls = urls
        self.scraper = RecommendationScraper()
        self.account = PaperTradingAccount()
        self.notifier = TelegramNotifier(telegram_token)
        self.telegram_channel = telegram_channel
        self.telegram_chat_id = telegram_chat_id
        self.last_check_time = None
        
    def fetch_recommendations(self):
        all_recommendations = []
        
        for url in self.urls:
            html_content = self.scraper.fetch_page(url)
            if html_content:
                recommendations = self.scraper.parse_recommendations(html_content)
                for rec in recommendations:
                    if rec['target_price'] is None:
                        rec['target_price'] = self.scraper.estimate_target_price(rec)
                all_recommendations.extend(recommendations)
                
        return all_recommendations
    
    def execute_paper_trades(self, recommendations):
        if not recommendations:
            logger.info("No recommendations to trade")
            return
            
        for rec in recommendations:
            symbol = rec['symbol']
            action = rec['action']
            price = rec['price']
            
            # Calculate quantity based on a percentage of portfolio
            trade_value = min(self.account.current_balance * 0.1, 10000)  # 10% of balance or 10k max
            quantity = max(1, int(trade_value / price))
            
            if rec['action'] == 'BUY':
                self.account.execute_trade(symbol, action, price, quantity)
            elif rec['action'] == 'SELL':
                # Check if we have the position first
                if symbol in self.account.positions:
                    quantity = min(quantity, self.account.positions[symbol]['quantity'])
                    self.account.execute_trade(symbol, action, price, quantity)
            
            # Send trade notification
            trade_msg = f"""
🔔 *New Paper Trade* 🔔
{action} {quantity} {symbol} @ ₹{price:,.2f}
Target Price: ₹{rec['target_price']:,.2f}
"""
            self.notifier.send_notification(self.telegram_chat_id, trade_msg)
            self.notifier.send_notification(self.telegram_channel, trade_msg)
    
    def check_exit_conditions(self):
        """Check if any positions need to be exited based on target prices or stop losses"""
        for symbol, position in list(self.account.positions.items()):
            # In a real scenario, we would fetch current market price
            # For paper trading, we'll simulate price movement
            current_price = position['price'] * (1 + random.uniform(-0.05, 0.05))
            
            # Exit logic - this is simplified
            if position['action'] == 'BUY':
                # For long positions
                profit_percent = (current_price - position['price']) / position['price'] * 100
                
                # Take profit at 5% or cut loss at -3%
                if profit_percent >= 5 or profit_percent <= -3:
                    action = "SELL"
                    reason = "Target Reached" if profit_percent >= 5 else "Stop Loss"
                    self.account.execute_trade(symbol, action, current_price, position['quantity'])
                    
                    exit_msg = f"""
🔄 *Position Closed: {reason}* 🔄
{action} {position['quantity']} {symbol} @ ₹{current_price:,.2f}
Entry: ₹{position['price']:,.2f}
P/L: ₹{(current_price - position['price']) * position['quantity']:,.2f} ({profit_percent:.2f}%)
"""
                    self.notifier.send_notification(self.telegram_chat_id, exit_msg)
                    self.notifier.send_notification(self.telegram_channel, exit_msg)
    
    def run_daily_cycle(self):
        """Run the daily trading cycle"""
        try:
            logger.info("Starting daily trading cycle")
            
            # Check if market is open (simplified - usually would check actual market hours)
            now = datetime.datetime.now()
            is_weekday = now.weekday() < 5  # Monday to Friday
            is_market_hours = 9 <= now.hour < 16  # 9 AM to 4 PM
            
            if not (is_weekday and is_market_hours):
                logger.info("Market is closed. Skipping cycle.")
                return
                
            # Fetch and process recommendations
            recommendations = self.fetch_recommendations()
            logger.info(f"Fetched {len(recommendations)} recommendations")
            
            # Execute trades based on recommendations
            self.execute_paper_trades(recommendations)
            
            # Check exit conditions for existing positions
            self.check_exit_conditions()
            
            # Generate and send daily summary
            summary = self.account.generate_summary()
            self.notifier.send_notification(self.telegram_chat_id, summary)
            self.notifier.send_notification(self.telegram_channel, summary)
            
            logger.info("Completed daily trading cycle")
            
        except Exception as e:
            logger.error(f"Error in daily cycle: {e}")
            error_msg = f"⚠️ *Trading Bot Error* ⚠️\n{str(e)}"
            self.notifier.send_notification(self.telegram_chat_id, error_msg)
    
    def start(self, interval_minutes=60):
        """Start the trading bot with specified check interval"""
        logger.info(f"Starting trading bot with {interval_minutes} minute interval")
        
        try:
            # Send startup notification
            startup_msg = f"""
🚀 *Trading Bot Started* 🚀
Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Check Interval: {interval_minutes} minutes
Initial Balance: ₹{self.account.initial_balance:,.2f}
"""
            self.notifier.send_notification(self.telegram_chat_id, startup_msg)
            self.notifier.send_notification(self.telegram_channel, startup_msg)
            
            while True:
                self.run_daily_cycle()
                # Sleep until next check
                logger.info(f"Sleeping for {interval_minutes} minutes")
                time.sleep(interval_minutes * 60)
                
        except KeyboardInterrupt:
            logger.info("Trading bot stopped by user")
            shutdown_msg = f"""
🛑 *Trading Bot Stopped* 🛑
Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
Final Portfolio Value: ₹{self.account.get_portfolio_value():,.2f}
"""
            self.notifier.send_notification(self.telegram_chat_id, shutdown_msg)
            self.notifier.send_notification(self.telegram_channel, shutdown_msg)
        except Exception as e:
            logger.critical(f"Critical error: {e}")
            error_msg = f"❌ *Trading Bot Crashed* ❌\n{str(e)}"
            self.notifier.send_notification(self.telegram_chat_id, error_msg)

if __name__ == "__main__":
    bot = TradingBot(
        urls=URLS,
        telegram_token=TELEGRAM_TOKEN,
        telegram_channel=TELEGRAM_CHANNEL,
        telegram_chat_id=TELEGRAM_CHAT_ID
    )
    
    # Set the check interval (in minutes)
    check_interval = 60  # Check every hour
    
    # Start the bot
    bot.start(interval_minutes=check_interval)
