import os
import requests
import re
from datetime import datetime, timezone
from flask import Flask, Response, render_template_string
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from dateutil import parser as dateparser
from dateutil.parser import ParserError

# --- Configuration ---
SITES_FILE = 'sites.txt'
APP_TITLE = 'Custom Scraped RSS Aggregator'
APP_LINK = 'http://localhost:5000/' # Placeholder, will be the live Render URL
RSS_PATH = '/rss'
CONTACT_EMAIL = 'contact@example.com' # Use a real one for feed validity

app = Flask(__name__)

# Utility function to get safe path for file storage
def get_site_list():
    """Reads the list of sites from sites.txt."""
    try:
        with open(SITES_FILE, 'r') as f:
            # Filter out comments and empty lines
            urls = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
        return urls
    except FileNotFoundError:
        print(f"Error: {SITES_FILE} not found.")
        return []

def extract_date(soup):
    """
    Attempts to extract a publication date from various meta tags and JSON-LD.
    Returns a datetime object or None.
    """
    date_str = None
    
    # 1. Check common meta tags
    date_tags = [
        ('meta', {'property': 'article:published_time'}),
        ('meta', {'name': 'date'}),
        ('meta', {'name': 'pubdate'}),
        ('meta', {'property': 'og:pubdate'}),
        ('time', {'datetime': True})
    ]

    for tag_name, attrs in date_tags:
        tag = soup.find(tag_name, attrs=attrs)
        if tag:
            if tag_name == 'time':
                date_str = tag.get('datetime')
            elif 'content' in tag.attrs:
                date_str = tag['content']
            
            if date_str:
                try:
                    # Attempt to parse
                    dt = dateparser.parse(date_str)
                    # Convert to UTC if necessary, and ensure timezone awareness
                    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except (ParserError, TypeError):
                    continue # Try the next tag

    # Fallback to current time if no date is found
    return datetime.now(timezone.utc)

def scrape_and_generate_feed():
    """
    Scrapes data from all sites in sites.txt and generates the RSS feed XML.
    Returns the XML string.
    """
    fg = FeedGenerator()
    fg.id(APP_LINK + RSS_PATH)
    fg.title(APP_TITLE)
    fg.author({'name': 'RSS Generator', 'email': CONTACT_EMAIL})
    fg.link(href=APP_LINK, rel='alternate')
    fg.link(href=APP_LINK + RSS_PATH, rel='self')
    fg.language('en')
    fg.description('An aggregated feed of custom URLs scraped for title, summary, image, date, and author.')
    fg.lastBuildDate(datetime.now(timezone.utc))

    sites = get_site_list()
    
    if not sites:
        # Create a placeholder entry if no sites are defined
        fe = fg.add_entry()
        fe.id(APP_LINK + '/nosites')
        fe.title('No Sites Configured')
        fe.link(href=APP_LINK)
        fe.description('Please add URLs to the sites.txt file to generate a feed.')
        return fg.rss_str(pretty=True)

    for url in sites:
        print(f"Scraping: {url}")
        
        # Initialize scraping variables
        page_title = f"Untitled Page: {url}"
        description = ""
        image_url = None
        author = "Unknown Author"
        
        # Use a short timeout and a common user-agent
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

        try:
            response = requests.get(url, timeout=10, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # --- 1. Title ---
            if soup.title:
                page_title = soup.title.string.strip()

            # --- 2. Author ---
            author_meta = soup.find('meta', attrs={'name': 'author'})
            if author_meta and 'content' in author_meta.attrs:
                author = author_meta['content'].strip()
            else:
                # Check OpenGraph Site Name
                og_site_name = soup.find('meta', attrs={'property': 'og:site_name'})
                if og_site_name and 'content' in og_site_name.attrs:
                    author = og_site_name['content'].strip()
                # Simple fallback to common byline/author tags
                else:
                    byline = soup.find(['span', 'div', 'p'], class_=re.compile(r'author|byline', re.I))
                    if byline:
                         author = byline.get_text().strip()


            # --- 3. Description/Summary ---
            description_meta = soup.find('meta', attrs={'name': 'description'})
            og_description = soup.find('meta', attrs={'property': 'og:description'})

            if og_description and 'content' in og_description.attrs:
                description = og_description['content'].strip()
            elif description_meta and 'content' in description_meta.attrs:
                description = description_meta['content'].strip()
            
            # X/Twitter specific handling (relies heavily on metadata)
            if 'x.com' in url or 'twitter.com' in url:
                if not description:
                    twitter_desc = soup.find('meta', attrs={'name': 'twitter:description'})
                    if twitter_desc and 'content' in twitter_desc.attrs:
                        description = f"[Tweet content]: {twitter_desc['content'].strip()}"
                
                # Twitter profile name as author
                twitter_creator = soup.find('meta', attrs={'name': 'twitter:creator'})
                if twitter_creator and 'content' in twitter_creator.attrs:
                    author = twitter_creator['content'].strip()
                
                if not description:
                     description = "Note: X/Twitter content is often protected; basic metadata was used."
            
            # General Fallback: Use the first paragraph of the body
            if not description or len(description) < 50:
                first_p = soup.find('p')
                if first_p:
                    description = first_p.get_text().strip()[:500] + "..." if len(first_p.get_text()) > 500 else first_p.get_text().strip()
                elif not description:
                    description = f"Content scraped from {url}. No robust summary found."

            # --- 4. Image URL ---
            og_image = soup.find('meta', attrs={'property': 'og:image'})
            twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
            
            if og_image and 'content' in og_image.attrs:
                image_url = og_image['content'].strip()
            elif twitter_image and 'content' in twitter_image.attrs:
                image_url = twitter_image['content'].strip()
            else:
                # Fallback: Find the largest image on the page
                first_img = soup.find('img', src=True)
                if first_img and first_img['src'] and not first_img['src'].endswith(('.svg', '.gif')):
                    # Ensure it's an absolute URL
                    if first_img['src'].startswith('http'):
                        image_url = first_img['src']
                    elif first_img['src'].startswith('/'):
                        # Resolve relative URL using base URL
                        from urllib.parse import urljoin
                        image_url = urljoin(url, first_img['src'])
            
            # --- 5. Date ---
            pub_date = extract_date(soup)


            # --- Create Feed Entry ---
            fe = fg.add_entry()
            fe.id(url)
            fe.title(page_title)
            fe.link(href=url)
            fe.pubDate(pub_date)

            # Build rich content for the feed description/content field
            rich_content = ""
            if image_url:
                rich_content += f'<p><img src="{image_url}" alt="{page_title}" style="max-width: 100%; height: auto;"></p>'
            
            rich_content += f'<p><strong>Author:</strong> {author}</p>'
            rich_content += f'<p><strong>Date:</strong> {pub_date.strftime("%Y-%m-%d %H:%M:%S %Z")}</p>'
            rich_content += '<hr>'
            rich_content += f'<p>{description}</p>'
            
            fe.content(rich_content, type='html')
            fe.author({'name': author})

        except requests.exceptions.RequestException as e:
            print(f"Failed to scrape {url}: {e}")
            # Add an error entry to the feed
            fe = fg.add_entry()
            fe.id(url + "/error")
            fe.title(f"Scraping Failed for: {url}")
            fe.link(href=url)
            fe.description(f"Could not reach or parse site: {url}. Error: {str(e)}")
            continue

    return fg.rss_str(pretty=True)

@app.route('/')
def homepage():
    """The main page, showing the link to the generated RSS feed."""
    rss_url = RSS_PATH
    
    # Simple HTML template for the homepage
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{APP_TITLE} | Generator</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
            body {{ font-family: 'Inter', sans-serif; }}
        </style>
    </head>
    <body class="bg-gray-50 flex items-center justify-center min-h-screen p-4">
        <div class="max-w-xl w-full bg-white shadow-2xl rounded-xl p-8 md:p-10 border border-gray-100">
            <h1 class="text-3xl font-extrabold text-gray-900 mb-4 text-center">
                {APP_TITLE}
            </h1>
            <p class="text-gray-600 mb-6 text-center">
                This server aggregates content from the sites listed in 
                <code class="bg-gray-100 p-1 rounded text-sm font-mono">sites.txt</code> 
                into a single, custom RSS feed, now including **Image, Date, and Author** data.
            </p>
            
            <div class="bg-indigo-50 border-l-4 border-indigo-500 text-indigo-800 p-4 mb-6 rounded-lg" role="alert">
                <p class="font-bold">Instructions:</p>
                <p class="text-sm">Edit the <code class="bg-indigo-100 p-0.5 rounded text-sm font-mono">sites.txt</code> file (in the editor) and then refresh this page. The feed will be updated instantly.</p>
            </div>
            
            <div class="text-center">
                <p class="text-lg font-semibold text-gray-700 mb-4">Your Custom RSS Feed Link:</p>
                <a href="{rss_url}" 
                   class="inline-flex items-center justify-center px-6 py-3 border border-transparent text-base font-medium rounded-full shadow-lg text-white bg-indigo-600 hover:bg-indigo-700 transition duration-150 ease-in-out transform hover:scale-[1.02] active:scale-[0.98]" 
                   target="_blank"
                   title="Click to view the RSS XML or copy the URL below">
                    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 5c7.18 0 13 5.82 13 13M6 9c4.97 0 9 4.03 9 9m-4 5a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>
                    View RSS Feed
                </a>
            </div>

            <div class="mt-8">
                <p class="text-sm font-medium text-gray-500 mb-2">Feed URL to use in your RSS Reader:</p>
                <div class="relative">
                    <input id="rss-link-input" type="text" readonly value="{APP_LINK + rss_url}" 
                           class="w-full bg-gray-100 border border-gray-300 rounded-lg py-2 px-3 pr-16 text-gray-800 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"/>
                    <button onclick="copyToClipboard()"
                            class="absolute right-1 top-1 bottom-1 px-3 py-1 bg-gray-200 text-gray-700 rounded-lg hover:bg-gray-300 transition duration-150 text-xs font-semibold"
                            title="Copy URL to clipboard">
                        Copy
                    </button>
                </div>
            </div>
        </div>

        <script>
            // Note: This script uses execCommand for broader compatibility in sandboxed environments.
            function copyToClipboard() {{
                const copyText = document.getElementById("rss-link-input");
                copyText.select();
                copyText.setSelectionRange(0, 99999); // For mobile devices
                
                try {{
                    document.execCommand('copy');
                    const copyButton = document.querySelector('button[title="Copy URL to clipboard"]');
                    copyButton.textContent = 'Copied!';
                    setTimeout(() => {{ copyButton.textContent = 'Copy'; }}, 2000);
                }} catch (err) {{
                    console.error('Could not copy text: ', err);
                }}
            }}
        </script>
    </body>
    </html>
    """
    return render_template_string(html_content)

@app.route(RSS_PATH)
def rss_feed():
    """The endpoint that serves the generated RSS XML."""
    xml_data = scrape_and_generate_feed()
    return Response(xml_data, mimetype='application/rss+xml')

if __name__ == '__main__':
    # When running locally, set the App link to reflect that
    APP_LINK = 'http://127.0.0.1:5000'
    app.run(debug=True)
else:
    # When deployed (e.g., on Render), Flask automatically handles the host/port
    # You should update APP_LINK in the Configuration section above with your actual Render URL
    # if you want the link to be perfect, but the relative links will still work.
    pass
