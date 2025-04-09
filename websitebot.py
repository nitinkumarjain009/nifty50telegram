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
TELEGRAM_BOT_TOKEN = '8017759392:AAEwM-W-y83lLXTjlPl8sC_aBmizuIrFXnU'
TELEGRAM_USER_ID = '711856868'

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
        is_table INTEGER DEFAULT 1,
        active INTEGER DEFAULT 1,
        last_scan TIMESTAMP
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
    
    # Add chartink.com if it doesn't exist
    cursor.execute("SELECT id FROM sites WHERE url LIKE '%chartink.com%'")
    if not cursor.fetchone():
        cursor.execute('''
        INSERT INTO sites (name, url, selector, is_table, active)
        VALUES (?, ?, ?, ?, ?)
        ''', ('ChartInk Scanner', 'https://chartink.com/screener/', 'table.table', 1, 1))
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

class ChartInkScraper:
    def __init__(self, site_id, name, url, selector='table.table'):
        """
        Initialize the ChartInk scraper with the target URL and options.
        
        Args:
            site_id (int): Database ID of the site
            name (str): Name of the site
            url (str): The target website URL to scrape
            selector (str): CSS selector for finding tables
        """
        self.site_id = site_id
        self.name = name
        self.url = url
        self.selector = selector
        self.recommendations = []
        # Advanced headers to mimic a browser
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Referer': 'https://www.google.com/',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        }
    
    def fetch_page(self):
        """Fetch the ChartInk page content with special handling for dynamic content"""
        try:
            # For ChartInk, we need to handle it specially
            logger.info(f"Fetching data from ChartInk at {self.url}")
            
            # First, we'll try to get the main scanner page
            response = requests.get(self.url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            # For ChartInk scanner, we may need to interact with their API too
            scanner_url = 'https://chartink.com/screener/process'
            payload = {
                'scan_clause': 'ema(close,10) < close AND ema(close,20) < close AND close > 100'
            }
            
            # Get the CSRF token from the page
            soup = BeautifulSoup(response.text, 'html.parser')
            csrf_token = soup.select_one('meta[name="csrf-token"]')
            
            if csrf_token:
                headers_with_csrf = self.headers.copy()
                headers_with_csrf['X-CSRF-TOKEN'] = csrf_token['content']
                
                # Make the API request to get scanner data
                scanner_response = requests.post(
                    scanner_url, 
                    headers=headers_with_csrf,
                    data=payload,
                    timeout=15
                )
                
                if scanner_response.status_code == 200:
                    logger.info(f"Successfully fetched scanner data from ChartInk")
                    return scanner_response.text
            
            # Fallback to the regular page response
            logger.info(f"Using regular page content from ChartInk")
            return response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching ChartInk page: {e}")
            return None
    
    def parse_recommendations(self, json_content):
        """
        Parse stock information from ChartInk JSON response.
        
        Args:
            json_content (str): The JSON content to parse
        """
        if not json_content:
            return
        
        try:
            # Try to parse as JSON (ChartInk API response)
            data = json.loads(json_content)
            
            if 'data' in data:
                # Process data from ChartInk API
                for i, stock_data in enumerate(data['data']):
                    sr_num = str(i + 1)
                    symbol = stock_data.get('nsecode', '')
                    stock_name = stock_data.get('name', '')
                    
                    # Extract metrics
                    pct_change = stock_data.get('per_chg', 'N/A')
                    if pct_change != 'N/A':
                        pct_change = f"{pct_change}%"
                    
                    volume = stock_data.get('volume', 'N/A')
                    
                    # Link to the stock detail page
                    links = f"https://chartink.com/stocks/{symbol}.html" if symbol else ""
                    
                    # Determine recommendation type based on % change
                    rec_type = self._determine_recommendation_type(pct_change)
                    
                    # Create raw data
                    raw_data = json.dumps(stock_data)
                    
                    # Create hash for uniqueness checking
                    unique_data = f"{symbol}|{stock_name}|{pct_change}|{volume}"
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
            else:
                # Fallback to HTML parsing
                soup = BeautifulSoup(json_content, 'html.parser')
                self._parse_table_stock_data(soup)
                
        except json.JSONDecodeError:
            # If not valid JSON, try to parse as HTML
            soup = BeautifulSoup(json_content, 'html.parser')
            self._parse_table_stock_data(soup)
        except Exception as e:
            logger.error(f"Error parsing ChartInk data: {e}")
    
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
                
                # Get header row to identify columns
                header_row = rows[0] if rows else None
                if not header_row:
                    continue
                    
                headers = [self._clean_text(th.text) for th in header_row.find_all(['th', 'td'])]
                
                # Find important column indices
                symbol_col = -1
                name_col = -1
                pct_chg_col = -1
                volume_col = -1
                
                for i, header in enumerate(headers):
                    header_lower = header.lower()
                    if any(term in header_lower for term in ['symbol', 'nsecode', 'ticker']):
                        symbol_col = i
                    elif any(term in header_lower for term in ['name', 'company']):
                        name_col = i
                    elif any(term in header_lower for term in ['%', 'change', 'chg']):
                        pct_chg_col = i
                    elif any(term in header_lower for term in ['volume', 'vol']):
                        volume_col = i
                
                # Skip header row
                for i, row in enumerate(rows[1:], 1):
                    cells = row.find_all(['td', 'th'])
                    if len(cells) < 3:  # Need at least a few cells for meaningful data
                        continue
                    
                    # Extract data based on identified columns
                    sr_num = str(i)
                    
                    symbol = "N/A"
                    if symbol_col >= 0 and symbol_col < len(cells):
                        symbol = self._clean_text(cells[symbol_col].text)
                    
                    stock_name = "N/A"
                    if name_col >= 0 and name_col < len(cells):
                        stock_name = self._clean_text(cells[name_col].text)
                    elif symbol:
                        stock_name = symbol  # Use symbol as name if no name column
                    
                    pct_change = "N/A"
                    if pct_chg_col >= 0 and pct_chg_col < len(cells):
                        pct_change = self._clean_text(cells[pct_chg_col].text)
                    
                    volume = "N/A"
                    if volume_col >= 0 and volume_col < len(cells):
                        volume = self._clean_text(cells[volume_col].text)
                    
                    # Extract links if present
                    links = ""
                    for cell in cells:
                        link_elements = cell.find_all('a', href=True)
                        if link_elements:
                            links = link_elements[0].get('href', '')
                            # If link is relative, make it absolute
                            if links and links.startswith('/'):
                                links = f"https://chartink.com{links}"
                            break
                    
                    # If no links found but we have a symbol, create a generic link
                    if not links and symbol != "N/A":
                        links = f"https://chartink.com/stocks/{symbol}.html"
                    
                    # Determine recommendation type
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
                logger.error(f"Error parsing table: {e}")
    
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
        
        # Update the last scan time for the site
        cursor.execute("UPDATE sites SET last_scan = ? WHERE id = ?", 
                      (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.site_id))
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
    """Send a notification to the Telegram user"""
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
              f"*Source:* ChartInk Scanner\n" \
              f"*Date:* {datetime.now().strftime('%Y-%m-%d')}\n"
    
    # Add link if available
    if recommendation['links']:
        message += f"*Link:* {recommendation['links']}\n"
    
    # Send the message
    try:
        send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_USER_ID,
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
    """Continuously scan ChartInk in the background every 5 minutes"""
    while True:
        try:
            # Get all active sites from the database
            conn = sqlite3.connect('recommendations.db')
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, name, url, selector, active, last_scan 
                FROM sites WHERE active = 1 AND url LIKE '%chartink%'
            """)
            sites = cursor.fetchall()
            conn.close()
            
            total_new_recommendations = 0
            
            # Scan each ChartInk site
            for site in sites:
                try:
                    site_id, name, url, selector, active, last_scan = site
                    
                    # Create ChartInk specific scraper
                    scraper = ChartInkScraper(site_id, name, url, selector)
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
                logger.info(f"Found {total_new_recommendations} new stock recommendations from ChartInk")
                
            # Wait for the next scan cycle
            logger.info("Scan cycle completed. Waiting for next cycle...")
            time.sleep(300)  # 5 minutes between scans
            
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
    """Add a new ChartInk scanner URL"""
    name = request.form.get('name')
    url = request.form.get('url')
    
    if name and url and 'chartink.com' in url:
        conn = sqlite3.connect('recommendations.db')
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO sites 
            (name, url, selector, is_table, active) 
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, url, 'table.table', 1, 1)
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
    """Trigger an immediate scan of ChartInk"""
    conn = sqlite3.connect('recommendations.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, name, url, selector, active
        FROM sites WHERE active = 1 AND url LIKE '%chartink%'
    """)
    sites = cursor.fetchall()
    conn.close()
    
    new_recommendations = []
    
    for site in sites:
        try:
            site_id, name, url, selector, active = site
            
            scraper = ChartInkScraper(site_id, name, url, selector)
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
    <title>ChartInk Stock Scanner</title>
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
        <h1 class="mb-4">ChartInk Stock Scanner</h1>
        
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
                                <h5 class="mb-0">ChartInk Stock Data</h5>
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
                                    No stock data found yet. Click "Scan Now" to start collecting data.
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
                           <!-- Rest of index.html template -->
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
                    <div class="col-md-6">
                        <div class="card mb-4">
                            <div class="card-header">
                                <h5 class="mb-0">ChartInk Scanner URLs</h5>
                            </div>
                            <div class="card-body">
                                <div class="table-responsive">
                                    <table class="table table-striped">
                                        <thead>
                                            <tr>
                                                <th>Name</th>
                                                <th>URL</th>
                                                <th>Last Scan</th>
                                                <th>Status</th>
                                                <th>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {% for site in sites %}
                                            <tr>
                                                <td>{{ site.name }}</td>
                                                <td><a href="{{ site.url }}" target="_blank">{{ site.url }}</a></td>
                                                <td>{{ site.last_scan or 'Never' }}</td>
                                                <td>
                                                    <span class="badge {{ 'bg-success' if site.active else 'bg-secondary' }}">
                                                        {{ 'Active' if site.active else 'Inactive' }}
                                                    </span>
                                                </td>
                                                <td>
                                                    <form method="post" action="{{ url_for('toggle_site', site_id=site.id) }}" class="d-inline">
                                                        <button type="submit" class="btn btn-sm {{ 'btn-outline-danger' if site.active else 'btn-outline-success' }}">
                                                            {{ 'Disable' if site.active else 'Enable' }}
                                                        </button>
                                                    </form>
                                                    <form method="post" action="{{ url_for('delete_site', site_id=site.id) }}" class="d-inline">
                                                        <button type="submit" class="btn btn-sm btn-danger" onclick="return confirm('Are you sure you want to delete this scanner?')">Delete</button>
                                                    </form>
                                                </td>
                                            </tr>
                                            {% endfor %}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="col-md-6">
                        <div class="card">
                            <div class="card-header">
                                <h5 class="mb-0">Add ChartInk Scanner</h5>
                            </div>
                            <div class="card-body">
                                <form method="post" action="{{ url_for('add_site') }}">
                                    <div class="mb-3">
                                        <label for="name" class="form-label">Scanner Name</label>
                                        <input type="text" class="form-control" id="name" name="name" required>
                                    </div>
                                    <div class="mb-3">
                                        <label for="url" class="form-label">ChartInk URL</label>
                                        <input type="url" class="form-control" id="url" name="url" placeholder="https://chartink.com/screener/..." required>
                                        <div class="form-text">Enter a valid ChartInk screener URL</div>
                                    </div>
                                    <button type="submit" class="btn btn-primary">Add Scanner</button>
                                </form>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net@1.11.5/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net-bs5@1.11.5/js/dataTables.bootstrap5.min.js"></script>
    <script>
        $(document).ready(function() {
            // Initialize DataTables
            $('#recommendationsTable').DataTable({
                order: [[1, 'desc']],
                pageLength: 25
            });
            
            $('#allRecommendationsTable').DataTable({
                order: [[0, 'desc']],
                pageLength: 50
            });
            
            // Handle scan now button
            $('#scanNowBtn').click(function() {
                const $btn = $(this);
                const $status = $('#scanStatus');
                
                $btn.prop('disabled', true).html('<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Scanning...');
                $status.removeClass('d-none alert-success alert-danger').addClass('alert-info').html('Scanning ChartInk for stock recommendations...');
                
                $.ajax({
                    url: '/scan/now',
                    method: 'POST',
                    success: function(response) {
                        if (response.success) {
                            $status.removeClass('alert-info').addClass('alert-success')
                                .html(`Scan completed! Found ${response.new_recommendations} new stock recommendations.`);
                            
                            // Reload page after 2 seconds to show new data
                            setTimeout(function() {
                                location.reload();
                            }, 2000);
                        } else {
                            $status.removeClass('alert-info').addClass('alert-danger')
                                .html('Error scanning ChartInk. Please try again.');
                        }
                    },
                    error: function() {
                        $status.removeClass('alert-info').addClass('alert-danger')
                            .html('Error scanning ChartInk. Please try again.');
                    },
                    complete: function() {
                        $btn.prop('disabled', false).html('Scan Now');
                    }
                });
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
    <title>Stock Recommendations - ChartInk Scanner</title>
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
        <nav aria-label="breadcrumb">
            <ol class="breadcrumb">
                <li class="breadcrumb-item"><a href="{{ url_for('index') }}">Home</a></li>
                <li class="breadcrumb-item active" aria-current="page">All Recommendations</li>
            </ol>
        </nav>
        
        <h1 class="mb-4">All Stock Recommendations</h1>
        
        <div class="card">
            <div class="card-body">
                <div class="table-responsive">
                    <table class="table table-hover recommendation-table" id="recommendationsTable">
                        <thead>
                            <tr>
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
    <script src="https://code.jquery.com/jquery-3.6.0.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net@1.11.5/js/jquery.dataTables.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/datatables.net-bs5@1.11.5/js/dataTables.bootstrap5.min.js"></script>
    <script>
        $(document).ready(function() {
            // Initialize DataTables
            $('#recommendationsTable').DataTable({
                order: [[0, 'desc']],
                pageLength: 50
            });
        });
    </script>
</body>
</html>
''')

# Now let's add the remaining code to run the application
if __name__ == "__main__":
    # Create templates
    create_templates()
    
    # Initialize database
    init_db()
    
    # Start background scanner thread
    scanner_thread = threading.Thread(target=background_scanner, daemon=True)
    scanner_thread.start()
    
    # Run Flask app
    app.run(host='0.0.0.0', port=5000, debug=True)
