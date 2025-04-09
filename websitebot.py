import requests
from bs4 import BeautifulSoup
import pandas as pd
import time
from datetime import datetime
import os
import json
import hashlib
import threading
import logging
from flask import Flask, render_template, request, redirect, url_for, jsonify
import sqlite3
import re

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("scraper.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Telegram settings
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')  # Set this in your environment variables
TELEGRAM_CHANNEL = '@Stockniftybot'  # Target channel

# Flask app
app = Flask(__name__)

# Database setup
def init_db():
    conn = sqlite3.connect('recommendations.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS sites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        selector TEXT,
        is_table INTEGER DEFAULT 0,
        table_ticker_col INTEGER DEFAULT 0,
        table_company_col INTEGER DEFAULT 1,
        table_price_col INTEGER DEFAULT 2,
        table_target_col INTEGER DEFAULT 3,
        table_rec_col INTEGER DEFAULT 4,
        active INTEGER DEFAULT 1
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id INTEGER,
        ticker TEXT,
        company TEXT,
        current_price TEXT,
        target_price TEXT,
        recommendation_type TEXT,
        analyst TEXT,
        date TEXT,
        raw_data TEXT,
        hash TEXT UNIQUE,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (site_id) REFERENCES sites (id)
    )
    ''')
    conn.commit()
    conn.close()

# Initialize database
init_db()

class RecommendationScraper:
    def __init__(self, site_id, name, url, selector=None, is_table=False, 
                 ticker_col=0, company_col=1, price_col=2, target_col=3, rec_col=4, headers=None):
        """
        Initialize the scraper with the target URL and options.
        
        Args:
            site_id (int): Database ID of the site
            name (str): Name of the site
            url (str): The target website URL to scrape
            selector (str, optional): CSS selector for finding recommendations or tables
            is_table (bool): Whether to process as HTML table
            ticker_col, company_col, etc.: Column indices for table extraction
            headers (dict, optional): HTTP headers to use for the request
        """
        self.site_id = site_id
        self.name = name
        self.url = url
        self.selector = selector
        self.is_table = is_table
        self.ticker_col = ticker_col
        self.company_col = company_col
        self.price_col = price_col
        self.target_col = target_col
        self.rec_col = rec_col
        self.headers = headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        self.recommendations = []
    
    def fetch_page(self):
        """Fetch the webpage content"""
        try:
            response = requests.get(self.url, headers=self.headers)
            response.raise_for_status()  # Raise exception for HTTP errors
            return response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching the page {self.url}: {e}")
            return None
    
    def parse_recommendations(self, html_content):
        """
        Parse buy and sell recommendations from HTML content.
        
        Args:
            html_content (str): The HTML content to parse
        """
        if not html_content:
            return
            
        soup = BeautifulSoup(html_content, 'html.parser')
        
        if self.is_table:
            self._parse_table_recommendations(soup)
        else:
            self._parse_standard_recommendations(soup)
    
    def _parse_table_recommendations(self, soup):
        """Parse recommendations from HTML tables"""
        # Find tables
        tables = []
        if self.selector:
            tables = soup.select(self.selector)
        else:
            tables = soup.find_all('table')
        
        for table in tables:
            try:
                # Process each row in the table
                rows = table.find_all('tr')
                
                # Skip header row if it exists
                start_index = 1 if len(rows) > 1 else 0
                
                for row in rows[start_index:]:
                    cells = row.find_all(['td', 'th'])
                    if len(cells) <= max(self.ticker_col, self.company_col, self.price_col, self.target_col, self.rec_col):
                        continue  # Skip rows with insufficient columns
                    
                    # Extract data from cells
                    ticker = self._clean_text(cells[self.ticker_col].text) if self.ticker_col < len(cells) else "N/A"
                    company = self._clean_text(cells[self.company_col].text) if self.company_col < len(cells) else "N/A"
                    current_price = self._clean_text(cells[self.price_col].text) if self.price_col < len(cells) else "N/A"
                    target_price = self._clean_text(cells[self.target_col].text) if self.target_col < len(cells) else "N/A"
                    rec_text = self._clean_text(cells[self.rec_col].text) if self.rec_col < len(cells) else "N/A"
                    
                    # Skip if ticker is missing or looks like a header
                    if ticker == "N/A" or ticker.lower() in ["symbol", "ticker", "stock", "company"]:
                        continue
                    
                    # Determine recommendation type
                    rec_type = self._determine_recommendation_type(rec_text)
                    
                    # Get all cell text for raw data
                    raw_data = " | ".join([self._clean_text(cell.text) for cell in cells])
                    
                    # Create hash for uniqueness checking
                    unique_data = f"{ticker}|{company}|{current_price}|{target_price}|{rec_type}|{raw_data}"
                    unique_hash = hashlib.md5(unique_data.encode()).hexdigest()
                    
                    recommendation = {
                        'site_id': self.site_id,
                        'ticker': ticker,
                        'company': company,
                        'current_price': current_price,
                        'target_price': target_price,
                        'recommendation_type': rec_type,
                        'analyst': self.name,  # Use site name as analyst for table data
                        'date': datetime.now().strftime("%Y-%m-%d"),
                        'raw_data': raw_data,
                        'hash': unique_hash,
                        'processed_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    
                    self.recommendations.append(recommendation)
            except Exception as e:
                logger.error(f"Error parsing table in {self.name}: {e}")
    
    def _parse_standard_recommendations(self, soup):
        """Parse recommendations from standard HTML elements"""
        # Use the site-specific selector if provided
        if self.selector:
            recommendation_elements = soup.select(self.selector)
        else:
            # Default: look for common patterns in financial sites
            recommendation_elements = soup.select('.recommendation, .stock-recommendation, .buy-rec, .sell-rec, article.recommendation')
            
        for element in recommendation_elements:
            try:
                # Extract data - modify these based on the actual website structure
                ticker = element.select_one('.ticker, .symbol')
                ticker = self._clean_text(ticker.text) if ticker else "N/A"
                
                company = element.select_one('.company, .name')
                company = self._clean_text(company.text) if company else "N/A"
                
                price = element.select_one('.price, .current-price')
                price = self._clean_text(price.text) if price else "N/A"
                
                target = element.select_one('.target, .target-price')
                target = self._clean_text(target.text) if target else "N/A"
                
                analyst = element.select_one('.analyst, .source')
                analyst = self._clean_text(analyst.text) if analyst else self.name
                
                date = element.select_one('.date, .pub-date')
                date = self._clean_text(date.text) if date else datetime.now().strftime("%Y-%m-%d")
                
                # Determine if it's a buy or sell recommendation
                rec_type_elem = element.select_one('.recommendation-type, .rec-type, .rating')
                rec_text = self._clean_text(rec_type_elem.text) if rec_type_elem else ""
                rec_type = self._determine_recommendation_type(rec_text)
                
                # Get raw content for storage
                raw_data = element.text.strip()
                
                # Create hash for uniqueness checking
                unique_data = f"{ticker}|{company}|{price}|{target}|{rec_type}|{analyst}|{date}"
                unique_hash = hashlib.md5(unique_data.encode()).hexdigest()
                
                recommendation = {
                    'site_id': self.site_id,
                    'ticker': ticker,
                    'company': company,
                    'current_price': price,
                    'target_price': target,
                    'recommendation_type': rec_type,
                    'analyst': analyst,
                    'date': date,
                    'raw_data': raw_data[:500],  # Limit raw data length
                    'hash': unique_hash,
                    'processed_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                
                self.recommendations.append(recommendation)
            except Exception as e:
                logger.error(f"Error parsing recommendation element on {self.name}: {e}")
    
    def _clean_text(self, text):
        """Clean and normalize text from HTML"""
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)  # Replace multiple spaces with single space
        return text
    
    def _determine_recommendation_type(self, text):
        """Determine recommendation type from text"""
        if not text:
            return "Unknown"
            
        text = text.lower()
        
        # Buy indicators
        if any(term in text for term in ['buy', 'long', 'bullish', 'overweight', 'accumulate', 'add', 'outperform']):
            return "Buy"
        # Sell indicators
        elif any(term in text for term in ['sell', 'short', 'bearish', 'underweight', 'reduce', 'underperform']):
            return "Sell"
        # Hold indicators
        elif any(term in text for term in ['hold', 'neutral', 'market perform', 'equal weight']):
            return "Hold"
        
        return "Unknown"
    
    def save_recommendations(self):
        """Save recommendations to database and return new ones"""
        if not self.recommendations:
            return []
            
        conn = sqlite3.connect('recommendations.db')
        cursor = conn.cursor()
        
        new_recommendations = []
        
        for rec in self.recommendations:
            try:
                # Check if this recommendation already exists
                cursor.execute("SELECT id FROM recommendations WHERE hash = ?", (rec['hash'],))
                existing = cursor.fetchone()
                
                if not existing:
                    # Insert new recommendation
                    cursor.execute("""
                    INSERT INTO recommendations 
                    (site_id, ticker, company, current_price, target_price, recommendation_type, analyst, date, raw_data, hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        rec['site_id'], rec['ticker'], rec['company'], rec['current_price'], 
                        rec['target_price'], rec['recommendation_type'], rec['analyst'], rec['date'], rec['raw_data'], rec['hash']
                    ))
                    
                    # Get the ID of the newly inserted recommendation
                    new_rec_id = cursor.lastrowid
                    rec['id'] = new_rec_id
                    new_recommendations.append(rec)
            except Exception as e:
                logger.error(f"Error saving recommendation: {e}")
                
        conn.commit()
        conn.close()
        
        return new_recommendations

    def run(self):
        """Run the scraper and return new recommendations"""
        logger.info(f"Scanning {self.name} at {self.url}")
        html_content = self.fetch_page()
        self.recommendations = []
        
        if html_content:
            self.parse_recommendations(html_content)
            new_recommendations = self.save_recommendations()
            logger.info(f"Found {len(self.recommendations)} recommendations, {len(new_recommendations)} are new")
            return new_recommendations
        return []


def send_telegram_notification(recommendation):
    """Send a notification to the Telegram channel"""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("Telegram bot token not set. Skipping notification.")
        return False
    
    # Format the message with more details
    if recommendation['recommendation_type'] == "Buy":
        emoji = "🟢"
    elif recommendation['recommendation_type'] == "Sell":
        emoji = "🔴"
    elif recommendation['recommendation_type'] == "Hold":
        emoji = "🟡"
    else:
        emoji = "⚪"
        
    message = f"{emoji} *{recommendation['recommendation_type'].upper()} RECOMMENDATION*\n\n" \
              f"*Ticker:* {recommendation['ticker']}\n" \
              f"*Company:* {recommendation['company']}\n" \
              f"*Current Price:* {recommendation['current_price']}\n" \
              f"*Target Price:* {recommendation['target_price']}\n" \
              f"*Source:* {recommendation['analyst']}\n" \
              f"*Date:* {recommendation['date']}\n"
    
    # For table-based recommendations, add any additional data
    if 'raw_data' in recommendation and recommendation['raw_data']:
        extra_data = recommendation['raw_data'].replace('|', '\n').strip()
        if len(extra_data) > 100:  # Limit extra data length
            extra_data = extra_data[:100] + "..."
    
    # Send the message
    try:
        send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHANNEL,
            'text': message,
            'parse_mode': 'Markdown'
        }
        response = requests.post(send_url, json=payload)
        response.raise_for_status()
        logger.info(f"Sent Telegram notification for {recommendation['ticker']}")
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False


# Background scanner thread
def background_scanner():
    """Continuously scan sites in the background"""
    while True:
        try:
            # Get all active sites from the database
            conn = sqlite3.connect('recommendations.db')
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, url, selector, is_table, 
                table_ticker_col, table_company_col, table_price_col, 
                table_target_col, table_rec_col 
                FROM sites WHERE active = 1
            """)
            sites = cursor.fetchall()
            conn.close()
            
            total_new_recommendations = 0
            
            # Scan each site
            for site in sites:
                try:
                    site_id, name, url, selector, is_table, ticker_col, company_col, price_col, target_col, rec_col = site
                    
                    scraper = RecommendationScraper(
                        site_id, name, url, selector, bool(is_table),
                        ticker_col, company_col, price_col, target_col, rec_col
                    )
                    new_recommendations = scraper.run()
                    
                    # Send Telegram notifications for new recommendations
                    for rec in new_recommendations:
                        send_telegram_notification(rec)
                        total_new_recommendations += 1
                except Exception as e:
                    logger.error(f"Error scanning site {site[1]}: {e}")
                
                # Small delay between sites to avoid overloading
                time.sleep(2)
            
            if total_new_recommendations > 0:
                logger.info(f"Found {total_new_recommendations} new recommendations across all sites")
                
            # Wait for the next scan cycle
            logger.info("Scan cycle completed. Waiting for next cycle...")
            time.sleep(300)  # 5 minutes between scans by default
            
        except Exception as e:
            logger.error(f"Error in background scanner: {e}")
            time.sleep(60)  # Wait a bit before retrying after an error


# Flask routes for web interface
@app.route('/')
def index():
    """Render the main dashboard"""
    conn = sqlite3.connect('recommendations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all sites
    cursor.execute("SELECT * FROM sites")
    sites = cursor.fetchall()
    
    # Get recent recommendations
    cursor.execute("""
    SELECT r.*, s.name as site_name 
    FROM recommendations r 
    JOIN sites s ON r.site_id = s.id 
    ORDER BY r.processed_at DESC LIMIT 50
    """)
    recommendations = cursor.fetchall()
    
    conn.close()
    
    return render_template('index.html', sites=sites, recommendations=recommendations)

@app.route('/site/add', methods=['POST'])
def add_site():
    """Add a new site to monitor"""
    name = request.form.get('name')
    url = request.form.get('url')
    selector = request.form.get('selector')
    is_table = int(request.form.get('is_table', 0))
    
    # Table column mappings
    ticker_col = int(request.form.get('ticker_col', 0))
    company_col = int(request.form.get('company_col', 1))
    price_col = int(request.form.get('price_col', 2))
    target_col = int(request.form.get('target_col', 3))
    rec_col = int(request.form.get('rec_col', 4))
    
    if name and url:
        conn = sqlite3.connect('recommendations.db')
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO sites 
            (name, url, selector, is_table, table_ticker_col, table_company_col, 
            table_price_col, table_target_col, table_rec_col) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, url, selector, is_table, ticker_col, company_col, price_col, target_col, rec_col)
        )
        conn.commit()
        conn.close()
        
    return redirect(url_for('index'))

@app.route('/site/delete/<int:site_id>', methods=['POST'])
def delete_site(site_id):
    """Delete a site"""
    conn = sqlite3.connect('recommendations.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    conn.commit()
    conn.close()
    
    return redirect(url_for('index'))

@app.route('/site/toggle/<int:site_id>', methods=['POST'])
def toggle_site(site_id):
    """Toggle a site's active status"""
    conn = sqlite3.connect('recommendations.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE sites SET active = NOT active WHERE id = ?", (site_id,))
    conn.commit()
    conn.close()
    
    return redirect(url_for('index'))

@app.route('/recommendations')
def view_recommendations():
    """View all recommendations"""
    conn = sqlite3.connect('recommendations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("""
    SELECT r.*, s.name as site_name 
    FROM recommendations r 
    JOIN sites s ON r.site_id = s.id 
    ORDER BY r.processed_at DESC
    """)
    recommendations = cursor.fetchall()
    
    conn.close()
    
    return render_template('recommendations.html', recommendations=recommendations)

@app.route('/scan/now', methods=['POST'])
def scan_now():
    """Trigger an immediate scan of all sites"""
    conn = sqlite3.connect('recommendations.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, url, selector, is_table, 
        table_ticker_col, table_company_col, table_price_col, 
        table_target_col, table_rec_col 
        FROM sites WHERE active = 1
    """)
    sites = cursor.fetchall()
    conn.close()
    
    new_recommendations = []
    
    for site in sites:
        try:
            site_id, name, url, selector, is_table, ticker_col, company_col, price_col, target_col, rec_col = site
            
            scraper = RecommendationScraper(
                site_id, name, url, selector, bool(is_table),
                ticker_col, company_col, price_col, target_col, rec_col
            )
            site_new_recommendations = scraper.run()
            
            # Send Telegram notifications for new recommendations
            for rec in site_new_recommendations:
                send_telegram_notification(rec)
                new_recommendations.append(rec)
        except Exception as e:
            logger.error(f"Error scanning site {site[1]}: {e}")
    
    return jsonify({
        'success': True,
        'new_recommendations': len(new_recommendations)
    })


# Create HTML templates
def create_templates():
    """Create the necessary HTML templates if they don't exist"""
    os.makedirs('templates', exist_ok=True)
    
    # Create index.html
    if not os.path.exists('templates/index.html'):
        with open('templates/index.html', 'w') as f:
            f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stock Recommendation Tracker</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/datatables.net-bs5@1.11.5/css/dataTables.bootstrap5.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .recommendation-card { margin-bottom: 10px; }
        .buy { border-left: 4px solid #28a745; }
        .sell { border-left: 4px solid #dc3545; }
        .hold { border-left: 4px solid #ffc107; }
        .unknown { border-left: 4px solid #6c757d; }
        .recommendation-table tr.buy { background-color: rgba(40, 167, 69, 0.05); }
        .recommendation-table tr.sell { background-color: rgba(220, 53, 69, 0.05); }
        .recommendation-table tr.hold { background-color: rgba(255, 193, 7, 0.05); }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4">Stock Recommendation Tracker</h1>
        
        <ul class="nav nav-tabs mb-4" id="myTab" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="dashboard-tab" data-bs-toggle="tab" data-bs-target="#dashboard" 
                    type="button" role="tab" aria-controls="dashboard" aria-selected="true">Dashboard</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="recommendations-tab" data-bs-toggle="tab" data-bs-target="#recommendations" 
                    type="button" role="tab" aria-controls="recommendations" aria-selected="false">Recommendations</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="settings-tab" data-bs-toggle="tab" data-bs-target="#settings" 
                    type="button" role="tab" aria-controls="settings" aria-selected="false">Settings</button>
            </li>
        </ul>
        
        <div class="tab-content" id="myTabContent">
            <!-- Dashboard Tab -->
            <div class="tab-pane fade show active" id="dashboard" role="tabpanel" aria-labelledby="dashboard-tab">
                <div class="row">
                    <div class="col-md-12">
                        <div class="card mb-4">
                            <div class="card-header d-flex justify-content-between align-items-center">
                                <h5 class="mb-0">Recent Stock Recommendations</h5>
                                <button id="scanNowBtn" class="btn btn-warning">Scan Now</button>
                            </div>
                            <div class="card-body">
                                <div id="scanStatus" class="alert alert-info d-none mb-3"></div>
                                
                                {% if recommendations %}
                                <div class="table-responsive">
                                    <table class="table table-hover recommendation-table" id="recommendationsTable">
                                        <thead>
                                            <tr>
                                                <th>Date</th>
                                                <th>Source</th>
                                                <th>Ticker</th>
                                                <th>Company</th>
                                                <th>Type</th>
                                                <th>Current</th>
                                                <th>Target</th>
                                                <th>Analyst</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for rec in recommendations %}
                                            <tr class="{{ rec.recommendation_type.lower() }}">
                                                <td>{{ rec.date }}</td>
                                                <td>{{ rec.site_name }}</td>
                                                <td>{{ rec.ticker }}</td>
                                                <td>{{ rec.company }}</td>
                                                <td>
                                                    <span class="badge {{ 'bg-success' if rec.recommendation_type == 'Buy' else 'bg-danger' if rec.recommendation_type == 'Sell' else 'bg-warning' if rec.recommendation_type == 'Hold' else 'bg-secondary' }}">
                                                        {{ rec.recommendation_type }}
                                                    </span>
                                                </td>
                                                <td>{{ rec.current_price }}</td>
                                                <td>{{ rec.target_price }}</td>
                                                <td>{{ rec.analyst }}</td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                                {% else %}
                                <div class="alert alert-info">
                                    No recommendations found yet. Add sites in the Settings tab and click "Scan Now" to start collecting recommendations.
                                </div>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Recommendations Tab -->
            <div class="tab-pane fade" id="recommendations" role="tabpanel" aria-labelledby="recommendations-tab">
                <div class="row">
                    <div class="col-md-12">
                        <div class="card">
                            <div class="card-header">
                                <h5 class="mb-0">All Stock Recommendations</h5>
                            </div>
                            <div class="card-body">
                                <div class="table-responsive">
                                    <table class="table table-hover recommendation-table" id="allRecommendationsTable">
                                        <thead>
                                            <tr>
                                                <th>Date</th>
                                                <th>Source</th>
                                                <th>Ticker</th>
                                                <th>Company</th>
                                                <th>Type</th>
                                                <th>Current</th>
                                                <th>Target</th>
                                                <th>Analyst</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for rec in recommendations %}
                                            <tr class="{{ rec.recommendation_type.lower() }}">
                                                <td>{{ rec.date }}</td>
                                                <td>{{ rec.site_name }}</td>
                                                <td>{{ rec.ticker }}</td>
                                                <td>{{ rec.company }}</td>
                                                <td>
                                                    <span class="badge {{ 'bg-success' if rec.recommendation_type == 'Buy' else 'bg-danger' if rec.recommendation_type == 'Sell' else 'bg-warning' if rec.recommendation_type == 'Hold' else 'bg-secondary' }}">
                                                        {{ rec.recommendation_type }}
                                                    </span>
                                                </td>
                                                <td>{{ rec.current_price }}</td>
                                                <td>{{ rec.target_price }}</td>
                                                <td>{{ rec.analyst }}</td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            
            <!-- Settings Tab -->
            <div class="tab-pane fade" id="settings" role="tabpanel" aria-labelledby="settings-tab">
                <div class="row">
                    <div class="col-md-12">
                        <div class="card mb-4">
                            <div class="card-header d-flex justify-content-between align-items-center">
                                <h5 class="mb-0">Sites to Monitor</h5>
                                <button class="btn btn-primary" data-bs-toggle="modal" data-bs-target="#addSiteModal">Add Site</button>
                            </div>
                            <div class="card-body">
                                {% if sites %}
                                <div class="table-responsive">
                                    <table class="table table-striped" id="sitesTable">
                                        <thead>
                                            <tr>
                                                <th>ID</th>
                                                <th>Name</th>
                                                <th>URL</th>
                                                <th>Status</th>
                                                <th>Type</th>
                                                <th>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for site in sites %}
                                            <tr>
                                                <td>{{ site.id }}</td>
                                                <td>{{ site.name }}</td>
                                                <td><a href="{{ site.url }}" target="_blank">{{ site.url }}</a></td>
                                                <td>
                                                    <span class="badge {{ 'bg-success' if site.active else 'bg-secondary' }}">
                                                        {{ 'Active' if site.active else 'Inactive' }}
                                                    </span>
                                                </td>
                                                <td>{{ 'Table' if site.is_table else 'Standard' }}</td>
                                                <td>
                                                    <div class="btn-group" role="group">
                                                        <form action="{{ url_for('toggle_site', site_id=site.id) }}" method="post" class="d-inline">
                                                            <button type="submit" class="btn btn-sm {{ 'btn-secondary' if site.active else 'btn-success' }}">
                                                                {{ 'Deactivate' if site.active else 'Activate' }}
                                                            </button>
                                                        </form>
                                                        <form action="{{ url_for('delete_site', site_id=site.id) }}" method="post" class="d-inline ms-1">
                                                            <button type="submit" class="btn btn-sm btn-danger" 
                                                                onclick="return confirm('Are you sure you want to delete this site?')">Delete</button>
                                                        </form>
                                                    </div>
                                                </td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                                {% else %}
                                <div class="alert alert-info">
                                    No sites have been added yet. Click "Add Site" to start monitoring stock recommendations.
                                </div>
                                {% endif %}
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Add Site Modal -->
    <div class="modal fade" id="addSiteModal" tabindex="-1" aria-labelledby="addSiteModalLabel" aria-hidden="true">
        <div class="modal-dialog modal-lg">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="addSiteModalLabel">Add New Site to Monitor</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <form action="{{ url_for('add_site') }}" method="post">
                    <div class="modal-body">
                        <div class="row mb-3">
                            <div class="col-md-6">
                                <label for="name" class="form-label">Site Name</label>
                                <input type="text" class="form-control" id="name" name="name" required placeholder="e.g., Motley Fool">
                            </div>
                            <div class="col-md-6">
                                <label for="url" class="form-label">URL</label>
                                <input type="url" class="form-control" id="url" name="url" required placeholder="https://example.com/recommendations">
                            </div>
                        </div>
                        
                        <div class="mb-3">
                            <label for="selector" class="form-label">CSS Selector (optional)</label>
                            <input type="text" class="form-control" id="selector" name="selector" placeholder="e.g., table.recommendations, div.stock-picks">
                            <div class="form-text">CSS selector to find recommendation elements or tables. Leave blank to use automatic detection.</div>
                        </div>
                        
                        <div class="form-check mb-3">
                            <input class="form-check-input" type="checkbox" id="is_table" name="is_table" value="1">
                            <label class="form-check-label" for="is_table">
                                This site uses tables for recommendations
                            </label>
                        </div>
                        
                        <div id="tableOptions" class="row mb-3 d-none">
                            <h6>Table Column Mapping</h6>
                            <div class="col-md-4 mb-2">
                                <label for="ticker_col" class="form-label">Ticker Column</label>
                                <input type="number" class="form-control" id="ticker_col" name="ticker_col" value="0" min="0">
                            </div>
                            <div class="col-md-4 mb-2">
                                <label for="company_col" class="form-label">Company Column</label>
                                <input type="number" class="form-control" id="company_col" name="company_col" value="1" min="0">
                            </div>
                            <div class="col-md-4 mb-2">
                                <label for="price_col" class="form-label">Current Price Column</label>
                                <input type="number" class="form-control" id="price_col" name="price_col" value="2" min="0">
                            </div>
                            <div class="col-md-4 mb-2">
                                <label for="target_col" class="form-label">Target Price Column</label>
                                <input type="number" class="form-control" id="target_col" name="target_col" value="3" min="0">
                            </div>
                            <div class="col-md-4 mb-2">
                                <label for="rec_col" class="form-label">Recommendation Column</label>
                                <input type="number" class="form-control" id="rec_col" name="rec_col" value="4" min="0">
                            </div>
                            <div class="form-text">Column numbers start from 0. First column = 0, second column = 1, etc.</div>
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                        <button type="submit" class="btn btn-primary">Add Site</button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <!-- Scripts -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/jquery@3.6.0/dist/jquery.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net@1.11.5/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net-bs5@1.11.5/js/dataTables.bootstrap5.min.js"></script>
    
    <script>
        $(document).ready(function() {
            // Initialize DataTables
            $('#recommendationsTable, #allRecommendationsTable').DataTable({
                order: [[0, 'desc']],
                pageLength: 10,
                lengthMenu: [[10, 25, 50, -1], [10, 25, 50, "All"]]
            });
            
            $('#sitesTable').DataTable({
                order: [[0, 'asc']],
                pageLength: 10
            });
            
            // Toggle table options in the Add Site modal
            $('#is_table').change(function() {
                if($(this).is(':checked')) {
                    $('#tableOptions').removeClass('d-none');
                } else {
                    $('#tableOptions').addClass('d-none');
                }
            });
            
            // Scan Now button handler
            $('#scanNowBtn').click(function() {
                var btn = $(this);
                var originalText = btn.text();
                var status = $('#scanStatus');
                
                btn.prop('disabled', true);
                btn.text('Scanning...');
                status.removeClass('d-none alert-success alert-danger').addClass('alert-info');
                status.text('Scanning sites for new recommendations...');
                
                $.ajax({
                    url: '/scan/now',
                    type: 'POST',
                    dataType: 'json',
                    success: function(response) {
                        status.removeClass('alert-info').addClass('alert-success');
                        status.text('Scan completed! Found ' + response.new_recommendations + ' new recommendations.');
                        
                        if(response.new_recommendations > 0) {
                            setTimeout(function() {
                                location.reload();
                            }, 2000);
                        }
                    },
                    error: function(xhr, status, error) {
                        status.removeClass('alert-info').addClass('alert-danger');
                        status.text('Error scanning sites: ' + error);
                    },
                    complete: function() {
                        btn.prop('disabled', false);
                        btn.text(originalText);
                        
                        setTimeout(function() {
                            status.addClass('d-none');
                        }, 5000);
                    }
                });
            });
        });
    </script>
</body>
</html>
''')
    
    # Create recommendations.html template
    if not os.path.exists('templates/recommendations.html'):
        with open('templates/recommendations.html', 'w') as f:
            f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>All Recommendations - Stock Recommendation Tracker</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.jsdelivr.net/npm/datatables.net-bs5@1.11.5/css/dataTables.bootstrap5.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .buy { border-left: 4px solid #28a745; }
        .sell { border-left: 4px solid #dc3545; }
        .hold { border-left: 4px solid #ffc107; }
        .unknown { border-left: 4px solid #6c757d; }
        .recommendation-table tr.buy { background-color: rgba(40, 167, 69, 0.05); }
        .recommendation-table tr.sell { background-color: rgba(220, 53, 69, 0.05); }
        .recommendation-table tr.hold { background-color: rgba(255, 193, 7, 0.05); }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>All Stock Recommendations</h1>
            <a href="{{ url_for('index') }}" class="btn btn-secondary">Back to Dashboard</a>
        </div>
        
        <div class="card">
            <div class="card-body">
                {% if recommendations %}
                <div class="table-responsive">
                    <table class="table table-hover recommendation-table" id="fullRecommendationsTable">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Source</th>
                                <th>Ticker</th>
                                <th>Company</th>
                                <th>Type</th>
                                <th>Current</th>
                                <th>Target</th>
                                <th>Analyst</th>
                                <th>Details</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for rec in recommendations %}
                            <tr class="{{ rec.recommendation_type.lower() }}">
                                <td>{{ rec.date }}</td>
                                <td>{{ rec.site_name }}</td>
                                <td>{{ rec.ticker }}</td>
                                <td>{{ rec.company }}</td>
                                <td>
                                    <span class="badge {{ 'bg-success' if rec.recommendation_type == 'Buy' else 'bg-danger' if rec.recommendation_type == 'Sell' else 'bg-warning' if rec.recommendation_type == 'Hold' else 'bg-secondary' }}">
                                        {{ rec.recommendation_type }}
                                    </span>
                                </td>
                                <td>{{ rec.current_price }}</td>
                                <td>{{ rec.target_price }}</td>
                                <td>{{ rec.analyst }}</td>
                                <td>
                                    <button class="btn btn-sm btn-info" 
                                            data-bs-toggle="modal" 
                                            data-bs-target="#recDetailsModal" 
                                            data-raw="{{ rec.raw_data }}">
                                        View
                                    </button>
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
                {% else %}
                <div class="alert alert-info">No recommendations found in the database.</div>
                {% endif %}
            </div>
        </div>
    </div>
    
    <!-- Recommendation Details Modal -->
    <div class="modal fade" id="recDetailsModal" tabindex="-1" aria-labelledby="recDetailsModalLabel" aria-hidden="true">
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title" id="recDetailsModalLabel">Recommendation Details</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <pre id="recRawData" class="border p-3 bg-light" style="white-space: pre-wrap;"></pre>
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Close</button>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Scripts -->
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/jquery@3.6.0/dist/jquery.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net@1.11.5/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net-bs5@1.11.5/js/dataTables.bootstrap5.min.js"></script>
    
    <script>
        $(document).ready(function() {
            // Initialize DataTable
            $('#fullRecommendationsTable').DataTable({
                order: [[0, 'desc']],
                pageLength: 25,
                lengthMenu: [[10, 25, 50, 100, -1], [10, 25, 50, 100, "All"]]
            });
            
            // Handle view details button
            $('#recDetailsModal').on('show.bs.modal', function(event) {
                var button = $(event.relatedTarget);
                var rawData = button.data('raw');
                $('#recRawData').text(rawData);
            });
        });
    </script>
</body>
</html>
''')

# Main entry point
if __name__ == '__main__':
    # Create template files if they don't exist
    create_templates()
    
    # Start background scanning thread
    scanner_thread = threading.Thread(target=background_scanner, daemon=True)
    scanner_thread.start()
    
    # Start Flask application
    app.run(host='0.0.0.0', port=5000, debug=True)
