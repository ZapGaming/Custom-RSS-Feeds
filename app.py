import os
import requests
import re
from datetime import datetime, timezone
from flask import Flask, Response
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from dateutil import parser as dateparser
from dateutil.parser import ParserError
from urllib.parse import urljoin

# --- Configuration ---
SITES_FILE = 'sites.txt'
APP_TITLE = 'Pure XML Site Aggregator Feed'
# --- IMPORTANT --- Set the application's external URL for correct RSS links.
# Set APP_LINK to the confirmed render domain.
APP_LINK = 'https://custom-rss-feeds.onrender.com' 
RSS_PATH = '/feed.xml' # Path for the RSS XML feed
CONTACT_EMAIL = 'contact@example.com' 

app = Flask(__name__)

# Utility: Reads the list of sites
def get_site_list():
    """Reads the list of sites from sites.txt."""
    try:
        with open(SITES_FILE, 'r') as f:
            # Filter out comments and empty lines
            urls = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
        return urls
    except FileNotFoundError:
        return []

# Utility: Extracts date from HTML
def extract_date(soup):
    """Attempts to extract a publication date, falling back to current time."""
    date_str = None
    date_tags = [
        ('meta', {'property': 'article:published_time'}),
        ('meta', {'name': 'date'}),
        ('time', {'datetime': True})
    ]
    for tag_name, attrs in date_tags:
        tag = soup.find(tag_name, attrs=attrs)
        if tag:
            date_str = tag.get('datetime') if tag_name == 'time' else tag.get('content')
            if date_str:
                try:
                    dt = dateparser.parse(date_str)
                    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except (ParserError, TypeError):
                    continue
    return datetime.now(timezone.utc)

# Utility: Improved summary extraction
def get_article_summary(soup, default_description="No robust summary found."):
    """Aggressively tries to extract a meaningful summary from the page content."""
    
    # 1. Try OG/Meta descriptions first (Standard best practice)
    og_description = soup.find('meta', attrs={'property': 'og:description'})
    description_meta = soup.find('meta', attrs={'name': 'description'})
    
    if og_description and 'content' in og_description.attrs:
        return og_description['content'].strip()
    if description_meta and 'content' in description_meta.attrs:
        return description_meta['content'].strip()

    # 2. Aggressive fallback to main content area (Improved scraping)
    main_content_selectors = ['article', 'main', '.post-content', '.entry-content', '#content', '#main']
    
    for selector in main_content_selectors:
        main_block = soup.select_one(selector)
        if main_block:
            paragraphs = main_block.find_all('p', limit=3)
            if paragraphs:
                summary = ' '.join(p.get_text().strip() for p in paragraphs if p.get_text().strip())
                # Clean up multiple spaces/newlines and limit length
                summary = re.sub(r'\s+', ' ', summary)
                return summary[:500] + "..." if len(summary) > 500 else summary
    
    # 3. Last fallback to the first paragraph
    first_p = soup.find('p')
    if first_p:
        text = first_p.get_text().strip()
        text = re.sub(r'\s+', ' ', text)
        return text[:200] + "..." if len(text) > 200 else text

    return default_description

# Core Function: Scrapes data and returns JSON (one entry per site)
def get_site_metadata():
    """Scrapes metadata from all sites, creating one single entry per URL."""
    data = []
    sites = get_site_list()
    
    if not sites:
        return [{"error": "No sites configured in sites.txt"}]

    # Enhanced headers to better mimic a browser and pass basic bot checks
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive'
    }

    for url in sites:
        try:
            # Attempt to fetch the content. Added verify=False to bypass SSLCertVerificationError.
            response = requests.get(url, timeout=10, headers=headers, verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # --- Robust Parsing Block ---
            try:
                # 1. Title (Cleaned for RSS compatibility)
                page_title = soup.title.string.strip() if soup.title else f"Untitled Page: {url}"
                # Remove special characters that can break XML feeds
                page_title = re.sub(r'[^\w\s\-\|&]', '', page_title) 

                # 2. Author/Source Name
                author = "Unknown Source"
                author_meta = soup.find('meta', attrs={'name': 'author'})
                og_site_name = soup.find('meta', attrs={'property': 'og:site_name'})
                
                if author_meta and 'content' in author_meta.attrs:
                    author = author_meta['content'].strip()
                elif og_site_name and 'content' in og_site_name.attrs:
                    author = og_site_name['content'].strip()
                
                # 3. Description/Summary (Using improved function)
                description = get_article_summary(soup)
                
                # 4. Image URL
                image_url = None
                og_image = soup.find('meta', attrs={'property': 'og:image'})
                if og_image and 'content' in og_image.attrs:
                    image_url = og_image['content'].strip()
                # Relative URL handling: make sure the image URL is absolute
                if image_url and image_url.startswith('/'):
                    image_url = urljoin(url, image_url)


                # 5. Date
                pub_date = extract_date(soup)

                data.append({
                    'title': page_title,
                    'url': url,
                    'author': author,
                    'description': description,
                    'image_url': image_url,
                    'pub_date': pub_date.isoformat(), 
                    'source_name': author, 
                })

            except Exception as e:
                # Catch specific parsing errors (e.g., KeyError, AttributeError)
                print(f"Failed to PARSE {url}: {e}")
                data.append({
                    'title': f"[FAIL] Parsing Failed for: {url}",
                    'url': url,
                    'author': "Scraping Error",
                    'description': f"Site content could not be parsed correctly. Error: {str(e)}",
                    'image_url': 'https://placehold.co/150x100/505050/FFFFFF?text=Parsing+Error',
                    'pub_date': datetime.now(timezone.utc).isoformat(),
                    'source_name': "Error"
                })
                continue

        except requests.exceptions.RequestException as e:
            # Catch HTTP errors (timeouts, 404s, 403s)
            print(f"Failed to FETCH {url}: {e}")
            data.append({
                'title': f"[FAIL] Fetching Failed for: {url}",
                'url': url,
                'author': "Network Error",
                'description': f"Could not reach or retrieve site content. Error: {str(e)}",
                'image_url': 'https://placehold.co/150x100/A0A0A0/FFFFFF?text=Fetching+Error',
                'pub_date': datetime.now(timezone.utc).isoformat(),
                'source_name': "Error"
            })
            continue
            
    # Sort data by publication date (newest first)
    data.sort(key=lambda x: x.get('pub_date', ''), reverse=True)
    return data

@app.route(RSS_PATH)
@app.route('/') # Serve the XML feed on the root path
def rss_feed():
    """The endpoint that serves the generated RSS XML."""
    
    # 1. Fetch the single-entry metadata for each site
    data = get_site_metadata()
    
    fg = FeedGenerator()
    fg.id(APP_LINK + RSS_PATH)
    fg.title(APP_TITLE)
    fg.author({'name': 'RSS Generator', 'email': CONTACT_EMAIL})
    fg.link(href=APP_LINK, rel='alternate')
    fg.link(href=APP_LINK + RSS_PATH, rel='self') 
    fg.language('en')
    fg.description('An aggregated feed of custom URLs scraped for rich content, one entry per site.')
    fg.lastBuildDate(datetime.now(timezone.utc))

    for item in data:
        fe = fg.add_entry()
        fe.id(item['url'])
        
        # Ensure title is clean for RSS item compatibility
        clean_title = item['title'].replace('[FAIL]', 'Scraping Failed')
        fe.title(clean_title)

        # Crucial: The link must always point to the original site.
        fe.link(href=item['url'])
        
        is_failed = item.get('title', '').startswith('[FAIL]')

        # Convert ISO date string back to datetime object for feedgen
        try:
             fe.pubDate(dateparser.parse(item['pub_date']))
        except:
             # Default to current time if parsing fails
             fe.pubDate(datetime.now(timezone.utc))

        # --- Handle Content Generation: Using fe.description() for compatibility ---
        if is_failed:
            error_description = item["description"]
            
            # Use simple HTML wrapped in a CDATA section by feedgen
            rich_content = f"""
            <div style="color: #CC0000; border: 1px solid #CC0000; padding: 10px; background-color: #FFEEFF; border-radius: 4px;">
                <h3 style="margin-top: 0; font-weight: bold;">❌ SCRAPING FAILED - INCOMPATIBLE SITE ❌</h3>
                <p><strong>Status:</strong> This link is not working with the current RSS scraping engine.</p>
                <p><strong>Attempted URL:</strong> <a href="{item['url']}">{item['url']}</a></p>
                <hr style="border-top: 1px solid #CC0000;">
                <p><strong>Error Details:</strong> {error_description}</p>
            </div>
            """
            fe.description(rich_content)
            fe.author({'name': 'System Error'})
        
        else:
            # Content for successful entries
            rich_content = ""
            if item['image_url']:
                # Adding image as an enclosure is best practice for mobile news readers
                fe.enclosure(url=item['image_url'], length='0', type='image/jpeg') 

                rich_content += f'<p><img src="{item["image_url"]}" alt="{item["title"]}" style="max-width: 100%; height: auto; border-radius: 8px;"></p>'
            
            rich_content += f'<p><strong>Source:</strong> {item["author"]}</p>'
            rich_content += f'<p><strong>Date:</strong> {dateparser.parse(item["pub_date"]).strftime("%Y-%m-%d %H:%M:%S %Z")}</p>'
            rich_content += '<hr>'
            rich_content += f'<p>{item["description"]}</p>'
            
            fe.description(rich_content)
            fe.author({'name': item['author']})

    # Generate RSS string
    return Response(fg.rss_str(pretty=True), mimetype='application/rss+xml')


if __name__ == '__main__':
    # Set the App link for local testing when not deployed on Render
    APP_LINK = 'http://127.0.0.1:5000'
    print(f"\n[SERVER START] Starting local server at {APP_LINK}")
    print(f"[SERVER START] RSS Feed will be available at: {APP_LINK} or {APP_LINK + RSS_PATH}")
    app.run(debug=True)
