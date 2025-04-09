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
    def __init__(self, site_id, name, url, selector=None, headers=None):
        """
        Initialize the scraper with the target URL and optional headers.
        
        Args:
            site_id (int): Database ID of the site
            name (str): Name of the site
            url (str): The target website URL to scrape
            selector (str, optional): CSS selector for finding recommendations
            headers (dict, optional): HTTP headers to use for the request
        """
        self.site_id = site_id
        self.name = name
        self.url = url
        self.selector = selector
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
                ticker = ticker.text.strip() if ticker else "N/A"
                
                company = element.select_one('.company, .name')
                company = company.text.strip() if company else "N/A"
                
                price = element.select_one('.price, .current-price')
                price = price.text.strip() if price else "N/A"
                
                target = element.select_one('.target, .target-price')
                target = target.text.strip() if target else "N/A"
                
                analyst = element.select_one('.analyst, .source')
                analyst = analyst.text.strip() if analyst else "N/A"
                
                date = element.select_one('.date, .pub-date')
                date = date.text.strip() if date else datetime.now().strftime("%Y-%m-%d")
                
                # Determine if it's a buy or sell recommendation
                rec_type_elem = element.select_one('.recommendation-type, .rec-type, .rating')
                rec_type = "Unknown"
                if rec_type_elem:
                    rec_text = rec_type_elem.text.lower()
                    if any(term in rec_text for term in ['buy', 'long', 'bullish', 'overweight']):
                        rec_type = "Buy"
                    elif any(term in rec_text for term in ['sell', 'short', 'bearish', 'underweight']):
                        rec_type = "Sell"
                
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
                    'hash': unique_hash,
                    'processed_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                
                self.recommendations.append(recommendation)
            except Exception as e:
                logger.error(f"Error parsing recommendation element on {self.name}: {e}")
    
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
                    (site_id, ticker, company, current_price, target_price, recommendation_type, analyst, date, hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        rec['site_id'], rec['ticker'], rec['company'], rec['current_price'], 
                        rec['target_price'], rec['recommendation_type'], rec['analyst'], rec['date'], rec['hash']
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
    
    # Format the message
    emoji = "🟢" if recommendation['recommendation_type'] == "Buy" else "🔴" if recommendation['recommendation_type'] == "Sell" else "⚪"
    message = f"{emoji} *NEW {recommendation['recommendation_type'].upper()} RECOMMENDATION*\n\n" \
              f"*Ticker:* {recommendation['ticker']}\n" \
              f"*Company:* {recommendation['company']}\n" \
              f"*Current Price:* {recommendation['current_price']}\n" \
              f"*Target Price:* {recommendation['target_price']}\n" \
              f"*Analyst:* {recommendation['analyst']}\n" \
              f"*Date:* {recommendation['date']}\n"
    
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
            cursor.execute("SELECT id, name, url, selector FROM sites WHERE active = 1")
            sites = cursor.fetchall()
            conn.close()
            
            total_new_recommendations = 0
            
            # Scan each site
            for site_id, name, url, selector in sites:
                try:
                    scraper = RecommendationScraper(site_id, name, url, selector)
                    new_recommendations = scraper.run()
                    
                    # Send Telegram notifications for new recommendations
                    for rec in new_recommendations:
                        send_telegram_notification(rec)
                        total_new_recommendations += 1
                except Exception as e:
                    logger.error(f"Error scanning site {name}: {e}")
                
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
    
    if name and url:
        conn = sqlite3.connect('recommendations.db')
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sites (name, url, selector) VALUES (?, ?, ?)",
            (name, url, selector)
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
    cursor.execute("SELECT id, name, url, selector FROM sites WHERE active = 1")
    sites = cursor.fetchall()
    conn.close()
    
    new_recommendations = []
    
    for site_id, name, url, selector in sites:
        try:
            scraper = RecommendationScraper(site_id, name, url, selector)
            site_new_recommendations = scraper.run()
            
            # Send Telegram notifications for new recommendations
            for rec in site_new_recommendations:
                send_telegram_notification(rec)
                new_recommendations.append(rec)
        except Exception as e:
            logger.error(f"Error scanning site {name}: {e}")
    
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
    <style>
        body { padding-top: 20px; }
        .recommendation-card { margin-bottom: 10px; }
        .buy { border-left: 4px solid green; }
        .sell { border-left: 4px solid red; }
        .unknown { border-left: 4px solid gray; }
    </style>
</head>
<body>
    <div class="container">
        <h1 class="mb-4">Stock Recommendation Tracker</h1>
        
        <div class="row">
            <div class="col-md-6">
                <div class="card mb-4">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <h5 class="mb-0">Sites to Monitor</h5>
                        <button class="btn btn-sm btn-primary" data-bs-toggle="modal" data-bs-target="#addSiteModal">Add Site</button>
                    </div>
                    <div class="card-body">
                        {% if sites %}
                            <div class="table-responsive">
                                <table class="table table-hover">
                                    <thead>
                                        <tr>
                                            <th>Name</th>
                                            <th>URL</th>
                                            <th>Status</th>
                                            <th>Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {% for site in sites %}
                                            <tr>
                                                <td>{{ site.name }}</td>
                                                <td><a href="{{ site.url }}" target="_blank">{{ site.url | truncate(30) }}</a></td>
                                                <td>
                                                    <span class="badge {{ 'bg-success' if site.active else 'bg-danger' }}">
                                                        {{ 'Active' if site.active else 'Inactive' }}
                                                    </span>
                                                </td>
                                                <td>
                                                    <form method="POST" action="/site/toggle/{{ site.id }}" class="d-inline">
                                                        <button type="submit" class="btn btn-sm {{ 'btn-danger' if site.active else 'btn-success' }}">
                                                            {{ 'Deactivate' if site.active else 'Activate' }}
                                                        </button>
                                                    </form>
                                                    <form method="POST" action="/site/delete/{{ site.id }}" class="d-inline">
                                                        <button type="submit" class="btn btn-sm btn-danger">Delete</button>
                                                    </form>
                                                </td>
                                            </tr>
                                        {% endfor %}
                                    </tbody>
                                </table>
                            </div>
                        {% else %}
                            <p class="text-center">No sites added yet. Add your first site to begin monitoring.</p>
                        {% endif %}
                    </div>
                    <div class="card-footer">
                        <button id="scanNowBtn" class="btn btn-warning">Scan Now</button>
                        <span id="scanStatus" class="ms-2"></span>
                    </div>
                </div>
            </div>
            
            <div class="col-md-6">
                <div class="card">
                    <div class="card-header d-flex justify-content-between align-items-center">
                        <h5 class="mb-0">Recent Recommendations</h5>
                        <a href="/recommendations" class="btn btn-sm btn-info">View All</a>
                    </div>
                    <div class="card-body">
                        {% if recommendations %}
                            {% for rec in recommendations[:10] %}
                                <div class="card recommendation-card {{ rec.recommendation_type.lower() }}">
                                    <div class="card-body">
                                        <h5 class="card-title">
                                            {{ rec.ticker }} - {{ rec.company }}
                                            <span class="badge {{ 'bg-success' if rec.recommendation_type == 'Buy' else 'bg-danger' if rec.recommendation_type == 'Sell' else 'bg-secondary' }}">
                                                {{ rec.recommendation_type }}
                                            </span>
                                        </h5>
                                        <h6 class="card-subtitle mb-2 text-muted">{{ rec.site_name }} - {{ rec.analyst }}</h6>
                                        <p class="card-text">
                                            Current: {{ rec.current_price }} | Target: {{ rec.target_price }} | Date: {{ rec.date }}
                                        </p>
                                    </div>
                                </div>
                            {% endfor %}
                        {% else %}
                            <p class="text-center">No recommendations found yet.</p>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Add Site Modal -->
    <div class="modal fade" id="addSiteModal" tabindex="-1" aria-hidden="true">
        <div class="modal-dialog">
            <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">Add New Site</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                </div>
                <form method="POST" action="/site/add">
                    <div class="modal-body">
                        <div class="mb-3">
                            <label for="name" class="form-label">Site Name</label>
                            <input type="text" class="form-control" id="name" name="name" required>
                        </div>
                        <div class="mb-3">
                            <label for="url" class="form-label">URL</label>
                            <input type="url" class="form-control" id="url" name="url" required>
                        </div>
                        <div class="mb-3">
                            <label for="selector" class="form-label">CSS Selector (optional)</label>
                            <input type="text" class="form-control" id="selector" name="selector" placeholder=".recommendation-card">
                            <div class="form-text">CSS selector to identify recommendation elements on the page</div>
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
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        document.getElementById('scanNowBtn').addEventListener('click', function() {
            const statusEl = document.getElementById('scanStatus');
            statusEl.textContent = 'Scanning...';
            
            fetch('/scan/now', {
                method: 'POST'
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    statusEl.textContent = `Scan complete! Found ${data.new_recommendations} new recommendations.`;
                    if (data.new_recommendations > 0) {
                        setTimeout(() => {
                            location.reload();
                        }, 2000);
                    }
                } else {
                    statusEl.textContent = 'Error during scan.';
                }
            })
            .catch(error => {
                statusEl.textContent = 'Error: ' + error;
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
    <title>All Recommendations</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { padding-top: 20px; }
        .buy { background-color: rgba(0, 255, 0, 0.1); }
        .sell { background-color: rgba(255, 0, 0, 0.1); }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>All Stock Recommendations</h1>
            <a href="/" class="btn btn-primary">Back to Dashboard</a>
        </div>
        
        <div class="card">
            <div class="card-body">
                <div class="table-responsive">
                    <table class="table table-hover" id="recommendationsTable">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Site</th>
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
                                        <span class="badge {{ 'bg-success' if rec.recommendation_type == 'Buy' else 'bg-danger' if rec.recommendation_type == 'Sell' else 'bg-secondary' }}">
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
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>
            ''')


# Main entry point
if __name__ == "__main__":
    # Create HTML templates
    create_templates()
    
    # Start the background scanner thread
    scanner_thread = threading.Thread(target=background_scanner, daemon=True)
    scanner_thread.start()
    
    # Start the Flask app
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
