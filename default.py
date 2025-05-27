import sys
import xbmcgui
import xbmcplugin
import xbmc
import urllib.parse
import os, re, json
import xbmcvfs
from bs4 import BeautifulSoup
import requests
import time

# ID của addon (thư mục của addon)
ADDON_ID = 'plugin.video.myimdbfshare'
addon_handle = int(sys.argv[1])

# Đường dẫn đến tệp HTML đã tải về
IMDB_HTML_FILE = os.path.join(xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources'), 'IMDb Top 250 movies.html')

# URL tìm kiếm Fshare.vn (có thể cần điều chỉnh nếu Fshare thay đổi)
FSHARE_SEARCH_API_URL = "https://api.timfshare.com/v1/string-query-search?query="

TMDB_API_KEY = 'YOUR_TMDB_API_KEY'  # Thay bằng API key của bạn

PLOT_CACHE_FILE = os.path.join(xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources'), 'plots.json')

def load_plot_cache():
    if os.path.exists(PLOT_CACHE_FILE):
        with open(PLOT_CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_plot_cache(cache):
    with open(PLOT_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

def get_vietnamese_plot(title, year, refresh=False):
    """
    Lấy plot tiếng Việt từ Wikipedia và lưu cache local.
    """
    cache = load_plot_cache()
    key = f"{title} ({year})"
    if not refresh and key in cache:
        return cache[key]

    # Tìm kiếm Wikipedia tiếng Việt
    try:
        search_url = f"https://vi.wikipedia.org/w/api.php?action=query&list=search&srsearch={urllib.parse.quote(title)}&format=json"
        resp = requests.get(search_url, timeout=10)
        data = resp.json()
        if data.get('query', {}).get('search'):
            page_title = data['query']['search'][0]['title']
            # Lấy nội dung trang
            page_url = f"https://vi.wikipedia.org/w/api.php?action=query&prop=extracts&exintro&explaintext&titles={urllib.parse.quote(page_title)}&format=json"
            resp2 = requests.get(page_url, timeout=10)
            data2 = resp2.json()
            pages = data2.get('query', {}).get('pages', {})
            for page in pages.values():
                plot = page.get('extract', '')
                if plot:
                    cache[key] = plot
                    save_plot_cache(cache)
                    return plot
    except Exception as e:
        xbmc.log(f"Lỗi lấy plot Wikipedia: {e}", level=xbmc.LOGWARNING)
    return ""

def refresh_plot_cache():
    """
    Làm mới toàn bộ plot từ Wikipedia và lưu lại.
    """
    movies = get_imdb_top250_from_file()
    cache = {}
    for movie in movies:
        plot = get_vietnamese_plot(movie['title'], movie['year'], refresh=True)
        cache[f"{movie['title']} ({movie['year']})"] = plot
    save_plot_cache(cache)
    xbmcgui.Dialog().ok("Hoàn tất", "Đã làm mới toàn bộ plot từ Wikipedia.")

def get_imdb_top250_from_file():
    """
    Lấy danh sách Top 250 IMDB từ tệp HTML cục bộ.
    """
    xbmc.log(f"Reading IMDB Top 250 from file: {IMDB_HTML_FILE}", level=xbmc.LOGINFO)
    movies = []
    try:
        with open(IMDB_HTML_FILE, 'r', encoding='utf-8') as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, 'html.parser')

        for item in soup.select('li.ipc-metadata-list-summary-item'):
            try:
                title_element = item.select_one('h3.ipc-title__text')
                if title_element:
                    title_full = title_element.get_text(strip=True)
                    if '.' in title_full:
                        title = title_full.split('.', 1)[1].strip()
                    else:
                        title = title_full
                else:
                    continue

                year_element = item.select_one('span.sc-4b408797-8.iurwGb.cli-title-metadata-item')
                year = year_element.get_text(strip=True) if year_element else "N/A"

                rating_element = item.select_one('span.ipc-rating-star--rating')
                imdb_rating = rating_element.get_text(strip=True) if rating_element else "N/A"

                poster_element = item.select_one('img')
                poster_url = poster_element['src'] if poster_element and 'src' in poster_element.attrs else ""
                if not poster_url and poster_element and 'data-src' in poster_element.attrs:
                    poster_url = poster_element['data-src']
                poster_url = poster_url.replace('./IMDb Top 250 movies_files/', '')

                plot_vi = get_vietnamese_plot(title, year)
                movies.append({
                    'title': title,
                    'year': year,
                    'imdb_rating': imdb_rating,
                    'poster': poster_url,
                    'plot': plot_vi
                })
            except Exception as e:
                xbmc.log(f"Error parsing IMDB item in file: {e}", level=xbmc.LOGWARNING)
                continue
        xbmc.log(f"Found {len(movies)} movies from local HTML file.", level=xbmc.LOGINFO)
        return movies
    except FileNotFoundError:
        xbmc.log(f"IMDB HTML file not found: {IMDB_HTML_FILE}", level=xbmc.LOGERROR)
        xbmcgui.Dialog().ok("Lỗi", "Không tìm thấy tệp 'IMDb Top 250 movies.html'. Vui lòng đặt nó trong thư mục addon.")
        return []
    except Exception as e:
        xbmc.log(f"Error reading or parsing IMDB HTML file: {e}", level=xbmc.LOGERROR)
        xbmcgui.Dialog().ok("Lỗi", f"Không thể đọc hoặc phân tích tệp IMDB HTML: {e}")
        return []

def search_fshare(movie_title, movie_year=None):
    """
    Tìm kiếm link Fshare cho bộ phim bằng cách gọi hàm timfshare.
    """
    xbmc.log(f"Calling timfshare for: {movie_title} ({movie_year})", level=xbmc.LOGINFO)
    search_query = f"{movie_title} {movie_year or ''}".strip()
    
    fshare_results = timfshare(search_query)

    links = []
    if 'items' in fshare_results and isinstance(fshare_results['items'], list):
        for item in fshare_results['items']:
            size = item.get('info', {}).get('size', 0)
            links.append({'title': item.get('label', 'No Title'), 'url': item.get('path', ''),'size': size})
            
    return links

def timfshare(query):
    query = query.replace("\n", "").replace(".", " ")
    query = query.replace('&ref=ref','')
    query = urllib.parse.quote_plus(query)
    api_timfshare = 'https://api.timfshare.com/v1/string-query-search?query='

    headers = {
        'user-agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36",
        'authorization': 'Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJuYW1lIjoiZnNoYXJlIiwidXVpZCI6IjcxZjU1NjFkMTUiLCJ0eXBlIjoicGFydG5lciIsImV4cGlyZXMiOjAsImV4cGlyZSI6MH0.WBWRKbFf7nJ7gDn1rOgENh1_doPc07MNsKwiKCJg40U'
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(api_timfshare + query, headers=headers, timeout=10)
            response.raise_for_status()
            jsondata = response.json()
            break
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                notify("Không thể kết nối tới API sau 3 lần thử.")
                return {"content_type": "episodes", "items": []}

    items = []
    for i in jsondata.get('data', []):
        item = {}
        name = i.get('name', '')
        furl = i.get('url', '')
        filesize = float(i.get("size", 0))
        type_f = i.get('file_type', '')

        if furl:
            furl = re.search(r"(.*)\?", furl).group(1) if '?' in furl else furl
            link = f'plugin://plugin.video.vietmediaF?action=play&url={furl}'
            playable = type_f != '0'
        else:
            continue

        item["label"] = name
        item["is_playable"] = playable
        item["path"] = link
        item["thumbnail"] = 'fshare.png'
        item["icon"] = "fshareicon.png"
        item["label2"] = ""
        item["info"] = {'plot': '', 'size': filesize}
        items.append(item)

    data = {"content_type": "episodes", "items": items}
    valid_extensions = ['.mkv', '.mp4', '.wmv', '.iso', '.ISO', '.ts']

    new_items = []
    for item in data['items']:
        label = item['label']
        extension = label[label.rfind('.'):].lower()
        if extension in valid_extensions:
            new_items.append(item)

    data['items'] = new_items
    t = len(data['items'])

    if t == 0:
        notify("Không tìm thấy kết quả phù hợp.")
    return data

def list_movies():
    """
    Hiển thị danh sách phim Top 250 IMDB trong Kodi.
    """
    xbmcplugin.setPluginCategory(addon_handle, 'IMDB Top 250 Movies')
    xbmcplugin.setContent(addon_handle, 'movies')

    movies = get_imdb_top250_from_file()
    for i, movie in enumerate(movies):
        title = f"{i+1}. {movie['title']} ({movie['year']}) - IMDB Rating: {movie['imdb_rating']}"
        list_item = xbmcgui.ListItem(title)
        list_item.setInfo('video', {
            'title': movie['title'],
            'year': movie['year'],
            'plot': movie.get('plot', '')
        })
        poster_path = os.path.join(xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources/IMDb Top 250 movies_files'), movie['poster'])
        list_item.setArt({'thumb': poster_path, 'icon': poster_path, 'poster': poster_path})
        url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'search_fshare', 'title': movie['title'], 'year': movie['year']})
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=url, listitem=list_item, isFolder=True)

    refresh_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'refresh_imdb'})
    refresh_item = xbmcgui.ListItem('[Làm mới IMDb Top 250]')
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=refresh_url, listitem=refresh_item, isFolder=False)

    refresh_plot_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'refresh_plot'})
    refresh_plot_item = xbmcgui.ListItem('[Làm mới Plot Wikipedia]')
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=refresh_plot_url, listitem=refresh_plot_item, isFolder=False)

    xbmcplugin.endOfDirectory(addon_handle)

def show_fshare_links(movie_title, movie_year):
    xbmcplugin.setPluginCategory(addon_handle, f'Fshare Links for {movie_title}')
    xbmcplugin.setContent(addon_handle, 'files')

    links = search_fshare(movie_title, movie_year)

    if not links:
        notify(f"Không tìm thấy link Fshare cho {movie_title}")
        xbmcplugin.endOfDirectory(addon_handle, succeeded=True)
        return

    # Lấy info phim từ danh sách IMDb
    imdb_movies = get_imdb_top250_from_file()
    movie_info = next((m for m in imdb_movies if m['title'] == movie_title and m['year'] == movie_year), None)

    for i, link_info in enumerate(links):
        size = link_info.get('size', 0)
        if size >= 1024*1024*1024:
            size_str = f"{size/(1024*1024*1024):.2f} GB"
        elif size >= 1024*1024:
            size_str = f"{size/(1024*1024):.2f} MB"
        else:
            size_str = f"{size} B"
        title = f"{i+1}: {link_info['title']} - ({size_str})"
        list_item = xbmcgui.ListItem(title)

        # Thêm info chi tiết nếu có
        info = {
            'title': link_info['title'],
            'size': size_str
        }
        art = {}
        if movie_info:
            info.update({
                'plot': movie_info.get('plot', ''),
                'year': movie_info.get('year', ''),
                'rating': movie_info.get('imdb_rating', ''),
                'poster': movie_info.get('poster', ''),
            })
            # Đường dẫn poster local nếu có
            poster_file = movie_info.get('poster', '')
            if poster_file:
                poster_path = os.path.join(
                    xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources/IMDb Top 250 movies_files'),
                    poster_file
                )
                art = {'thumb': poster_path, 'icon': poster_path, 'poster': poster_path}
        list_item.setInfo('video', info)
        if art:
            list_item.setArt(art)
        list_item.setProperty('IsPlayable', 'true')
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=link_info['url'], listitem=list_item, isFolder=False)

    xbmcplugin.endOfDirectory(addon_handle)

def router(paramstring):
    """
    Router xử lý các yêu cầu từ Kodi.
    """
    params = dict(urllib.parse.parse_qsl(paramstring))

    if params:
        if params['action'] == 'search_fshare':
            movie_title = params.get('title')
            movie_year = params.get('year')
            if movie_title:
                show_fshare_links(movie_title, movie_year)
        if params['action'] == 'refresh_imdb':
            refresh_imdb_top250()
            list_movies()
        if params['action'] == 'refresh_plot':
            refresh_plot_cache()
            list_movies()
    else:
        list_movies()

def refresh_imdb_top250():
    """
    Tải lại file IMDb Top 250 và poster về thư mục addon.
    """
    import requests
    from bs4 import BeautifulSoup

    url = "https://www.imdb.com/chart/top/"
    html_path = os.path.join(xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources'), 'IMDb Top 250 movies.html')
    posters_dir = os.path.join(xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources/IMDb Top 250 movies_files'))

    if not os.path.exists(posters_dir):
        os.makedirs(posters_dir)

    response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    response.raise_for_status()
    html = response.text

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    soup = BeautifulSoup(html, 'html.parser')
    for row in soup.select('tbody.lister-list tr'):
        img = row.find('img')
        if img and img.get('src'):
            poster_url = img['src']
            poster_name = poster_url.split('/')[-1].split('?')[0]
            poster_path = os.path.join(posters_dir, poster_name)
            if not os.path.exists(poster_path):
                try:
                    img_data = requests.get(poster_url, timeout=10).content
                    with open(poster_path, 'wb') as pf:
                        pf.write(img_data)
                except Exception as e:
                    xbmc.log(f"Không tải được poster: {poster_url} - {e}", level=xbmc.LOGWARNING)
    xbmcgui.Dialog().ok("Hoàn tất", "Đã làm mới danh sách IMDb Top 250 và poster.")

if __name__ == '__main__':
    router(sys.argv[2][1:])