import os
import requests
import re
import json
from datetime import datetime, timezone
from flask import Flask, Response, render_template_string, jsonify
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from dateutil import parser as dateparser
from dateutil.parser import ParserError
from urllib.parse import urljoin

# --- Configuration ---
SITES_FILE = 'sites.txt'
APP_TITLE = 'Frosted Glass News Feed'
APP_LINK = 'http://localhost:5000' # Placeholder, will be the live Render URL
RSS_PATH = '/rss'
API_PATH = '/api/news'
CONTACT_EMAIL = 'contact@example.com' 

app = Flask(__name__)

# Utility: Reads the list of sites
def get_site_list():
    """Reads the list of sites from sites.txt."""
    try:
        with open(SITES_FILE, 'r') as f:
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

# Core Function: Scrapes data and returns JSON
def scrape_data_to_json():
    """Scrapes data from all sites and returns a list of dictionaries (JSON data)."""
    data = []
    sites = get_site_list()
    
    if not sites:
        return [{"error": "No sites configured in sites.txt"}]

    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    for url in sites:
        try:
            response = requests.get(url, timeout=10, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.content, 'html.parser')

            # 1. Title
            page_title = soup.title.string.strip() if soup.title else f"Untitled Page: {url}"

            # 2. Author
            author = "Unknown Source"
            author_meta = soup.find('meta', attrs={'name': 'author'})
            og_site_name = soup.find('meta', attrs={'property': 'og:site_name'})
            
            if author_meta and 'content' in author_meta.attrs:
                author = author_meta['content'].strip()
            elif og_site_name and 'content' in og_site_name.attrs:
                author = og_site_name['content'].strip()
            
            # 3. Description/Summary
            description_meta = soup.find('meta', attrs={'name': 'description'})
            og_description = soup.find('meta', attrs={'property': 'og:description'})
            description = ""

            if og_description and 'content' in og_description.attrs:
                description = og_description['content'].strip()
            elif description_meta and 'content' in description_meta.attrs:
                description = description_meta['content'].strip()
            
            if not description:
                first_p = soup.find('p')
                if first_p:
                    description = first_p.get_text().strip()[:200] + "..." if len(first_p.get_text().strip()) > 200 else first_p.get_text().strip()
                elif not description:
                    description = "No robust summary found."

            # 4. Image URL
            image_url = None
            og_image = soup.find('meta', attrs={'property': 'og:image'})
            if og_image and 'content' in og_image.attrs:
                image_url = og_image['content'].strip()

            # 5. Date
            pub_date = extract_date(soup)

            data.append({
                'title': page_title,
                'url': url,
                'author': author,
                'description': description,
                'image_url': image_url,
                # Convert datetime to ISO string for safe JSON serialization
                'pub_date': pub_date.isoformat(), 
                'source_name': author, # Use author as source for display
            })

        except requests.exceptions.RequestException as e:
            print(f"Failed to scrape {url}: {e}")
            data.append({
                'title': f"Scraping Failed for: {url}",
                'url': url,
                'author': "System Error",
                'description': f"Could not reach or parse site. Error: {str(e)}",
                'image_url': 'https://placehold.co/150x100/A0A0A0/FFFFFF?text=Error',
                'pub_date': datetime.now(timezone.utc).isoformat(),
                'source_name': "Error"
            })
            continue
            
    # Sort data by publication date (newest first)
    data.sort(key=lambda x: x.get('pub_date', ''), reverse=True)
    return data

@app.route(API_PATH)
def news_api():
    """Endpoint that returns scraped data as JSON."""
    return jsonify(scrape_data_to_json())

@app.route(RSS_PATH)
def rss_feed():
    """The endpoint that serves the generated RSS XML."""
    data = scrape_data_to_json()
    
    fg = FeedGenerator()
    fg.id(APP_LINK + RSS_PATH)
    fg.title(APP_TITLE)
    fg.author({'name': 'RSS Generator', 'email': CONTACT_EMAIL})
    fg.link(href=APP_LINK, rel='alternate')
    fg.link(href=APP_LINK + RSS_PATH, rel='self')
    fg.language('en')
    fg.description('An aggregated feed of custom URLs scraped for rich content.')
    fg.lastBuildDate(datetime.now(timezone.utc))

    for item in data:
        if 'error' in item: continue

        fe = fg.add_entry()
        fe.id(item['url'])
        fe.title(item['title'])
        fe.link(href=item['url'])
        
        # Convert ISO date string back to datetime object for feedgen
        try:
             fe.pubDate(dateparser.parse(item['pub_date']))
        except:
             pass

        # Build rich content for the feed description/content field
        rich_content = ""
        if item['image_url']:
            rich_content += f'<p><img src="{item["image_url"]}" alt="{item["title"]}" style="max-width: 100%; height: auto; border-radius: 8px;"></p>'
        
        rich_content += f'<p><strong>Author:</strong> {item["author"]}</p>'
        rich_content += f'<p><strong>Date:</strong> {dateparser.parse(item["pub_date"]).strftime("%Y-%m-%d %H:%M:%S %Z")}</p>'
        rich_content += '<hr>'
        rich_content += f'<p>{item["description"]}</p>'
        
        fe.content(rich_content, type='html')
        fe.author({'name': item['author']})

    return Response(fg.rss_str(pretty=True), mimetype='application/rss+xml')


@app.route('/')
def homepage():
    """The main page, serving the Glassmorphism News Dashboard."""
    
    # Log the RSS link to the console as requested
    print(f"\n[SERVER LOG] Generated RSS Feed Link: {APP_LINK + RSS_PATH}\n")

    # The HTML template for the news dashboard
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{APP_TITLE}</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
            body {{
                font-family: 'Inter', sans-serif;
                /* Background for the blur effect */
                background: linear-gradient(135deg, #1f005c 0%, #a63f70 50%, #f68900 100%);
                min-height: 100vh;
                padding: 1rem;
            }}
            .glass-container {{
                background: rgba(255, 255, 255, 0.15);
                backdrop-filter: blur(10px); /* The Frosted Glass effect */
                -webkit-backdrop-filter: blur(10px);
                border: 1px solid rgba(255, 255, 255, 0.2);
                box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.37);
                border-radius: 1rem;
            }}
            .glass-card {{
                background: rgba(255, 255, 255, 0.05);
                backdrop-filter: blur(4px);
                border: 1px solid rgba(255, 255, 255, 0.1);
            }}
        </style>
    </head>
    <body class="p-4">
        <div class="glass-container mx-auto p-6 md:p-10 max-w-6xl">
            <!-- Header and Search Bar -->
            <header class="text-white mb-8">
                <h1 class="text-4xl font-extrabold mb-2 text-shadow-lg">
                    {APP_TITLE}
                </h1>
                <p class="text-indigo-200">Aggregated content from your custom sites, sorted by date.</p>
                <div class="mt-6 flex flex-col md:flex-row gap-4 items-center">
                    <input type="text" id="searchInput" placeholder="Search titles and summaries..."
                           class="w-full md:w-2/3 p-3 rounded-full bg-white bg-opacity-20 border border-white border-opacity-30 placeholder-white text-white focus:outline-none focus:ring-2 focus:ring-indigo-300 transition duration-300"
                           oninput="filterArticles()">
                    
                    <a href="{RSS_PATH}" class="w-full md:w-1/3 text-center text-sm font-semibold rounded-full p-3 bg-white text-indigo-700 hover:bg-indigo-100 transition duration-300 shadow-md">
                        View RSS Feed (XML)
                    </a>
                </div>
            </header>

            <!-- Loading Indicator -->
            <div id="loading" class="text-white text-center py-10">
                <svg class="animate-spin h-8 w-8 text-white mx-auto mb-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                    <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                <p>Scraping data and building your feed. This may take a moment...</p>
            </div>

            <!-- News Grid -->
            <main id="newsGrid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6" style="display: none;"></main>

            <!-- No Results Message -->
            <div id="noResults" class="text-white text-center py-10" style="display: none;">
                <p class="text-xl font-semibold">No articles matched your search query.</p>
            </div>
        </div>

        <script>
            const newsGrid = document.getElementById('newsGrid');
            const loadingIndicator = document.getElementById('loading');
            const searchInput = document.getElementById('searchInput');
            const noResultsMessage = document.getElementById('noResults');
            let allArticles = [];

            // Fetches data from the API endpoint
            async function fetchArticles() {{
                try {{
                    const response = await fetch('{API_PATH}');
                    if (!response.ok) {{
                        throw new Error(`HTTP error! status: ${{response.status}}`);
                    }}
                    allArticles = await response.json();
                    
                    loadingIndicator.style.display = 'none';
                    newsGrid.style.display = 'grid';
                    
                    displayArticles(allArticles);

                }} catch (error) {{
                    console.error("Failed to fetch news data:", error);
                    loadingIndicator.innerHTML = '<p class="text-red-300">Error loading data. Check server logs or sites.txt configuration.</p>';
                }}
            }}
            
            // Renders articles to the grid
            function displayArticles(articles) {{
                newsGrid.innerHTML = '';
                
                if (articles.length === 0 || (articles.length === 1 && articles[0].error)) {{
                    newsGrid.innerHTML = '<div class="col-span-full text-center text-xl text-white py-10">' + 
                                         (articles[0] && articles[0].error ? articles[0].error : 'No articles found. Please check sites.txt.') +
                                         '</div>';
                    noResultsMessage.style.display = 'none';
                    return;
                }}
                
                if (articles.length === 0) {{
                    noResultsMessage.style.display = 'block';
                    return;
                }}

                noResultsMessage.style.display = 'none';

                articles.forEach(article => {{
                    const dateObj = new Date(article.pub_date);
                    const formattedDate = dateObj.toLocaleDateString('en-US', {{ year: 'numeric', month: 'short', day: 'numeric' }});
                    
                    const articleHTML = `
                        <a href="${{article.url}}" target="_blank" rel="noopener" 
                           class="glass-card p-4 rounded-xl shadow-lg transition transform hover:scale-[1.03] duration-300 text-white block">
                            ${{article.image_url ? 
                                `<img src="${{article.image_url}}" alt="${{article.title}}" 
                                      class="w-full h-40 object-cover rounded-lg mb-4 shadow-md"
                                      onerror="this.onerror=null; this.src='https://placehold.co/400x200/505050/FFFFFF?text=No+Image';">` : 
                                `<div class="w-full h-40 bg-gray-700 bg-opacity-30 rounded-lg mb-4 flex items-center justify-center text-sm">No Image Available</div>`
                            }}
                            
                            <h2 class="text-xl font-bold mb-2 leading-snug">${{article.title}}</h2>
                            <p class="text-xs font-semibold text-indigo-200 uppercase mb-2">
                                ${{article.source_name}} • ${{formattedDate}}
                            </p>
                            <p class="text-sm text-gray-200">${{article.description}}</p>
                        </a>
                    `;
                    newsGrid.insertAdjacentHTML('beforeend', articleHTML);
                }});
            }

            // Handles the search feature
            function filterArticles() {{
                const query = searchInput.value.toLowerCase();
                const filtered = allArticles.filter(article => 
                    article.title.toLowerCase().includes(query) || 
                    article.description.toLowerCase().includes(query) ||
                    article.source_name.toLowerCase().includes(query)
                );
                
                displayArticles(filtered);
                
                if (filtered.length === 0 && allArticles.length > 0) {{
                    noResultsMessage.style.display = 'block';
                }} else {{
                    noResultsMessage.style.display = 'none';
                }}
            }

            // Load articles when the page loads
            window.onload = fetchArticles;

        </script>
    </body>
    </html>
    """
    return render_template_string(html_content)

if __name__ == '__main__':
    # Set the App link for local testing
    APP_LINK = 'http://127.0.0.1:5000'
    print(f"\n[SERVER START] Starting local server at {APP_LINK}")
    print(f"[SERVER START] RSS Feed will be available at: {APP_LINK + RSS_PATH}")
    app.run(debug=True)
