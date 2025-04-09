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
