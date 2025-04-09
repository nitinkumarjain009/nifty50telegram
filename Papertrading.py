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
import pytz

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
    "https://nifty500-trading-bot.onrender.com/"
]

# Set IST timezone
IST = pytz.timezone('Asia/Kolkata')

class PaperTradingAccount:
    def __init__(self, initial_balance=100000):
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.positions = {}  # symbol: {'quantity': qty, 'price': price, 'action': 'BUY', 'timestamp': timestamp}
        self.trade_history = []
        self.daily_pl = {}  # date: profit/loss
        
    def execute_trade(self, symbol, action, price, quantity=1):
        timestamp = datetime.datetime.now(IST)
        trade_value = price * quantity
        profit_loss = 0
        
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
                self.positions[symbol] = {
                    'quantity': total_quantity, 
                    'price': avg_price, 
                    'action': 'BUY',
                    'timestamp': timestamp
                }
            else:
                self.positions[symbol] = {
                    'quantity': quantity, 
                    'price': price, 
                    'action': 'BUY',
                    'timestamp': timestamp
                }
                
        elif action == "SELL":
            if symbol not in self.positions or self.positions[symbol]['quantity'] < quantity:
                logger.warning(f"Insufficient {symbol} to sell {quantity}")
                return False
                
            # Execute sell
            buy_price = self.positions[symbol]['price']
            profit_loss = (price - buy_price) * quantity
            
            # Update daily P/L
            date_key = timestamp.strftime('%Y-%m-%d')
            if date_key not in self.daily_pl:
                self.daily_pl[date_key] = 0
            self.daily_pl[date_key] += profit_loss
            
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
        logger.info(f"Executed {action} trade: {symbol} x{quantity} @ {price} at {timestamp}")
        return True
        
    def get_portfolio_value(self):
        portfolio_value = self.current_balance
        unrealized_pl = 0
        
        for symbol, data in self.positions.items():
            # In a real scenario, we would fetch the current market price
            # For paper trading, we'll estimate using a random fluctuation
            current_price = data['price'] * (1 + random.uniform(-0.02, 0.02))
            position_value = current_price * data['quantity']
            position_pl = (current_price - data['price']) * data['quantity']
            
            portfolio_value += position_value
            unrealized_pl += position_pl
            
        return portfolio_value, unrealized_pl
        
    def generate_summary(self):
        portfolio_value, unrealized_pl = self.get_portfolio_value()
        total_profit_loss = portfolio_value - self.initial_balance
        total_profit_loss_percent = (total_profit_loss / self.initial_balance) * 100
        
        active_positions = len(self.positions)
        total_trades = len(self.trade_history)
        
        # Calculate P/L from closed positions
        closed_trades_pl = sum(trade.get('profit_loss', 0) for trade in self.trade_history if 'profit_loss' in trade)
        
        # Get today's date in IST
        today = datetime.datetime.now(IST).strftime('%Y-%m-%d')
        today_pl = self.daily_pl.get(today, 0) + unrealized_pl
        
        summary = f"""
📊 *Trading Summary* 📊
⏱️ *Date*: {datetime.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST

💰 *Account Overview*:
- Initial Balance: ₹{self.initial_balance:,.2f}
- Current Cash: ₹{self.current_balance:,.2f}
- Portfolio Value: ₹{portfolio_value:,.2f}
- Total P/L: ₹{total_profit_loss:,.2f} ({total_profit_loss_percent:.2f}%)

📈 *Daily Analysis*:
- Today's P/L: ₹{today_pl:,.2f}
- Realized P/L: ₹{closed_trades_pl:,.2f}
- Unrealized P/L: ₹{unrealized_pl:,.2f}

🔄 *Trading Activity*:
- Total Trades: {total_trades}
- Active Positions: {active_positions}

🔍 *Active Positions*:
"""
        if self.positions:
            for symbol, data in self.positions.items():
                current_price = data['price'] * (1 + random.uniform(-0.02, 0.02))
                position_value = current_price * data['quantity']
                unrealized_pl = (current_price - data['price']) * data['quantity']
                unrealized_pl_percent = (unrealized_pl / (data['price'] * data['quantity'])) * 100
                holding_time = datetime.datetime.now(IST) - data['timestamp']
                
                summary += f"- {symbol}: {data['quantity']} @ ₹{data['price']:.2f} | Current: ₹{current_price:.2f} | P/L: ₹{unrealized_pl:.2f} ({unrealized_pl_percent:.2f}%) | Held for: {holding_time.days}d {holding_time.seconds//3600}h\n"
        else:
            summary += "- No active positions\n"
            
        # Add daily P/L history
        if len(self.daily_pl) > 0:
            summary += "\n📆 *Daily P/L History*:\n"
            for date, pl in sorted(self.daily_pl.items(), reverse=True)[:5]:  # Show last 5 days
                summary += f"- {date}: ₹{pl:,.2f}\n"
            
        summary += "\n🤖 Generated by Automated Trading Bot"
        return summary

class RecommendationScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.last_recommendations = set()  # Track previously seen recommendations
    
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
                        'target_price': target_price,
                        'timestamp': datetime.datetime.now(IST)
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
                            'target_price': float(target.replace(',', '')),
                            'timestamp': datetime.datetime.now(IST)
                        })
        
        # Filter out recommendations we've already seen
        new_recommendations = []
        for rec in recommendations:
            rec_key = f"{rec['symbol']}_{rec['action']}_{rec['price']}"
            if rec_key not in self.last_recommendations:
                new_recommendations.append(rec)
                self.last_recommendations.add(rec_key)
        
        # Keep track of only the last 100 recommendations
        if len(self.last_recommendations) > 100:
            self.last_recommendations = set(list(self.last_recommendations)[-100:])
                
        return new_recommendations
    
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
        self.max_positions = 10  # Maximum number of stocks to hold
        self.last_daily_summary = None
        
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
            logger.info("No new recommendations to trade")
            return
            
        # Sort recommendations by expected return
        for rec in recommendations:
            if rec['action'] == 'BUY':
                expected_return = (rec['target_price'] - rec['price']) / rec['price'] * 100
            else:  # SELL
                expected_return = (rec['price'] - rec['target_price']) / rec['price'] * 100
            rec['expected_return'] = expected_return
            
        # Sort by expected return, highest first
        sorted_recommendations = sorted(recommendations, key=lambda x: x['expected_return'], reverse=True)
        
        # Limit to available slots (considering existing positions)
        available_slots = self.max_positions - len(self.account.positions)
        if available_slots <= 0:
            logger.info(f"Already at maximum positions ({self.max_positions}). Checking if any recommendations are better than current positions.")
            
            # Check if any new recommendations are better than current positions
            for rec in sorted_recommendations:
                for symbol, pos in list(self.account.positions.items()):
                    # Calculate current expected return
                    current_price = pos['price'] * (1 + random.uniform(-0.02, 0.02))
                    current_return = abs((current_price - pos['price']) / pos['price'] * 100)
                    
                    # If new recommendation is better by at least 2%, exit current position
                    if rec['expected_return'] > current_return + 2:
                        logger.info(f"Exiting {symbol} to make room for {rec['symbol']} with higher expected return")
                        self.account.execute_trade(symbol, "SELL", current_price, pos['quantity'])
                        available_slots += 1
                        break
                        
                if available_slots > 0:
                    break
        
        trades_executed = 0
        for rec in sorted_recommendations:
            if trades_executed >= available_slots:
                break
                
            symbol = rec['symbol']
            action = rec['action']
            price = rec['price']
            
            # If this is a SELL recommendation, check if we own the stock
            if action == 'SELL' and symbol not in self.account.positions:
                continue  # Skip selling stocks we don't own
                
            # Calculate quantity based on a percentage of portfolio
            trade_value = min(self.account.current_balance * 0.1, 10000)  # 10% of balance or 10k max
            quantity = max(1, int(trade_value / price))
            
            # Execute the trade
            if action == 'BUY':
                success = self.account.execute_trade(symbol, action, price, quantity)
                if success:
                    trades_executed += 1
            elif action == 'SELL' and symbol in self.account.positions:
                quantity = self.account.positions[symbol]['quantity']
                success = self.account.execute_trade(symbol, action, price, quantity)
                if success:
                    trades_executed += 1
            
            if success:
                # Send trade notification with timestamp
                trade_msg = f"""
🔔 *New Paper Trade* 🔔
⏱️ *Time*: {datetime.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST
{action} {quantity} {symbol} @ ₹{price:,.2f}
Target Price: ₹{rec['target_price']:,.2f}
Expected Return: {rec['expected_return']:.2f}%
"""
                self.notifier.send_notification(self.telegram_chat_id, trade_msg)
                self.notifier.send_notification(self.telegram_channel, trade_msg)
    
    def check_exit_conditions(self):
        """Check if any positions need to be exited based on target prices or stop losses"""
        for symbol, position in list(self.account.positions.items()):
            # In a real scenario, we would fetch current market price
            # For paper trading, we'll simulate price movement
            current_price = position['price'] * (1 + random.uniform(-0.05, 0.05))
            
            # Calculate holding time
            holding_time = datetime.datetime.now(IST) - position['timestamp']
            
            # Exit logic - this is simplified
            if position['action'] == 'BUY':
                # For long positions
                profit_percent = (current_price - position['price']) / position['price'] * 100
                
                # Take profit at 5% or cut loss at -3% or hold for more than 7 days
                exit_triggered = False
                reason = ""
                
                if profit_percent >= 5:
                    exit_triggered = True
                    reason = "Target Reached"
                elif profit_percent <= -3:
                    exit_triggered = True
                    reason = "Stop Loss"
                elif holding_time.days >= 7:
                    exit_triggered = True
                    reason = "Time-based Exit"
                
                if exit_triggered:
                    action = "SELL"
                    self.account.execute_trade(symbol, action, current_price, position['quantity'])
                    
                    exit_msg = f"""
🔄 *Position Closed: {reason}* 🔄
⏱️ *Time*: {datetime.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST
{action} {position['quantity']} {symbol} @ ₹{current_price:,.2f}
Entry: ₹{position['price']:,.2f}
P/L: ₹{(current_price - position['price']) * position['quantity']:,.2f} ({profit_percent:.2f}%)
Holding Period: {holding_time.days}d {holding_time.seconds//3600}h
"""
                    self.notifier.send_notification(self.telegram_chat_id, exit_msg)
                    self.notifier.send_notification(self.telegram_channel, exit_msg)
    
    def generate_daily_analysis(self):
        """Generate a more detailed analysis of the day's trading activity"""
        today = datetime.datetime.now(IST).strftime('%Y-%m-%d')
        portfolio_value, unrealized_pl = self.account.get_portfolio_value()
        
        # Get today's trades
        today_trades = [t for t in self.account.trade_history if t['timestamp'].strftime('%Y-%m-%d') == today]
        buy_trades = [t for t in today_trades if t['action'] == 'BUY']
        sell_trades = [t for t in today_trades if t['action'] == 'SELL']
        
        # Calculate daily metrics
        today_realized_pl = sum(t.get('profit_loss', 0) for t in sell_trades)
        today_total_pl = today_realized_pl + unrealized_pl
        
        # Calculate portfolio metrics
        total_invested = sum(p['price'] * p['quantity'] for p in self.account.positions.values())
        
        if total_invested > 0:
            roi = unrealized_pl / total_invested * 100
        else:
            roi = 0
            
        # Get best and worst performers
        performance = []
        for symbol, pos in self.account.positions.items():
            current_price = pos['price'] * (1 + random.uniform(-0.02, 0.02))
            pl_percent = (current_price - pos['price']) / pos['price'] * 100
            performance.append((symbol, pl_percent))
            
        best_performers = sorted(performance, key=lambda x: x[1], reverse=True)[:3]
        worst_performers = sorted(performance, key=lambda x: x[1])[:3]
        
        analysis = f"""
📈 *Daily Trading Analysis* 📈
⏱️ *Date*: {datetime.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST

💰 *Today's Performance*:
- Realized P/L: ₹{today_realized_pl:,.2f}
- Unrealized P/L: ₹{unrealized_pl:,.2f}
- Total P/L: ₹{today_total_pl:,.2f}

🔄 *Trading Activity*:
- Buys: {len(buy_trades)} trades
- Sells: {len(sell_trades)} trades
- Total Volume: ₹{sum(t['value'] for t in today_trades):,.2f}

📊 *Portfolio Analysis*:
- Current Holdings: {len(self.account.positions)} stocks
- Cash: ₹{self.account.current_balance:,.2f}
- Invested: ₹{total_invested:,.2f}
- Portfolio Value: ₹{portfolio_value:,.2f}
- Portfolio ROI: {roi:.2f}%
"""

        if best_performers:
            analysis += "\n🥇 *Top Performers*:\n"
            for symbol, pl_percent in best_performers:
                analysis += f"- {symbol}: {pl_percent:.2f}%\n"
                
        if worst_performers:
            analysis += "\n🥉 *Worst Performers*:\n"
            for symbol, pl_percent in worst_performers:
                analysis += f"- {symbol}: {pl_percent:.2f}%\n"
        
        # Market timing analysis based on paper trades
        if today_trades:
            morning_trades = [t for t in today_trades if t['timestamp'].hour < 12]
            afternoon_trades = [t for t in today_trades if 12 <= t['timestamp'].hour < 15]
            closing_trades = [t for t in today_trades if t['timestamp'].hour >= 15]
            
            morning_pl = sum(t.get('profit_loss', 0) for t in morning_trades if 'profit_loss' in t)
            afternoon_pl = sum(t.get('profit_loss', 0) for t in afternoon_trades if 'profit_loss' in t)
            closing_pl = sum(t.get('profit_loss', 0) for t in closing_trades if 'profit_loss' in t)
            
            analysis += f"""
⏰ *Trading Session Analysis*:
- Morning (9:00-12:00): ₹{morning_pl:,.2f}
- Afternoon (12:00-15:00): ₹{afternoon_pl:,.2f}
- Closing (15:00-16:00): ₹{closing_pl:,.2f}
"""
            
        analysis += "\n🤖 Generated by Automated Trading Bot"
        return analysis
    
    def run_daily_cycle(self):
        """Run the daily trading cycle"""
        try:
            logger.info("Starting daily trading cycle")
            
            # Check if market is open (IST time)
            now = datetime.datetime.now(IST)
            is_weekday = now.weekday() < 5  # Monday to Friday
            is_market_hours = 9 <= now.hour < 16  # 9 AM to 4 PM IST
            
            if not (is_weekday and is_market_hours):
                logger.info(f"Market is closed. Current time: {now}. Skipping cycle.")
                return
                
            # Fetch and process recommendations
            recommendations = self.fetch_recommendations()
            logger.info(f"Fetched {len(recommendations)} new recommendations")
            
            if recommendations:
                # Execute trades based on recommendations immediately
                self.execute_paper_trades(recommendations)
            
            # Check exit conditions for existing positions
            self.check_exit_conditions()
            
            # Generate and send daily summary (only once per day)
            today = now.strftime('%Y-%m-%d')
            if self.last_daily_summary != today and now.hour >= 15:  # After 3 PM
                summary = self.account.generate_summary()
                analysis = self.generate_daily_analysis()
                
                self.notifier.send_notification(self.telegram_chat_id, summary)
                self.notifier.send_notification(self.telegram_channel, summary)
                
                self.notifier.send_notification(self.telegram_chat_id, analysis)
                self.notifier.send_notification(self.telegram_channel, analysis)
                
                self.last_daily_summary = today
                logger.info("Sent daily summary and analysis")
            
            logger.info("Completed daily trading cycle")
            
        except Exception as e:
            logger.error(f"Error in daily cycle: {e}")
            error_msg = f"⚠️ *Trading Bot Error* ⚠️\n{str(e)}"
            self.notifier.send_notification(self.telegram_chat_id, error_msg)
    
    def start(self, interval_minutes=15):  # Changed interval to 15 minutes for more frequent checks
        """Start the trading bot with specified check interval"""
        logger.info(f"Starting trading bot with {interval_minutes} minute interval")
        
        try:
            # Send startup notification
            startup_msg = f"""
🚀 *Trading Bot Started* 🚀
⏱️ *Time*: {datetime.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST
📊 *Configuration*:
- Check Interval: {interval_minutes} minutes
- Max Positions: {self.max_positions} stocks
- Initial Balance: ₹{self.account.initial_balance:,.2f}
- Trading Hours: 9:00 AM - 4:00 PM IST (Monday-Friday)
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
⏱️ *Time*: {datetime.datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')} IST
💰 *Final Results*:
- Initial Balance: ₹{self.account.initial_balance:,.2f}
- Final Portfolio Value: ₹{self.account.get_portfolio_value()[0]:,.2f}
- Total P/L: ₹{self.account.get_portfolio_value()[0] - self.account.initial_balance:,.2f}
- P/L %: {((self.account.get_portfolio_value()[0] - self.account.initial_balance) / self.account.initial_balance * 100):.2f}%
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
    
    # Set the check interval (in minutes) - more frequent to catch recommendations quickly
    check_interval = 15  # Check every 15 minutes
    
    # Start the bot
    bot.start(interval_minutes=check_interval)
