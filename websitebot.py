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
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
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
    
class DailyAnalysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    analysis_date = db.Column(db.Date, nullable=False)
    data = db.Column(db.Text, nullable=False)  # JSON string of aggregated data
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.datetime.utcnow)

# Create templates directory
def create_templates():
    # Create templates directory if it doesn't exist
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Create base template
    with open('templates/base.html', 'w') as f:
        f.write("""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}ChartInk Stock Scanner{% endblock %}</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; padding-bottom: 30px; }
        .table-responsive { overflow-x: auto; }
        .timestamp { font-size: 0.8rem; color: #666; }
        .navbar { margin-bottom: 20px; }
        .navbar-brand { font-weight: bold; }
        .stock-up { color: #28a745; }
        .stock-down { color: #dc3545; }
        .card { margin-bottom: 20px; }
        .data-table th { position: sticky; top: 0; background-color: #f8f9fa; }
        .scan-time { font-style: italic; color: #6c757d; }
        #loader { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background-color: rgba(255,255,255,0.7); z-index: 9999; }
        #loader .spinner-border { position: absolute; top: 50%; left: 50%; margin-top: -1rem; margin-left: -1rem; }
        .footer { margin-top: 30px; padding: 15px 0; border-top: 1px solid #e9ecef; }
        @media (max-width: 767px) {
            .card-body { padding: 0.5rem; }
            .table td, .table th { padding: 0.3rem; }
        }
    </style>
    {% block extra_css %}{% endblock %}
</head>
<body>
    <div id="loader">
        <div class="spinner-border text-primary" role="status">
            <span class="visually-hidden">Loading...</span>
        </div>
    </div>

    <nav class="navbar navbar-expand-lg navbar-dark bg-primary">
        <div class="container">
            <a class="navbar-brand" href="{{ url_for('index') }}">ChartInk Stock Scanner</a>
            <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav" aria-controls="navbarNav" aria-expanded="false" aria-label="Toggle navigation">
                <span class="navbar-toggler-icon"></span>
            </button>
            <div class="collapse navbar-collapse" id="navbarNav">
                <ul class="navbar-nav">
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('index') }}">Home</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('daily_report') }}">Daily Reports</a>
                    </li>
                    <li class="nav-item">
                        <a class="nav-link" href="{{ url_for('manage_urls') }}">Manage URLs</a>
                    </li>
                </ul>
                <ul class="navbar-nav ms-auto">
                    <li class="nav-item">
                        <a class="btn btn-light" href="{{ url_for('force_scan') }}">
                            <i class="fas fa-sync-alt"></i> Force Scan Now
                        </a>
                    </li>
                </ul>
            </div>
        </div>
    </nav>

    <div class="container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="alert alert-{{ category }} alert-dismissible fade show" role="alert">
                        {{ message }}
                        <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
        
        {% block content %}{% endblock %}
        
        <div class="footer text-center text-muted">
            <div class="timestamp">
                Current time: {{ current_time }} IST
                {% if is_market_hours %}
                    <span class="badge bg-success">Market Open</span>
                {% else %}
                    <span class="badge bg-secondary">Market Closed</span>
                {% endif %}
            </div>
            <p class="mt-2">
                {% if is_market_hours %}
                    Next automatic scan in 10 minutes
                {% else %}
                    Next automatic scan at 9:15 AM on the next trading day
                {% endif %}
            </p>
        </div>
    </div>
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.1.3/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js"></script>
    <script>
        // Show loader during page transitions
        $(document).ready(function() {
            $(document).on('click', 'a', function() {
                // Don't show loader for external links or tabs
                if (this.hostname === window.location.hostname && !$(this).attr('target')) {
                    $('#loader').show();
                }
            });
            
            // Auto-refresh the page every 5 minutes
            setTimeout(function() {
                $('#loader').show();
                location.reload();
            }, 5 * 60 * 1000);
        });
    </script>
    {% block extra_js %}{% endblock %}
</body>
</html>
        """)
    
    # Create index.html template
    with open('templates/index.html', 'w') as f:
        f.write("""
{% extends "base.html" %}

{% block title %}ChartInk Stock Scanner - Dashboard{% endblock %}

{% block extra_css %}
<style>
    .status-card { border-radius: 10px; }
    .status-card .icon { font-size: 2rem; color: #fff; }
    .stock-count { font-size: 1.5rem; font-weight: bold; }
    .screener-tag { display: inline-block; margin-right: 5px; margin-bottom: 5px; }
</style>
{% endblock %}

{% block content %}
<div class="row">
    <div class="col-md-12">
        <h2 class="mb-4">Dashboard</h2>
    </div>
</div>

<div class="row mb-4">
    <div class="col-md-3">
        <div class="card text-white bg-primary status-card h-100">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="card-title">Total Screeners</h6>
                        <div class="stock-count">{{ urls|length }}</div>
                    </div>
                    <div class="icon">
                        <i class="fas fa-search"></i>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-white bg-success status-card h-100">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="card-title">Active Screeners</h6>
                        <div class="stock-count">{{ active_count }}</div>
                    </div>
                    <div class="icon">
                        <i class="fas fa-check-circle"></i>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-white bg-info status-card h-100">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="card-title">Total Stocks</h6>
                        <div class="stock-count">{{ total_stocks }}</div>
                    </div>
                    <div class="icon">
                        <i class="fas fa-chart-line"></i>
                    </div>
                </div>
            </div>
        </div>
    </div>
    <div class="col-md-3">
        <div class="card text-white bg-warning status-card h-100">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <h6 class="card-title">Last Scan</h6>
                        <div class="scan-time">{{ last_scan_time }}</div>
                    </div>
                    <div class="icon">
                        <i class="fas fa-clock"></i>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

{% if common_stocks %}
<div class="card mb-4">
    <div class="card-header bg-success text-white">
        <h5 class="mb-0">Stocks Appearing in Multiple Screeners</h5>
    </div>
    <div class="card-body">
        <div class="row">
            {% for stock, details in common_stocks.items() %}
            <div class="col-md-4 mb-3">
                <div class="card">
                    <div class="card-body">
                        <h5 class="card-title">{{ stock }}</h5>
                        <p class="card-text">Appears in {{ details.count }} screeners</p>
                        <div>
                            {% for screener in details.screeners %}
                            <span class="badge bg-primary screener-tag">{{ screener }}</span>
                            {% endfor %}
                        </div>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
</div>
{% endif %}

{% for url in urls %}
    {% if url.id in scan_results %}
        <div class="card mb-4">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h5 class="mb-0">{{ url.name }}</h5>
                <div>
                    {% if url.active %}
                        <span class="badge bg-success">Active</span>
                    {% else %}
                        <span class="badge bg-danger">Inactive</span>
                    {% endif %}
                    <span class="timestamp ms-2">Last scanned: {{ scan_results[url.id].time }} IST</span>
                </div>
            </div>
            <div class="card-body">
                {% if scan_results[url.id].data|length > 0 %}
                    <div class="table-responsive" style="max-height: 400px;">
                        <table class="table table-sm table-striped table-hover data-table">
                            <thead class="sticky-top">
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
                                            <td>
                                                {% if key == "nsecode" %}
                                                    <a href="https://www.tradingview.com/chart/?symbol=NSE:{{ value }}" target="_blank">{{ value }}</a>
                                                {% elif key == "change" or key == "pctchange" %}
                                                    {% if value|float > 0 %}
                                                        <span class="stock-up">+{{ value }}</span>
                                                    {% elif value|float < 0 %}
                                                        <span class="stock-down">{{ value }}</span>
                                                    {% else %}
                                                        {{ value }}
                                                    {% endif %}
                                                {% else %}
                                                    {{ value }}
                                                {% endif %}
                                            </td>
                                        {% endfor %}
                                    </tr>
                                {% endfor %}
                            </tbody>
                        </table>
                    </div>
                {% else %}
                    <p class="text-muted">No stocks matched the criteria in the last scan.</p>
                {% endif %}
                <div class="text-end mt-2">
                    <a href="{{ url.url }}" target="_blank" class="btn btn-sm btn-outline-primary">
                        <i class="fas fa-external-link-alt"></i> View on ChartInk
                    </a>
                </div>
            </div>
        </div>
    {% endif %}
{% endfor %}
{% endblock %}
        """)
    
    # Create manage_urls.html template
    with open('templates/manage_urls.html', 'w') as f:
        f.write("""
{% extends "base.html" %}

{% block title %}Manage ChartInk URLs{% endblock %}

{% block content %}
<div class="row">
    <div class="col-md-12">
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
        
        <div class="card">
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
    </div>
</div>
{% endblock %}
        """)
    
    # Create daily_report.html template
    with open('templates/daily_report.html', 'w') as f:
        f.write("""
{% extends "base.html" %}

{% block title %}Daily Reports{% endblock %}

{% block extra_css %}
<style>
    .date-selector { margin-bottom: 20px; }
    .report-date { font-weight: bold; }
</style>
{% endblock %}

{% block content %}
<div class="row">
    <div class="col-md-12">
        <h2 class="mb-4">Daily Analysis Reports</h2>
        
        <div class="date-selector">
            <form method="get" class="row g-3">
                <div class="col-md-4">
                    <label for="report_date" class="form-label">Select Date</label>
                    <input type="date" class="form-control" id="report_date" name="date" 
                           value="{{ selected_date }}" max="{{ today }}">
                </div>
                <div class="col-md-2 d-flex align-items-end">
                    <button type="submit" class="btn btn-primary">View Report</button>
                </div>
            </form>
        </div>
        
        {% if report_data %}
            <div class="card mb-4">
                <div class="card-header bg-primary text-white">
                    <h5 class="mb-0">Daily Report for <span class="report-date">{{ formatted_date }}</span></h5>
                </div>
                <div class="card-body">
                    {% for screener_name, stocks in report_data.items() %}
                        <h5 class="mt-3 mb-2">{{ screener_name }}</h5>
                        {% if stocks|length > 0 %}
                            <div class="table-responsive">
                                <table class="table table-sm table-striped table-hover">
                                    <thead>
                                        <tr>
                                            {% for key in stocks[0].keys() %}
                                                <th>{{ key }}</th>
                                            {% endfor %}
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for stock in stocks %}
                                            <tr>
                                                {% for key, value in stock.items() %}
                                                    <td>
                                                        {% if key == "nsecode" %}
                                                            <a href="https://www.tradingview.com/chart/?symbol=NSE:{{ value }}" target="_blank">{{ value }}</a>
                                                        {% elif key == "change" or key == "pctchange" %}
                                                            {% if value|float > 0 %}
                                                                <span class="stock-up">+{{ value }}</span>
                                                            {% elif value|float < 0 %}
                                                                <span class="stock-down">{{ value }}</span>
                                                            {% else %}
                                                                {{ value }}
                                                            {% endif %}
                                                        {% else %}
                                                            {{ value }}
                                                        {% endif %}
                                                    </td>
                                                {% endfor %}
                                            </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        {% else %}
                            <p class="text-muted">No stocks matched the criteria for this screener.</p>
                        {% endif %}
                        <hr>
                    {% endfor %}
                </div>
            </div>
        {% else %}
            <div class="alert alert-info">
                No report data available for {{ formatted_date }}. Reports are generated after market hours each trading day.
            </div>
        {% endif %}
        
        <div class="card">
            <div class="card-header">
                <h5 class="mb-0">Available Reports</h5>
            </div>
            <div class="card-body">
                {% if available_dates %}
                    <div class="row">
                        {% for date_obj in available_dates %}
                            <div class="col-md-3 mb-2">
                                <a href="{{ url_for('daily_report', date=date_obj.strftime('%Y-%m-%d')) }}" class="btn btn-outline-primary w-100">
                                    {{ date_obj.strftime('%d %b %Y') }}
                                </a>
                            </div>
                        {% endfor %}
                    </div>
                {% else %}
                    <p class="text-muted">No reports available yet. Reports are generated after market hours each trading day.</p>
                {% endif %}
            </div>
        </div>
    </div>
</div>
{% endblock %}
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
        bot.send_message(chat_id='@Stockniftybot', text=message, parse_mode='HTML')
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
    """Perform daily analysis after market hours and store the daily report"""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    today = now.date()
    
    with app.app_context():
        active_urls = ChartinkURL.query.filter_by(active=True).all()
        
        if not active_urls:
            logger.info("No active ChartInk URLs for daily analysis")
            return
        
        message_parts = [f"<b>ChartInk Daily Analysis - {now.strftime('%d-%b-%Y')}</b>\n"]
        
        # Create daily report data
        daily_report = {}
        
        for url_obj in active_urls:
            # Get the latest scan result
            latest_scan = ScanResult.query.filter_by(url_id=url_obj.id).order_by(ScanResult.scan_time.desc()).first()
            
            if latest_scan:
                df = pd.read_json(latest_scan.data)
                
                # Add to daily report
                daily_report[url_obj.name] = json.loads(df.to_json(orient='records'))
                
                # Add to Telegram message
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
                daily_report[url_obj.name] = []
        
        # Save daily report to database
        daily_analysis_entry = DailyAnalysis(
            analysis_date=today,
            data=json.dumps(daily_report)
        )
        db.session.add(daily_analysis_entry)
        db.session.commit()
        
        message_parts.append("\nNext scan will be performed at 9:15 AM on the next trading day.")
        
       # Send Telegram message
message = "\n".join(message_parts)
send_telegram_message(message)

logger.info("Daily analysis completed successfully")

# Flask routes
@app.route('/')
def index():
    """Home page route"""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    
    urls = ChartinkURL.query.all()
    active_count = ChartinkURL.query.filter_by(active=True).count()
    
    # Get latest scan results
    scan_results = {}
    total_stocks = 0
    last_scan_time = "Not available"
    
    for url in urls:
        latest_scan = ScanResult.query.filter_by(url_id=url.id).order_by(ScanResult.scan_time.desc()).first()
        if latest_scan:
            data = json.loads(latest_scan.data)
            total_stocks += len(data)
            scan_time = latest_scan.scan_time.astimezone(ist).strftime('%d-%b-%Y %H:%M:%S')
            scan_results[url.id] = {'data': data, 'time': scan_time}
            
            # Update last scan time
            if last_scan_time == "Not available" or latest_scan.scan_time > datetime.datetime.strptime(last_scan_time, '%d-%b-%Y %H:%M:%S'):
                last_scan_time = scan_time
    
    # Find common stocks across screeners
    common_stocks = {}
    stock_appearances = {}
    
    for url_id, result in scan_results.items():
        url = next((u for u in urls if u.id == url_id), None)
        if url and url.active:
            for stock_data in result['data']:
                if 'nsecode' in stock_data:
                    stock_name = stock_data['nsecode']
                    if stock_name not in stock_appearances:
                        stock_appearances[stock_name] = {'count': 0, 'screeners': []}
                    
                    stock_appearances[stock_name]['count'] += 1
                    stock_appearances[stock_name]['screeners'].append(url.name)
    
    # Filter for stocks appearing in multiple screeners
    for stock, details in stock_appearances.items():
        if details['count'] > 1:
            common_stocks[stock] = details
    
    # Sort common stocks by appearance count (descending)
    common_stocks = dict(sorted(common_stocks.items(), key=lambda x: x[1]['count'], reverse=True))
    
    return render_template(
        'index.html', 
        urls=urls, 
        scan_results=scan_results,
        active_count=active_count,
        total_stocks=total_stocks,
        last_scan_time=last_scan_time,
        common_stocks=common_stocks,
        current_time=now.strftime('%d-%b-%Y %H:%M:%S'),
        is_market_hours=is_market_hours()
    )

@app.route('/manage_urls')
def manage_urls():
    """Manage URLs page route"""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    
    urls = ChartinkURL.query.all()
    
    return render_template(
        'manage_urls.html', 
        urls=urls,
        current_time=now.strftime('%d-%b-%Y %H:%M:%S'),
        is_market_hours=is_market_hours()
    )

@app.route('/add_url', methods=['POST'])
def add_url():
    """Add a new ChartInk URL"""
    url = request.form.get('url', '').strip()
    name = request.form.get('name', '').strip()
    
    if not url or not name:
        flash('URL and name are required', 'danger')
        return redirect(url_for('manage_urls'))
    
    # Check if URL is already in the database
    existing_url = ChartinkURL.query.filter_by(url=url).first()
    if existing_url:
        flash('This URL already exists in the database', 'warning')
        return redirect(url_for('manage_urls'))
    
    # Test if the URL is valid
    try:
        df = parse_chartink_table(url)
        if df is None:
            flash('Could not parse data from this URL. Please check if it is a valid ChartInk screener URL', 'danger')
            return redirect(url_for('manage_urls'))
    except Exception as e:
        flash(f'Error accessing URL: {str(e)}', 'danger')
        return redirect(url_for('manage_urls'))
    
    # Add URL to database
    new_url = ChartinkURL(url=url, name=name, active=True)
    db.session.add(new_url)
    db.session.commit()
    
    flash(f'Added ChartInk URL: {name}', 'success')
    return redirect(url_for('manage_urls'))

@app.route('/toggle_url/<int:url_id>')
def toggle_url(url_id):
    """Toggle URL active status"""
    url = ChartinkURL.query.get_or_404(url_id)
    url.active = not url.active
    db.session.commit()
    
    flash(f'{"Activated" if url.active else "Deactivated"} URL: {url.name}', 'success')
    return redirect(url_for('manage_urls'))

@app.route('/delete_url/<int:url_id>')
def delete_url(url_id):
    """Delete a URL"""
    url = ChartinkURL.query.get_or_404(url_id)
    
    # Delete associated scan results first
    ScanResult.query.filter_by(url_id=url_id).delete()
    
    # Delete URL
    db.session.delete(url)
    db.session.commit()
    
    flash(f'Deleted URL: {url.name}', 'success')
    return redirect(url_for('manage_urls'))

@app.route('/force_scan')
def force_scan():
    """Force a scan of all active URLs"""
    try:
        scan_all_chartink_urls()
        flash('Forced scan completed successfully', 'success')
    except Exception as e:
        flash(f'Error during forced scan: {str(e)}', 'danger')
    
    return redirect(url_for('index'))

@app.route('/daily_report')
def daily_report():
    """Show daily report page"""
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    today = now.date().strftime('%Y-%m-%d')
    
    # Get selected date (default to today)
    selected_date = request.args.get('date', today)
    
    try:
        date_obj = datetime.datetime.strptime(selected_date, '%Y-%m-%d').date()
        formatted_date = date_obj.strftime('%d %b %Y')
    except ValueError:
        flash('Invalid date format', 'danger')
        return redirect(url_for('daily_report'))
    
    # Get daily report for selected date
    report = DailyAnalysis.query.filter_by(analysis_date=date_obj).first()
    report_data = json.loads(report.data) if report else None
    
    # Get all available dates
    available_reports = DailyAnalysis.query.order_by(DailyAnalysis.analysis_date.desc()).all()
    available_dates = [report.analysis_date for report in available_reports]
    
    return render_template(
        'daily_report.html',
        report_data=report_data,
        selected_date=selected_date,
        formatted_date=formatted_date,
        today=today,
        available_dates=available_dates,
        current_time=now.strftime('%d-%b-%Y %H:%M:%S'),
        is_market_hours=is_market_hours()
    )

@app.route('/api/scan_results')
def api_scan_results():
    """API endpoint to get latest scan results"""
    try:
        urls = ChartinkURL.query.filter_by(active=True).all()
        results = {}
        
        for url in urls:
            latest_scan = ScanResult.query.filter_by(url_id=url.id).order_by(ScanResult.scan_time.desc()).first()
            if latest_scan:
                results[url.name] = {
                    'scan_time': latest_scan.scan_time.isoformat(),
                    'data': json.loads(latest_scan.data)
                }
        
        return jsonify({
            'status': 'success',
            'results': results
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# Scheduler functions
def schedule_scans():
    """Set up scheduler for scans"""
    # Scan during market hours (9:15 AM to 3:30 PM, every 10 minutes)
    schedule.every().monday.at("09:15").do(scan_all_chartink_urls)
    schedule.every().tuesday.at("09:15").do(scan_all_chartink_urls)
    schedule.every().wednesday.at("09:15").do(scan_all_chartink_urls)
    schedule.every().thursday.at("09:15").do(scan_all_chartink_urls)
    schedule.every().friday.at("09:15").do(scan_all_chartink_urls)
    
    schedule.every(10).minutes.do(lambda: scan_all_chartink_urls() if is_market_hours() else None)
    
    # Daily analysis after market hours
    schedule.every().monday.at("15:40").do(daily_analysis)
    schedule.every().tuesday.at("15:40").do(daily_analysis)
    schedule.every().wednesday.at("15:40").do(daily_analysis)
    schedule.every().thursday.at("15:40").do(daily_analysis)
    schedule.every().friday.at("15:40").do(daily_analysis)
    
    # Create a separate thread for the scheduler
    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(1)
    
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()

def init_db():
    """Initialize database and create tables"""
    with app.app_context():
        db.create_all()
        logger.info("Database initialized")

# Main function
def main():
    """Main function to start the application"""
    # Create templates if they don't exist
    create_templates()
    
    # Initialize database
    init_db()
    
    # Set up scheduler
    schedule_scans()
    
    # Run initial scan if during market hours
    if is_market_hours():
        with app.app_context():
            scan_all_chartink_urls()
    
    # Start Flask server
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting ChartInk Stock Scanner on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)

if __name__ == "__main__":
    main()
                    
