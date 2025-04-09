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
        table_sr_col INTEGER DEFAULT 0,
        table_stock_name_col INTEGER DEFAULT 1,
        table_symbol_col INTEGER DEFAULT 2,
        table_links_col INTEGER DEFAULT -1,
        table_pct_chg_col INTEGER DEFAULT 3,
        table_volume_col INTEGER DEFAULT 4,
        active INTEGER DEFAULT 1
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id INTEGER,
        sr_num TEXT,
        stock_name TEXT,
        symbol TEXT,
        links TEXT,
        pct_change TEXT,
        volume TEXT,
        recommendation_type TEXT,
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
                 sr_col=0, stock_name_col=1, symbol_col=2, links_col=-1, 
                 pct_chg_col=3, volume_col=4, headers=None):
        """
        Initialize the scraper with the target URL and options.
        
        Args:
            site_id (int): Database ID of the site
            name (str): Name of the site
            url (str): The target website URL to scrape
            selector (str, optional): CSS selector for finding tables
            is_table (bool): Whether to process as HTML table
            sr_col, stock_name_col, etc.: Column indices for table extraction
            headers (dict, optional): HTTP headers to use for the request
        """
        self.site_id = site_id
        self.name = name
        self.url = url
        self.selector = selector
        self.is_table = is_table
        self.sr_col = sr_col
        self.stock_name_col = stock_name_col  
        self.symbol_col = symbol_col
        self.links_col = links_col
        self.pct_chg_col = pct_chg_col
        self.volume_col = volume_col
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
        Parse stock information from HTML content.
        
        Args:
            html_content (str): The HTML content to parse
        """
        if not html_content:
            return
            
        soup = BeautifulSoup(html_content, 'html.parser')
        
        if self.is_table:
            self._parse_table_stock_data(soup)
        else:
            logger.warning(f"Non-table parsing not implemented for {self.name}. Use is_table=True.")
    
    def _parse_table_stock_data(self, soup):
        """Parse stock data from HTML tables"""
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
                    max_col = max(
                        self.sr_col, 
                        self.stock_name_col, 
                        self.symbol_col, 
                        self.pct_chg_col, 
                        self.volume_col
                    )
                    
                    # Also check links_col only if it's positive
                    if self.links_col >= 0:
                        max_col = max(max_col, self.links_col)
                        
                    if len(cells) <= max_col:
                        continue  # Skip rows with insufficient columns
                    
                    # Extract data from cells
                    sr_num = self._clean_text(cells[self.sr_col].text) if self.sr_col < len(cells) else "N/A"
                    stock_name = self._clean_text(cells[self.stock_name_col].text) if self.stock_name_col < len(cells) else "N/A"
                    symbol = self._clean_text(cells[self.symbol_col].text) if self.symbol_col < len(cells) else "N/A"
                    pct_change = self._clean_text(cells[self.pct_chg_col].text) if self.pct_chg_col < len(cells) else "N/A"
                    volume = self._clean_text(cells[self.volume_col].text) if self.volume_col < len(cells) else "N/A"
                    
                    # Extract links if present
                    links = ""
                    if self.links_col >= 0 and self.links_col < len(cells):
                        link_elements = cells[self.links_col].find_all('a', href=True)
                        links = ', '.join([a.get('href', '') for a in link_elements])
                    
                    # Skip if stock name or symbol is missing or looks like a header
                    if (stock_name == "N/A" and symbol == "N/A") or stock_name.lower() in ["stock name", "company", "name"]:
                        continue
                    
                    # Determine recommendation type based on % change
                    rec_type = self._determine_recommendation_type(pct_change)
                    
                    # Get all cell text for raw data
                    raw_data = " | ".join([self._clean_text(cell.text) for cell in cells])
                    
                    # Create hash for uniqueness checking
                    unique_data = f"{sr_num}|{stock_name}|{symbol}|{pct_change}|{volume}"
                    unique_hash = hashlib.md5(unique_data.encode()).hexdigest()
                    
                    recommendation = {
                        'site_id': self.site_id,
                        'sr_num': sr_num,
                        'stock_name': stock_name,
                        'symbol': symbol,
                        'links': links,
                        'pct_change': pct_change,
                        'volume': volume,
                        'recommendation_type': rec_type,
                        'raw_data': raw_data,
                        'hash': unique_hash,
                        'processed_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    
                    self.recommendations.append(recommendation)
            except Exception as e:
                logger.error(f"Error parsing table in {self.name}: {e}")
    
    def _clean_text(self, text):
        """Clean and normalize text from HTML"""
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)  # Replace multiple spaces with single space
        return text
    
    def _determine_recommendation_type(self, pct_change):
        """Determine recommendation type based on percentage change"""
        if not pct_change or pct_change == "N/A":
            return "Unknown"
            
        # Try to extract a number from the percentage change text
        match = re.search(r'([+-]?\d+\.?\d*)%?', pct_change)
        if match:
            try:
                change_val = float(match.group(1))
                if change_val > 2.0:  # Strong positive change
                    return "Buy"
                elif change_val > 0:  # Moderate positive change
                    return "Hold"
                elif change_val < -2.0:  # Strong negative change
                    return "Sell"
                else:  # Moderate negative change
                    return "Hold"
            except ValueError:
                pass
        
        # Fallback logic based on text indicators
        text = pct_change.lower()
        
        # Buy indicators
        if any(term in text for term in ['buy', 'long', 'bullish', 'overweight', '+', 'up']):
            return "Buy"
        # Sell indicators
        elif any(term in text for term in ['sell', 'short', 'bearish', 'underweight', '-', 'down']):
            return "Sell"
        # Hold indicators
        elif any(term in text for term in ['hold', 'neutral', 'market perform']):
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
                    (site_id, sr_num, stock_name, symbol, links, pct_change, volume, recommendation_type, raw_data, hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        rec['site_id'], rec['sr_num'], rec['stock_name'], rec['symbol'], 
                        rec['links'], rec['pct_change'], rec['volume'], rec['recommendation_type'], rec['raw_data'], rec['hash']
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
            logger.info(f"Found {len(self.recommendations)} stocks, {len(new_recommendations)} are new")
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
              f"*Symbol:* {recommendation['symbol']}\n" \
              f"*Stock Name:* {recommendation['stock_name']}\n" \
              f"*% Change:* {recommendation['pct_change']}\n" \
              f"*Volume:* {recommendation['volume']}\n" \
              f"*Source:* {recommendation['site_id']}\n" \
              f"*Date:* {datetime.now().strftime('%Y-%m-%d')}\n"
    
    # Add link if available
    if recommendation['links']:
        message += f"*Link:* {recommendation['links']}\n"
    
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
        logger.info(f"Sent Telegram notification for {recommendation['symbol']}")
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
                table_sr_col, table_stock_name_col, table_symbol_col, 
                table_links_col, table_pct_chg_col, table_volume_col 
                FROM sites WHERE active = 1
            """)
            sites = cursor.fetchall()
            conn.close()
            
            total_new_recommendations = 0
            
            # Scan each site
            for site in sites:
                try:
                    site_id, name, url, selector, is_table, sr_col, stock_name_col, symbol_col, links_col, pct_chg_col, volume_col = site
                    
                    scraper = RecommendationScraper(
                        site_id, name, url, selector, bool(is_table),
                        sr_col, stock_name_col, symbol_col, links_col, pct_chg_col, volume_col
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
                logger.info(f"Found {total_new_recommendations} new stock recommendations across all sites")
                
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
    sr_col = int(request.form.get('sr_col', 0))
    stock_name_col = int(request.form.get('stock_name_col', 1))
    symbol_col = int(request.form.get('symbol_col', 2))
    links_col = int(request.form.get('links_col', -1))
    pct_chg_col = int(request.form.get('pct_chg_col', 3))
    volume_col = int(request.form.get('volume_col', 4))
    
    if name and url:
        conn = sqlite3.connect('recommendations.db')
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO sites 
            (name, url, selector, is_table, table_sr_col, table_stock_name_col, 
            table_symbol_col, table_links_col, table_pct_chg_col, table_volume_col) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, url, selector, is_table, sr_col, stock_name_col, symbol_col, links_col, pct_chg_col, volume_col)
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
        table_sr_col, table_stock_name_col, table_symbol_col, 
        table_links_col, table_pct_chg_col, table_volume_col 
        FROM sites WHERE active = 1
    """)
    sites = cursor.fetchall()
    conn.close()
    
    new_recommendations = []
    
    for site in sites:
        try:
            site_id, name, url, selector, is_table, sr_col, stock_name_col, symbol_col, links_col, pct_chg_col, volume_col = site
            
            scraper = RecommendationScraper(
                site_id, name, url, selector, bool(is_table),
                sr_col, stock_name_col, symbol_col, links_col, pct_chg_col, volume_col
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
    <title>Stock Data Tracker</title>
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
        <h1 class="mb-4">Stock Data Tracker</h1>
        
        <ul class="nav nav-tabs mb-4" id="myTab" role="tablist">
            <li class="nav-item" role="presentation">
                <button class="nav-link active" id="dashboard-tab" data-bs-toggle="tab" data-bs-target="#dashboard" 
                    type="button" role="tab" aria-controls="dashboard" aria-selected="true">Dashboard</button>
            </li>
            <li class="nav-item" role="presentation">
                <button class="nav-link" id="recommendations-tab" data-bs-toggle="tab" data-bs-target="#recommendations" 
                    type="button" role="tab" aria-controls="recommendations" aria-selected="false">Stocks</button>
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
                                <h5 class="mb-0">Recent Stock Data</h5>
                                <button id="scanNowBtn" class="btn btn-warning">Scan Now</button>
                            </div>
                            <div class="card-body">
                                <div id="scanStatus" class="alert alert-info d-none mb-3"></div>
                                
                                {% if recommendations %}
                                <div class="table-responsive">
                                    <table class="table table-hover recommendation-table" id="recommendationsTable">
                                        <thead>
                                            <tr>
                                                <th>Sr.</th>
                                                <th>Date</th>
                                                <th>Source</th>
                                                <th>Symbol</th>
                                                <th>Stock Name</th>
                                                <th>% Change</th>
                                                <th>Volume</th>
                                                <th>Type</th>
                                                <th>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for rec in recommendations %}
                                            <tr class="{{ rec.recommendation_type.lower() }}">
                                                <td>{{ rec.sr_num }}</td>
                                                <td>{{ rec.processed_at }}</td>
                                                <td>{{ rec.site_name }}</td>
                                                <td>{{ rec.symbol }}</td>
                                                <td>{{ rec.stock_name }}</td>
                                                <td>{{ rec.pct_change }}</td>
                                                <td>{{ rec.volume }}</td>
                                                <td>
                                                    <span class="badge {{ 'bg-success' if rec.recommendation_type == 'Buy' else 'bg-danger' if rec.recommendation_type == 'Sell' else 'bg-warning' if rec.recommendation_type == 'Hold' else 'bg-secondary' }}">
                                                        {{ rec.recommendation_type }}
                                                    </span>
                                                </td>
                                                <td>
                                                    {% if rec.links %}
                                                    <a href="{{ rec.links }}" target="_blank" class="btn btn-sm btn-info">View</a>
                                                    {% endif %}
                                                </td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                                {% else %}
                                <div class="alert alert-info">
                                    No stock data found yet. Add sites in the Settings tab and click "Scan Now" to start collecting data.
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
                                <h5 class="mb-0">All Stock Data</h5>
                            </div>
                            <div class="card-body">
                                <div class="table-responsive">
                                    <table class="table table-hover recommendation-table" id="allRecommendationsTable">
                                        <thead>
                                            <tr>
                                                <th>Sr.</th>
                                                <th>Date</th>
                                                <th>Source</th>
                                                <th>Symbol</th>
                                                <th>Stock Name</th>
                                                <th>% Change</th>
                                                <th>Volume</th>
                                                <th>Type</th>
                                                <th>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for rec in recommendations %}
                                            <tr class="{{ rec.recommendation_type.lower() }}">
                                                <td>{{ rec.sr_num }}</td>
                                                <td>{{ rec.processed_at }}</td>
                                                <td>{{ rec.site_name }}</td>
                                                <td>{{ rec.symbol }}</td>
                                                <td>{{ rec.stock_name }}</td>
                                                <td>{{ rec.pct_change }}</td>
                                                <td>{{ rec.volume }}</td>
                                                <td>
                                                    <span class="badge {{ 'bg-success' if rec.recommendation_type == 'Buy' else 'bg-danger' if rec.recommendation_type == 'Sell' else 'bg-warning' if rec.recommendation_type == 'Hold' else 'bg-secondary' }}">
                                                        {{ rec.recommendation_type }}
                                                    </span>
                                                </td>
                                                <td>
                                                    {% if rec.links %}
                                                    <a href="{{ rec.links }}" target="_blank" class="btn btn-sm btn-info">View</a>
                                                    {% endif %}
                                                </td>
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
                                                <th>Selector</th>
                                                <th>Type</th>
                                                <th>Status</th>
                                                <th>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for site in sites %}
                                            <tr>
                                                <td>{{ site.id }}</td>
                                                <td>{{ site.name }}</td>
                                                <td><a href="{{ site.url }}" target="_blank">{{ site.url }}</a></td>
                                                <td>{{ site.selector }}</td>
                                                <td>{{ 'Table' if site.is_table else 'Custom' }}</td>
                                                <td>
                                                    <span class="badge {{ 'bg-success' if site.active else 'bg-secondary' }}">
                                                        {{ 'Active' if site.active else 'Inactive' }}
                                                    </span>
                                                </td>
                                                <td>
                                                    <form method="post" action="{{ url_for('toggle_site', site_id=site.id) }}" class="d-inline">
                                                        <button type="submit" class="btn btn-sm {{ 'btn-secondary' if site.active else 'btn-primary' }}">
                                                            {{ 'Deactivate' if site.active else 'Activate' }}
                                                        </button>
                                                    </form>
                                                    <form method="post" action="{{ url_for('delete_site', site_id=site.id) }}" class="d-inline">
                                                        <button type="submit" class="btn btn-sm btn-danger" onclick="return confirm('Are you sure you want to delete this site?')">Delete</button>
                                                    </form>
                                                </td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                                {% else %}
                                <div class="alert alert-info">
                                    No sites added yet. Use the "Add Site" button to start.
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
                    <h5 class="modal-title" id="addSiteModalLabel">Add Site</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <div class="modal-body">
                    <form action="{{ url_for('add_site') }}" method="post">
                        <div class="mb-3">
                            <label for="name" class="form-label">Site Name</label>
                            <input type="text" class="form-control" id="name" name="name" required>
                        </div>
                        <div class="mb-3">
                            <label for="url" class="form-label">URL</label>
                            <input type="url" class="form-control" id="url" name="url" required>
                            <small class="text-muted">Enter the full URL including http:// or https://</small>
                        </div>
                        <div class="mb-3">
                            <label for="selector" class="form-label">CSS Selector (optional)</label>
                            <input type="text" class="form-control" id="selector" name="selector">
                            <small class="text-muted">CSS selector to find the table, e.g., "table.stock-data"</small>
                        </div>
                        
                        <div class="form-check mb-3">
                            <input class="form-check-input" type="checkbox" value="1" id="is_table" name="is_table" checked>
                            <label class="form-check-label" for="is_table">
                                Content is a table
                            </label>
                        </div>
                        
                        <div id="tableColumnsSection">
                            <h6>Table Column Mapping</h6>
                            <div class="row g-3">
                                <div class="col-md-2">
                                    <label for="sr_col" class="form-label">Sr # Col</label>
                                    <input type="number" class="form-control" id="sr_col" name="sr_col" value="0" min="-1">
                                </div>
                                <div class="col-md-2">
                                    <label for="stock_name_col" class="form-label">Name Col</label>
                                    <input type="number" class="form-control" id="stock_name_col" name="stock_name_col" value="1" min="-1">
                                </div>
                                <div class="col-md-2">
                                    <label for="symbol_col" class="form-label">Symbol Col</label>
                                    <input type="number" class="form-control" id="symbol_col" name="symbol_col" value="2" min="-1">
                                </div>
                                <div class="col-md-2">
                                    <label for="links_col" class="form-label">Links Col</label>
                                    <input type="number" class="form-control" id="links_col" name="links_col" value="-1" min="-1">
                                </div>
                                <div class="col-md-2">
                                    <label for="pct_chg_col" class="form-label">% Change Col</label>
                                    <input type="number" class="form-control" id="pct_chg_col" name="pct_chg_col" value="3" min="-1">
                                </div>
                                <div class="col-md-2">
                                    <label for="volume_col" class="form-label">Volume Col</label>
                                    <input type="number" class="form-control" id="volume_col" name="volume_col" value="4" min="-1">
                                </div>
                            </div>
                            <small class="text-muted">Enter column indices (0-based). Use -1 to ignore a column.</small>
                        </div>
                        
                        <div class="mt-4">
                            <button type="submit" class="btn btn-primary">Add Site</button>
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/jquery@3.6.0/dist/jquery.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net@1.11.5/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net-bs5@1.11.5/js/dataTables.bootstrap5.min.js"></script>
    <script>
        $(document).ready(function() {
            // Initialize DataTables
            $('#recommendationsTable').DataTable({
                order: [[1, 'desc']],
                pageLength: 10,
                responsive: true
            });
            
            $('#allRecommendationsTable').DataTable({
                order: [[1, 'desc']],
                pageLength: 25,
                responsive: true
            });
            
            $('#sitesTable').DataTable({
                pageLength: 10,
                responsive: true
            });
            
            // Handle scan now button
            $('#scanNowBtn').click(function() {
                var btn = $(this);
                var originalText = btn.text();
                
                btn.prop('disabled', true).text('Scanning...');
                $('#scanStatus').removeClass('d-none alert-success alert-danger').addClass('alert-info').text('Scanning sites...');
                
                $.ajax({
                    url: '{{ url_for("scan_now") }}',
                    method: 'POST',
                    success: function(response) {
                        if(response.success) {
                            $('#scanStatus').removeClass('alert-info alert-danger').addClass('alert-success')
                                .text('Scan completed successfully! Found ' + response.new_recommendations + ' new stock recommendations.');
                            
                            if(response.new_recommendations > 0) {
                                setTimeout(function() {
                                    location.reload();
                                }, 2000);
                            }
                        } else {
                            $('#scanStatus').removeClass('alert-info alert-success').addClass('alert-danger')
                                .text('Scan failed: ' + response.error);
                        }
                    },
                    error: function() {
                        $('#scanStatus').removeClass('alert-info alert-success').addClass('alert-danger')
                            .text('Error connecting to server');
                    },
                    complete: function() {
                        btn.prop('disabled', false).text(originalText);
                    }
                });
            });
            
            // Toggle table columns section based on is_table checkbox
            $('#is_table').change(function() {
                if($(this).is(':checked')) {
                    $('#tableColumnsSection').show();
                } else {
                    $('#tableColumnsSection').hide();
                }
            });
        });
    </script>
</body>
</html>
            ''')
    
    # Create recommendations.html
    if not os.path.exists('templates/recommendations.html'):
        with open('templates/recommendations.html', 'w') as f:
            f.write('''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stock Recommendations</title>
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
            <h1>Stock Recommendations</h1>
            <a href="{{ url_for('index') }}" class="btn btn-primary">Back to Dashboard</a>
        </div>
        
        <div class="card">
            <div class="card-header">
                <h5 class="mb-0">All Stock Data</h5>
            </div>
            <div class="card-body">
                <div class="table-responsive">
                    <table class="table table-striped recommendation-table" id="allRecommendationsTable">
                        <thead>
                            <tr>
                                <th>ID</th>
                                <th>Date</th>
                                <th>Source</th>
                                <th>Symbol</th>
                                <th>Stock Name</th>
                                <th>% Change</th>
                                <th>Volume</th>
                                <th>Type</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for rec in recommendations %}
                            <tr class="{{ rec.recommendation_type.lower() }}">
                                <td>{{ rec.id }}</td>
                                <td>{{ rec.processed_at }}</td>
                                <td>{{ rec.site_name }}</td>
                                <td>{{ rec.symbol }}</td>
                                <td>{{ rec.stock_name }}</td>
                                <td>{{ rec.pct_change }}</td>
                                <td>{{ rec.volume }}</td>
                                <td>
                                    <span class="badge {{ 'bg-success' if rec.recommendation_type == 'Buy' else 'bg-danger' if rec.recommendation_type == 'Sell' else 'bg-warning' if rec.recommendation_type == 'Hold' else 'bg-secondary' }}">
                                        {{ rec.recommendation_type }}
                                    </span>
                                </td>
                                <td>
                                    {% if rec.links %}
                                    <a href="{{ rec.links }}" target="_blank" class="btn btn-sm btn-info">View</a>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/jquery@3.6.0/dist/jquery.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net@1.11.5/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net-bs5@1.11.5/js/dataTables.bootstrap5.min.js"></script>
    <script>
        $(document).ready(function() {
            $('#allRecommendationsTable').DataTable({
                order: [[1, 'desc']],
                pageLength: 25,
                responsive: true
            });
        });
    </script>
</body>
</html>
            ''')


# Main startup
if __name__ == '__main__':
    # Create HTML templates
    create_templates()
    
    # Initialize database
    init_db()
    
    # Start the background scanner in a separate thread
    scanner_thread = threading.Thread(target=background_scanner, daemon=True)
    scanner_thread.start()
    
    # Run the Flask app
    app.run(host='0.0.0.0', port=5000, debug=True)
