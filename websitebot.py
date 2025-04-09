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
        current_price REAL,
        pct_change TEXT,
        volume TEXT,
        volume_sma REAL,
        vol_by_sma REAL,
        market_cap TEXT,
        year_high REAL,
        year_low REAL,
        pe_ratio REAL,
        industry TEXT,
        recommendation_type TEXT,
        raw_data TEXT,
        hash TEXT UNIQUE,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (site_id) REFERENCES sites (id)
    )
    ''')
    
    # Add chartink.com - Close Above 20 EMA if it doesn't exist
    cursor.execute("SELECT id FROM sites WHERE url = 'https://chartink.com/screener/close-above-20-ema'")
    if not cursor.fetchone():
        cursor.execute('''
        INSERT INTO sites (name, url, selector, is_table, active)
        VALUES (?, ?, ?, ?, ?)
        ''', ('Close Above 20 EMA', 'https://chartink.com/screener/close-above-20-ema', 'table.table', 1, 1))
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

class ChartInkScraper:
    def __init__(self, site_id, name, url, selector='div.table-responsive table'):
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
        """Fetch the ChartInk page content"""
        try:
            logger.info(f"Fetching data from ChartInk at {self.url}")
            
            # First, get the main page
            response = requests.get(self.url, headers=self.headers, timeout=15)
            response.raise_for_status()
            
            # Check if it's a scanner URL
            if '/screener/' in self.url:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # For ChartInk, we need to get the condition clause and CSRF token
                scan_clause = None
                
                # Try to find scan clause in URL or page
                if '/' in self.url.split('/screener/')[-1]:
                    # It's likely a named scanner with a specific condition
                    script_tags = soup.find_all('script')
                    for script in script_tags:
                        script_text = script.string
                        if script_text and 'var scan_clause' in script_text:
                            match = re.search(r'var\s+scan_clause\s*=\s*[\'"](.+?)[\'"]', script_text)
                            if match:
                                scan_clause = match.group(1)
                                logger.info(f"Found scan clause in script: {scan_clause}")
                                break
                
                # If we couldn't find it in the script, let's check for an input field
                if not scan_clause:
                    scan_input = soup.select_one('textarea#scan_clause')
                    if scan_input:
                        scan_clause = scan_input.get('value', '')
                        logger.info(f"Found scan clause in textarea: {scan_clause}")
                
                # For "Close Above 20 EMA" specifically
                if "close-above-20-ema" in self.url and not scan_clause:
                    scan_clause = '( {close} > ema(close,20) )'
                    logger.info(f"Using hardcoded scan clause for Close Above 20 EMA: {scan_clause}")
                
                # Default scan clause if we can't find anything
                if not scan_clause:
                    scan_clause = '( {close} > ema(close,20) )'
                    logger.info(f"Using default scan clause: {scan_clause}")
                
                # Get the CSRF token
                csrf_token = soup.select_one('meta[name="csrf-token"]')
                
                if csrf_token:
                    headers_with_csrf = self.headers.copy()
                    headers_with_csrf['X-CSRF-TOKEN'] = csrf_token['content']
                    headers_with_csrf['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'
                    headers_with_csrf['X-Requested-With'] = 'XMLHttpRequest'
                    
                    # Make the API request to get scanner data
                    scanner_url = 'https://chartink.com/screener/process'
                    payload = {
                        'scan_clause': scan_clause
                    }
                    
                    scanner_response = requests.post(
                        scanner_url, 
                        headers=headers_with_csrf,
                        data=payload,
                        timeout=15
                    )
                    
                    if scanner_response.status_code == 200:
                        logger.info(f"Successfully fetched scanner data from ChartInk API")
                        return scanner_response.text
            
            # Fallback to the regular page response
            logger.info(f"Using regular page content from ChartInk")
            return response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching ChartInk page: {e}")
            return None
    
    def parse_recommendations(self, content):
        """
        Parse stock information from ChartInk content.
        
        Args:
            content (str): The HTML or JSON content to parse
        """
        if not content:
            return
        
        try:
            # First, try to parse as JSON (ChartInk API response)
            try:
                data = json.loads(content)
                self._parse_json_data(data)
                return
            except json.JSONDecodeError:
                # Not JSON, try HTML parsing
                pass
            
            # If not JSON, parse as HTML
            soup = BeautifulSoup(content, 'html.parser')
            self._parse_html_data(soup)
            
        except Exception as e:
            logger.error(f"Error parsing ChartInk data: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _parse_json_data(self, data):
        """Parse stock data from ChartInk JSON response"""
        try:
            if 'data' in data:
                # Process data from ChartInk API
                for i, stock_data in enumerate(data['data']):
                    sr_num = str(i + 1)
                    
                    # Extract stock information
                    symbol = stock_data.get('nsecode', '')
                    if not symbol:
                        symbol = stock_data.get('name', '').split('-')[0].strip()
                    
                    stock_name = stock_data.get('name', '')
                    
                    # Extract metrics (adding new fields from ChartInk)
                    current_price = self._extract_number(stock_data.get('close', 'N/A'))
                    pct_change = stock_data.get('per_chg', 'N/A')
                    if pct_change != 'N/A':
                        pct_change = f"{pct_change}%"
                    
                    volume = stock_data.get('volume', 'N/A')
                    volume_sma = self._extract_number(stock_data.get('volume_sma_20', 'N/A'))
                    vol_by_sma = self._extract_number(stock_data.get('volume_ratio', 'N/A'))
                    market_cap = stock_data.get('mcap', 'N/A')
                    
                    # Try to get 52-week high/low
                    year_high = self._extract_number(stock_data.get('high_52w', 'N/A'))
                    year_low = self._extract_number(stock_data.get('low_52w', 'N/A'))
                    
                    # Get PE ratio and industry if available
                    pe_ratio = self._extract_number(stock_data.get('pe', 'N/A'))
                    industry = stock_data.get('industry', 'N/A')
                    
                    # Link to the stock detail page
                    links = f"https://chartink.com/stocks/{symbol}.html" if symbol else ""
                    
                    # Determine recommendation type based on % change
                    rec_type = self._determine_recommendation_type(pct_change, current_price, volume_sma)
                    
                    # Create raw data
                    raw_data = json.dumps(stock_data)
                    
                    # Create hash for uniqueness checking
                    unique_data = f"{symbol}|{stock_name}|{current_price}|{pct_change}|{datetime.now().strftime('%Y-%m-%d')}"
                    unique_hash = hashlib.md5(unique_data.encode()).hexdigest()
                    
                    recommendation = {
                        'site_id': self.site_id,
                        'sr_num': sr_num,
                        'stock_name': stock_name,
                        'symbol': symbol,
                        'links': links,
                        'current_price': current_price,
                        'pct_change': pct_change,
                        'volume': volume,
                        'volume_sma': volume_sma,
                        'vol_by_sma': vol_by_sma,
                        'market_cap': market_cap,
                        'year_high': year_high,
                        'year_low': year_low,
                        'pe_ratio': pe_ratio,
                        'industry': industry,
                        'recommendation_type': rec_type,
                        'raw_data': raw_data,
                        'hash': unique_hash,
                        'processed_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    
                    self.recommendations.append(recommendation)
                    
                logger.info(f"Parsed {len(data['data'])} records from JSON data")
        except Exception as e:
            logger.error(f"Error parsing JSON data: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _extract_number(self, value):
        """Extract number from string or return None"""
        if value in ('N/A', '', None):
            return None
        try:
            # Remove commas and convert to float
            if isinstance(value, str):
                value = value.replace(',', '')
            return float(value)
        except (ValueError, TypeError):
            return None
    
    def _parse_html_data(self, soup):
        """Parse stock data from HTML content"""
        tables = []
        
        # First try with the specific selector
        if self.selector:
            tables = soup.select(self.selector)
        
        # If no tables found with the selector, try different approaches
        if not tables:
            # Try common table classes
            tables = soup.select('table.table')
        
        if not tables:
            # Try any table in the document
            tables = soup.find_all('table')
            
        if not tables:
            # Try looking for tables within any div with class containing 'table'
            div_tables = soup.select('div[class*="table"]')
            for div in div_tables:
                inner_tables = div.find_all('table')
                if inner_tables:
                    tables.extend(inner_tables)
        
        # If still no tables, let's look for stock data directly
        if not tables:
            self._parse_stock_data_from_text(soup)
            return
        
        for table in tables:
            self._parse_table(table)
    
    def _parse_table(self, table):
        """Parse a single table for stock data"""
        try:
            # Get all rows from the table
            rows = table.find_all('tr')
            if not rows:
                return
                
            # Process header row
            header_row = rows[0]
            headers = [self._clean_text(th.text) for th in header_row.find_all(['th', 'td'])]
            
            # Find columns of interest - specific to ChartInk columns
            symbol_col = self._find_column_index(headers, ['nsecode', 'symbol', 'ticker', 'code', 'scrip', 'stock'])
            name_col = self._find_column_index(headers, ['name', 'company', 'stock name', 'description', 'security name'])
            
            # ChartInk specific columns
            price_col = self._find_column_index(headers, ['close', 'price', 'ltp', 'last', 'cmp', 'current'])
            pct_chg_col = self._find_column_index(headers, ['%', 'change', 'chg', 'per chg', 'per_chg', 'perc', 'percentage', '%change'])
            volume_col = self._find_column_index(headers, ['volume', 'vol', 'quantity', 'qty'])
            vol_sma_col = self._find_column_index(headers, ['vol_sma20', 'volume_sma', 'avg volume', 'volume_sma_20'])
            vol_ratio_col = self._find_column_index(headers, ['vol/sma20', 'vol_ratio', 'volume_ratio', 'vol/sma', 'vol_by_sma'])
            mcap_col = self._find_column_index(headers, ['mcap', 'market cap', 'market_cap'])
            
            # 52-week high/low columns
            high_52_col = self._find_column_index(headers, ['high_52w', '52w h', '52 week high', '52wk high', 'year high'])
            low_52_col = self._find_column_index(headers, ['low_52w', '52w l', '52 week low', '52wk low', 'year low'])
            
            # PE ratio and industry
            pe_col = self._find_column_index(headers, ['pe', 'pe ratio', 'pe_ratio', 'p/e'])
            industry_col = self._find_column_index(headers, ['industry', 'sector', 'segment'])
            
            # If we couldn't identify symbol column, try using indices based on typical ChartInk layout
            if symbol_col == -1:
                # ChartInk often has symbol in first column
                symbol_col = 0
            
            if name_col == -1 and len(headers) > 1:
                # Company name often in second column
                name_col = 1
            
            logger.info(f"Identified columns - Symbol: {symbol_col}, Name: {name_col}, Price: {price_col}, %Change: {pct_chg_col}")
            
            # Skip header row and process data rows
            for i, row in enumerate(rows[1:], 1):
                cells = row.find_all(['td', 'th'])
                if len(cells) < max(filter(lambda x: x != -1, [symbol_col, name_col, price_col, pct_chg_col])) + 1:
                    continue  # Skip rows with insufficient cells
                
                try:
                    # Extract data from identified columns
                    sr_num = str(i)
                    
                    symbol = "N/A"
                    if symbol_col >= 0 and symbol_col < len(cells):
                        cell_text = self._clean_text(cells[symbol_col].text)
                        # Check if the symbol might be in a link
                        link = cells[symbol_col].find('a')
                        if link and link.has_attr('href'):
                            href = link['href']
                            # Extract symbol from href if it's a stock link
                            if '/stocks/' in href:
                                symbol_match = re.search(r'/stocks/([^/]+)\.html', href)
                                if symbol_match:
                                    symbol = symbol_match.group(1)
                                else:
                                    symbol = cell_text
                            else:
                                symbol = cell_text
                        else:
                            symbol = cell_text
                    
                    stock_name = "N/A"
                    if name_col >= 0 and name_col < len(cells):
                        stock_name = self._clean_text(cells[name_col].text)
                    
                    # Get current price if available
                    current_price = None
                    if price_col >= 0 and price_col < len(cells):
                        price_text = self._clean_text(cells[price_col].text)
                        current_price = self._extract_number(price_text)
                    
                    pct_change = "N/A"
                    if pct_chg_col >= 0 and pct_chg_col < len(cells):
                        pct_change = self._clean_text(cells[pct_chg_col].text)
                        if pct_change and not '%' in pct_change:
                            try:
                                # Try to format as percentage if not already
                                float_val = float(pct_change.replace(',', ''))
                                pct_change = f"{float_val}%"
                            except:
                                pass
                    
                    # Extract volume metrics
                    volume = "N/A"
                    if volume_col >= 0 and volume_col < len(cells):
                        volume = self._clean_text(cells[volume_col].text)
                    
                    volume_sma = None
                    if vol_sma_col >= 0 and vol_sma_col < len(cells):
                        vol_sma_text = self._clean_text(cells[vol_sma_col].text)
                        volume_sma = self._extract_number(vol_sma_text)
                    
                    vol_by_sma = None
                    if vol_ratio_col >= 0 and vol_ratio_col < len(cells):
                        vol_ratio_text = self._clean_text(cells[vol_ratio_col].text)
                        vol_by_sma = self._extract_number(vol_ratio_text)
                    
                    # Extract market cap
                    market_cap = "N/A"
                    if mcap_col >= 0 and mcap_col < len(cells):
                        market_cap = self._clean_text(cells[mcap_col].text)
                    
                    # Extract 52-week high/low
                    year_high = None
                    if high_52_col >= 0 and high_52_col < len(cells):
                        high_text = self._clean_text(cells[high_52_col].text)
                        year_high = self._extract_number(high_text)
                    
                    year_low = None
                    if low_52_col >= 0 and low_52_col < len(cells):
                        low_text = self._clean_text(cells[low_52_col].text)
                        year_low = self._extract_number(low_text)
                    
                    # Extract PE ratio and industry
                    pe_ratio = None
                    if pe_col >= 0 and pe_col < len(cells):
                        pe_text = self._clean_text(cells[pe_col].text)
                        pe_ratio = self._extract_number(pe_text)
                    
                    industry = "N/A"
                    if industry_col >= 0 and industry_col < len(cells):
                        industry = self._clean_text(cells[industry_col].text)
                    
                    # Extract links if present
                    links = ""
                    for cell in cells:
                        link_elements = cell.find_all('a', href=True)
                        if link_elements:
                            href = link_elements[0].get('href', '')
                            if '/stocks/' in href:
                                links = href
                                # If link is relative, make it absolute
                                if links and links.startswith('/'):
                                    links = f"https://chartink.com{links}"
                                break
                    
                    # If no links found but we have a symbol, create a generic link
                    if not links and symbol != "N/A":
                        links = f"https://chartink.com/stocks/{symbol}.html"
                    
                    # Determine recommendation type
                    rec_type = self._determine_recommendation_type(pct_change, current_price, volume_sma)
                    
                    # Get all cell text for raw data
                    raw_data = " | ".join([self._clean_text(cell.text) for cell in cells])
                    
                    # Create hash for uniqueness checking
                    unique_data = f"{symbol}|{stock_name}|{current_price}|{pct_change}|{datetime.now().strftime('%Y-%m-%d')}"
                    unique_hash = hashlib.md5(unique_data.encode()).hexdigest()
                    
                    recommendation = {
                        'site_id': self.site_id,
                        'sr_num': sr_num,
                        'stock_name': stock_name,
                        'symbol': symbol,
                        'links': links,
                        'current_price': current_price,
                        'pct_change': pct_change,
                        'volume': volume,
                        'volume_sma': volume_sma,
                        'vol_by_sma': vol_by_sma,
                        'market_cap': market_cap,
                        'year_high': year_high,
                        'year_low': year_low,
                        'pe_ratio': pe_ratio,
                        'industry': industry,
                        'recommendation_type': rec_type,
                        'raw_data': raw_data,
                        'hash': unique_hash,
                        'processed_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    }
                    
                    self.recommendations.append(recommendation)
                except Exception as e:
                    logger.error(f"Error processing row {i}: {e}")
                    
            logger.info(f"Parsed {len(self.recommendations)} records from table")
        except Exception as e:
            logger.error(f"Error parsing table: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def _find_column_index(self, headers, possible_names):
        """Find column index based on possible column names"""
        for i, header in enumerate(headers):
            header_lower = header.lower()
            for name in possible_names:
                if name.lower() in header_lower:
                    return i
        return -1
    
    def _parse_stock_data_from_text(self, soup):
        """
        Try to find stock data directly in text when tables aren't available
        """
        # Look for stock entries in text format
        stock_entries = soup.select('div.stock-entry, div.stock-item, div.stock-card')
        if not stock_entries:
            return
        
        for i, entry in enumerate(stock_entries, 1):
            try:
                # Extract info from the entry
                symbol_elem = entry.select_one('.symbol, .stock-code, .ticker')
                name_elem = entry.select_one('.name, .company-name, .stock-name')
                price_elem = entry.select_one('.price, .close, .current')
                change_elem = entry.select_one('.change, .pct-change, .percentage')
                volume_elem = entry.select_one('.volume, .vol')
                vol_sma_elem = entry.select_one('.vol-sma, .volume-sma')
                vol_ratio_elem = entry.select_one('.vol-ratio, .volume-ratio')
                mcap_elem = entry.select_one('.mcap, .market-cap')
                
                symbol = self._clean_text(symbol_elem.text) if symbol_elem else "N/A"
                stock_name = self._clean_text(name_elem.text) if name_elem else "N/A"
                current_price = self._extract_number(price_elem.text) if price_elem else None
                pct_change = self._clean_text(change_elem.text) if change_elem else "N/A"
                volume = self._clean_text(volume_elem.text) if volume_elem else "N/A"
                volume_sma = self._extract_number(vol_sma_elem.text) if vol_sma_elem else None
                vol_by_sma = self._extract_number(vol_ratio_elem.text) if vol_ratio_elem else None
                market_cap = self._clean_text(mcap_elem.text) if mcap_elem else "N/A"
                
                # Look for links
                link_elem = entry.find('a', href=True)
                links = link_elem['href'] if link_elem else ""
                if links and links.startswith('/'):
                    links = f"https://chartink.com{links}"
                
                # If no links found but we have a symbol, create a generic link
                if not links and symbol != "N/A":
                    links = f"https://chartink.com/stocks/{symbol}.html"
                
                # Determine recommendation type
                rec_type = self._determine_recommendation_type(pct_change, current_price, volume_sma)
                
                # Get raw text
                raw_data = self._clean_text(entry.text)
                
                # Create hash
                unique_data = f"{symbol}|{stock_name}|{current_price}|{pct_change}|{datetime.now().strftime('%Y-%m-%d')}"
                unique_hash = hashlib.md5(unique_data.encode()).hexdigest()
                
                recommendation = {
                    'site_id': self.site_id,
                    'sr_num': str(i),
                    'stock_name': stock_name,
                    'symbol': symbol,
                    'links': links,
                    'current_price': current_price,
                    'pct_change': pct_change,
                    'volume': volume,
                    'volume_sma': volume_sma,
                    'vol_by_sma': vol_by_sma,
                    'market_cap': market_cap,
                    'year_high': None,
                    'year_low': None,
                    'pe_ratio': None,
                    'industry': "N/A",
                    'recommendation_type': rec_type,
                    'raw_data': raw_data,
                    'hash': unique_hash,
                    'processed_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                
                self.recommendations.append(recommendation)
            except Exception as e:
                logger.error(f"Error parsing stock entry {i}: {e}")
    
    def _clean_text(self, text):
        """Clean and normalize text from HTML"""
        if not text:
            return ""
        text = text.strip()
        text = re.sub(r'\s+', ' ', text)  # Replace multiple spaces with single space
        return text
    
    def _determine_recommendation_type(self, pct_change, current_price=None, volume_sma=None):
        """
        Enhanced recommendation type determination based on:
        - Percentage change
        - Price compared to 20 EMA (implied by the scanner "Close Above 20 EMA")
        - Volume compared to its average
        """
        # Default recommendation is Unknown
        rec_type = "Unknown"
        
        # Extract percentage change value if possible
        pct_change_val = None
        if pct_change and pct_change != "N/A":
            match = re.search(r'([+-]?\d+\.?\d*)%?', pct_change)
            if match:
                try:
                    pct_change_val = float(match.group(1))
                except ValueError:
                    pass
        
        # Enhanced logic using multiple factors
        if pct_change_val is not None:
            # If price is above 20 EMA (this is implied by the scanner name)
            # and good percentage change
            if pct_change_val > 2.0:
                rec_type = "Strong Buy"
            elif pct_change_val > 0.5:
                rec_type = "Buy"
            elif pct_change_val > -0.5:
                rec_type = "Hold"
            elif pct_change_val > -2.0:
                rec_type = "Weak Sell"
            else:
                rec_type = "Sell"
                
        # Consider volume if available
        if volume_sma is not None and rec_type in ["Buy", "Strong Buy"]:
            # If volume is significantly higher than average, strengthen buy recommendation
            if isinstance(volume_sma, (int, float)) and volume_sma > 1.5:
                rec_type = "Strong Buy"
        
        return rec_type
    
def save_recommendations(self):
     """Save recommendations to database and return new ones"""
        new_recommendations = []
        conn = sqlite3.connect('recommendations.db')
        cursor = conn.cursor()
    
    try:
        # Update last scan time for the site
        cursor.execute(
            "UPDATE sites SET last_scan = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.site_id)
        )
        
        # Process and save each recommendation
        for rec in self.recommendations:
            try:
                # Check if this recommendation already exists (by hash)
                cursor.execute(
                    "SELECT id FROM recommendations WHERE hash = ?", 
                    (rec['hash'],)
                )
                existing = cursor.fetchone()
                
                if not existing:
                    # Insert new recommendation
                    cursor.execute('''
                    INSERT INTO recommendations (
                        site_id, sr_num, stock_name, symbol, links, 
                        current_price, pct_change, volume, volume_sma, 
                        vol_by_sma, market_cap, year_high, year_low, 
                        pe_ratio, industry, recommendation_type, raw_data, hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        rec['site_id'], rec['sr_num'], rec['stock_name'], 
                        rec['symbol'], rec['links'], rec['current_price'], 
                        rec['pct_change'], rec['volume'], rec['volume_sma'], 
                        rec['vol_by_sma'], rec['market_cap'], rec['year_high'], 
                        rec['year_low'], rec['pe_ratio'], rec['industry'], 
                        rec['recommendation_type'], rec['raw_data'], rec['hash']
                    ))
                    
                    # Add to new recommendations list for notification
                    new_recommendations.append(rec)
                    logger.info(f"Added new recommendation: {rec['symbol']} - {rec['stock_name']}")
            except Exception as e:
                logger.error(f"Error saving recommendation {rec.get('symbol', 'unknown')}: {e}")
                continue
        
        conn.commit()
        return new_recommendations
    except Exception as e:
        logger.error(f"Database error in save_recommendations: {e}")
        conn.rollback()
        return []
    finally:
        conn.close()
        
# Add Telegram notification functionality
def send_telegram_notification(message):
    """Send notification via Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_USER_ID:
        logger.warning("Telegram credentials not set. Skipping notification.")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_USER_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
        logger.info("Telegram notification sent successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")
        return False

# Complete the Flask routes
@app.route('/')
def index():
    """Home page showing list of active sites and recent recommendations"""
    conn = sqlite3.connect('recommendations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all active sites
    cursor.execute("SELECT * FROM sites WHERE active = 1 ORDER BY name")
    sites = cursor.fetchall()
    
    # Get recent recommendations (last 24 hours)
    cursor.execute('''
    SELECT r.*, s.name as site_name 
    FROM recommendations r
    JOIN sites s ON r.site_id = s.id
    WHERE r.processed_at > datetime('now', '-1 day')
    ORDER BY r.processed_at DESC
    LIMIT 50
    ''')
    recommendations = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        'index.html', 
        sites=sites, 
        recommendations=recommendations,
        current_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

@app.route('/scan/<int:site_id>')
def scan_site(site_id):
    """Manually trigger a scan for a specific site"""
    conn = sqlite3.connect('recommendations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get site details
    cursor.execute("SELECT * FROM sites WHERE id = ?", (site_id,))
    site = cursor.fetchone()
    
    if not site:
        conn.close()
        return jsonify({'error': 'Site not found'}), 404
    
    conn.close()
    
    # Run the scraper
    try:
        scraper = ChartInkScraper(
            site_id=site['id'],
            name=site['name'],
            url=site['url'],
            selector=site['selector'] if site['selector'] else 'div.table-responsive table'
        )
        
        content = scraper.fetch_page()
        if content:
            scraper.parse_recommendations(content)
            new_recommendations = scraper.save_recommendations()
            
            # Send notification if new recommendations found
            if new_recommendations:
                message = f"<b>New stock recommendations from {site['name']}</b>\n\n"
                for i, rec in enumerate(new_recommendations[:5], 1):  # Limit to 5 for brevity
                    message += f"{i}. <b>{rec['symbol']}</b> - {rec['stock_name']}\n"
                    message += f"   Price: {rec['current_price']} ({rec['pct_change']})\n"
                    message += f"   Recommendation: {rec['recommendation_type']}\n\n"
                
                if len(new_recommendations) > 5:
                    message += f"...and {len(new_recommendations) - 5} more recommendations."
                
                send_telegram_notification(message)
            
            return jsonify({
                'success': True,
                'message': f"Scan completed for {site['name']}",
                'new_recommendations': len(new_recommendations)
            })
        else:
            return jsonify({
                'success': False,
                'message': f"Failed to fetch content from {site['url']}"
            }), 500
    except Exception as e:
        logger.error(f"Error in scan_site: {e}")
        return jsonify({
            'success': False,
            'message': f"Error: {str(e)}"
        }), 500

@app.route('/sites', methods=['GET', 'POST'])
def manage_sites():
    """Manage site configurations"""
    conn = sqlite3.connect('recommendations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    if request.method == 'POST':
        # Add or update site
        site_id = request.form.get('id')
        name = request.form.get('name')
        url = request.form.get('url')
        selector = request.form.get('selector')
        is_table = 1 if request.form.get('is_table') == 'on' else 0
        active = 1 if request.form.get('active') == 'on' else 0
        
        if site_id:
            # Update existing site
            cursor.execute('''
            UPDATE sites 
            SET name = ?, url = ?, selector = ?, is_table = ?, active = ?
            WHERE id = ?
            ''', (name, url, selector, is_table, active, site_id))
            message = f"Site '{name}' updated successfully"
        else:
            # Add new site
            cursor.execute('''
            INSERT INTO sites (name, url, selector, is_table, active)
            VALUES (?, ?, ?, ?, ?)
            ''', (name, url, selector, is_table, active))
            message = f"Site '{name}' added successfully"
        
        conn.commit()
        conn.close()
        return redirect(url_for('manage_sites'))
    
    # GET request - show list of sites
    cursor.execute("SELECT * FROM sites ORDER BY name")
    sites = cursor.fetchall()
    conn.close()
    
    return render_template('sites.html', sites=sites)

@app.route('/sites/delete/<int:site_id>', methods=['POST'])
def delete_site(site_id):
    """Delete a site configuration"""
    conn = sqlite3.connect('recommendations.db')
    cursor = conn.cursor()
    
    # Get site name for confirmation message
    cursor.execute("SELECT name FROM sites WHERE id = ?", (site_id,))
    site = cursor.fetchone()
    
    if site:
        site_name = site[0]
        
        # Delete site
        cursor.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f"Site '{site_name}' deleted successfully"
        })
    else:
        conn.close()
        return jsonify({
            'success': False,
            'message': "Site not found"
        }), 404

@app.route('/recommendations')
def view_recommendations():
    """View all recommendations with filtering options"""
    conn = sqlite3.connect('recommendations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get filter parameters
    site_id = request.args.get('site_id', type=int)
    symbol = request.args.get('symbol', '')
    rec_type = request.args.get('type', '')
    days = request.args.get('days', 7, type=int)
    
    # Build query conditions
    conditions = ["r.processed_at > datetime('now', ? || ' days')"]
    params = [f"-{days}"]
    
    if site_id:
        conditions.append("r.site_id = ?")
        params.append(site_id)
    
    if symbol:
        conditions.append("r.symbol LIKE ?")
        params.append(f"%{symbol}%")
    
    if rec_type:
        conditions.append("r.recommendation_type = ?")
        params.append(rec_type)
    
    # Get sites for filter dropdown
    cursor.execute("SELECT id, name FROM sites WHERE active = 1 ORDER BY name")
    sites = cursor.fetchall()
    
    # Get recommendation types for filter dropdown
    cursor.execute("SELECT DISTINCT recommendation_type FROM recommendations ORDER BY recommendation_type")
    rec_types = [row[0] for row in cursor.fetchall()]
    
    # Build and execute query
    query = f'''
    SELECT r.*, s.name as site_name 
    FROM recommendations r
    JOIN sites s ON r.site_id = s.id
    WHERE {' AND '.join(conditions)}
    ORDER BY r.processed_at DESC
    LIMIT 500
    '''
    
    cursor.execute(query, params)
    recommendations = cursor.fetchall()
    
    conn.close()
    
    return render_template(
        'recommendations.html',
        recommendations=recommendations,
        sites=sites,
        rec_types=rec_types,
        filters={
            'site_id': site_id,
            'symbol': symbol,
            'type': rec_type,
            'days': days
        }
    )

# Add scheduled scanning functionality
def run_scheduled_scans():
    """Run scans for all active sites on a schedule"""
    conn = sqlite3.connect('recommendations.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all active sites
    cursor.execute("SELECT * FROM sites WHERE active = 1")
    sites = cursor.fetchall()
    conn.close()
    
    for site in sites:
        try:
            logger.info(f"Running scheduled scan for {site['name']}")
            
            scraper = ChartInkScraper(
                site_id=site['id'],
                name=site['name'],
                url=site['url'],
                selector=site['selector'] if site['selector'] else 'div.table-responsive table'
            )
            
            content = scraper.fetch_page()
            if content:
                scraper.parse_recommendations(content)
                new_recommendations = scraper.save_recommendations()
                
                # Send notification if new recommendations found
                if new_recommendations:
                    message = f"<b>New stock recommendations from {site['name']}</b>\n\n"
                    for i, rec in enumerate(new_recommendations[:5], 1):
                        message += f"{i}. <b>{rec['symbol']}</b> - {rec['stock_name']}\n"
                        message += f"   Price: {rec['current_price']} ({rec['pct_change']})\n"
                        message += f"   Recommendation: {rec['recommendation_type']}\n\n"
                    
                    if len(new_recommendations) > 5:
                        message += f"...and {len(new_recommendations) - 5} more recommendations."
                    
                    send_telegram_notification(message)
                
                logger.info(f"Scan completed for {site['name']}. Found {len(new_recommendations)} new recommendations.")
            else:
                logger.error(f"Failed to fetch content from {site['url']}")
            
            # Wait between requests to avoid overloading servers
            time.sleep(5)
        except Exception as e:
            logger.error(f"Error scanning site {site['name']}: {e}")
            continue

# Start the scheduled scanning in a separate thread
def start_scheduler():
    """Start the scheduler in a background thread"""
    def run_scheduler():
        while True:
            try:
                run_scheduled_scans()
                # Run every 4 hours
                time.sleep(4 * 60 * 60)
            except Exception as e:
                logger.error(f"Error in scheduler: {e}")
                time.sleep(10 * 60)  # Wait 10 minutes before retry on error
    
    scheduler_thread = threading.Thread(target=run_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    logger.info("Scheduler started in background")

# Add HTML templates - index.html
def create_templates():
    """Create template directory and HTML files if they don't exist"""
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    # Create index.html
    index_html = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>ChartInk Stock Recommendations</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            .recommendation-card {
                margin-bottom: 15px;
            }
            .rec-buy {
                background-color: #d4edda;
                border-color: #c3e6cb;
            }
            .rec-strong-buy {
                background-color: #c3e6cb;
                border-color: #b1dfbb;
            }
            .rec-hold {
                background-color: #fff3cd;
                border-color: #ffeeba;
            }
            .rec-sell, .rec-weak-sell {
                background-color: #f8d7da;
                border-color: #f5c6cb;
            }
        </style>
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
            <div class="container">
                <a class="navbar-brand" href="/">ChartInk Scraper</a>
                <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                    <span class="navbar-toggler-icon"></span>
                </button>
                <div class="collapse navbar-collapse" id="navbarNav">
                    <ul class="navbar-nav">
                        <li class="nav-item">
                            <a class="nav-link active" href="/">Home</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" href="/recommendations">Recommendations</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" href="/sites">Manage Sites</a>
                        </li>
                    </ul>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <h2>Active Sites</h2>
            <div class="row">
                {% for site in sites %}
                <div class="col-md-4 mb-3">
                    <div class="card">
                        <div class="card-body">
                            <h5 class="card-title">{{ site.name }}</h5>
                            <p class="card-text">
                                <small class="text-muted">Last scan: 
                                    {% if site.last_scan %}
                                        {{ site.last_scan }}
                                    {% else %}
                                        Never
                                    {% endif %}
                                </small>
                            </p>
                            <a href="{{ site.url }}" target="_blank" class="btn btn-sm btn-outline-primary">View Site</a>
                            <a href="/scan/{{ site.id }}" class="btn btn-sm btn-success scan-site">Run Scan</a>
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>

            <h2 class="mt-4">Recent Recommendations</h2>
            <div class="row">
                {% for rec in recommendations %}
                <div class="col-md-4">
                    <div class="card recommendation-card rec-{{ rec.recommendation_type.lower().replace(' ', '-') }}">
                        <div class="card-body">
                            <h5 class="card-title">{{ rec.symbol }} - {{ rec.stock_name }}</h5>
                            <h6 class="card-subtitle mb-2 text-muted">{{ rec.recommendation_type }}</h6>
                            <p class="card-text">
                                Price: {{ rec.current_price }} ({{ rec.pct_change }})<br>
                                Volume: {{ rec.volume }}<br>
                                From: {{ rec.site_name }}<br>
                                <small class="text-muted">{{ rec.processed_at }}</small>
                            </p>
                            {% if rec.links %}
                            <a href="{{ rec.links }}" target="_blank" class="btn btn-sm btn-outline-info">View Details</a>
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>

        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            document.addEventListener('DOMContentLoaded', function() {
                // Handle scan site buttons
                document.querySelectorAll('.scan-site').forEach(button => {
                    button.addEventListener('click', function(e) {
                        e.preventDefault();
                        const scanUrl = this.getAttribute('href');
                        
                        this.textContent = 'Scanning...';
                        this.disabled = true;
                        
                        fetch(scanUrl)
                            .then(response => response.json())
                            .then(data => {
                                if (data.success) {
                                    alert(data.message + ': ' + data.new_recommendations + ' new recommendations');
                                } else {
                                    alert('Error: ' + data.message);
                                }
                                location.reload();
                            })
                            .catch(error => {
                                alert('Error: ' + error);
                                this.textContent = 'Run Scan';
                                this.disabled = false;
                            });
                    });
                });
            });
        </script>
    </body>
    </html>
    '''
    
    with open('templates/index.html', 'w') as f:
        f.write(index_html)
    
    # Create sites.html
    sites_html = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Manage Sites - ChartInk Scraper</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
            <div class="container">
                <a class="navbar-brand" href="/">ChartInk Scraper</a>
                <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                    <span class="navbar-toggler-icon"></span>
                </button>
                <div class="collapse navbar-collapse" id="navbarNav">
                    <ul class="navbar-nav">
                        <li class="nav-item">
                            <a class="nav-link" href="/">Home</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" href="/recommendations">Recommendations</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link active" href="/sites">Manage Sites</a>
                        </li>
                    </ul>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <h2>Manage Sites</h2>
            
            <button class="btn btn-primary mb-3" data-bs-toggle="modal" data-bs-target="#siteModal">Add New Site</button>
            
            <table class="table table-striped">
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>URL</th>
                        <th>Selector</th>
                        <th>Table Format</th>
                        <th>Status</th>
                        <th>Last Scan</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for site in sites %}
                    <tr>
                        <td>{{ site.name }}</td>
                        <td><a href="{{ site.url }}" target="_blank">{{ site.url }}</a></td>
                        <td>{{ site.selector }}</td>
                        <td>{{ "Table" if site.is_table else "Non-Table" }}</td>
                        <td>
                            <span class="badge {{ 'bg-success' if site.active else 'bg-danger' }}">
                                {{ "Active" if site.active else "Inactive" }}
                            </span>
                        </td>
                        <td>{{ site.last_scan if site.last_scan else "Never" }}</td>
                        <td>
                            <button class="btn btn-sm btn-outline-primary edit-site" 
                                    data-id="{{ site.id }}"
                                    data-name="{{ site.name }}"
                                    data-url="{{ site.url }}"
                                    data-selector="{{ site.selector }}"
                                    data-is-table="{{ site.is_table }}"
                                    data-active="{{ site.active }}">
                                Edit
                            </button>
                            <button class="btn btn-sm btn-outline-danger delete-site" 
                                    data-id="{{ site.id }}"
                                    data-name="{{ site.name }}">
                                Delete
                            </button>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>

        <!-- Site Modal -->
        <div class="modal fade" id="siteModal" tabindex="-1">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="siteModalLabel">Site Configuration</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <form action="/sites" method="post">
                        <div class="modal-body">
                            <input type="hidden" id="site-id" name="id">
                            
                            <div class="mb-3">
                                <label for="site-name" class="form-label">Name</label>
                                <input type="text" class="form-control" id="site-name" name="name" required>
                            </div>
                            
                            <div class="mb-3">
                                <label for="site-url" class="form-label">URL</label>
                                <input type="url" class="form-control" id="site-url" name="url" required>
                            </div>
                            
                            <div class="mb-3">
                                <label for="site-selector" class="form-label">CSS Selector</label>
                                <input type="text" class="form-control" id="site-selector" name="selector" 
                                       placeholder="table.table, div.table-responsive table">
                                <small class="text-muted">Leave empty for default selector</small>
                            </div>
                            
                            <div class="mb-3 form-check">
                                <input type="checkbox" class="form-check-input" id="site-is-table" name="is_table" checked>
                                <label class="form-check-label" for="site-is-table">Table Format</label>
                            </div>
                            
                            <div class="mb-3 form-check">
                                <input type="checkbox" class="form-check-input" id="site-active" name="active" checked>
                                <label class="form-check-label" for="site-active">Active</label>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                            <button type="submit" class="btn btn-primary">Save</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>

        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
        <script>
            document.addEventListener('DOMContentLoaded', function() {
                // Edit site button
                document.querySelectorAll('.edit-site').forEach(button => {
                    button.addEventListener('click', function() {
                        const modal = new bootstrap.Modal(document.getElementById('siteModal'));
                        document.getElementById('siteModalLabel').textContent = 'Edit Site';
                        document.getElementById('site-id').value = this.dataset.id;
                        document.getElementById('site-name').value = this.dataset.name;
                        document.getElementById('site-url').value = this.dataset.url;
                        document.getElementById('site-selector').value = this.dataset.selector;
                        document.getElementById('site-is-table').checked = this.dataset.isTable === '1';
                        document.getElementById('site-active').checked = this.dataset.active === '1';
                        modal.show();
                    });
                });
                
                // Add new site button
                document.querySelector('[data-bs-target="#siteModal"]').addEventListener('click', function() {
                    document.getElementById('siteModalLabel').textContent = 'Add New Site';
                    document.getElementById('site-id').value = '';
                    document.getElementById('site-name').value = '';
                    document.getElementById('site-url').value = '';
                    document.getElementById('site-selector').value = '';
                    document.getElementById('site-is-table').checked = true;
                    document.getElementById('site-active').checked = true;
                });
                
                // Delete site button
                document.querySelectorAll('.delete-site').forEach(button => {
                    button.addEventListener('click', function() {
                        if (confirm(`Are you sure you want to delete the site "${this.dataset.name}"?`)) {
                            fetch(`/sites/delete/${this.dataset.id}`, {
                                method: 'POST'
                            })
                            .then(response => response.json())
                            .then(data => {
                                if (data.success) {
                                    alert(data.message);
                                    location.reload();
                                } else {
                                    alert('Error: ' + data.message);
                                }
                            })
                            .catch(error => {
                                alert('Error: ' + error);
                            });
                        }
                    });
                });
            });
        </script>
    </body>
    </html>
    '''
    
    with open('templates/sites.html', 'w') as f:
        f.write(sites_html)
    
    # Create recommendations.html
    recommendations_html = '''
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Stock Recommendations - ChartInk Scraper</title>
        <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
        <style>
            .recommendation-card {
                margin-bottom: 15px;
            }
            .rec-buy {
                background-color: #d4edda;
                border-color: #c3e6cb;
            }
            .rec-strong-buy {
                background-color: #c3e6cb;
                border-color: #b1dfbb;
            }
            .rec-hold {
                background-color: #fff3cd;
                border-color: #ffeeba;
            }
            .rec-sell, .rec-weak-sell {
                background-color: #f8d7da;
                border-color: #f5c6cb;
            }
        </style>
    </head>
    <body>
        <nav class="navbar navbar-expand-lg navbar-dark bg-dark">
    # Continue recommendations.html template
    recommendations_html_continued = '''
            <div class="container">
                <a class="navbar-brand" href="/">ChartInk Scraper</a>
                <button class="navbar-toggler" type="button" data-bs-toggle="collapse" data-bs-target="#navbarNav">
                    <span class="navbar-toggler-icon"></span>
                </button>
                <div class="collapse navbar-collapse" id="navbarNav">
                    <ul class="navbar-nav">
                        <li class="nav-item">
                            <a class="nav-link" href="/">Home</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link active" href="/recommendations">Recommendations</a>
                        </li>
                        <li class="nav-item">
                            <a class="nav-link" href="/sites">Manage Sites</a>
                        </li>
                    </ul>
                </div>
            </div>
        </nav>

        <div class="container mt-4">
            <h2>Stock Recommendations</h2>
            
            <!-- Filter Form -->
            <div class="card mb-4">
                <div class="card-body">
                    <form method="get" class="row g-3">
                        <div class="col-md-3">
                            <label for="site_id" class="form-label">Site</label>
                            <select id="site_id" name="site_id" class="form-select">
                                <option value="">All Sites</option>
                                {% for site in sites %}
                                <option value="{{ site.id }}" {% if filters.site_id == site.id %}selected{% endif %}>
                                    {{ site.name }}
                                </option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-3">
                            <label for="symbol" class="form-label">Symbol</label>
                            <input type="text" class="form-control" id="symbol" name="symbol" 
                                   value="{{ filters.symbol }}" placeholder="Enter symbol">
                        </div>
                        <div class="col-md-3">
                            <label for="type" class="form-label">Recommendation Type</label>
                            <select id="type" name="type" class="form-select">
                                <option value="">All Types</option>
                                {% for type in rec_types %}
                                <option value="{{ type }}" {% if filters.type == type %}selected{% endif %}>
                                    {{ type }}
                                </option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="col-md-2">
                            <label for="days" class="form-label">Days</label>
                            <select id="days" name="days" class="form-select">
                                <option value="1" {% if filters.days == 1 %}selected{% endif %}>1 day</option>
                                <option value="3" {% if filters.days == 3 %}selected{% endif %}>3 days</option>
                                <option value="7" {% if filters.days == 7 %}selected{% endif %}>7 days</option>
                                <option value="14" {% if filters.days == 14 %}selected{% endif %}>14 days</option>
                                <option value="30" {% if filters.days == 30 %}selected{% endif %}>30 days</option>
                            </select>
                        </div>
                        <div class="col-md-1">
                            <label class="form-label">&nbsp;</label>
                            <button type="submit" class="btn btn-primary w-100">Filter</button>
                        </div>
                    </form>
                </div>
            </div>
            
            <!-- Results Count -->
            <p>Found {{ recommendations|length }} recommendations</p>
            
            <!-- Recommendations List -->
            <div class="row">
                {% for rec in recommendations %}
                <div class="col-md-4">
                    <div class="card recommendation-card rec-{{ rec.recommendation_type.lower().replace(' ', '-') }}">
                        <div class="card-body">
                            <h5 class="card-title">{{ rec.symbol }} - {{ rec.stock_name }}</h5>
                            <h6 class="card-subtitle mb-2 text-muted">{{ rec.recommendation_type }}</h6>
                            <p class="card-text">
                                Price: {{ rec.current_price }} ({{ rec.pct_change }})<br>
                                Volume: {{ rec.volume }}<br>
                                {% if rec.industry != 'N/A' %}Industry: {{ rec.industry }}<br>{% endif %}
                                {% if rec.pe_ratio %}P/E: {{ rec.pe_ratio }}<br>{% endif %}
                                {% if rec.year_high %}52W High/Low: {{ rec.year_high }}/{{ rec.year_low }}<br>{% endif %}
                                From: {{ rec.site_name }}<br>
                                <small class="text-muted">{{ rec.processed_at }}</small>
                            </p>
                            {% if rec.links %}
                            <a href="{{ rec.links }}" target="_blank" class="btn btn-sm btn-outline-info">View Details</a>
                            {% endif %}
                        </div>
                    </div>
                </div>
                {% endfor %}
            </div>
            
            {% if not recommendations %}
            <div class="alert alert-info">
                No recommendations found for the selected filters.
            </div>
            {% endif %}
        </div>

        <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    </body>
    </html>
    '''
    
    recommendations_html = recommendations_html + recommendations_html_continued
    with open('templates/recommendations.html', 'w') as f:
        f.write(recommendations_html)

# Main application entry point
if __name__ == "__main__":
    # Initialize database and create templates
    init_db()
    create_templates()
    
    # Start the background scheduler
    start_scheduler()
    
    # Run the Flask app
    app.run(debug=True, host='0.0.0.0', port=5000)
