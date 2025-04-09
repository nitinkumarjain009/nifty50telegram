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
import re
import traceback

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
    .raw-html-container { overflow-x: auto; max-height: 500px; }
    .status-message { padding: 20px; background-color: #f8f9fa; border-radius: 5px; }
    .error-message { color: #721c24; background-color: #f8d7da; border-color: #f5c6cb; padding: 15px; border-radius: 5px; }
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
                    {% set first_item = scan_results[url.id].data[0] %}
                    
                    {# Check if we have a special status message #}
                    {% if 'status' in first_item %}
                        <div class="status-message">
                            {{ first_item.status }}
                        </div>
                    
                    {# Check if we have an error message #}
                    {% elif 'error' in first_item %}
                        <div class="error-message">
                            {{ first_item.error }}
                        </div>
                    
                    {# Check if we have raw HTML to display #}
                    {% elif 'raw_html' in first_item %}
                        <div class="raw-html-container">
                            {{ first_item.raw_html|safe }}
                        </div>
                    
                    {# Standard data table display #}
                    {% else %}
                        <div class="table-responsive" style="max-height: 400px;">
                            <table class="table table-sm table-striped table-hover data-table">
                                <thead class="sticky-top">
                                    <tr>
                                        {% for key in first_item.keys() %}
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
                    {% endif %}
                {% else %}
                    <p class="text-muted">No stocks matched the criteria in the last scan. The scan executed successfully but returned no results.</p>
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
        bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode='HTML')
        logger.info(f"Sent Telegram message successfully: {message[:50]}...")
        return True
    except TelegramError as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False

def parse_chartink_table(url):
    """Parse table data from ChartInk URL with improved handling"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-US,en;q=0.9',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': url
        }
        
        logger.info(f"Fetching data from ChartInk URL: {url}")
        
        # Extract the scan code from the URL
        scan_code = url.split('/')[-1]
        
        # First attempt: Try API approach to get JSON data (most reliable)
        api_url = "https://chartink.com/screener/process"
        
        # Get the page first to extract scan_clause
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Try to extract scan_clause from the page
        script_tags = soup.find_all('script')
        scan_clause = None
        
        for script in script_tags:
            if script.string and 'scan_clause' in script.string:
                # Extract the scan clause using regex
                match = re.search(r'scan_clause\s*:\s*[\'"](.+?)[\'"]', script.string)
                if match:
                    scan_clause = match.group(1)
                    break
        
        if scan_clause:
            payload = {
                'scan_clause': scan_clause,
                'screener_id': scan_code
            }
            
            # Make API request
            api_response = requests.post(api_url, data=payload, headers=headers)
            api_response.raise_for_status()
            
            # Parse JSON response
            data = api_response.json()
            
            if 'data' in data:
                stock_data = data['data']
                logger.info(f"Successfully retrieved data from API: {len(stock_data)} records")
                # Convert to DataFrame even if empty (0 rows)
                df = pd.DataFrame(stock_data)
                return df
        
        # Second attempt: Try embedded JSON data
        logger.info("API approach didn't yield results, trying embedded JSON...")
        try:
            # Look for data in the page's JSON script tags
            for script in soup.find_all('script'):
                if script.string and 'var scanData' in script.string:
                    # Extract JSON data using regex
                    match = re.search(r'var\s+scanData\s*=\s*(\[.+?\]);', script.string, re.DOTALL)
                    if match:
                        json_data = match.group(1)
                        # Parse the JSON
                        stock_data = json.loads(json_data)
                        logger.info(f"Successfully retrieved data from embedded JSON: {len(stock_data)} records")
                        df = pd.DataFrame(stock_data)
                        return df
        except Exception as json_error:
            logger.error(f"Error parsing embedded JSON: {json_error}")

        # Third attempt: Direct HTML parsing
        logger.info("Trying HTML table parsing...")
        
        # Look for the table - ChartInk might use different table structures
        table = soup.find('table', class_='table table-striped table-bordered table-hover')
        
        if not table:
            # Try to find any table with class containing 'table'
            table = soup.find('table', class_=lambda c: c and 'table' in c)
        
        if table:
            logger.info("Found HTML table, parsing data...")
            # Extract table headers
            headers = []
            thead = table.find('thead')
            if thead:
                for th in thead.find_all('th'):
                    headers.append(th.text.strip())
            else:
                # If no thead, try to get headers from first row
                first_row = table.find('tr')
                if first_row:
                    for th in first_row.find_all(['th', 'td']):
                        headers.append(th.text.strip())
            
            # Extract table rows
            rows = []
            tbody = table.find('tbody')
            if tbody:
                for tr in tbody.find_all('tr'):
                    row = []
                    for td in tr.find_all('td'):
                        row.append(td.text.strip())
                    if row:  # Only add non-empty rows
                        rows.append(row)
            else:
                # If no tbody, get rows directly (skip header row if headers were found)
                skip_first = len(headers) > 0
                for tr in table.find_all('tr')[1:] if skip_first else table.find_all('tr'):
                    row = []
                    for td in tr.find_all('td'):
                        row.append(td.text.strip())
                    if row:  # Only add non-empty rows
                        rows.append(row)
            
            # Create DataFrame
            if headers and rows:
                if len(headers) == len(rows[0]):
                    df = pd.DataFrame(rows, columns=headers)
                    logger.info(f"Successfully parsed {len(df)} rows from HTML table")
                    return df
                else:
                    logger.warning(f"Headers count ({len(headers)}) doesn't match row column count ({len(rows[0])})")
        
        # Fourth attempt: Try to extract raw HTML content of the table
        logger.info("Trying to extract raw table HTML...")
        
        # If we can't parse it properly, return the raw HTML in a specially formatted DataFrame
        if table:
            raw_html = str(table)
            # Create a special DataFrame with HTML content
            df = pd.DataFrame([{"raw_html": raw_html}])
            logger.info("Returning raw HTML table content")
            return df
        
        # If we reach here, all methods failed but don't return empty DataFrame
        # Instead, return a DataFrame with a status message for display
        logger.warning(f"Could not parse data from {url} using standard methods")
        return pd.DataFrame([{"status": "No parsable data found. View directly on ChartInk."}])
        
    except Exception as e:
        logger.error(f"Error fetching or parsing ChartInk data: {e}")
        logger.error(traceback.format_exc())
        # Return DataFrame with error message instead of empty DataFrame
        return pd.DataFrame([{"error": f"Error: {str(e)}"}])

def scan_chartink_urls():
    """Scan all active ChartInk URLs and store results"""
    logger.info("Starting ChartInk scan...")
    
    # Get all active URLs
    active_urls = ChartinkURL.query.filter_by(active=True).all()
    
    if not active_urls:
        logger.info("No active URLs to scan")
        return
    
    scan_count = 0
    total_stocks = 0
    message_parts = ["📊 <b>ChartInk Scan Results</b>\n"]
    ist = pytz.timezone('Asia/Kolkata')
    now = datetime.datetime.now(ist)
    
    for url in active_urls:
        try:
            logger.info(f"Scanning URL: {url.name} ({url.url})")
            df = parse_chartink_table(url.url)
            
            # Check if we got a special error or status message DataFrame
            if "error" in df.columns or "status" in df.columns or "raw_html" in df.columns:
                logger.warning(f"Special case for {url.name}: {df.iloc[0].to_dict()}")
                
                # Store the result as is
                records = df.to_dict('records')
                result = ScanResult(
                    url_id=url.id,
                    scan_time=now,
                    data=json.dumps(records)
                )
                db.session.add(result)
                
                # Add to message
                if "error" in df.columns:
                    message_parts.append(f"❌ <b>{url.name}</b>: {df.iloc[0]['error']}")
                elif "status" in df.columns:
                    message_parts.append(f"⚠️ <b>{url.name}</b>: {df.iloc[0]['status']}")
                elif "raw_html" in df.columns:
                    message_parts.append(f"ℹ️ <b>{url.name}</b>: Raw HTML content captured")
                
                continue
            
            # For truly empty DataFrame (no rows but has columns)
            if df.empty:
                logger.info(f"No stocks found for {url.name} but received valid structure")
                message_parts.append(f"ℹ️ <b>{url.name}</b>: No stocks matched the criteria")
                
                # Store empty result with column names intact
                result = ScanResult(
                    url_id=url.id,
                    scan_time=now,
                    data=json.dumps([])  # Empty list with valid structure
                )
                db.session.add(result)
                continue
            
            # Convert DataFrame to JSON-compatible format
            records = df.to_dict('records')
            
            # Clean up the data to ensure JSON serialization
            for record in records:
                for key, value in record.items():
                    if pd.isna(value):
                        record[key] = ""
                    elif isinstance(value, (float, int)):
                        # Format numbers to avoid floating point precision issues
                        record[key] = float(f"{value:.2f}") if isinstance(value, float) else value
            
            # Store in database
            result = ScanResult(
                url_id=url.id,
                scan_time=now,
                data=json.dumps(records)
            )
            db.session.add(result)
            
            # Prepare Telegram message
            stock_count = len(records)
            total_stocks += stock_count
            scan_count += 1
            
            message_parts.append(f"✅ <b>{url.name}</b>: {stock_count} stocks")
            
            # Add top 5 stocks to message
            if stock_count > 0:
                message_parts.append("<i>Top stocks:</i>")
                for i, record in enumerate(records[:5]):
                    stock_name = record.get('nsecode', record.get('name', 'Unknown'))
                    message_parts.append(f"  {i+1}. {stock_name}")
                
                if stock_count > 5:
                    message_parts.append(f"  ...and {stock_count - 5} more")
                
                message_parts.append("")  # Empty line for spacing
            
        except Exception as e:
            logger.error(f"Error scanning URL {url.name}: {e}")
            message_parts.append(f"❌ <b>{url.name}</b>: Error - {str(e)[:100]}")
    
    # Commit changes to database
    try:
        db.session.commit()
        logger.info("Scan results saved to database")
    except Exception as e:
        db.session.rollback()
        logger.error(f"Failed to save scan results: {e}")
    
    # Send Telegram notification with specific chat ID
    if scan_count > 0:
        message_parts.append(f"\n📈 <b>Total:</b> {total_stocks} stocks across {scan_count} scanners")
        message_parts.append(f"🕒 <b>Scan time:</b> {now.strftime('%d-%b-%Y %H:%M:%S')} IST")
        
        message = "\n".join(message_parts)
        send_telegram_message(message)

def send_telegram_message(message):
    """Send a message to Telegram"""
    # Use specific chat ID
    SPECIFIC_CHAT_ID = "711856868"
    
    if not TELEGRAM_TOKEN:
        logger.warning("Telegram token not configured. Message not sent.")
        return False
    
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=SPECIFIC_CHAT_ID, text=message, parse_mode='HTML')
        logger.info(f"Sent Telegram message successfully to chat ID {SPECIFIC_CHAT_ID}")
        return True
    except TelegramError as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False

def generate_daily_analysis():
    """Generate daily analysis from today's scan results"""
    logger.info("Generating daily analysis...")
    
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.datetime.now(ist).date()
    
    # Check if analysis already exists for today
    existing = DailyAnalysis.query.filter_by(analysis_date=today).first()
    if existing:
        logger.info(f"Daily analysis for {today} already exists. Updating...")
    
    # Get all active URLs
    urls = ChartinkURL.query.all()
    
    # Initialize report data
    report_data = {}
    
    for url in urls:
        # Get the latest scan result for this URL from today
        latest_scan = ScanResult.query.filter(
            ScanResult.url_id == url.id,
            ScanResult.scan_time >= datetime.datetime.combine(today, datetime.time.min),
            ScanResult.scan_time <= datetime.datetime.combine(today, datetime.time.max)
        ).order_by(ScanResult.scan_time.desc()).first()
        
        if latest_scan:
            report_data[url.name] = json.loads(latest_scan.data)
    
    # Store in database
    if report_data:
        if existing:
            existing.data = json.dumps(report_data)
            existing.created_at = datetime.datetime.utcnow()
        else:
            analysis = DailyAnalysis(
                analysis_date=today,
                data=json.dumps(report_data)
            )
            db.session.add(analysis)
        
        try:
            db.session.commit()
            logger.info(f"Daily analysis for {today} saved to database")
            
            # Send Telegram notification
            message = f"📊 <b>Daily Analysis Report Generated</b>\n\n"
            message += f"📅 <b>Date:</b> {today.strftime('%d-%b-%Y')}\n"
            message += f"🔍 <b>Screeners included:</b> {len(report_data)}\n\n"
            
            # Add summary of stocks found in each screener
            for name, stocks in report_data.items():
                message += f"• <b>{name}</b>: {len(stocks)} stocks\n"
            
            send_telegram_message(message)
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Failed to save daily analysis: {e}")
    else:
        logger.info("No scan data available for today to generate analysis")

def schedule_jobs():
    """Schedule recurring jobs"""
    # Clear existing jobs
    schedule.clear()
    
    # Schedule scans during market hours
    schedule.every(10).minutes.do(scan_chartink_urls)
    
    # Schedule daily analysis at 3:45 PM IST (after market close)
    ist = pytz.timezone('Asia/Kolkata')
    schedule.every().day.at("15:45").do(generate_daily_analysis)
    
    logger.info("Jobs scheduled")

def run_scheduler():
    """Run the scheduler in a separate thread"""
    while True:
        schedule.run_pending()
        time.sleep(1)

# Flask routes
@app.route('/')
def index():
    """Main dashboard page"""
    urls = ChartinkURL.query.all()
    active_count = ChartinkURL.query.filter_by(active=True).count()
    
    # Get the latest scan result for each URL
    scan_results = {}
    total_stocks = 0
    common_stocks = {}
    
    for url in urls:
        latest_scan = ScanResult.query.filter_by(url_id=url.id).order_by(ScanResult.scan_time.desc()).first()
        if latest_scan:
            data = json.loads(latest_scan.data)
            ist = pytz.timezone('Asia/Kolkata')
            scan_time = latest_scan.scan_time.replace(tzinfo=pytz.UTC).astimezone(ist)
            
            scan_results[url.id] = {
                'time': scan_time.strftime('%d-%b-%Y %H:%M:%S'),
                'data': data
            }
            
            # Count total stocks
            total_stocks += len(data)
            
            # Track common stocks across screeners
            for stock in data:
                stock_name = stock.get('nsecode', stock.get('name', 'Unknown'))
                if stock_name not in common_stocks:
                    common_stocks[stock_name] = {'count': 0, 'screeners': []}
                
                common_stocks[stock_name]['count'] += 1
                common_stocks[stock_name]['screeners'].append(url.name)
    
    # Filter for stocks in multiple screeners
    common_stocks = {k: v for k, v in common_stocks.items() if v['count'] > 1}
    # Sort by frequency (most common first)
    common_stocks = dict(sorted(common_stocks.items(), key=lambda x: x[1]['count'], reverse=True))
    
    # Get the last scan time
    last_scan_time = "No scans yet"
    latest_scan = ScanResult.query.order_by(ScanResult.scan_time.desc()).first()
    if latest_scan:
        ist = pytz.timezone('Asia/Kolkata')
        scan_time = latest_scan.scan_time.replace(tzinfo=pytz.UTC).astimezone(ist)
        last_scan_time = scan_time.strftime('%d-%b-%Y %H:%M:%S')
    
    # Current time in IST
    ist = pytz.timezone('Asia/Kolkata')
    current_time = datetime.datetime.now(ist).strftime('%d-%b-%Y %H:%M:%S')
    
    return render_template(
        'index.html', 
        urls=urls, 
        active_count=active_count,
        scan_results=scan_results,
        total_stocks=total_stocks,
        common_stocks=common_stocks,
        last_scan_time=last_scan_time,
        current_time=current_time,
        is_market_hours=is_market_hours()
    )

@app.route('/manage')
def manage_urls():
    """Manage ChartInk URLs"""
    urls = ChartinkURL.query.all()
    
    # Current time in IST
    ist = pytz.timezone('Asia/Kolkata')
    current_time = datetime.datetime.now(ist).strftime('%d-%b-%Y %H:%M:%S')
    
    return render_template(
        'manage_urls.html', 
        urls=urls,
        current_time=current_time,
        is_market_hours=is_market_hours()
    )

@app.route('/add', methods=['POST'])
def add_url():
    """Add a new ChartInk URL"""
    url = request.form.get('url')
    name = request.form.get('name')
    
    if not url or not name:
        flash('URL and name are required', 'danger')
        return redirect(url_for('manage_urls'))
    
    # Check if URL is valid
    if not url.startswith('https://chartink.com/'):
        flash('Please enter a valid ChartInk URL', 'danger')
        return redirect(url_for('manage_urls'))
    
    # Check if URL already exists
    existing = ChartinkURL.query.filter_by(url=url).first()
    if existing:
        flash('This URL is already in the system', 'warning')
        return redirect(url_for('manage_urls'))
    
    # Add URL to database
    new_url = ChartinkURL(url=url, name=name)
    db.session.add(new_url)
    
    try:
        db.session.commit()
        flash(f'Added "{name}" successfully', 'success')
        
        # Try to scan it immediately
        try:
            df = parse_chartink_table(url)
            if not df.empty:
                records = df.to_dict('records')
                
                # Clean up the data to ensure JSON serialization
                for record in records:
                    for key, value in record.items():
                        if pd.isna(value):
                            record[key] = ""
                        elif isinstance(value, (float, int)):
                            record[key] = float(f"{value:.2f}") if isinstance(value, float) else value
                
                ist = pytz.timezone('Asia/Kolkata')
                now = datetime.datetime.now(ist)
                
                result = ScanResult(
                    url_id=new_url.id,
                    scan_time=now,
                    data=json.dumps(records)
                )
                db.session.add(result)
                db.session.commit()
                flash(f'Initial scan completed with {len(records)} stocks', 'info')
        except Exception as e:
            logger.error(f"Error during initial scan: {e}")
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding URL: {str(e)}', 'danger')
    
    return redirect(url_for('manage_urls'))

@app.route('/toggle/<int:url_id>')
def toggle_url(url_id):
    """Toggle a URL's active status"""
    url = ChartinkURL.query.get_or_404(url_id)
    url.active = not url.active
    
    try:
        db.session.commit()
        status = 'activated' if url.active else 'deactivated'
        flash(f'"{url.name}" {status} successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error toggling URL: {str(e)}', 'danger')
    
    return redirect(url_for('manage_urls'))

@app.route('/delete/<int:url_id>')
def delete_url(url_id):
    """Delete a URL"""
    url = ChartinkURL.query.get_or_404(url_id)
    name = url.name
    
    try:
        # Delete associated scan results first
        ScanResult.query.filter_by(url_id=url_id).delete()
        
        # Then delete the URL
        db.session.delete(url)
        db.session.commit()
        flash(f'"{name}" deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting URL: {str(e)}', 'danger')
    
    return redirect(url_for('manage_urls'))

@app.route('/scan')
def force_scan():
    """Force an immediate scan"""
    try:
        scan_chartink_urls()
        flash('Scan completed successfully', 'success')
    except Exception as e:
        flash(f'Error during scan: {str(e)}', 'danger')
    
    return redirect(url_for('index'))

@app.route('/daily')
def daily_report():
    """View daily analysis reports"""
    # Get selected date or default to today
    date_str = request.args.get('date')
    
    ist = pytz.timezone('Asia/Kolkata')
    today = datetime.datetime.now(ist).date()
    
    if date_str:
        try:
            selected_date = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            selected_date = today
    else:
        selected_date = today
    
    # Format date for display
    formatted_date = selected_date.strftime('%d-%b-%Y')
    
    # Get available report dates
    available_dates = [report.analysis_date for report in DailyAnalysis.query.order_by(DailyAnalysis.analysis_date.desc()).all()]
    
    # Get report data for selected date
    report = DailyAnalysis.query.filter_by(analysis_date=selected_date).first()
    report_data = json.loads(report.data) if report else None
    
    # Current time in IST
    current_time = datetime.datetime.now(ist).strftime('%d-%b-%Y %H:%M:%S')
    
    return render_template(
        'daily_report.html',
        report_data=report_data,
        selected_date=date_str or today.strftime('%Y-%m-%d'),
        formatted_date=formatted_date,
        available_dates=available_dates,
        today=today.strftime('%Y-%m-%d'),
        current_time=current_time,
        is_market_hours=is_market_hours()
    )

@app.route('/api/results', methods=['GET'])
def api_results():
    """API endpoint for latest scan results"""
    urls = ChartinkURL.query.all()
    results = {}
    
    for url in urls:
        latest_scan = ScanResult.query.filter_by(url_id=url.id).order_by(ScanResult.scan_time.desc()).first()
        if latest_scan:
            data = json.loads(latest_scan.data)
            ist = pytz.timezone('Asia/Kolkata')
            scan_time = latest_scan.scan_time.replace(tzinfo=pytz.UTC).astimezone(ist)
            results[url.name] = {
                'time': scan_time.strftime('%d-%b-%Y %H:%M:%S'),
                'data': data
            }
    
    return jsonify(results)

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

# Initialize application
def init_app():
    """Initialize the application"""
    # Create templates directory and files
    create_templates()
    
    # Create database tables
    with app.app_context():
        db.create_all()
    
    # Schedule jobs
    schedule_jobs()
    
    # Start scheduler in a separate thread
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    logger.info("Application initialized")

if __name__ == '__main__':
    init_app()
    
    # Initial scan if during market hours
    if is_market_hours():
        with app.app_context():
            scan_chartink_urls()
    
    # Run the Flask app
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
