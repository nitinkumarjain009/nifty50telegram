import os
import time
import datetime
import pytz
import requests
import schedule
import pandas as pd
from bs4 import BeautifulSoup
from telegram import Bot
from telegram.error import TelegramError
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
import threading
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Flask app configuration
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///chartink.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Set up Telegram bot
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
bot = Bot(token=TELEGRAM_TOKEN) if TELEGRAM_TOKEN else None

# Database models
class ChartinkURL(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.String(500), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    active = db.Column(db.Boolean, default=True)

class ScanResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    url_id = db.Column(db.Integer, db.ForeignKey('chartink_url.id'), nullable=False)
    scan_time = db.Column(db.DateTime, nullable=False)
    data = db.Column(db.Text, nullable=False)  # JSON string

# Create templates directory
def create_templates():
    # Create templates directory if it doesn't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Create index.html template
    with open('templates/index.html', 'w') as f:
        f.write("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ChartInk Stock Scanner</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .table-responsive { overflow-x: auto; }
        .timestamp { font-size: 0.8rem; color: #666; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4">ChartInk Stock Scanner</h1>
        
        <div class="card mb-4">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h5 class="mb-0">Current Status</h5>
                <div class="timestamp">
                    Current time: {{ current_time }} IST
                    {% if is_market_hours %}
                        <span class="badge bg-success">Market Open</span>
                    {% else %}
                        <span class="badge bg-secondary">Market Closed</span>
                    {% endif %}
                </div>
            </div>
            <div class="card-body">
                <a href="{{ url_for('force_scan') }}" class="btn btn-primary">Force Scan Now</a>
                <p class="mt-2">
                    {% if is_market_hours %}
                        Next automatic scan in 10 minutes.
                    {% else %}
                        Next automatic scan at 9:15 AM on the next trading day.
                    {% endif %}
                </p>
                
                {% with messages = get_flashed_messages(with_categories=true) %}
                    {% if messages %}
                        {% for category, message in messages %}
                            <div class="alert alert-{{ category }} mt-3">{{ message }}</div>
                        {% endfor %}
                    {% endif %}
                {% endwith %}
            </div>
        </div>
        
        <div class="card mb-4">
            <div class="card-header">
                <h5 class="mb-0">Add ChartInk URL</h5>
            </div>
            <div class="card-body">
                <form action="{{ url_for('add_url') }}" method="post" class="row g-3">
                    <div class="col-md-6">
                        <label for="url" class="form-label">ChartInk URL</label>
                        <input type="url" class="form-control" id="url" name="url" required placeholder="https://chartink.com/screener/...">
                    </div>
                    <div class="col-md-4">
                        <label for="name" class="form-label">Friendly Name</label>
                        <input type="text" class="form-control" id="name" name="name" required placeholder="e.g., Above 20 EMA">
                    </div>
                    <div class="col-md-2 d-flex align-items-end">
                        <button type="submit" class="btn btn-success w-100">Add URL</button>
                    </div>
                </form>
            </div>
        </div>
        
        <div class="card mb-4">
            <div class="card-header">
                <h5 class="mb-0">Configured URLs</h5>
            </div>
            <div class="card-body">
                {% if urls %}
                    <div class="table-responsive">
                        <table class="table table-striped table-hover">
                            <thead>
                                <tr>
                                    <th>Name</th>
                                    <th>URL</th>
                                    <th>Status</th>
                                    <th>Actions</th>
                                </tr>
                            </thead>
                            <tbody>
                                {% for url in urls %}
                                    <tr>
                                        <td>{{ url.name }}</td>
                                        <td><a href="{{ url.url }}" target="_blank">{{ url.url }}</a></td>
                                        <td>
                                            {% if url.active %}
                                                <span class="badge bg-success">Active</span>
                                            {% else %}
                                                <span class="badge bg-danger">Inactive</span>
                                            {% endif %}
                                        </td>
                                        <td>
                                            <a href="{{ url_for('toggle_url', url_id=url.id) }}" class="btn btn-sm {{ 'btn-warning' if url.active else 'btn-success' }}">
                                                {{ 'Deactivate' if url.active else 'Activate' }}
                                            </a>
                                            <a href="{{ url_for('delete_url', url_id=url.id) }}" class="btn btn-sm btn-danger" onclick="return confirm('Are you sure you want to delete this URL?')">Delete</a>
                                        </td>
                                    </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                {% else %}
                    <p class="text-muted">No URLs configured yet. Add one above to get started.</p>
                {% endif %}
            </div>
        </div>
        
        {% for url in urls %}
            {% if url.id in scan_results %}
                <div class="card mb-4">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <h5 class="mb-0">{{ url.name }}</h5>
                        <span class="timestamp">Last scanned: {{ scan_results[url.id].time }} IST</span>
                    </div>
                    <div class="card-body">
                        {% if scan_results[url.id].data|length > 0 %}
                            <div class="table-responsive">
                                <table class="table table-sm table-striped table-hover">
                                    <thead>
                                        <tr>
                                            {% for key in scan_results[url.id].data[0].keys() %}
                                                <th>{{ key }}</th>
                                            {% endfor %}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for row in scan_results[url.id].data %}
                                            <tr>
                                                {% for key, value in row.items() %}
                                                    <td>{{ value }}</td>
                                                {% endfor %}
                                            </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        {% else %}
                            <p class="text-muted">No stocks matched the criteria in the last scan.</p>
                        {% endif %}
                    </div>
                </div>
            {% endif %}
        {% endfor %}
    </div>
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    <script>
        // Auto-refresh the page every 5 minutes
        setTimeout(function() {
            location.reload();
        }, 5 * 60 * 1000);
    </script>
</body>
</html>
        """)

# Helper functions
def is_market_hours():
    """Check if current time is during market hours (9:15 AM to 3:30 PM IST, Mon-Fri)"""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    
    # Check if it's a weekday (0 is Monday, 6 is Sunday)
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    
    # Check if it's between 9:15 AM and 3:30 PM
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return market_start <= now <= market_end

def send_telegram_message(message):
    """Send a message to Telegram"""
    if not bot or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram bot not configured. Message not sent.")
        return False
    
    try:
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='HTML')
        return True
    except TelegramError as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False

def parse_chartink_table(url):
    """Parse table data from ChartInk URL"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table', class_='table table-striped table-bordered table-hover')
        
        if not table:
            return None
        
        # Extract table headers
        headers = []
        for th in table.find('thead').find_all('th'):
            headers.append(th.text.strip())
        
        # Extract table rows
        rows = []
        for tr in table.find('tbody').find_all('tr'):
            row = []
            for td in tr.find_all('td'):
                row.append(td.text.strip())
            rows.append(row)
        
        # Create DataFrame
        df = pd.DataFrame(rows, columns=headers)
        return df
    except Exception as e:
        logger.error(f"Error parsing ChartInk URL {url}: {e}")
        return None

def scan_all_chartink_urls():
    """Scan all active ChartInk URLs and save results"""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    
    with app.app_context():
        active_urls = ChartinkURL.query.filter_by(active=True).all()
        
        if not active_urls:
            logger.info("No active ChartInk URLs to scan")
            return
        
        message_parts = [f"<b>ChartInk Scan Results - {now.strftime('%d-%b-%Y %H:%M:%S')}</b>\n"]
        
        for url_obj in active_urls:
            logger.info(f"Scanning URL: {url_obj.url}")
            df = parse_chartink_table(url_obj.url)
            
            if df is not None:
                # Save scan result to database
                scan_result = ScanResult(
                    url_id=url_obj.id,
                    scan_time=now,
                    data=df.to_json(orient='records')
                )
                db.session.add(scan_result)
                
                # Add to Telegram message
                message_parts.append(f"\n<b>{url_obj.name}</b>")
                if len(df) > 0:
                    stocks = ", ".join(df['nsecode'].tolist()[:10]) if 'nsecode' in df.columns else "Data available"
                    if len(df) > 10:
                        stocks += f" and {len(df) - 10} more"
                    message_parts.append(stocks)
                else:
                    message_parts.append("No stocks matched criteria")
            else:
                message_parts.append(f"\n<b>{url_obj.name}</b>: Error fetching data")
        
        db.session.commit()
        
        # Send Telegram message
        message = "\n".join(message_parts)
        send_telegram_message(message)
        
        logger.info("Scan completed")

def daily_analysis():
    """Perform daily analysis after market hours"""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    
    with app.app_context():
        active_urls = ChartinkURL.query.filter_by(active=True).all()
        
        if not active_urls:
            logger.info("No active ChartInk URLs for daily analysis")
            return
        
        message_parts = [f"<b>ChartInk Daily Analysis - {now.strftime('%d-%b-%Y')}</b>\n"]
        
        for url_obj in active_urls:
            # Get the latest scan result
            latest_scan = ScanResult.query.filter_by(url_id=url_obj.id).order_by(ScanResult.scan_time.desc()).first()
            
            if latest_scan:
                df = pd.read_json(latest_scan.data)
                
                message_parts.append(f"\n<b>{url_obj.name}</b>")
                if len(df) > 0:
                    stocks = ", ".join(df['nsecode'].tolist()[:15]) if 'nsecode' in df.columns else "Data available"
                    if len(df) > 15:
                        stocks += f" and {len(df) - 15} more"
                    message_parts.append(stocks)
                else:
                    message_parts.append("No stocks matched criteria")
            else:
                message_parts.append(f"\n<b>{url_obj.name}</b>: No data available")
        
        message_parts.append("\nNext scan will be performed at 9:15 AM on the next trading day.")
        
        # Send Telegram message
        message = "\n".join(message_parts)
        send_telegram_message(message)
        
        logger.info("Daily analysis completed")

def schedule_next_run():
    """Schedule the next run based on market hours"""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    
    if is_market_hours():
        # During market hours, scan every 10 minutes
        logger.info("Scheduling next scan in 10 minutes (during market hours)")
        threading.Timer(600, run_scheduled_task).start()  # 10 minutes = 600 seconds
    else:
        # Outside market hours, schedule for 9:15 AM next trading day
        next_run = now.replace(hour=9, minute=15, second=0, microsecond=0)
        
        # If it's already past 9:15 AM, schedule for tomorrow
        if now > next_run:
            next_run = next_run + datetime.timedelta(days=1)
        
        # Skip weekends
        while next_run.weekday() >= 5:  # Saturday or Sunday
            next_run = next_run + datetime.timedelta(days=1)
        
        seconds_until_next_run = (next_run - now).total_seconds()
        logger.info(f"Scheduling next scan at {next_run.strftime('%d-%b-%Y %H:%M:%S')} ({seconds_until_next_run} seconds from now)")
        threading.Timer(seconds_until_next_run, run_scheduled_task).start()

def run_scheduled_task():
    """Run the appropriate task based on market hours"""
    if is_market_hours():
        logger.info("Running scheduled scan during market hours")
        scan_all_chartink_urls()
    else:
        logger.info("Running daily analysis outside market hours")
        daily_analysis()
    
    # Schedule the next run
    schedule_next_run()

# Flask routes
@app.route('/')
def index():
    with app.app_context():
        urls = ChartinkURL.query.all()
        
        # Get latest scan results for each URL
        scan_results = {}
        for url in urls:
            latest_scan = ScanResult.query.filter_by(url_id=url.id).order_by(ScanResult.scan_time.desc()).first()
            if latest_scan:
                data = json.loads(latest_scan.data)
                scan_results[url.id] = {
                    'time': latest_scan.scan_time.strftime('%d-%b-%Y %H:%M:%S'),
                    'data': data
                }
        
        return render_template('index.html', urls=urls, scan_results=scan_results, 
                               is_market_hours=is_market_hours(),
                               current_time=datetime.datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%d-%b-%Y %H:%M:%S'))

@app.route('/add_url', methods=['POST'])
def add_url():
    if request.method == 'POST':
        url = request.form.get('url')
        name = request.form.get('name')
        
        if not url or not name:
            flash('URL and name are required', 'danger')
            return redirect(url_for('index'))
        
        # Validate URL
        if not url.startswith('https://chartink.com/screener/'):
            flash('Invalid ChartInk URL', 'danger')
            return redirect(url_for('index'))
        
        # Check if URL already exists
        existing_url = ChartinkURL.query.filter_by(url=url).first()
        if existing_url:
            flash('URL already exists', 'warning')
            return redirect(url_for('index'))
        
        # Add URL to database
        new_url = ChartinkURL(url=url, name=name)
        db.session.add(new_url)
        db.session.commit()
        
        flash('URL added successfully', 'success')
        return redirect(url_for('index'))

@app.route('/toggle_url/<int:url_id>')
def toggle_url(url_id):
    url_obj = ChartinkURL.query.get_or_404(url_id)
    url_obj.active = not url_obj.active
    db.session.commit()
    
    status = 'activated' if url_obj.active else 'deactivated'
    flash(f'URL {status} successfully', 'success')
    return redirect(url_for('index'))

@app.route('/delete_url/<int:url_id>')
def delete_url(url_id):
    url_obj = ChartinkURL.query.get_or_404(url_id)
    
    # Delete associated scan results
    ScanResult.query.filter_by(url_id=url_id).delete()
    
    # Delete URL
    db.session.delete(url_obj)
    db.session.commit()
    
    flash('URL deleted successfully', 'success')
    return redirect(url_for('index'))

@app.route('/force_scan')
def force_scan():
    thread = threading.Thread(target=scan_all_chartink_urls)
    thread.start()
    
    flash('Scan initiated', 'info')
    return redirect(url_for('index'))

# Main function
def main():
    # Create database tables
    with app.app_context():
        db.create_all()
    
    # Create template files
    create_templates()
    
    # Start the scheduler thread
    scheduler_thread = threading.Thread(target=run_scheduled_task)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    # Start the Flask app
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    main()
