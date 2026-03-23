import sys
import xbmcgui
import xbmcplugin
import xbmc
import xbmcaddon
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

STRM_CONFIG_FILE = os.path.join(xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources'), 'strm_config.json')

GSHEET_CONFIG_FILE = os.path.join(xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources'), 'gsheet_config.json')

def load_gsheet_id():
    try:
        with open(GSHEET_CONFIG_FILE, 'r') as f:
            return json.load(f).get('sheet_id', '')
    except:
        return ''

def save_gsheet_id(sheet_id):
    try:
        with open(GSHEET_CONFIG_FILE, 'w') as f:
            json.dump({'sheet_id': sheet_id}, f)
    except:
        pass

def load_strm_dir():
    try:
        with open(STRM_CONFIG_FILE, 'r') as f:
            return json.load(f).get('strm_dir', '')
    except:
        return ''

def save_strm_dir(strm_dir):
    try:
        with open(STRM_CONFIG_FILE, 'w') as f:
            json.dump({'strm_dir': strm_dir}, f)
    except:
        pass


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
    Lấy danh sách Top 250 IMDB từ tệp HTML cục bộ, bao gồm cả IMDb ID.
    """
    movies = []
    try:
        with open(IMDB_HTML_FILE, 'r', encoding='utf-8') as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, 'html.parser')

        # Duyệt qua từng item trong danh sách
        for item in soup.select('li.ipc-metadata-list-summary-item'):
            try:
                # 1. Lấy IMDb ID từ link tiêu đề
                link_element = item.select_one('a.ipc-title-link-wrapper')
                imdb_id = ""
                if link_element and 'href' in link_element.attrs:
                    match = re.search(r'(tt\d+)', link_element['href'])
                    if match:
                        imdb_id = match.group(1)

                # 2. Lấy tiêu đề và năm
                title_element = item.select_one('h3.ipc-title__text')
                if title_element:
                    title_full = title_element.get_text(strip=True)
                    title = title_full.split('.', 1)[1].strip() if '.' in title_full else title_full
                else: continue

                year_element = item.select_one('span.sc-4b408797-8.iurwGb.cli-title-metadata-item')
                year = year_element.get_text(strip=True) if year_element else "N/A"

                # 3. Lấy Rating và Poster
                rating_element = item.select_one('span.ipc-rating-star--rating')
                imdb_rating = rating_element.get_text(strip=True) if rating_element else "N/A"

                poster_element = item.select_one('img')
                poster_url = poster_element['src'] if poster_element and 'src' in poster_element.attrs else ""
                poster_url = poster_url.replace('./IMDb Top 250 movies_files/', '')

                # 4. Lấy Plot từ cache (Wikipedia)
                plot_vi = get_vietnamese_plot(title, year)

                movies.append({
                    'title': title,
                    'year': year,
                    'imdb_id': imdb_id, # THÊM MỚI
                    'imdb_rating': imdb_rating,
                    'poster': poster_url,
                    'plot': plot_vi
                })
            except Exception as e:
                continue
        return movies
    except Exception as e:
        xbmc.log(f"Lỗi đọc file IMDb: {e}", level=xbmc.LOGERROR)
        return []

def search_fshare(movie_title, movie_year=None, season=None, episode=None):
    if season and episode:
        # TV series: tìm theo tên + SxxExx
        search_query = f"{movie_title} S{int(season):02d}E{int(episode):02d}"
    else:
        # Movie: tìm theo tên + năm
        search_query = f"{movie_title} {movie_year or ''}".strip()
    
    fshare_results = timfshare(search_query)

    links = []
    if 'items' in fshare_results and isinstance(fshare_results['items'], list):
        for item in fshare_results['items']:
            size = item.get('info', {}).get('size', 0)
            plugin_url = item.get('path', '')
            # Lấy URL Fshare gốc từ plugin URL
            fshare_url = ''
            fshare_match = re.search(r'url=(https?://[^\s&]+)', plugin_url)
            if fshare_match:
                fshare_url = urllib.parse.unquote(fshare_match.group(1))
            links.append({
                'title': item.get('label', 'No Title'),
                'url': plugin_url,
                'fshare_url': fshare_url,
                'size': size
            })
            
    return links

def search_fshare_manual():
    """
    Cho phép người dùng nhập từ khóa tìm kiếm từ bàn phím.
    """
    keyboard = xbmc.Keyboard('', 'Nhập tên phim cần tìm trên Fshare')
    keyboard.doModal()
    if keyboard.isConfirmed():
        query = keyboard.getText().strip()
        if query:
            show_fshare_links(query, '')

def create_strm_file(title, url, movie_info=None):
    """
    Tạo file .strm với tên chuẩn hóa để Kodi nhận diện chất lượng.
    """
    def parse_language_prefix(filename):
        langs = []
        prefix_match = re.match(r'^\s*\((.*?)\)\s*', filename)
        if prefix_match:
            prefix = prefix_match.group(1).upper()
            if any(x in prefix for x in ['THUYET MINH', 'TM']):
                langs.append('TM')
            if any(x in prefix for x in ['SUB VIET', 'SUBVIET', 'VIETSUB']):
                langs.append('VieSub')
            filename = filename[prefix_match.end():]
        return filename, langs

    def normalize_tech_part(tech_part):
        normalize_map = {
            # Độ phân giải
            '2160P': '2160p', '1080P': '1080p', '720P': '720p',
            '480P': '480p', '576P': '576p',
            # Nguồn
            'BLURAY': 'BluRay', 'BLU-RAY': 'BluRay', 'BDRIP': 'BDRip',
            'WEBRIP': 'WEBRip', 'WEB-DL': 'WEB-DL', 'WEBDL': 'WEB-DL',
            'HDTV': 'HDTV', 'DVDRIP': 'DVDRip', 'REMUX': 'REMUX',
            'AMZN': 'AMZN', 'NF': 'NF', 'HMAX': 'HMAX',
            'DSNP': 'DSNP', 'MA': 'MA', 'UHD': 'UHD',
            # Codec video
            'X264': 'x264', 'X265': 'x265',
            'H264': 'H.264', 'H.264': 'H.264',
            'H265': 'H.265', 'H.265': 'H.265',
            'HEVC': 'HEVC', 'AVC': 'AVC', 'AV1': 'AV1',
            # Codec audio
            'DTS-HD': 'DTS-HD', 'DTS': 'DTS', 'TRUEHD': 'TrueHD',
            'ATMOS': 'Atmos', 'DD5.1': 'DD5.1', 'DDP5.1': 'DDP5.1',
            'DDP': 'DDP', 'AAC': 'AAC', 'AC3': 'AC3',
            'FLAC': 'FLAC', 'MP3': 'MP3',
            # HDR
            'HDR10+': 'HDR10+', 'HDR10': 'HDR10', 'HDR': 'HDR',
            'DOLBY VISION': 'DV', 'DV': 'DV',
            # Ngôn ngữ
            'VIE': 'ViE', 'ENG': 'ENG', 'VIET': 'ViE',
            # Bit depth
            '10BIT': '10bit', '8BIT': '8bit',
        }
        tokens = tech_part.split('.')
        normalized = []
        for token in tokens:
            upper = token.upper()
            if upper in normalize_map:
                normalized.append(normalize_map[upper])
            else:
                normalized.append(token)
        return '.'.join(normalized)

    def parse_media_info(filename):
        info = {}
        # Tìm năm trong tên file kể cả trong ngoặc
        year_match = re.search(r'\b(19|20)\d{2}\b', filename)
        info['year'] = year_match.group(0) if year_match else ''

        # Thay (2008) → 2008, giữ lại năm làm mốc tách tên phim
        filename_clean = re.sub(r'\(((19|20)\d{2})\)', r'\1', filename)
        # Xóa các ngoặc khác không phải năm
        filename_clean = re.sub(r'\(.*?\)', '', filename_clean)
        # Xóa số thứ tự đầu như "02. "
        filename_clean = re.sub(r'^\d+\.\s*', '', filename_clean)
        filename_clean = re.sub(r'\s+', ' ', filename_clean).strip()

        # Tên phim = phần trước năm
        year_match2 = re.search(r'\b(19|20)\d{2}\b', filename_clean)
        if year_match2:
            title_part = filename_clean[:year_match2.start()]
        else:
            title_part = filename_clean
        title_part = re.sub(r'\.(mkv|mp4|wmv|iso|ts)$', '', title_part, flags=re.IGNORECASE)
        title_part = re.sub(r'[._]', ' ', title_part).strip()
        title_part = re.sub(r'[\s\-]+$', '', title_part)
        info['parsed_title'] = title_part

        return info

    # Xử lý tên file: tách prefix ngôn ngữ trước
    clean_title, lang_tags = parse_language_prefix(title)

    # Parse năm và tên phim
    media = parse_media_info(clean_title)

    # Lấy title và year
    movie_title = ''
    year = media['year']
    if movie_info:
        if movie_info.get('title'):
            movie_title = movie_info.get('title')
        year = movie_info.get('year', '') or year
    if not movie_title:
        movie_title = media['parsed_title']

    # Tạo bản clean để lấy tech_part
    # Thay (2008) → 2008 để giữ năm làm mốc
    clean_for_tech = re.sub(r'\(((19|20)\d{2})\)', r'\1', clean_title)
    # Xóa các ngoặc khác không phải năm
    clean_for_tech = re.sub(r'\(.*?\)', '', clean_for_tech)
    # Xóa số thứ tự đầu
    clean_for_tech = re.sub(r'^\d+\.\s*', '', clean_for_tech)
    clean_for_tech = re.sub(r'\s+', ' ', clean_for_tech).strip()

    # Lấy phần kỹ thuật từ năm trở đi
    year_match = re.search(r'\b(19|20)\d{2}\b', clean_for_tech)
    if year_match:
        tech_part = clean_for_tech[year_match.start():]
        tech_part = re.sub(r'\.(mkv|mp4|wmv|iso|ts)$', '', tech_part, flags=re.IGNORECASE)
        tech_part = tech_part.replace(' ', '.')
        tech_part = re.sub(r'\.+', '.', tech_part).strip('.')
        tech_part = normalize_tech_part(tech_part)
    else:
        tech_part = ''

    # Ghép tên phim + phần kỹ thuật + tag ngôn ngữ sau năm
    name_part = movie_title.replace(' ', '.')

    if tech_part and lang_tags:
        year_in_tech = re.search(r'\b(19|20)\d{2}\b', tech_part)
        if year_in_tech:
            insert_pos = year_in_tech.end()
            tech_part = tech_part[:insert_pos] + '.' + '.'.join(lang_tags) + tech_part[insert_pos:]
        else:
            tech_part = '.'.join(lang_tags) + '.' + tech_part

    safe_title = f"{name_part}.{tech_part}" if tech_part else name_part
    safe_title = re.sub(r'[\\/*?:"<>|()\[\]]', '_', safe_title)
    safe_title = re.sub(r'_+', '_', safe_title)
    safe_title = safe_title.strip('_').strip('.')

# Lấy thư mục đã lưu lần trước
    dialog = xbmcgui.Dialog()
    saved_dir = load_strm_dir()

    if saved_dir:
        choice = dialog.yesno(
            'Thư mục lưu .strm',
            f"Dùng lại thư mục:\n{saved_dir}",
            nolabel='Đổi thư mục',
            yeslabel='Dùng lại'
        )
        if choice:
            strm_dir = saved_dir
        else:
            strm_dir = dialog.browse(3, 'Chọn thư mục lưu file .strm', 'files')
            if not strm_dir:
                return
            save_strm_dir(strm_dir)
    else:
        strm_dir = dialog.browse(3, 'Chọn thư mục lưu file .strm', 'files')
        if not strm_dir:
            return
        save_strm_dir(strm_dir)

    strm_path = os.path.join(strm_dir, f"{safe_title}.strm")

    try:
        strm_file = xbmcvfs.File(strm_path, 'w')
        strm_file.write(url)
        strm_file.close()
        xbmc.log(f"Created STRM: {strm_path}", level=xbmc.LOGINFO)

        # Scan chỉ file vừa tạo vào library qua JSON-RPC
        import json as _json
        scan_query = _json.dumps({
            "jsonrpc": "2.0",
            "method": "VideoLibrary.Scan",
            "params": {
                "directory": strm_path,  # Chỉ scan đúng file này
                "showdialogs": False
            },
            "id": 1
        })
        xbmc.executeJSONRPC(scan_query)

        # Poll chờ scan xong tối đa 15 giây
        for _ in range(15):
            xbmc.sleep(1000)
            status_query = _json.dumps({
                "jsonrpc": "2.0",
                "method": "XBMC.GetInfoBooleans",
                "params": {"booleans": ["Library.IsScanning"]},
                "id": 2
            })
            result = _json.loads(xbmc.executeJSONRPC(status_query))
            if not result.get('result', {}).get('Library.IsScanning', False):
                break

        xbmcgui.Dialog().ok("Thành công", f"Đã tạo và thêm vào library:\n{safe_title}.strm")

    except Exception as e:
        xbmcgui.Dialog().ok("Lỗi", f"Không tạo được file:\n{e}")
        xbmc.log(f"STRM error: {e}", level=xbmc.LOGERROR)

def download_fshare(fshare_url, title, url, movie_info=None):
    """
    Download file Fshare về cùng thư mục với file .strm.
    Dùng token/session từ VietmediaF để lấy direct link.
    Tên file giống quy tắc đặt tên .strm nhưng giữ extension gốc.
    """
    import threading

    # --- Lấy token/session từ VietmediaF settings ---
    def get_fshare_token():
        try:
            vietmediaf = xbmcaddon.Addon('plugin.video.vietmediaF')
            token = vietmediaf.getSetting('tokenfshare')
            session_id = vietmediaf.getSetting('sessionfshare')
            return token, session_id
        except Exception as e:
            xbmc.log(f"Không lấy được VietmediaF settings: {e}", level=xbmc.LOGERROR)
            return None, None

    # --- Lấy direct download link từ Fshare API ---
    def get_direct_link(fshare_url, token, session_id):
        try:
            modified_url = fshare_url
            if '?' not in modified_url:
                modified_url += '?share=8805984'
            else:
                modified_url += '&share=8805984'

            payload = {
                'zipflag': 0,
                'url': modified_url,
                'password': '',
                'token': token
            }
            headers = {
                'Content-Type': 'application/json',
                'User-Agent': 'kodivietmediaf-K58W6U',
                'Cookie': f'session_id={session_id}'
            }
            resp = requests.post(
                'https://api.fshare.vn/api/session/download',
                json=payload,
                headers=headers,
                timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get('location', '')
            else:
                xbmc.log(f"Fshare API error: {resp.status_code} {resp.text}", level=xbmc.LOGERROR)
                return None
        except Exception as e:
            xbmc.log(f"get_direct_link error: {e}", level=xbmc.LOGERROR)
            return None

    # --- Tạo tên file theo quy tắc .strm ---
    def make_safe_name(title, movie_info):
        def parse_language_prefix(filename):
            langs = []
            prefix_match = re.match(r'^\s*\((.*?)\)\s*', filename)
            if prefix_match:
                prefix = prefix_match.group(1).upper()
                if any(x in prefix for x in ['THUYET MINH', 'TM']):
                    langs.append('TM')
                if any(x in prefix for x in ['SUB VIET', 'SUBVIET', 'VIETSUB']):
                    langs.append('VieSub')
                filename = filename[prefix_match.end():]
            return filename, langs

        def normalize_tech_part(tech_part):
            normalize_map = {
                '2160P': '2160p', '1080P': '1080p', '720P': '720p',
                '480P': '480p', '576P': '576p',
                'BLURAY': 'BluRay', 'BLU-RAY': 'BluRay', 'BDRIP': 'BDRip',
                'WEBRIP': 'WEBRip', 'WEB-DL': 'WEB-DL', 'WEBDL': 'WEB-DL',
                'HDTV': 'HDTV', 'DVDRIP': 'DVDRip', 'REMUX': 'REMUX',
                'AMZN': 'AMZN', 'NF': 'NF', 'HMAX': 'HMAX',
                'DSNP': 'DSNP', 'MA': 'MA', 'UHD': 'UHD',
                'X264': 'x264', 'X265': 'x265',
                'H264': 'H.264', 'H.264': 'H.264',
                'H265': 'H.265', 'H.265': 'H.265',
                'HEVC': 'HEVC', 'AVC': 'AVC', 'AV1': 'AV1',
                'DTS-HD': 'DTS-HD', 'DTS': 'DTS', 'TRUEHD': 'TrueHD',
                'ATMOS': 'Atmos', 'DD5.1': 'DD5.1', 'DDP5.1': 'DDP5.1',
                'DDP': 'DDP', 'AAC': 'AAC', 'AC3': 'AC3',
                'FLAC': 'FLAC', 'MP3': 'MP3',
                'HDR10+': 'HDR10+', 'HDR10': 'HDR10', 'HDR': 'HDR',
                'DOLBY VISION': 'DV', 'DV': 'DV',
                'VIE': 'ViE', 'ENG': 'ENG', 'VIET': 'ViE',
                '10BIT': '10bit', '8BIT': '8bit',
            }
            return '.'.join([normalize_map.get(t.upper(), t) for t in tech_part.split('.')])

        clean_title, lang_tags = parse_language_prefix(title)

        # Tên phim từ movie_info hoặc parse từ tên file
        movie_title_parsed = ''
        if movie_info and movie_info.get('title'):
            movie_title_parsed = movie_info.get('title')

        if not movie_title_parsed:
            clean_for_name = re.sub(r'\(((19|20)\d{2})\)', r'\1', clean_title)
            clean_for_name = re.sub(r'\(.*?\)', '', clean_for_name)
            clean_for_name = re.sub(r'^\d+\.\s*', '', clean_for_name)
            clean_for_name = re.sub(r'\s+', ' ', clean_for_name).strip()
            ym = re.search(r'\b(19|20)\d{2}\b', clean_for_name)
            title_part = clean_for_name[:ym.start()] if ym else clean_for_name
            title_part = re.sub(r'\.(mkv|mp4|wmv|iso|ts)$', '', title_part, flags=re.IGNORECASE)
            title_part = re.sub(r'[._]', ' ', title_part).strip()
            movie_title_parsed = re.sub(r'[\s\-]+$', '', title_part)

        # Tech part
        clean_for_tech = re.sub(r'\(((19|20)\d{2})\)', r'\1', clean_title)
        clean_for_tech = re.sub(r'\(.*?\)', '', clean_for_tech)
        clean_for_tech = re.sub(r'^\d+\.\s*', '', clean_for_tech)
        clean_for_tech = re.sub(r'\s+', ' ', clean_for_tech).strip()

        year_match = re.search(r'\b(19|20)\d{2}\b', clean_for_tech)
        season_match = re.search(r'\bS\d{2}E\d{2}\b', clean_for_tech, re.IGNORECASE)
        if year_match:
            tech_part = clean_for_tech[year_match.start():]
            tech_part = re.sub(r'\.(mkv|mp4|wmv|iso|ts)$', '', tech_part, flags=re.IGNORECASE)
            tech_part = tech_part.replace(' ', '.')
            tech_part = re.sub(r'\.+', '.', tech_part).strip('.')
            tech_part = normalize_tech_part(tech_part)
        elif season_match:
            # TV series không có năm: lấy từ SxxExx trở đi
            tech_part = clean_for_tech[season_match.start():]
            tech_part = re.sub(r'\.(mkv|mp4|wmv|iso|ts)$', '', tech_part, flags=re.IGNORECASE)
            tech_part = tech_part.replace(' ', '.')
            tech_part = re.sub(r'\.+', '.', tech_part).strip('.')
            tech_part = normalize_tech_part(tech_part)
        else:
            tech_part = ''

        name_part = movie_title_parsed.replace(' ', '.')
        if tech_part and lang_tags:
            year_in_tech = re.search(r'\b(19|20)\d{2}\b', tech_part)
            if year_in_tech:
                insert_pos = year_in_tech.end()
                tech_part = tech_part[:insert_pos] + '.' + '.'.join(lang_tags) + tech_part[insert_pos:]
            else:
                tech_part = '.'.join(lang_tags) + '.' + tech_part

        safe_name = f"{name_part}.{tech_part}" if tech_part else name_part
        safe_name = re.sub(r'[\\/*?:"<>|()\[\]]', '_', safe_name)
        safe_name = re.sub(r'_+', '_', safe_name)
        return safe_name.strip('_').strip('.')

    # --- Kiểm tra fshare_url ---
    if not fshare_url:
        # Thử extract từ plugin URL
        fshare_match = re.search(r'url=(https?://[^\s&]+)', url)
        if fshare_match:
            fshare_url = urllib.parse.unquote(fshare_match.group(1))
    if not fshare_url:
        xbmcgui.Dialog().ok('Lỗi', 'Không lấy được URL Fshare để download.')
        return

    # --- Lấy thư mục lưu ---
    dialog = xbmcgui.Dialog()
    saved_dir = load_strm_dir()
    if saved_dir:
        choice = dialog.yesno(
            'Thư mục download',
            f"Download vào thư mục:\n{saved_dir}",
            nolabel='Đổi thư mục',
            yeslabel='Dùng lại'
        )
        if choice:
            download_dir = saved_dir
        else:
            download_dir = dialog.browse(3, 'Chọn thư mục download', 'files')
            if not download_dir:
                return
            save_strm_dir(download_dir)
    else:
        download_dir = dialog.browse(3, 'Chọn thư mục download', 'files')
        if not download_dir:
            return
        save_strm_dir(download_dir)

    # --- Lấy extension gốc ---
    ext = '.mkv'
    for e in ['.mkv', '.mp4', '.wmv', '.iso', '.ts']:
        if title.lower().endswith(e):
            ext = e
            break

    # --- Tạo tên file ---
    safe_name = make_safe_name(title, movie_info)
    dest_path = os.path.join(download_dir, f"{safe_name}{ext}")

    # --- Xác nhận ---
    if not dialog.yesno('Xác nhận download',
                        f"Download file:\n{safe_name}{ext}\n\nVào thư mục:\n{download_dir}"):
        return

    # --- Lấy token VietmediaF ---
    token, session_id = get_fshare_token()
    if not token or not session_id:
        xbmcgui.Dialog().ok('Lỗi', 'Không lấy được token Fshare từ VietmediaF.\nVui lòng đăng nhập Fshare trong VietmediaF trước.')
        return

    # --- Lấy direct link ---
    xbmcgui.Dialog().notification('Download', 'Đang lấy link từ Fshare...', time=2000)
    direct_url = get_direct_link(fshare_url, token, session_id)
    if not direct_url:
        xbmcgui.Dialog().ok('Lỗi', 'Không lấy được direct link từ Fshare.\nKiểm tra lại tài khoản VietmediaF.')
        return

    xbmc.log(f"Downloading: {direct_url} → {dest_path}", level=xbmc.LOGINFO)

    # --- Download trong thread riêng ---
    def download_thread():
        try:
            headers = {'User-Agent': 'kodivietmediaf-K58W6U'}
            resp = requests.get(direct_url, headers=headers, stream=True, timeout=30)
            resp.raise_for_status()

            total = int(resp.headers.get('content-length', 0))
            downloaded = 0

            progress_dialog = xbmcgui.DialogProgress()
            progress_dialog.create('Đang download', f'{safe_name}{ext}')

            # Dùng xbmcvfs.File thay vì open() để hỗ trợ SMB/network path
            vfs_file = xbmcvfs.File(dest_path, 'w')
            canceled = False
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    vfs_file.write(bytearray(chunk))
                    downloaded += len(chunk)
                    if progress_dialog.iscanceled():
                        canceled = True
                        break
                    if total:
                        percent = int(downloaded / total * 100)
                        dl_mb = downloaded / (1024 * 1024)
                        total_mb = total / (1024 * 1024)
                        progress_dialog.update(percent, f'{dl_mb:.1f} MB / {total_mb:.1f} MB ({percent}%)')
            vfs_file.close()

            progress_dialog.close()

            if canceled:
                xbmcvfs.delete(dest_path)
                xbmcgui.Dialog().notification('Download', 'Đã hủy download', time=2000)
                return

            xbmcgui.Dialog().ok('Download xong', f"Đã tải về:\n{safe_name}{ext}")
            xbmc.log(f"Download complete: {dest_path}", level=xbmc.LOGINFO)

        except Exception as e:
            xbmcgui.Dialog().ok('Lỗi download', f"Không tải được file:\n{e}")
            xbmc.log(f"Download error: {e}", level=xbmc.LOGERROR)
            if xbmcvfs.exists(dest_path):
                xbmcvfs.delete(dest_path)

    t = threading.Thread(target=download_thread)
    t.start()


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

def main_menu():
    """
    Menu gốc của addon.
    """
    xbmcplugin.setPluginCategory(addon_handle, 'Top 250 IMDB Fshare Finder')
    xbmcplugin.setContent(addon_handle, 'files')

    # --- Tìm kiếm thủ công ---
    search_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'search_manual'})
    search_item = xbmcgui.ListItem('[🔍 Tìm kiếm Fshare]')
    search_item.setArt({'icon': 'DefaultAddonsSearch.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=search_url, listitem=search_item, isFolder=True)

    # --- IMDb Top 250 ---
    imdb_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'list_imdb'})
    imdb_item = xbmcgui.ListItem('[🏆 IMDb Top 250]')
    imdb_item.setArt({'icon': 'DefaultMovies.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=imdb_url, listitem=imdb_item, isFolder=True)

    # --- Cộng đồng chia sẻ ---
    community_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'list_community'})
    community_item = xbmcgui.ListItem('[👥 Cộng đồng chia sẻ]')
    community_item.setArt({'icon': 'DefaultAddonVideo.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=community_url, listitem=community_item, isFolder=True)

    # --- Đổi Google Sheet ---
    change_sheet_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'change_gsheet'})
    change_sheet_item = xbmcgui.ListItem('[⚙️ Đổi Google Sheet cộng đồng]')
    change_sheet_item.setArt({'icon': 'DefaultAddonProgram.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=change_sheet_url, listitem=change_sheet_item, isFolder=False)

    xbmcplugin.endOfDirectory(addon_handle)


def list_community():
    """
    Hiển thị danh sách phim từ Google Sheet cộng đồng chia sẻ.
    """
    xbmcplugin.setPluginCategory(addon_handle, 'Cộng đồng chia sẻ')
    xbmcplugin.setContent(addon_handle, 'movies')

    # Lấy sheet_id đã lưu, nếu chưa có thì hỏi lần đầu
    sheet_id = load_gsheet_id()

    if not sheet_id:
        keyboard = xbmc.Keyboard('', 'Nhập Google Sheet ID hoặc URL')
        keyboard.doModal()
        if not keyboard.isConfirmed():
            xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
            return
        sheet_input = keyboard.getText().strip()
        if not sheet_input:
            xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
            return
        match = re.search(r'/d/([a-zA-Z0-9-_]+)', sheet_input)
        sheet_id = match.group(1) if match else sheet_input
        save_gsheet_id(sheet_id)

    # Fetch data từ Google Sheet - dùng luôn không hỏi
    gsheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?gid=0&headers=1"
    try:
        resp = requests.get(gsheet_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
        resp.raise_for_status()
        # Parse JSON: dùng find/rfind thay vì regex để tránh lỗi với dữ liệu dài
        text = resp.text
        start = text.find('(')
        end = text.rfind(')')
        if start == -1 or end == -1 or start >= end:
            xbmc.log(f"GSheet parse error, response[:200]: {text[:200]}", level=xbmc.LOGERROR)
            xbmcgui.Dialog().ok('Lỗi', 'Không đọc được dữ liệu từ Google Sheet.\nKiểm tra Sheet ID và quyền truy cập (phải set "Anyone with link can view").')
            xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
            return
        nd = json.loads(text[start+1:end])
    except Exception as e:
        xbmcgui.Dialog().ok('Lỗi', f"Không kết nối được Google Sheet:\n{str(e)[:200]}")
        xbmc.log(f"GSheet error: {e}", level=xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
        return

    rows = nd.get('table', {}).get('rows', [])
    if not rows:
        xbmcgui.Dialog().ok('Thông báo', 'Google Sheet không có dữ liệu.')
        xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
        return

    list_items = []
    for row in rows:
        try:
            cells = row.get('c', [])
            def cell(i):
                try:
                    v = cells[i]
                    return str(v['v']) if v and v.get('v') is not None else ''
                except:
                    return ''

            name_raw = cell(0)
            link     = cell(1)
            thumb    = cell(2)
            plot     = cell(3)
            fanart   = cell(4) or thumb
            genre    = cell(5)
            rating_raw = cell(6)

            # Xử lý tên có pipe
            if '|' in name_raw:
                parts = name_raw.split('|')
                name = parts[0].replace('*', '').replace('@', '').strip()
                if len(parts) > 1 and not link:
                    link = parts[1].strip()
                if len(parts) > 2 and not thumb:
                    thumb = parts[2].strip()
                if len(parts) > 3 and not plot:
                    plot = parts[3].strip()
            else:
                name = name_raw.replace('*', '').replace('@', '').strip()

            # Xử lý token trong link
            if link and 'token' in link:
                m = re.search(r'(https.+?)\/\?token', link)
                if m:
                    link = m.group(1)

            if not name or not link:
                continue
            if not any(x in link for x in ['http', 'rtp', 'udp', 'acestream', 'plugin']):
                continue

            # Xác định playable hay folder
            is_folder = any(x in link for x in [
                'fshare.vn/folder', 'docs.google.com', 'pastebin.com', 'menu', 'm3uhttp'
            ]) or ('4share.vn' in link and '/d/' in link)
            is_playable = not is_folder

            try:
                rating = float(rating_raw) if rating_raw and rating_raw.replace('.', '', 1).isdigit() else 0.0
            except:
                rating = 0.0

            list_item = xbmcgui.ListItem(label=name)
            list_item.setInfo('video', {
                'title': name, 'plot': plot,
                'genre': genre, 'rating': rating, 'mediatype': 'movie'
            })
            if thumb:
                list_item.setArt({'thumb': thumb, 'poster': thumb, 'icon': thumb, 'fanart': fanart})

            if is_playable:
                list_item.setProperty('IsPlayable', 'true')
                play_url = f'plugin://plugin.video.vietmediaF?action=play&url={link}'
                list_items.append((play_url, list_item, False))
            else:
                if 'docs.google.com' in link:
                    browse_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'browse_gsheet', 'url': link})
                else:
                    browse_url = f'plugin://plugin.video.vietmediaF?action=play&url={link}'
                list_items.append((browse_url, list_item, True))

        except Exception as e:
            xbmc.log(f"GSheet row error: {e}", level=xbmc.LOGWARNING)
            continue

    if list_items:
        xbmcplugin.addDirectoryItems(addon_handle, list_items)
    else:
        xbmcgui.Dialog().ok('Thông báo', 'Không có nội dung để hiển thị.')

    xbmcplugin.endOfDirectory(addon_handle)


def list_movies():
    """
    Hiển thị danh sách IMDb Top 250.
    """
    xbmcplugin.setPluginCategory(addon_handle, 'IMDb Top 250')
    xbmcplugin.setContent(addon_handle, 'movies')

    # Lấy danh sách phim
    movies = get_imdb_top250_from_file()
    
    for i, movie in enumerate(movies):
        title_label = f"{i+1}. {movie['title']} ({movie['year']}) - ⭐ {movie['imdb_rating']}"
        list_item = xbmcgui.ListItem(title_label)

        # THIẾT LẬP INFO CHO KODI VÀ TRAKT
        # Việc gán mediatype và imdbnumber ở đây giúp Trakt nhận diện ngay từ menu
        video_info = {
            'title': movie['title'],
            'year': int(movie['year']) if movie['year'].isdigit() else None,
            'plot': movie.get('plot', ''),
            'rating': movie['imdb_rating'],
            'mediatype': 'movie',       # Khai báo phim lẻ
            'imdbnumber': movie['imdb_id'] # ID định danh cho Trakt
        }
        list_item.setInfo('video', video_info)

        # Gắn Poster
        poster_path = os.path.join(
            xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources/IMDb Top 250 movies_files'), 
            movie['poster']
        )
        list_item.setArt({'thumb': poster_path, 'icon': poster_path, 'poster': poster_path})

        # TRUYỀN ID SANG ROUTER
        # Khi nhấn vào phim, ID này sẽ được gửi sang hàm show_fshare_links
        url_params = {
            'action': 'search_fshare',
            'title': movie['title'],
            'year': movie['year'],
            'imdb': movie['imdb_id'] # Truyền ID tt...
        }
        url = sys.argv[0] + '?' + urllib.parse.urlencode(url_params)
        
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=url, listitem=list_item, isFolder=True)

    # Các menu chức năng khác
    refresh_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'refresh_imdb'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=refresh_url, listitem=xbmcgui.ListItem('[🔄 Làm mới IMDb Top 250]'), isFolder=False)

    refresh_plot_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'refresh_plot'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=refresh_plot_url, listitem=xbmcgui.ListItem('[📖 Làm mới Plot Wikipedia]'), isFolder=False)

    xbmcplugin.endOfDirectory(addon_handle)

def show_fshare_links(movie_title, movie_year, imdb_id=None, tmdb_id=None, season=None, episode=None, tvshowtitle=None):
    content_type = 'movies' if not season else 'episodes'
    xbmcplugin.setContent(addon_handle, content_type)
    xbmcplugin.setPluginCategory(addon_handle, f'Links: {movie_title}')

    links = search_fshare(movie_title, movie_year, season=season, episode=episode)
    if not links:
        notify(f"Không tìm thấy link cho {movie_title}")
        xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
        return

    links.sort(key=lambda x: x.get('size', 0), reverse=True)

    poster_path = ""
    plot_text = ""
    movie_info_local = None
    if not season:
        imdb_movies = get_imdb_top250_from_file()
        movie_info_local = next((m for m in imdb_movies if (imdb_id and m['imdb_id'] == imdb_id) or m['title'] == movie_title), None)
        if movie_info_local:
            poster_path = os.path.join(
                xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}/resources/IMDb Top 250 movies_files'),
                movie_info_local.get('poster', '')
            )
            plot_text = movie_info_local.get('plot', '')

    list_items = []

    for i, link_info in enumerate(links):
        size = link_info.get('size', 0)
        size_str = f"{size/(1024**3):.2f} GB" if size >= 1024**3 else f"{size/(1024**2):.2f} MB"

        title_label = f"{i+1}: {link_info['title']} - ({size_str})"

        # URL gọi qua action play_trakt để set script.trakt.ids trước khi play
        play_params = {
            'action': 'play_trakt',
            'url': link_info['url'],
            'imdb': str(imdb_id) if imdb_id else '',
            'tmdb': str(tmdb_id) if tmdb_id else '',
            'title': movie_title,
            'year': str(movie_year) if movie_year else '',
            'season': str(season) if season else '',
            'episode': str(episode) if episode else '',
            'tvshowtitle': tvshowtitle or '',
        }
        play_url = sys.argv[0] + '?' + urllib.parse.urlencode(play_params)

        list_item = xbmcgui.ListItem(label=title_label, path=play_url)

        # Gán metadata vào ListItem
        info_tag = list_item.getVideoInfoTag()
        info_tag.setTitle(movie_title)

        if plot_text:
            info_tag.setPlot(plot_text)

        if movie_year and str(movie_year).isdigit():
            info_tag.setYear(int(movie_year))

        ids = {}
        if imdb_id:
            ids['imdb'] = str(imdb_id)
            info_tag.setIMDBNumber(str(imdb_id))
        if tmdb_id:
            ids['tmdb'] = str(tmdb_id)
        if ids:
            info_tag.setUniqueIDs(ids, 'imdb' if imdb_id else 'tmdb')

        if season and episode:
            info_tag.setMediaType('episode')
            info_tag.setTvShowTitle(tvshowtitle or movie_title)
            info_tag.setSeason(int(season))
            info_tag.setEpisode(int(episode))
        else:
            info_tag.setMediaType('movie')

        if poster_path:
            list_item.setArt({'thumb': poster_path, 'poster': poster_path, 'fanart': poster_path, 'icon': poster_path})

        list_item.setProperty('IsPlayable', 'true')

        # Context menu
        strm_params = {
            'action': 'create_strm',
            'title': link_info['title'],
            'url': link_info['url'],
            'movie_title': movie_title,
            'movie_year': str(movie_year) if movie_year else '',
            'imdb': str(imdb_id) if imdb_id else '',
            'tmdb': str(tmdb_id) if tmdb_id else '',
            'movie_plot': plot_text,
            'movie_rating': movie_info_local.get('imdb_rating', '') if movie_info_local else '',
        }
        strm_url = sys.argv[0] + '?' + urllib.parse.urlencode(strm_params)

        # Context menu download
        dl_params = {
            'action': 'download_fshare',
            'title': link_info['title'],
            'url': link_info['url'],
            'fshare_url': link_info.get('fshare_url', ''),
            'movie_title': movie_title,
            'movie_year': str(movie_year) if movie_year else '',
            'imdb': str(imdb_id) if imdb_id else '',
            'tmdb': str(tmdb_id) if tmdb_id else '',
        }
        dl_url = sys.argv[0] + '?' + urllib.parse.urlencode(dl_params)

        list_item.addContextMenuItems([
            ('💾 Tạo file .strm', f'RunPlugin({strm_url})'),
            ('⬇️ Download về máy', f'RunPlugin({dl_url})')
        ])

        list_items.append((play_url, list_item, False))

    xbmcplugin.addDirectoryItems(addon_handle, list_items)
    xbmcplugin.endOfDirectory(addon_handle)


def set_trakt_ids_and_play(play_url, imdb_id, tmdb_id, movie_title, movie_year, season=None, episode=None, tvshowtitle=None):
    import json as _json

    # Set script.trakt.ids vào Window(10000)
    if imdb_id or tmdb_id:
        trakt_ids = {}
        if imdb_id:
            trakt_ids['imdb'] = str(imdb_id)
        if tmdb_id:
            trakt_ids['tmdb'] = str(tmdb_id)
        xbmcgui.Window(10000).setProperty('script.trakt.ids', _json.dumps(trakt_ids))
        xbmc.log(f"Set script.trakt.ids: {_json.dumps(trakt_ids)}", level=xbmc.LOGINFO)

    # Dùng setResolvedUrl thay vì xbmc.Player().play() để tránh play 2 lần
    list_item = xbmcgui.ListItem(label=movie_title, path=play_url)
    info_tag = list_item.getVideoInfoTag()
    info_tag.setTitle(movie_title)

    if movie_year and str(movie_year).isdigit():
        info_tag.setYear(int(movie_year))

    ids = {}
    if imdb_id:
        ids['imdb'] = str(imdb_id)
        info_tag.setIMDBNumber(str(imdb_id))
    if tmdb_id:
        ids['tmdb'] = str(tmdb_id)
    if ids:
        info_tag.setUniqueIDs(ids, 'imdb' if imdb_id else 'tmdb')

    if season and episode:
        info_tag.setMediaType('episode')
        info_tag.setTvShowTitle(tvshowtitle or movie_title)
        info_tag.setSeason(int(season))
        info_tag.setEpisode(int(episode))
    else:
        info_tag.setMediaType('movie')

    # setResolvedUrl - Kodi đã biết đây là playable item, chỉ cần resolve URL
    xbmcplugin.setResolvedUrl(addon_handle, True, listitem=list_item)

def router(paramstring):
    """
    Router xử lý các yêu cầu từ Kodi, TMDB Helper và các lệnh nội bộ.
    """
    # Loại bỏ dấu '?' ở đầu nếu có và parse tham số
    params = dict(urllib.parse.parse_qsl(paramstring.lstrip('?')))

    if params:
        action = params.get('action')

        if action == 'search_manual':
            search_fshare_manual()

        elif action == 'list_imdb':
            list_movies()

        elif action == 'list_community':
            list_community()

        elif action == 'change_gsheet':
            keyboard = xbmc.Keyboard(load_gsheet_id(), 'Nhập Google Sheet ID hoặc URL mới')
            keyboard.doModal()
            if keyboard.isConfirmed():
                sheet_input = keyboard.getText().strip()
                if sheet_input:
                    match = re.search(r'/d/([a-zA-Z0-9-_]+)', sheet_input)
                    sheet_id = match.group(1) if match else sheet_input
                    save_gsheet_id(sheet_id)
                    xbmcgui.Dialog().notification('Thành công', f'Đã lưu Sheet ID: {sheet_id}', time=3000)

        elif action == 'browse_gsheet':
            gsheet_url = params.get('url', '')
            if gsheet_url:
                try:
                    match = re.search(r'/d/([a-zA-Z0-9-_]+)', gsheet_url)
                    if match:
                        saved = load_gsheet_id()
                        save_gsheet_id(match.group(1))
                        list_community()
                        save_gsheet_id(saved)
                except Exception as e:
                    xbmc.log(f"Browse gsheet error: {e}", level=xbmc.LOGERROR)

        elif action == 'search_fshare':
            # Hứng toàn bộ tham số định danh từ TMDB Helper hoặc List nội bộ
            movie_title = params.get('title')
            movie_year = params.get('year')
            imdb_id = params.get('imdb')
            tmdb_id = params.get('tmdb')
            
            # Các tham số dành riêng cho TV Show
            season = params.get('season')
            episode = params.get('episode')
            tvshowtitle = params.get('tvshowtitle')
            
            if movie_title:
                show_fshare_links(
                    movie_title, 
                    movie_year, 
                    imdb_id=imdb_id, 
                    tmdb_id=tmdb_id, 
                    season=season, 
                    episode=episode, 
                    tvshowtitle=tvshowtitle
                )

        
        elif action == 'refresh_imdb':
            refresh_imdb_top250()
            list_movies()

        elif action == 'refresh_plot':
            refresh_plot_cache()
            list_movies()

        elif action == 'download_fshare':
            info = {
                'title': params.get('movie_title', ''),
                'year': params.get('movie_year', ''),
            }
            download_fshare(
                fshare_url=params.get('fshare_url', ''),
                title=params.get('title', ''),
                url=params.get('url', ''),
                movie_info=info
            )

        elif action == 'create_strm':
            # Hứng dữ liệu để đóng gói file .strm
            # Đảm bảo truyền đủ ID vào info để file .strm sau này cũng scrobble được
            info = {
                'title': params.get('movie_title', params.get('title', '')),
                'year': params.get('movie_year', params.get('year', '')),
                'imdb_id': params.get('imdb_id', params.get('imdb', '')),
                'tmdb_id': params.get('tmdb', ''),
                'plot': params.get('movie_plot', ''),
                'rating': params.get('movie_rating', ''),
            }            
            create_strm_file(params.get('title', ''), params.get('url', ''), info)
        elif action == 'play_trakt':
            set_trakt_ids_and_play(
                play_url=params.get('url'),
                imdb_id=params.get('imdb'),
                tmdb_id=params.get('tmdb'),
                movie_title=params.get('title'),
                movie_year=params.get('year'),
                season=params.get('season') or None,
                episode=params.get('episode') or None,
                tvshowtitle=params.get('tvshowtitle') or None,
            )
    else:
        # Nếu không có tham số, hiển thị menu gốc
        main_menu()

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