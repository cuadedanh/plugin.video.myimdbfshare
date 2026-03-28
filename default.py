import sys
import xbmcgui
import xbmcplugin
import xbmc
import xbmcaddon
import urllib.parse
import os, re, json
import xbmcvfs
import requests
import time

# ID của addon (thư mục của addon)
ADDON_ID = 'plugin.video.myimdbfshare'
addon_handle = int(sys.argv[1])
ADDON = xbmcaddon.Addon(ADDON_ID)
ADDON_PATH = xbmcvfs.translatePath(f'special://home/addons/{ADDON_ID}')
VIDEO_FILE_EXTENSIONS = (
    '.mkv', '.mp4', '.avi', '.wmv', '.iso', '.ts', '.m2ts', '.mov',
    '.mpg', '.mpeg', '.m4v', '.webm', '.flv', '.3gp', '.asf', '.vob',
    '.ogm', '.ogv', '.divx', '.xvid', '.rm', '.rmvb', '.qt', '.f4v',
    '.mts', '.tp', '.trp', '.tod', '.vro', '.mxf'
)

# URL tìm kiếm Fshare.vn (có thể cần điều chỉnh nếu Fshare thay đổi)
FSHARE_SEARCH_API_URL = "https://api.timfshare.com/v1/string-query-search?query="

TMDB_API_KEY = 'YOUR_TMDB_API_KEY'  # Thay bằng API key của bạn

SETTINGS_FILE = os.path.join(ADDON_PATH, 'settings.json')
TMDB_LOOKUP_CACHE_FILE = os.path.join(ADDON_PATH, 'tmdb_lookup_cache.json')
GSHEET_CACHE_FILE = os.path.join(ADDON_PATH, 'gsheet_cache.json')
GSHEET_CACHE_TTL = 300

def load_local_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_local_settings(settings_data):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings_data, f, ensure_ascii=False, indent=2)


def get_local_setting(key, default=''):
    settings_data = load_local_settings()
    return settings_data.get(key, default)


def set_local_setting(key, value):
    settings_data = load_local_settings()
    settings_data[key] = value
    save_local_settings(settings_data)


def is_video_item(name, link=''):
    candidates = [name or '']
    if link:
        try:
            parsed = urllib.parse.urlparse(link)
            candidates.append(parsed.path or '')
        except Exception:
            candidates.append(link)

    for candidate in candidates:
        candidate_lower = str(candidate).lower()
        if any(candidate_lower.endswith(ext) for ext in VIDEO_FILE_EXTENSIONS):
            return True

    release_patterns = [
        r'\b(2160p|1080p|720p|480p|576p)\b',
        r'\b(bluray|blu-ray|bdrip|brrip|web[-.\s]?dl|webrip|hdtv|dvdrip|remux|uhd)\b',
        r'\b(x264|x265|h\.?264|h\.?265|hevc|av1|vc-1)\b',
        r'\b(hdr10\+|hdr10|hdr|dv|dolby[ .-]?vision|hlg)\b',
        r'\b(truehd|dts[-.: ]?x|dts[-.: ]?hd|ddp|eac3|ac3|aac|flac|atmos)\b',
        r'\bS\d{1,2}E\d{1,2}\b',
        r'\b(19|20)\d{2}\b',
    ]

    normalized_name = str(name or '').lower().replace('_', ' ')
    matched_patterns = sum(1 for pattern in release_patterns if re.search(pattern, normalized_name, re.IGNORECASE))
    if matched_patterns >= 2:
        return True

    return False


def load_gsheet_id():
    try:
        return str(get_local_setting('gsheet_id', '') or '').strip()
    except Exception:
        return ''


def save_gsheet_id(sheet_id):
    set_local_setting('gsheet_id', sheet_id or '')


def load_strm_dir():
    try:
        return str(get_local_setting('strm_dir', '') or '').strip()
    except Exception:
        return ''


def save_strm_dir(strm_dir):
    set_local_setting('strm_dir', strm_dir or '')


def load_gsheet_cache():
    if os.path.exists(GSHEET_CACHE_FILE):
        try:
            with open(GSHEET_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_gsheet_cache(cache):
    with open(GSHEET_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_gsheet_cache_ttl():
    try:
        ttl = int(get_local_setting('community_cache_ttl', GSHEET_CACHE_TTL) or GSHEET_CACHE_TTL)
        return max(0, ttl)
    except Exception:
        return GSHEET_CACHE_TTL


def get_community_items_per_page():
    try:
        value = int(get_local_setting('community_items_per_page', 30) or 30)
        return max(1, value)
    except Exception:
        return 30


def get_show_lookup_debug_ids():
    value = get_local_setting('show_lookup_debug_ids', False)
    if isinstance(value, bool):
        return value
    return str(value).lower() == 'true'


def get_metadata_source():
    """Tra ve nguon metadata: 'none', 'tmdb', 'omdb'. Mac dinh 'tmdb'."""
    value = get_local_setting('metadata_source', 'tmdb')
    if value in ('none', 'tmdb', 'omdb'):
        return value
    return 'tmdb'


def get_fetch_ids_on_play():
    value = get_local_setting('fetch_ids_on_play', True)
    if isinstance(value, bool):
        return value
    return str(value).lower() != 'false'


def cycle_metadata_source():
    current = get_metadata_source()
    order = ['none', 'tmdb', 'omdb']
    next_val = order[(order.index(current) + 1) % len(order)]
    set_local_setting('metadata_source', next_val)
    labels = {'none': 'Không tra', 'tmdb': 'TMDb', 'omdb': 'OMDb'}
    xbmcgui.Dialog().notification('Nguồn metadata', f'Đã chuyển sang: {labels[next_val]}', time=2500)


def toggle_bool_setting(key, label):
    current = get_local_setting(key, True)
    if isinstance(current, bool):
        new_val = not current
    else:
        new_val = str(current).lower() == 'false'
    set_local_setting(key, new_val)
    state = 'Bật' if new_val else 'Tắt'
    xbmcgui.Dialog().notification(label, state, time=2500)


def clear_gsheet_cache():
    try:
        if os.path.exists(GSHEET_CACHE_FILE):
            os.remove(GSHEET_CACHE_FILE)
    except Exception as e:
        xbmc.log(f"GSheet cache clear error: {e}", level=xbmc.LOGWARNING)


def choose_strm_directory():
    dialog = xbmcgui.Dialog()
    current_dir = load_strm_dir()
    selected_dir = dialog.browse(3, 'Chọn thư mục lưu .strm và download', 'files', defaultt=current_dir)
    if selected_dir:
        save_strm_dir(selected_dir)
        xbmcgui.Dialog().notification('Thành công', 'Đã lưu thư mục .strm và download', time=3000)


def prompt_text_setting(key, heading, default_value=''):
    keyboard = xbmc.Keyboard(str(default_value or ''), heading)
    keyboard.doModal()
    if keyboard.isConfirmed():
        set_local_setting(key, keyboard.getText().strip())


def prompt_number_setting(key, heading, default_value):
    keyboard = xbmc.Keyboard(str(default_value), heading)
    keyboard.doModal()
    if keyboard.isConfirmed():
        value = keyboard.getText().strip()
        if value.isdigit():
            set_local_setting(key, int(value))
            return True
        xbmcgui.Dialog().notification('Lỗi', 'Chỉ nhập số nguyên dương', time=3000)
    return False


def toggle_debug_setting():
    current_value = get_show_lookup_debug_ids()
    set_local_setting('show_lookup_debug_ids', not current_value)
    xbmcgui.Dialog().notification('Thành công', 'Đã đổi chế độ hiện debug IMDb/TMDb', time=3000)


def settings_menu():
    xbmcplugin.setPluginCategory(addon_handle, 'Cài đặt')
    xbmcplugin.setContent(addon_handle, 'files')

    tmdb_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'set_tmdb_api_key'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=tmdb_url, listitem=xbmcgui.ListItem('[TMDb API Key]'), isFolder=False)

    gsheet_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'change_gsheet'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=gsheet_url, listitem=xbmcgui.ListItem('[Google Sheet ID cộng đồng]'), isFolder=False)

    strm_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'choose_strm_dir'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=strm_url, listitem=xbmcgui.ListItem('[Thư mục lưu .strm và download]'), isFolder=False)

    # --- Nhóm: Nguồn metadata ---
    source_labels = {'none': 'Không tra', 'tmdb': 'TMDb', 'omdb': 'OMDb'}
    current_source = get_metadata_source()
    source_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'cycle_metadata_source'})
    source_item = xbmcgui.ListItem(f'[Nguồn metadata: {source_labels[current_source]}]')
    source_item.getVideoInfoTag().setPlot(
        'Chọn nguồn tra metadata khi duyệt danh sách. '
        'Không tra: tắt hoàn toàn (nhanh nhất). '
        'TMDb: cần TMDb API Key. '
        'OMDb: cần OMDb API Key. '
        'Bấm để chuyển vòng giữa các tùy chọn.'
    )
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=source_url, listitem=source_item, isFolder=False)

    # --- Tra ID khi play ---
    ids_on_play_state = 'Bật' if get_fetch_ids_on_play() else 'Tắt'
    ids_play_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'toggle_fetch_ids_on_play'})
    ids_play_item = xbmcgui.ListItem(f'[Tra IMDb/TMDb ID khi play: {ids_on_play_state}]')
    ids_play_item.getVideoInfoTag().setPlot(
        'Khi bật: nếu item chưa có IMDb/TMDb ID lúc play, addon sẽ tự tra API để lấy ID '
        'trước khi phát — giúp Trakt scrobble và TMDb Helper hiện metadata đúng. '
        'Khi tắt: chỉ dùng ID đã có sẵn từ lúc browse.'
    )
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=ids_play_url, listitem=ids_play_item, isFolder=False)

    debug_state = 'Bật' if get_show_lookup_debug_ids() else 'Tắt'
    debug_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'toggle_debug_ids'})
    debug_item = xbmcgui.ListItem(f'[Hiện debug IMDb/TMDb IDs: {debug_state}]')
    debug_info = debug_item.getVideoInfoTag()
    debug_info.setTitle('Hiện debug IMDb/TMDb IDs')
    debug_info.setPlot('Bật hoặc tắt việc hiển thị IMDb ID và TMDb ID ngay trong danh sách link. Hữu ích khi kiểm tra item đã resolve đúng ID cho Trakt trước khi phát.')
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=debug_url, listitem=debug_item, isFolder=False)

    items_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'set_items_per_page'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=items_url, listitem=xbmcgui.ListItem('[Số mục mỗi trang cộng đồng]'), isFolder=False)

    ttl_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'set_cache_ttl'})
    ttl_item = xbmcgui.ListItem('[Thời gian giữ cache cộng đồng]')
    ttl_info = ttl_item.getVideoInfoTag()
    ttl_info.setTitle('Thời gian giữ cache cộng đồng')
    ttl_info.setPlot('Xác định số giây addon giữ lại dữ liệu Google Sheet trước khi tải lại. Giá trị cao giúp chuyển trang nhanh hơn nhưng cập nhật chậm hơn; giá trị thấp giúp dữ liệu mới hơn nhưng sẽ tải lại thường xuyên hơn.')
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=ttl_url, listitem=ttl_item, isFolder=False)

    clear_cache_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'clear_gsheet_cache'})
    clear_cache_item = xbmcgui.ListItem('[Xóa cache cộng đồng]')
    clear_cache_info = clear_cache_item.getVideoInfoTag()
    clear_cache_info.setTitle('Xóa cache cộng đồng')
    clear_cache_info.setPlot('Xóa dữ liệu Google Sheet đã lưu tạm trong máy. Dùng khi bạn muốn buộc addon tải lại danh sách cộng đồng mới nhất ngay ở lần mở tiếp theo.')
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=clear_cache_url, listitem=clear_cache_item, isFolder=False)

    xbmcplugin.endOfDirectory(addon_handle)


def load_tmdb_lookup_cache():
    if os.path.exists(TMDB_LOOKUP_CACHE_FILE):
        try:
            with open(TMDB_LOOKUP_CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_tmdb_lookup_cache(cache):
    with open(TMDB_LOOKUP_CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_tmdb_api_key():
    try:
        addon_key = get_local_setting('tmdb_api_key', '')
        if addon_key:
            return str(addon_key).strip()
    except Exception:
        pass

    try:
        tmdb_helper = xbmcaddon.Addon('plugin.video.themoviedb.helper')
        for setting_id in ['tmdb_apikey', 'tmdb_api_key', 'api_key']:
            value = tmdb_helper.getSetting(setting_id)
            if value:
                return value.strip()
    except Exception:
        pass

    return ''


def get_omdb_api_key():
    try:
        addon_key = get_local_setting('omdb_api_key', '')
        if addon_key:
            return str(addon_key).strip()
    except Exception:
        pass

    try:
        tmdb_helper = xbmcaddon.Addon('plugin.video.themoviedb.helper')
        for setting_id in ['omdb_apikey', 'omdb_api_key']:
            value = tmdb_helper.getSetting(setting_id)
            if value:
                return value.strip()
    except Exception:
        pass

    return ''


def get_trakt_client_id():
    trakt_key_path = xbmcvfs.translatePath(
        'special://home/addons/plugin.video.themoviedb.helper/resources/tmdbhelper/lib/api/api_keys/trakt.py'
    )
    try:
        if os.path.exists(trakt_key_path):
            with open(trakt_key_path, 'r', encoding='utf-8') as f:
                content = f.read()
            matches = re.findall(r"CLIENT_ID\s*=\s*'([^']*)'", content)
            for value in matches:
                if value:
                    return value.strip()
    except Exception:
        pass
    return ''


def lookup_trakt_tmdb_id(imdb_id, trakt_type='movie'):
    client_id = get_trakt_client_id()
    if not client_id or not imdb_id:
        return ''

    headers = {
        'trakt-api-version': '2',
        'trakt-api-key': client_id,
        'Content-Type': 'application/json',
    }

    resp = requests.get(
        f'https://api.trakt.tv/search/imdb/{imdb_id}',
        headers=headers,
        params={'type': trakt_type},
        timeout=5
    )
    resp.raise_for_status()

    for item in resp.json() or []:
        if item.get('type') != trakt_type:
            continue
        ids = item.get(trakt_type, {}).get('ids', {})
        if str(ids.get('imdb', '') or '') != str(imdb_id):
            continue
        return str(ids.get('tmdb', '') or '')
    return ''


def lookup_omdb_movie(api_key, title, year=None):
    """Tra OMDb chi lay imdb_id - TMDb Helper se tu lo plot/poster/fanart."""
    if not api_key or not title:
        return {}

    params = {
        'apikey': api_key,
        't': title,
        'type': 'movie',
        'r': 'json',
    }
    if year:
        params['y'] = str(year)

    resp = requests.get('https://www.omdbapi.com/', params=params, timeout=5)
    resp.raise_for_status()
    movie = resp.json()

    if movie.get('Response') == 'False':
        return {}

    imdb_id = (movie.get('imdbID') or '').strip()
    if not imdb_id:
        return {}

    return {
        'mediatype': 'movie',
        'title': movie.get('Title', title),
        'year': str(movie.get('Year', '') or '')[:4],
        'tmdb_id': '',
        'imdb_id': imdb_id,
        'plot': '',
        'rating': '',
        'poster': '',
        'fanart': '',
    }


def lookup_omdb_episode(api_key, tvshowtitle, season, episode):
    """Tra OMDb chi lay imdb_id cua show - TMDb Helper se tu lo phan con lai."""
    if not api_key or not tvshowtitle or not season or not episode:
        return {}

    show_resp = requests.get(
        'https://www.omdbapi.com/',
        params={
            'apikey': api_key,
            't': tvshowtitle,
            'type': 'series',
            'r': 'json',
        },
        timeout=5
    )
    show_resp.raise_for_status()
    show = show_resp.json()

    if show.get('Response') == 'False':
        return {}

    imdb_id = (show.get('imdbID') or '').strip()
    if not imdb_id:
        return {}

    show_year = str(show.get('Year', '') or '')
    year_match = re.search(r'(19|20)\d{2}', show_year)

    return {
        'mediatype': 'episode',
        'title': f"{show.get('Title', tvshowtitle)} S{int(season):02d}E{int(episode):02d}",
        'tvshowtitle': show.get('Title', tvshowtitle),
        'year': year_match.group(0) if year_match else '',
        'season': str(season),
        'episode': str(episode),
        'tmdb_id': '',
        'imdb_id': imdb_id,
        'plot': '',
        'rating': '',
        'poster': '',
        'fanart': '',
        'thumb': '',
    }


def lookup_fallback_metadata(title=None, year=None, tvshowtitle=None, season=None, episode=None):
    """Tra OMDb lay imdb_id. TMDb Helper tu convert sang tmdb_id neu can."""
    omdb_api_key = get_omdb_api_key()
    if not omdb_api_key:
        return {}

    is_episode = bool(season and episode and tvshowtitle)

    if is_episode:
        return lookup_omdb_episode(omdb_api_key, tvshowtitle, season, episode)
    else:
        return lookup_omdb_movie(omdb_api_key, title, year)


def lookup_tmdb_movie(api_key, title, year=None):
    """Tra TMDb chi lay tmdb_id - 1 request duy nhat. TMDb Helper tu lo plot/poster."""
    if not api_key or not title:
        return {}

    params = {
        'api_key': api_key,
        'query': title,
        'language': 'en-US',
    }
    if year:
        params['year'] = str(year)

    resp = requests.get('https://api.themoviedb.org/3/search/movie', params=params, timeout=5)
    resp.raise_for_status()
    results = resp.json().get('results', [])
    if not results:
        return {}

    movie = results[0]
    movie_id = movie.get('id')
    if not movie_id:
        return {}

    return {
        'mediatype': 'movie',
        'title': movie.get('title', title),
        'year': (movie.get('release_date', '') or '')[:4],
        'tmdb_id': str(movie_id),
        'imdb_id': '',
        'plot': '',
        'rating': '',
        'poster': '',
        'fanart': '',
    }


def lookup_tmdb_episode(api_key, tvshowtitle, season, episode):
    """Tra TMDb chi lay tmdb_id cua show - 1 request duy nhat. TMDb Helper tu lo phan con lai."""
    if not api_key or not tvshowtitle:
        return {}

    resp = requests.get(
        'https://api.themoviedb.org/3/search/tv',
        params={
            'api_key': api_key,
            'query': tvshowtitle,
            'language': 'en-US',
        },
        timeout=5
    )
    resp.raise_for_status()
    results = resp.json().get('results', [])
    if not results:
        return {}

    show = results[0]
    show_id = show.get('id')
    if not show_id:
        return {}

    return {
        'mediatype': 'episode',
        'title': f"{show.get('name', tvshowtitle)} S{int(season):02d}E{int(episode):02d}",
        'tvshowtitle': show.get('name', tvshowtitle),
        'year': (show.get('first_air_date', '') or '')[:4],
        'season': str(season),
        'episode': str(episode),
        'tmdb_id': str(show_id),
        'imdb_id': '',
        'plot': '',
        'rating': '',
        'poster': '',
        'fanart': '',
        'thumb': '',
    }


def lookup_tmdb_metadata(title=None, year=None, tvshowtitle=None, season=None, episode=None,
                         force_source=None):
    """Tra cuu ID tu TMDb (cho ra tmdb_id) hoac OMDb (cho ra imdb_id) tuy theo setting.
    TMDb Helper se tu lo plot/poster/rating dua tren ID nay.
    force_source: 'tmdb' | 'omdb' | None (dung setting)
    """
    source = force_source or get_metadata_source()
    if source == 'none':
        return {}

    is_episode = bool(season and episode and tvshowtitle)
    lookup_title = tvshowtitle if is_episode else title
    cache_key = f"{'tv' if is_episode else 'movie'}|{(lookup_title or '').lower()}|{year or ''}|{season or ''}|{episode or ''}"

    cache = load_tmdb_lookup_cache()
    if cache_key in cache:
        return cache[cache_key]

    data = {}

    if source == 'tmdb':
        api_key = get_tmdb_api_key()
        if api_key:
            try:
                if is_episode:
                    data = lookup_tmdb_episode(api_key, tvshowtitle, season, episode)
                else:
                    data = lookup_tmdb_movie(api_key, title, year)
            except Exception as e:
                xbmc.log(f"TMDb lookup error: {e}", level=xbmc.LOGWARNING)

    if not data:
        try:
            data = lookup_fallback_metadata(
                title=title,
                year=year,
                tvshowtitle=tvshowtitle,
                season=season,
                episode=episode,
            )
        except Exception as e:
            xbmc.log(f"OMDb fallback lookup error: {e}", level=xbmc.LOGWARNING)
            data = {}

    if data:
        cache[cache_key] = data
        save_tmdb_lookup_cache(cache)

    return data or {}


    if get_metadata_fetch_plot():
        resolved['plot'] = resolved.get('plot') or raw_meta.get('plot', '')
    if get_metadata_fetch_rating():
        resolved['rating'] = resolved.get('rating') or raw_meta.get('rating', '')
    if get_metadata_fetch_poster():
        resolved['poster'] = resolved.get('poster') or raw_meta.get('poster', '')
        resolved['fanart'] = resolved.get('fanart') or raw_meta.get('fanart', '')

    # Cap nhat title/year/tvshowtitle tu API
    resolved['title'] = raw_meta.get('title', '') or resolved.get('title', '')
    resolved['year'] = raw_meta.get('year', '') or resolved.get('year', '')
    resolved['tvshowtitle'] = raw_meta.get('tvshowtitle', '') or resolved.get('tvshowtitle', '')
    return resolved


def parse_stream_tags_from_filename(filename):
    """
    Suy ra tag hinh anh va am thanh tu ten file Fshare.
    """
    basename = os.path.basename(filename or '')
    name_no_ext = re.sub(r'\.(mkv|mp4|avi|wmv|iso|ts|m2ts|mov|mpg|mpeg)$', '', basename, flags=re.IGNORECASE)
    normalized = name_no_ext.upper().replace('_', ' ')

    video_tag = []
    audio_tag = []
    video_stream = {}
    audio_stream = {}
    video_resolution = ''
    video_source = ''
    video_codec_label = ''
    hdr_label = ''
    hdr_type = ''
    audio_codec_label = ''
    audio_channels_label = ''
    audio_language = []
    audio_object = ''
    audio_profile = ''

    resolution_patterns = [
        (r'(?<!\d)(2160P|4K)(?!\d)|\bUHD\b', ('2160p', 3840, 2160)),
        (r'(?<!\d)1080P(?!\d)', ('1080p', 1920, 1080)),
        (r'(?<!\d)720P(?!\d)', ('720p', 1280, 720)),
        (r'(?<!\d)576P(?!\d)', ('576p', 1024, 576)),
        (r'(?<!\d)480P(?!\d)', ('480p', 854, 480)),
    ]
    for pattern, (label, width, height) in resolution_patterns:
        if re.search(pattern, normalized):
            video_tag.append(label)
            video_resolution = label
            video_stream.update({'width': width, 'height': height})
            break

    source_patterns = [
        (r'REMUX', 'REMUX'),
        (r'BLU[.\- ]?RAY|BDRIP|BRRIP|BDMV', 'BluRay'),
        (r'WEB[.\- ]?DL|WEB[.\- ]?RIP|WEBDL', 'WEB-DL'),
        (r'HDTV', 'HDTV'),
        (r'DVDRIP', 'DVDRip'),
        (r'ISO|M2TS', 'ISO'),
        (r'HDCAM|CAM', 'CAM'),
    ]
    for pattern, label in source_patterns:
        if re.search(pattern, normalized):
            video_tag.append(label)
            video_source = label
            break

    service_patterns = [
        (r'(?<![A-Z])NF(?![A-Z])|NETFLIX', 'NF'),
        (r'AMZN|AMAZON', 'AMZN'),
        (r'DSNP|DISNEY', 'DSNP'),
        (r'HMAX|MAX', 'HMAX'),
    ]
    for pattern, label in service_patterns:
        if re.search(pattern, normalized):
            video_tag.append(label)
            break

    codec_patterns = [
        (r'X265|H[.\- _]?265|HEVC', ('HEVC', 'hevc')),
        (r'X264|H[.\- _]?264|AVC', ('H.264', 'h264')),
        (r'VC[.\- ]?1', ('VC-1', 'vc1')),
        (r'AV1', ('AV1', 'av1')),
    ]
    for pattern, (label, codec) in codec_patterns:
        if re.search(pattern, normalized):
            video_tag.append(label)
            video_codec_label = label
            video_stream['codec'] = codec
            break

    hdr_matches = []
    hdr_patterns = [
        (r'HDR10\+', 'HDR10+'),
        (r'DOLBY[.\- ]?VISION|DO?VI| DV ', 'DV'),
        (r'HDR10', 'HDR10'),
        (r'(?<!SDR)HDR(?!IP)', 'HDR'),
    ]
    for pattern, label in hdr_patterns:
        if re.search(pattern, f" {normalized} "):
            hdr_matches.append(label)
            video_tag.append(label)
    if hdr_matches:
        hdr_label = ' '.join(dict.fromkeys(hdr_matches))
        if 'DV' in hdr_matches:
            hdr_type = 'dolbyvision'
        elif 'HDR10+' in hdr_matches or 'HDR10' in hdr_matches or 'HDR' in hdr_matches:
            hdr_type = 'hdr10'

    if re.search(r'(?<![A-Z])HLG(?![A-Z])', normalized):
        video_tag.append('HLG')
        hdr_matches.append('HLG')
        hdr_type = 'hlg'
        hdr_label = ' '.join(dict.fromkeys(hdr_matches))

    bit_depth_match = re.search(r'(10|12|8)BIT', normalized)
    if bit_depth_match:
        video_tag.append(f"{bit_depth_match.group(1)}bit")

    if re.search(r'\bHYBRID\b', normalized):
        video_tag.append('Hybrid')
    if re.search(r'(?<![A-Z0-9])3D(?![A-Z0-9])|HSBS|HOU|SBS', normalized):
        video_tag.append('3D')

    if re.search(r'ATMOS', normalized):
        audio_object = 'Atmos'

    if re.search(r'DTS[.\- ]?X', normalized):
        audio_profile = 'DTS:X'

    audio_codec_patterns = [
        (r'TRUEHD', 'TrueHD'),
        (r'DTS[.\- ]?X', 'DTS:X'),
        (r'DTS[.\- ]?HD[.\- ]?MA', 'DTS-HD MA'),
        (r'DTS[.\- ]?HD', 'DTS-HD'),
        (r'(?<!TRUE)DTS(?![A-Z])', 'DTS'),
        (r'LPCM|PCM', 'LPCM'),
        (r'DDP[.\- ]?(7\.1|5\.1|2\.0)?|EAC3', 'DDP'),
        (r'DD[.\- ]?(7\.1|5\.1|2\.0)?|AC3', 'DD'),
        (r'AAC[.\- ]?(7\.1|5\.1|2\.0)?', 'AAC'),
        (r'FLAC', 'FLAC'),
        (r'MP3', 'MP3'),
    ]
    for pattern, label in audio_codec_patterns:
        if re.search(pattern, normalized):
            audio_tag.append(label)
            audio_codec_label = label
            codec_map = {
                'TrueHD': 'truehd',
                'DTS-HD MA': 'dtshd_ma',
                'DTS-HD': 'dtshd_hra',
                'DTS:X': 'dtsx',
                'DTS': 'dts',
                'LPCM': 'pcm',
                'DDP': 'eac3',
                'DD': 'ac3',
                'AAC': 'aac',
                'FLAC': 'flac',
                'MP3': 'mp3',
            }
            audio_stream['codec'] = codec_map.get(label, label.lower().replace('-', ''))
            break

    if hdr_type:
        video_stream['hdrtype'] = hdr_type

    channels_match = re.search(r'(?<!\d)(7\.1|5\.1|2\.0|1\.0|MONO|2CH|6CH|8CH)(?!\d)', normalized)
    if channels_match:
        raw_channels = channels_match.group(1)
        channel_map = {
            '7.1': ('7.1', 8),
            '5.1': ('5.1', 6),
            '2.0': ('2.0', 2),
            '1.0': ('1.0', 1),
            'MONO': ('1.0', 1),
            '2CH': ('2.0', 2),
            '6CH': ('5.1', 6),
            '8CH': ('7.1', 8),
        }
        channel_label, channels = channel_map.get(raw_channels, (raw_channels, 0))
        audio_tag.append(channel_label)
        audio_channels_label = channel_label
        if channels:
            audio_stream['channels'] = channels

    if audio_object:
        audio_tag.append('Atmos')

    if audio_object:
        if audio_codec_label == 'TrueHD':
            audio_profile = 'TrueHD Atmos'
        elif audio_codec_label == 'DDP':
            audio_profile = 'DDP Atmos'
        elif audio_codec_label == 'DD':
            audio_profile = 'DD Atmos'
        elif not audio_profile:
            audio_profile = audio_object

    language_patterns = [
        (r'VIETSUB|SUB[.\- ]?VIET', 'VieSub'),
        (r'HARD[.\- ]?SUB|HARDSUB', 'VieSub'),
        (r'THUYET[.\- ]?MINH|\bTM\b', 'TM'),
        (r'USLT', 'USLT'),
        (r'(?<![A-Z])VIE(?![A-Z])|VIET', 'ViE'),
        (r'(?<![A-Z])ENG(?![A-Z])|ENGLISH', 'ENG'),
        (r'\bMULTI\b', 'Multi'),
        (r'\bDUAL\b', 'Dual'),
        (r'\bAI\b', 'AI'),
    ]
    for pattern, label in language_patterns:
        if re.search(pattern, normalized):
            audio_tag.append(label)
            audio_language.append(label)

    video_tag = list(dict.fromkeys(video_tag))
    audio_tag = list(dict.fromkeys(audio_tag))

    return {
        'video_tag': ' '.join(video_tag),
        'audio_tag': ' '.join(audio_tag),
        'video_stream': video_stream,
        'audio_stream': audio_stream,
        'video_resolution': video_resolution,
        'video_source': video_source,
        'video_codec': video_codec_label,
        'hdr': hdr_label,
        'hdr_type': hdr_type,
        'audio_codec': audio_codec_label,
        'audio_channels': audio_channels_label,
        'audio_language': ' '.join(audio_language),
        'audio_object': audio_object,
        'audio_profile': audio_profile,
    }


def parse_media_identity_from_filename(filename, fallback_title=''):
    basename = os.path.basename(filename or '')
    name_no_ext = re.sub(r'\.(mkv|mp4|avi|wmv|iso|ts|m2ts|mov|mpg|mpeg)$', '', basename, flags=re.IGNORECASE)
    name_no_ext = re.sub(r'^\s*\((.*?)\)\s*', '', name_no_ext)
    name_no_ext = re.sub(r'^\d+\.\s*', '', name_no_ext)

    season_episode_match = re.search(r'\bS(\d{1,2})E(\d{1,2})\b', name_no_ext, re.IGNORECASE)
    year_match = re.search(r'\b(19|20)\d{2}\b', name_no_ext)

    if season_episode_match:
        title_part = name_no_ext[:season_episode_match.start()]
    elif year_match:
        title_part = name_no_ext[:year_match.start()]
    else:
        title_part = name_no_ext

    title_part = re.sub(r'[._]+', ' ', title_part)
    title_part = re.sub(r'\s+', ' ', title_part).strip(' -._')
    parsed_title = title_part or (fallback_title or '').strip()

    season = season_episode_match.group(1).zfill(2) if season_episode_match else ''
    episode = season_episode_match.group(2).zfill(2) if season_episode_match else ''
    year = year_match.group(0) if year_match else ''

    return {
        'is_episode': bool(season and episode),
        'title': parsed_title,
        'tvshowtitle': parsed_title if season and episode else '',
        'year': year,
        'season': season,
        'episode': episode,
        'display_title': f"{parsed_title} S{season}E{episode}" if season and episode and parsed_title else parsed_title,
    }

def make_safe_media_name(title, movie_info=None):
    def parse_language_prefix(filename):
        langs = []
        prefix_match = re.match(r'^\s*\((.*?)\)\s*', filename or '')
        if prefix_match:
            prefix = prefix_match.group(1).upper()
            if any(x in prefix for x in ['THUYET MINH', 'TM']):
                langs.append('TM')
            if any(x in prefix for x in ['SUB VIET', 'SUBVIET', 'VIETSUB']):
                langs.append('VieSub')
            filename = filename[prefix_match.end():]
        return filename, langs

    def dotify(value):
        value = re.sub(r'\s+', ' ', value or '').strip()
        value = re.sub(r'[._]+', '.', value)
        return value.replace(' ', '.').strip('.')

    def normalize_tech_tokens(tokens):
        normalize_map = {
            '2160P': '2160p', '4K': '2160p', '1080P': '1080p', '720P': '720p',
            '480P': '480p', '576P': '576p',
            'BLURAY': 'BluRay', 'BLU-RAY': 'BluRay', 'BDRIP': 'BDRip', 'BRRIP': 'BDRip',
            'WEBRIP': 'WEBRip', 'WEB-DL': 'WEB-DL', 'WEBDL': 'WEB-DL', 'WEB': 'WEB-DL',
            'HDTV': 'HDTV', 'DVDRIP': 'DVDRip', 'REMUX': 'REMUX',
            'AMZN': 'AMZN', 'AMAZON': 'AMZN', 'NF': 'NF', 'NETFLIX': 'NF',
            'HMAX': 'HMAX', 'MAX': 'MAX', 'DSNP': 'DSNP', 'DISNEY': 'DSNP',
            'MA': 'MA', 'UHD': 'UHD',
            'X264': 'H.264', 'AVC': 'H.264',
            'X265': 'HEVC', 'H265': 'HEVC', 'H.265': 'HEVC', 'HEVC': 'HEVC',
            'H264': 'H.264', 'H.264': 'H.264', 'AV1': 'AV1',
            'DTS-HD': 'DTS-HD', 'DTS-HD-MA': 'DTS-HD.MA', 'DTSX': 'DTS-X',
            'DTS:X': 'DTS-X', 'DTS-X': 'DTS-X', 'DTS': 'DTS', 'TRUEHD': 'TrueHD',
            'ATMOS': 'Atmos', 'DD5.1': 'DD5.1', 'DDP5.1': 'DDP5.1', 'DDP7.1': 'DDP7.1',
            'DDP': 'DDP', 'AAC': 'AAC', 'AC3': 'AC3', 'EAC3': 'DDP',
            'FLAC': 'FLAC', 'MP3': 'MP3',
            'HDR10+': 'HDR10+', 'HDR10': 'HDR10', 'HDR': 'HDR',
            'DOLBY': 'Dolby', 'VISION': 'Vision', 'DOLBYVISION': 'DV', 'DV': 'DV', 'HLG': 'HLG',
            'VIE': 'ViE', 'ENG': 'ENG', 'VIET': 'ViE', 'VIETSUB': 'VieSub',
            '10BIT': '10bit', '8BIT': '8bit',
        }
        normalized = []
        seen = set()
        for token in tokens:
            cleaned = token.strip('.- _')
            if not cleaned:
                continue
            upper = cleaned.upper()
            normalized_token = normalize_map.get(upper, cleaned)
            if normalized_token in ['Dolby', 'Vision']:
                continue
            if normalized_token not in seen:
                normalized.append(normalized_token)
                seen.add(normalized_token)
        return normalized

    def parse_media_info(filename):
        info = {}
        basename = os.path.basename(filename or '')
        basename = re.sub(r'\.(mkv|mp4|wmv|iso|ts|m2ts|avi|mov|mpg|mpeg)$', '', basename, flags=re.IGNORECASE)
        season_episode_match = re.search(r'\bS(\d{1,2})E(\d{1,2})\b', basename, re.IGNORECASE)
        year_match = re.search(r'\b(19|20)\d{2}\b', basename)
        info['season'] = season_episode_match.group(1).zfill(2) if season_episode_match else ''
        info['episode'] = season_episode_match.group(2).zfill(2) if season_episode_match else ''
        info['season_episode'] = f"S{info['season']}E{info['episode']}" if season_episode_match else ''
        info['year'] = year_match.group(0) if year_match else ''

        filename_clean = re.sub(r'\(((19|20)\d{2})\)', r'\1', basename)
        filename_clean = re.sub(r'\(.*?\)', '', filename_clean)
        filename_clean = re.sub(r'^\d+\.\s*', '', filename_clean)
        filename_clean = re.sub(r'\s+', ' ', filename_clean).strip()

        title_end = len(filename_clean)
        if season_episode_match:
            title_end = min(title_end, season_episode_match.start())
        if year_match:
            title_end = min(title_end, year_match.start())
        title_part = filename_clean[:title_end]
        title_part = re.sub(r'[._]+', ' ', title_part).strip()
        title_part = re.sub(r'[\s\-]+$', '', title_part)
        info['parsed_title'] = title_part

        tech_start = None
        if season_episode_match:
            tech_start = season_episode_match.start()
        elif year_match:
            tech_start = year_match.start()
        info['tech_source'] = filename_clean[tech_start:] if tech_start is not None else ''
        return info

    clean_title, lang_tags = parse_language_prefix(title)
    media = parse_media_info(clean_title)
    info = movie_info or {}

    season = str(info.get('season') or media.get('season') or '').zfill(2) if (info.get('season') or media.get('season')) else ''
    episode = str(info.get('episode') or media.get('episode') or '').zfill(2) if (info.get('episode') or media.get('episode')) else ''
    is_episode = bool(season and episode)

    base_title = ''
    if is_episode:
        base_title = info.get('tvshowtitle') or info.get('title') or media.get('parsed_title', '')
    else:
        base_title = info.get('title') or media.get('parsed_title', '')
    base_title = dotify(base_title)

    year = str(info.get('year') or media.get('year') or '').strip()

    info_tech_tokens = []
    for value in [
        info.get('video_resolution', ''),
        info.get('video_source', ''),
        info.get('hdr', ''),
        info.get('video_codec', ''),
        info.get('audio_codec', ''),
        info.get('audio_channels', ''),
        info.get('audio_profile', ''),
        info.get('audio_object', ''),
        info.get('audio_language', ''),
    ]:
        info_tech_tokens.extend(re.split(r'[.\s\-_]+', str(value or '')))

    tech_tokens = normalize_tech_tokens(info_tech_tokens)
    if not tech_tokens:
        tech_tokens = normalize_tech_tokens(re.split(r'[.\s\-_]+', media.get('tech_source', '')))

    identity_tokens = [base_title] if base_title else []
    if is_episode:
        identity_tokens.append(f"S{season}E{episode}")
    elif year:
        identity_tokens.append(f"({year})")

    if lang_tags:
        insert_pos = 1 if len(identity_tokens) > 1 else len(identity_tokens)
        for tag in lang_tags:
            if tag not in tech_tokens:
                tech_tokens.append(tag)

    safe_name = '.'.join([token for token in identity_tokens + tech_tokens if token])
    safe_name = safe_name.replace(':', ' -')
    safe_name = re.sub(r'[\\/*?"<>|\[\]]', '_', safe_name)
    safe_name = re.sub(r'_+', '_', safe_name)
    safe_name = re.sub(r'\.+', '.', safe_name)
    return safe_name.strip('_. ')


def scan_path_into_library(media_path):
    try:
        scan_query = json.dumps({
            "jsonrpc": "2.0",
            "method": "VideoLibrary.Scan",
            "params": {
                "directory": media_path,
                "showdialogs": False
            },
            "id": 1
        })
        xbmc.executeJSONRPC(scan_query)

        for _ in range(15):
            xbmc.sleep(1000)
            status_query = json.dumps({
                "jsonrpc": "2.0",
                "method": "XBMC.GetInfoBooleans",
                "params": {"booleans": ["Library.IsScanning"]},
                "id": 2
            })
            result = json.loads(xbmc.executeJSONRPC(status_query))
            if not result.get('result', {}).get('Library.IsScanning', False):
                break
    except Exception as e:
        xbmc.log(f"Library scan warning for {media_path}: {e}", level=xbmc.LOGWARNING)

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
    safe_title = make_safe_media_name(title, movie_info)

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
        scan_path_into_library(strm_path)
        xbmcgui.Dialog().ok("Thành công", f"Đã tạo và thêm vào library:\n{safe_title}.strm")
    except Exception as e:
        xbmcgui.Dialog().ok("Lỗi", f"Không tạo được file:\n{e}")
        xbmc.log(f"STRM error: {e}", level=xbmc.LOGERROR)
    return

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
            'DTS-HD': 'DTS-HD', 'DTS-HD-MA': 'DTS-HD.MA', 'DTSX': 'DTS-X',
            'DTS:X': 'DTS-X', 'DTS-X': 'DTS-X', 'DTS': 'DTS', 'TRUEHD': 'TrueHD',
            'ATMOS': 'Atmos', 'DD5.1': 'DD5.1', 'DDP5.1': 'DDP5.1',
            'DDP': 'DDP', 'AAC': 'AAC', 'AC3': 'AC3',
            'FLAC': 'FLAC', 'MP3': 'MP3',
            # HDR
            'HDR10+': 'HDR10+', 'HDR10': 'HDR10', 'HDR': 'HDR',
            'DOLBY VISION': 'DV', 'DV': 'DV', 'HLG': 'HLG',
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
    season_match = re.search(r'\bS\d{2}E\d{2}\b', clean_for_tech, re.IGNORECASE)
    if year_match:
        tech_part = clean_for_tech[year_match.start():]
        tech_part = re.sub(r'\.(mkv|mp4|wmv|iso|ts)$', '', tech_part, flags=re.IGNORECASE)
        tech_part = tech_part.replace(' ', '.')
        tech_part = re.sub(r'\.+', '.', tech_part).strip('.')
        tech_part = normalize_tech_part(tech_part)
    elif season_match:
        tech_part = clean_for_tech[season_match.start():]
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

    safe_title = make_safe_media_name(title, movie_info)

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
        return make_safe_media_name(title, movie_info)
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
        """

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
            scan_path_into_library(dest_path)
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

def show_fshare_files_from_api_response(api_response_str):
    xbmcplugin.setPluginCategory(addon_handle, 'Fshare Files from API')
    xbmcplugin.setContent(addon_handle, 'files')

    try:
        api_data = json.loads(api_response_str)
    except json.JSONDecodeError as e:
        xbmc.log(f"Fshare: Error decoding API response: {e}", level=xbmc.LOGERROR)
        xbmcgui.Dialog().ok("Lỗi", f"Không thể giải mã dữ liệu API: {e}")
        xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
        return

    list_items = []
    for item in api_data:
        try:
            name = (item.get('name') or '').strip()
            furl = (item.get('furl') or '').strip()
            item_size = int(item.get('size') or 0)

            if not name or not furl:
                xbmc.log(f"Fshare: Skipping item due to missing name or furl: {item}", level=xbmc.LOGWARNING)
                continue

            size_str = ''
            if item_size > 0:
                size_str = f" ({item_size/(1024**3):.2f} GB)" if item_size >= 1024**3 else f" ({item_size/(1024**2):.2f} MB)"

            list_item = xbmcgui.ListItem(label=f"{name}{size_str}")
            
            # Set information for the list item
            info_tag = list_item.getVideoInfoTag()
            info_tag.setTitle(name)
            info_tag.setPlot(f"Fshare URL: {furl}")
            
            list_item.setProperty('IsPlayable', 'true')
            play_url = sys.argv[0] + '?' + urllib.parse.urlencode({
                'action': 'play_fshare_file',
                'url': furl,
                'title': name,
            })
            list_items.append((play_url, list_item, False))
        except Exception as e:
            xbmc.log(f"Fshare: Error processing API item {item.get('name', 'N/A')}: {e}", level=xbmc.LOGERROR)
            continue

    if list_items:
        xbmcplugin.addDirectoryItems(addon_handle, list_items)
    else:
        xbmcgui.Dialog().ok('Thông báo', 'Không có file Fshare nào để hiển thị từ dữ liệu API.')
    
    xbmcplugin.endOfDirectory(addon_handle)
def get_vietmediaf_fshare_credentials():
    try:
        vietmediaf = xbmcaddon.Addon('plugin.video.vietmediaF')
        token = (vietmediaf.getSetting('tokenfshare') or '').strip()
        session_id = (vietmediaf.getSetting('sessionfshare') or '').strip()
        if token and session_id:
            return token, session_id
    except Exception as e:
        xbmc.log(f"Không lấy được tài khoản Fshare từ VietmediaF: {e}", level=xbmc.LOGERROR)
    return '', ''


FSHARE_FILE_REALNAME_CACHE = {}

def get_fshare_file_realname(file_url):
    """Goi api/fileops/get de lay ten file that tu Fshare.
    Tra ve ten file that (str) hoac '' neu that bai.
    Cache ket qua trong bo nho theo session de tranh goi API lap lai.
    """
    linkcode_match = re.search(r'/file/([a-zA-Z0-9]+)', file_url)
    if not linkcode_match:
        return ''
    linkcode = linkcode_match.group(1)

    if linkcode in FSHARE_FILE_REALNAME_CACHE:
        return FSHARE_FILE_REALNAME_CACHE[linkcode]

    token, session_id = get_vietmediaf_fshare_credentials()
    if not token or not session_id:
        return ''

    try:
        payload = json.dumps({'token': token, 'url': file_url})
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'kodivietmediaf-K58W6U',
            'Cookie': f'session_id={session_id}'
        }
        resp = requests.post(
            'https://api.fshare.vn/api/fileops/get',
            data=payload,
            headers=headers,
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        # Neu session het han, tra ve rong (khong tu dong re-login o day)
        if isinstance(data, dict) and data.get('code') == 201:
            xbmc.log(f"Fshare get_file_realname: session expired for {linkcode}", level=xbmc.LOGWARNING)
            FSHARE_FILE_REALNAME_CACHE[linkcode] = ''
            return ''
        real_name = (data.get('name') or '').strip() if isinstance(data, dict) else ''
        FSHARE_FILE_REALNAME_CACHE[linkcode] = real_name
        xbmc.log(f"Fshare get_file_realname: {linkcode} -> '{real_name}'", level=xbmc.LOGINFO)
        return real_name
    except Exception as e:
        xbmc.log(f"Fshare get_file_realname error for {linkcode}: {e}", level=xbmc.LOGWARNING)
        FSHARE_FILE_REALNAME_CACHE[linkcode] = ''
        return ''


def trigger_vietmediaf_login_and_wait():
    """Goi action account_fshare cua VietmediaF de dang nhap lai Fshare,
    sau do cho toi da 60 giay cho den khi token moi duoc luu vao settings."""
    xbmc.log("Fshare: Triggering VietmediaF account_fshare login...", level=xbmc.LOGINFO)
    old_token, _ = get_vietmediaf_fshare_credentials()
    xbmc.executebuiltin('RunPlugin(plugin://plugin.video.vietmediaF?action=account_fshare)')
    for _ in range(60):
        xbmc.sleep(1000)
        new_token, new_session = get_vietmediaf_fshare_credentials()
        if new_token and new_session and new_token != old_token:
            xbmc.log("Fshare: New token detected after login.", level=xbmc.LOGINFO)
            return new_token, new_session
    return get_vietmediaf_fshare_credentials()


def _parse_fshare_folder_response(data):
    """Chuan hoa du lieu tra ve tu API: chap nhan list, dict.items hoac dict.data.items."""
    items = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if 'items' in data:
            items = data['items']
        elif 'data' in data:
            if isinstance(data['data'], list):
                items = data['data']
            elif isinstance(data['data'], dict):
                items = data['data'].get('items', [])
    else:
        xbmc.log(f"Fshare: Unexpected API response type: {type(data)}", level=xbmc.LOGERROR)

    for item in (items or []):
        if not item.get('linkcode'):
            url_val = item.get('furl') or item.get('url')
            if url_val:
                match = re.search(r'/(file|folder)/([a-zA-Z0-9]+)', url_val)
                if match:
                    item['linkcode'] = match.group(2)
                    if item.get('type') is None:
                        item['type'] = '0' if match.group(1) == 'folder' else '1'

    xbmc.log(f"Fshare: Total items parsed: {len(items)}", level=xbmc.LOGINFO)
    return {'items': items or []}


def _do_fshare_folder_request(token, session_id, folder_url, page_index, limit):
    """Thuc hien mot lan goi API getFolderList va tra ve response."""
    payload = {
        'token': token,
        'url': folder_url,
        'dirOnly': 0,
        'pageIndex': page_index,
        'limit': limit
    }
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'kodivietmediaf-K58W6U',
        'Cookie': f'session_id={session_id}'
    }
    xbmc.log(f"Fshare: Request payload: {json.dumps(payload)}", level=xbmc.LOGINFO)
    response = requests.post(
        'https://api.fshare.vn/api/fileops/getFolderList',
        json=payload,
        headers=headers,
        timeout=15
    )
    xbmc.log(f"Fshare: API response status code: {response.status_code}", level=xbmc.LOGINFO)
    xbmc.log(f"Fshare: API response text: {response.text}", level=xbmc.LOGINFO)
    return response


def fetch_fshare_folder_items(folder_url, page_index=0, limit=100):
    xbmc.log(f"Fshare: fetch_fshare_folder_items called for URL: {folder_url}", level=xbmc.LOGINFO)
    token, session_id = get_vietmediaf_fshare_credentials()
    xbmc.log(f"Fshare: Retrieved token (present: {bool(token)}) and session_id (present: {bool(session_id)})", level=xbmc.LOGINFO)

    if not token or not session_id:
        # Chua co token lan dau -> trigger login ngay
        xbmc.log("Fshare: No credentials found, triggering login...", level=xbmc.LOGWARNING)
        token, session_id = trigger_vietmediaf_login_and_wait()
        if not token or not session_id:
            raise RuntimeError('Khong lay duoc token Fshare tu VietmediaF sau khi dang nhap. Vui long thu lai.')

    try:
        response = _do_fshare_folder_request(token, session_id, folder_url, page_index, limit)
        response.raise_for_status()
        data = response.json()

        # Kiem tra loi "Not logged in" tu API (code 201)
        if isinstance(data, dict) and data.get('code') == 201:
            xbmc.log("Fshare: Session expired (code 201), triggering re-login via VietmediaF...", level=xbmc.LOGWARNING)
            confirm = xbmcgui.Dialog().yesno(
                'Fshare: Phien dang nhap het han',
                'Token Fshare da het han.\nBam [B]Dang nhap lai[/B] de VietmediaF tu dong lam moi tai khoan.',
                nolabel='Huy',
                yeslabel='Dang nhap lai'
            )
            if not confirm:
                raise RuntimeError('Phien dang nhap Fshare da het han. Vui long dang nhap lai trong VietmediaF.')

            token, session_id = trigger_vietmediaf_login_and_wait()
            if not token or not session_id:
                raise RuntimeError('Khong lay duoc token moi sau khi dang nhap lai. Vui long thu lai.')

            # Thu lai lan 2 sau khi login
            xbmc.log("Fshare: Retrying folder request with new credentials...", level=xbmc.LOGINFO)
            response = _do_fshare_folder_request(token, session_id, folder_url, page_index, limit)
            response.raise_for_status()
            data = response.json()

            if isinstance(data, dict) and data.get('code') == 201:
                raise RuntimeError('Dang nhap lai khong thanh cong. Vui long kiem tra tai khoan Fshare trong VietmediaF.')

        return _parse_fshare_folder_response(data)

    except requests.exceptions.RequestException as e:
        xbmc.log(f"Fshare: RequestException during API call: {e}", level=xbmc.LOGERROR)
        raise  # Re-raise to be caught by browse_fshare_folder


def browse_fshare_folder(folder_url, page_index=0, folder_name=''):
    folder_name = folder_name or 'Folder Fshare'
    xbmcplugin.setPluginCategory(addon_handle, folder_name)
    xbmcplugin.setContent(addon_handle, 'files')

    try:
        page_index = max(0, int(page_index))
    except Exception:
        page_index = 0

    show_lookup_debug_ids = get_show_lookup_debug_ids()
    lookup_cache = {}

    try:
        folder_data = fetch_fshare_folder_items(folder_url, page_index=page_index, limit=100)
    except Exception as e:
        xbmcgui.Dialog().ok('Lỗi', f'Không đọc được folder Fshare:\n{str(e)[:200]}')
        xbmc.log(f"Fshare folder browse error: {e}", level=xbmc.LOGERROR)
        xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
        return

    items = folder_data.get('items', []) or []
    list_items = []

    for item in items:
        try:
            name = (item.get('name') or '').strip()
            linkcode = str(item.get('linkcode') or '').strip()
            item_type = str(item.get('type') or '')
            item_size = int(item.get('size') or 0)

            if not name or not linkcode:
                continue

            is_folder = item_type == '0'
            item_url = f"https://www.fshare.vn/{'folder' if is_folder else 'file'}/{linkcode}"

            if is_folder:
                browse_url = sys.argv[0] + '?' + urllib.parse.urlencode({
                    'action': 'browse_fshare_folder',
                    'url': item_url,
                    'page_index': '0',
                    'folder_name': name,
                })
                folder_item = xbmcgui.ListItem(label=name)
                folder_item.setArt({'icon': 'DefaultFolder.png'})
                folder_item.getVideoInfoTag().setPlot(name)
                list_items.append((browse_url, folder_item, True))
                continue

            is_video_file = is_video_item(name, item_url)
            stream_tags = parse_stream_tags_from_filename(name) if is_video_file else {}
            identity_tags = parse_media_identity_from_filename(name, name) if is_video_file else {}
            effective_season = str(identity_tags.get('season') or '')
            effective_episode = str(identity_tags.get('episode') or '')
            is_episode_item = bool(effective_season and effective_episode)
            effective_tvshowtitle = identity_tags.get('tvshowtitle') or identity_tags.get('title') or name
            effective_title = identity_tags.get('title') or name
            effective_year = str(identity_tags.get('year') or '')
            if is_episode_item:
                effective_title = f"{effective_tvshowtitle} S{effective_season.zfill(2)}E{effective_episode.zfill(2)}"

            resolved_plot = ''
            resolved_rating = ''
            resolved_poster_path = ''
            resolved_fanart = ''
            resolved_imdb_id = ''
            resolved_tmdb_id = ''

            if is_video_file and get_metadata_source() != 'none':
                lookup_key = f"{effective_title}|{effective_year}|{effective_tvshowtitle}|{effective_season}|{effective_episode}"
                if lookup_key not in lookup_cache:
                    lookup_cache[lookup_key] = lookup_tmdb_metadata(
                        title=effective_title if not is_episode_item else '',
                        year=effective_year,
                        tvshowtitle=effective_tvshowtitle if is_episode_item else '',
                        season=effective_season,
                        episode=effective_episode,
                    )
                resolved_meta = lookup_cache.get(lookup_key, {})
                if resolved_meta:
                    resolved_imdb_id = resolved_meta.get('imdb_id', '') or ''
                    resolved_tmdb_id = resolved_meta.get('tmdb_id', '') or ''
                    if is_episode_item:
                        effective_title = resolved_meta.get('title', '') or effective_title
                        effective_tvshowtitle = resolved_meta.get('tvshowtitle', '') or effective_tvshowtitle
                    else:
                        effective_title = resolved_meta.get('title', '') or effective_title
                        effective_year = resolved_meta.get('year', '') or effective_year

            debug_label = ''
            if show_lookup_debug_ids:
                debug_parts = []
                if resolved_imdb_id:
                    debug_parts.append(f"IMDb:{resolved_imdb_id}")
                if resolved_tmdb_id:
                    debug_parts.append(f"TMDb:{resolved_tmdb_id}")
                if debug_parts:
                    debug_label = f" [IDs: {' | '.join(debug_parts)}]"
                elif is_video_file and get_metadata_source() != 'none':
                    debug_label = ' [IDs: not found]'

            size_str = ''
            if item_size > 0:
                size_str = f" ({item_size/(1024**3):.2f} GB)" if item_size >= 1024**3 else f" ({item_size/(1024**2):.2f} MB)"

            list_item = xbmcgui.ListItem(label=f"{name}{debug_label}{size_str}")
            info_tag = list_item.getVideoInfoTag()
            info_tag.setTitle(effective_title)
            if resolved_plot:
                info_tag.setPlot(resolved_plot)
            if effective_year and str(effective_year).isdigit():
                info_tag.setYear(int(effective_year))

            ids = {}
            if resolved_imdb_id:
                ids['imdb'] = resolved_imdb_id
                info_tag.setIMDBNumber(resolved_imdb_id)
            if resolved_tmdb_id:
                ids['tmdb'] = resolved_tmdb_id
            if ids:
                info_tag.setUniqueIDs(ids, 'imdb' if resolved_imdb_id else 'tmdb')

            if is_video_file and is_episode_item:
                info_tag.setMediaType('episode')
                info_tag.setTvShowTitle(effective_tvshowtitle)
                info_tag.setSeason(int(effective_season))
                info_tag.setEpisode(int(effective_episode))
            elif is_video_file:
                info_tag.setMediaType('movie')

            if resolved_rating:
                try:
                    info_tag.setRating(float(resolved_rating))
                except Exception:
                    pass

            art = {}
            if resolved_poster_path:
                art['thumb'] = resolved_poster_path
                art['poster'] = resolved_poster_path
                art['icon'] = resolved_poster_path
            if resolved_fanart:
                art['fanart'] = resolved_fanart
            if art:
                list_item.setArt(art)

            if is_video_file:
                video_stream = stream_tags.get('video_stream', {})
                audio_stream = stream_tags.get('audio_stream', {})
                if video_stream:
                    list_item.addStreamInfo('video', video_stream)
                if audio_stream:
                    list_item.addStreamInfo('audio', audio_stream)

                prop_map = {
                    'video_tag': stream_tags.get('video_tag', ''),
                    'audio_tag': stream_tags.get('audio_tag', ''),
                    'VideoResolution': stream_tags.get('video_resolution', ''),
                    'VideoCodec': stream_tags.get('video_codec', ''),
                    'VideoSource': stream_tags.get('video_source', ''),
                    'VideoHDR': stream_tags.get('hdr', ''),
                    'HdrType': stream_tags.get('hdr_type', ''),
                    'AudioCodec': stream_tags.get('audio_codec', ''),
                    'AudioChannels': stream_tags.get('audio_channels', ''),
                    'AudioLanguage': stream_tags.get('audio_language', ''),
                    'AudioProfile': stream_tags.get('audio_profile', ''),
                    'AudioObject': stream_tags.get('audio_object', ''),
                    'AudioAtmos': stream_tags.get('audio_object', ''),
                    'AudioCodec2': stream_tags.get('audio_object', ''),
                    'AudioCodecAlt': stream_tags.get('audio_object', ''),
                    'AudioCodecExtra': stream_tags.get('audio_profile', ''),
                    'AudioCodecCombined': stream_tags.get('audio_profile', ''),
                    'VideoPlayer.AudioCodec': stream_tags.get('audio_codec', ''),
                    'VideoPlayer.AudioChannels': stream_tags.get('audio_channels', ''),
                    'VideoPlayer.AudioProfile': stream_tags.get('audio_profile', ''),
                    'VideoPlayer.AudioObject': stream_tags.get('audio_object', ''),
                    'VideoPlayer.VideoResolution': stream_tags.get('video_resolution', ''),
                    'VideoPlayer.VideoCodec': stream_tags.get('video_codec', ''),
                    'VideoPlayer.VideoSource': stream_tags.get('video_source', ''),
                    'VideoPlayer.HdrType': stream_tags.get('hdr_type', ''),
                    'VideoInfo.VideoResolution': stream_tags.get('video_resolution', ''),
                    'VideoInfo.VideoCodec': stream_tags.get('video_codec', ''),
                    'VideoInfo.VideoSource': stream_tags.get('video_source', ''),
                    'VideoInfo.AudioCodec': stream_tags.get('audio_codec', ''),
                    'VideoInfo.AudioChannels': stream_tags.get('audio_channels', ''),
                    'VideoInfo.AudioLanguage': stream_tags.get('audio_language', ''),
                    'VideoInfo.AudioProfile': stream_tags.get('audio_profile', ''),
                    'media.videoresolution': stream_tags.get('video_resolution', ''),
                    'media.videocodec': stream_tags.get('video_codec', ''),
                    'media.videosource': stream_tags.get('video_source', ''),
                    'media.hdr': stream_tags.get('hdr', ''),
                    'media.hdrtype': stream_tags.get('hdr_type', ''),
                    'media.audiocodec': stream_tags.get('audio_codec', ''),
                    'media.audiochannels': stream_tags.get('audio_channels', ''),
                    'media.audiolanguage': stream_tags.get('audio_language', ''),
                    'media.audioprofile': stream_tags.get('audio_profile', ''),
                    'media.audioobject': stream_tags.get('audio_object', ''),
                    'audio.codec': stream_tags.get('audio_codec', ''),
                    'audio.channels': stream_tags.get('audio_channels', ''),
                    'audio.language': stream_tags.get('audio_language', ''),
                    'audio.profile': stream_tags.get('audio_profile', ''),
                    'audio.object': stream_tags.get('audio_object', ''),
                    'video.codec': stream_tags.get('video_codec', ''),
                    'video.resolution': stream_tags.get('video_resolution', ''),
                    'video.source': stream_tags.get('video_source', ''),
                    'video.hdr': stream_tags.get('hdr', ''),
                    'video.hdrtype': stream_tags.get('hdr_type', ''),
                    'HasAtmos': 'true' if stream_tags.get('audio_object', '').lower() == 'atmos' else '',
                    'AudioIsAtmos': 'true' if stream_tags.get('audio_object', '').lower() == 'atmos' else '',
                    'HasDTSX': 'true' if 'dts:x' in stream_tags.get('audio_profile', '').lower() else '',
                    'AudioIsDTSX': 'true' if 'dts:x' in stream_tags.get('audio_profile', '').lower() else '',
                }
                for prop_name, prop_value in prop_map.items():
                    if prop_value:
                        list_item.setProperty(prop_name, str(prop_value))

            if show_lookup_debug_ids:
                list_item.setProperty('debug.imdb_id', resolved_imdb_id)
                list_item.setProperty('debug.tmdb_id', resolved_tmdb_id)
                list_item.setProperty('debug.lookup_status', 'ok' if (resolved_imdb_id or resolved_tmdb_id) else 'missing')

            list_item.setProperty('IsPlayable', 'true')
            play_params = {
                'action': 'play_trakt',
                'url': f'plugin://plugin.video.vietmediaF?action=play&url={item_url}',
                'imdb': resolved_imdb_id,
                'tmdb': resolved_tmdb_id,
                'title': effective_title,
                'filename': name,
                'year': effective_year,
                'season': effective_season,
                'episode': effective_episode,
                'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
            }
            play_url = sys.argv[0] + '?' + urllib.parse.urlencode(play_params)
            list_items.append((play_url, list_item, False))
        except Exception as e:
            xbmc.log(f"Fshare folder item error: {e}", level=xbmc.LOGWARNING)
            continue

    if list_items:
        xbmcplugin.addDirectoryItems(addon_handle, list_items)
    else:
        xbmcgui.Dialog().ok('Thông báo', 'Folder Fshare không có nội dung phù hợp để hiển thị.')

    if page_index > 0:
        prev_url = sys.argv[0] + '?' + urllib.parse.urlencode({
            'action': 'browse_fshare_folder',
            'url': folder_url,
            'page_index': str(page_index - 1),
            'folder_name': folder_name,
        })
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=prev_url, listitem=xbmcgui.ListItem(f'[Trang trước {page_index}]'), isFolder=True)

    if len(items) >= 100:
        next_url = sys.argv[0] + '?' + urllib.parse.urlencode({
            'action': 'browse_fshare_folder',
            'url': folder_url,
            'page_index': str(page_index + 1),
            'folder_name': folder_name,
        })
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=next_url, listitem=xbmcgui.ListItem(f'[Trang sau {page_index + 2}]'), isFolder=True)

    xbmcplugin.endOfDirectory(addon_handle)


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

    # --- Cộng đồng chia sẻ ---
    community_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'list_community'})
    community_item = xbmcgui.ListItem('[👥 Cộng đồng chia sẻ]')
    community_item.setArt({'icon': 'DefaultAddonVideo.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=community_url, listitem=community_item, isFolder=True)

    settings_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'settings_menu'})
    settings_item = xbmcgui.ListItem('[⚙️ Cài đặt]')
    settings_item.setArt({'icon': 'DefaultAddonProgram.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=settings_url, listitem=settings_item, isFolder=True)

    xbmcplugin.endOfDirectory(addon_handle)


def list_community(page=1):
    """
    Hiển thị danh sách phim từ Google Sheet cộng đồng chia sẻ.
    """
    xbmcplugin.setPluginCategory(addon_handle, 'Cộng đồng chia sẻ')
    try:
        page = max(1, int(page))
    except Exception:
        page = 1

    per_page = get_community_items_per_page()
    xbmcplugin.setContent(addon_handle, 'movies')

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

    gsheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?gid=0&headers=1"
    nd = {}
    cache_key = f"sheet:{sheet_id}"
    gsheet_cache = load_gsheet_cache()
    cache_entry = gsheet_cache.get(cache_key, {})
    cache_age = time.time() - float(cache_entry.get('timestamp', 0) or 0)

    cache_ttl = get_gsheet_cache_ttl()

    if cache_entry.get('data') and cache_age < cache_ttl:
        nd = cache_entry.get('data', {})
    else:
        try:
            resp = requests.get(gsheet_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
            resp.raise_for_status()
            text = resp.text
            start = text.find('(')
            end = text.rfind(')')
            if start == -1 or end == -1 or start >= end:
                xbmc.log(f"GSheet parse error, response[:200]: {text[:200]}", level=xbmc.LOGERROR)
                xbmcgui.Dialog().ok('Lỗi', 'Không đọc được dữ liệu từ Google Sheet.\nKiểm tra Sheet ID và quyền truy cập (phải set "Anyone with link can view").')
                xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
                return
            nd = json.loads(text[start+1:end])
            gsheet_cache[cache_key] = {
                'timestamp': time.time(),
                'data': nd,
            }
            save_gsheet_cache(gsheet_cache)
        except Exception as e:
            if cache_entry.get('data'):
                nd = cache_entry.get('data', {})
                xbmc.log(f"GSheet error, using stale cache: {e}", level=xbmc.LOGWARNING)
            else:
                xbmcgui.Dialog().ok('Lỗi', f"Không kết nối được Google Sheet:\n{str(e)[:200]}")
                xbmc.log(f"GSheet error: {e}", level=xbmc.LOGERROR)
                xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
                return

    rows = nd.get('table', {}).get('rows', [])
    if not rows:
        xbmcgui.Dialog().ok('Thông báo', 'Google Sheet không có dữ liệu.')
        xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
        return

    total_items = len(rows)
    total_pages = max(1, (total_items + per_page - 1) // per_page)
    if page > total_pages:
        page = total_pages
    start_index = (page - 1) * per_page
    end_index = min(start_index + per_page, total_items)
    rows = rows[start_index:end_index]

    show_lookup_debug_ids = get_show_lookup_debug_ids()

    list_items = []
    lookup_cache = {}
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
            link = cell(1)
            thumb = cell(2)
            plot = cell(3)
            fanart = cell(4) or thumb
            genre = cell(5)
            rating_raw = cell(6)

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

            if link and 'token' in link:
                m = re.search(r'(https.+?)/\?token', link)
                if m:
                    link = m.group(1)

            if not name or not link:
                continue
            if not any(x in link for x in ['http', 'rtp', 'udp', 'acestream', 'plugin']):
                continue

            is_folder = any(x in link for x in [
                'fshare.vn/folder', 'docs.google.com', 'pastebin.com', 'menu', 'm3uhttp'
            ]) or ('4share.vn' in link and '/d/' in link)
            is_playable = not is_folder
            is_video_file = is_playable and is_video_item(name, link)

            # Neu ten trong Sheet khong nhan dang duoc la video nhung link la fshare file,
            # thi goi API lay ten file that de thu nhan dang lai
            real_name = ''
            if is_playable and not is_video_file and 'fshare.vn/file/' in link:
                real_name = get_fshare_file_realname(link)
                if real_name and is_video_item(real_name):
                    is_video_file = True
                    xbmc.log(f"list_community: resolved real name '{real_name}' for sheet item '{name}'", level=xbmc.LOGINFO)

            try:
                rating = float(rating_raw) if rating_raw and rating_raw.replace('.', '', 1).isdigit() else 0.0
            except:
                rating = 0.0

            # Dung ten file that (neu co) de parse stream/identity tags thay vi ten Sheet
            parse_name = real_name if real_name and is_video_file else name
            stream_tags = parse_stream_tags_from_filename(parse_name) if is_video_file else {}
            identity_tags = parse_media_identity_from_filename(parse_name, parse_name) if is_video_file else {}
            effective_season = str(identity_tags.get('season') or '')
            effective_episode = str(identity_tags.get('episode') or '')
            is_episode_item = bool(effective_season and effective_episode)
            effective_tvshowtitle = identity_tags.get('tvshowtitle') or identity_tags.get('title') or name
            effective_title = identity_tags.get('title') or name
            effective_year = str(identity_tags.get('year') or '')
            if is_episode_item:
                effective_title = f"{effective_tvshowtitle} S{effective_season.zfill(2)}E{effective_episode.zfill(2)}"

            resolved_plot = plot
            resolved_rating = str(rating_raw or '')
            resolved_poster_path = thumb
            resolved_fanart = fanart or thumb
            resolved_imdb_id = ''
            resolved_tmdb_id = ''

            lookup_key = f"{effective_title}|{effective_year}|{effective_tvshowtitle}|{effective_season}|{effective_episode}"
            needs_lookup = is_video_file and get_metadata_source() != 'none'
            if needs_lookup:
                if lookup_key not in lookup_cache:
                    lookup_cache[lookup_key] = lookup_tmdb_metadata(
                        title=effective_title if not is_episode_item else '',
                        year=effective_year,
                        tvshowtitle=effective_tvshowtitle if is_episode_item else '',
                        season=effective_season,
                        episode=effective_episode,
                    )
                resolved_meta = lookup_cache.get(lookup_key, {})
                if resolved_meta:
                    resolved_imdb_id = resolved_meta.get('imdb_id', '') or resolved_imdb_id
                    resolved_tmdb_id = resolved_meta.get('tmdb_id', '') or resolved_tmdb_id
                    if is_episode_item:
                        effective_title = resolved_meta.get('title', '') or effective_title
                        effective_tvshowtitle = resolved_meta.get('tvshowtitle', '') or effective_tvshowtitle
                    else:
                        effective_title = resolved_meta.get('title', '') or effective_title
                        effective_year = resolved_meta.get('year', '') or effective_year

            if rating <= 0 and resolved_rating:
                try:
                    rating = float(resolved_rating)
                except Exception:
                    pass

            debug_label = ''
            if show_lookup_debug_ids:
                debug_parts = []
                if resolved_imdb_id:
                    debug_parts.append(f"IMDb:{resolved_imdb_id}")
                if resolved_tmdb_id:
                    debug_parts.append(f"TMDb:{resolved_tmdb_id}")
                if debug_parts:
                    debug_label = f" [IDs: {' | '.join(debug_parts)}]"
                elif is_video_file and get_metadata_source() != 'none':
                    debug_label = ' [IDs: not found]'

            list_item = xbmcgui.ListItem(label=f"{name}{debug_label}")
            info_tag = list_item.getVideoInfoTag()
            info_tag.setTitle(effective_title)
            if resolved_plot:
                info_tag.setPlot(resolved_plot)
            if effective_year and str(effective_year).isdigit():
                info_tag.setYear(int(effective_year))

            ids = {}
            if resolved_imdb_id:
                ids['imdb'] = resolved_imdb_id
                info_tag.setIMDBNumber(resolved_imdb_id)
            if resolved_tmdb_id:
                ids['tmdb'] = resolved_tmdb_id
            if ids:
                info_tag.setUniqueIDs(ids, 'imdb' if resolved_imdb_id else 'tmdb')

            if is_video_file and is_episode_item:
                info_tag.setMediaType('episode')
                info_tag.setTvShowTitle(effective_tvshowtitle)
                info_tag.setSeason(int(effective_season))
                info_tag.setEpisode(int(effective_episode))
            else:
                info_tag.setMediaType('movie')

            if rating > 0:
                try:
                    info_tag.setRating(rating)
                except Exception:
                    pass

            art = {}
            if resolved_poster_path:
                art['thumb'] = resolved_poster_path
                art['poster'] = resolved_poster_path
                art['icon'] = resolved_poster_path
            if resolved_fanart:
                art['fanart'] = resolved_fanart
            if art:
                list_item.setArt(art)

            if is_video_file:
                video_stream = stream_tags.get('video_stream', {})
                audio_stream = stream_tags.get('audio_stream', {})
                if video_stream:
                    list_item.addStreamInfo('video', video_stream)
                if audio_stream:
                    list_item.addStreamInfo('audio', audio_stream)

                prop_map = {
                    'video_tag': stream_tags.get('video_tag', ''),
                    'audio_tag': stream_tags.get('audio_tag', ''),
                    'VideoResolution': stream_tags.get('video_resolution', ''),
                    'VideoCodec': stream_tags.get('video_codec', ''),
                    'VideoSource': stream_tags.get('video_source', ''),
                    'VideoHDR': stream_tags.get('hdr', ''),
                    'HdrType': stream_tags.get('hdr_type', ''),
                    'AudioCodec': stream_tags.get('audio_codec', ''),
                    'AudioChannels': stream_tags.get('audio_channels', ''),
                    'AudioLanguage': stream_tags.get('audio_language', ''),
                    'AudioProfile': stream_tags.get('audio_profile', ''),
                    'AudioObject': stream_tags.get('audio_object', ''),
                    'AudioAtmos': stream_tags.get('audio_object', ''),
                    'AudioCodec2': stream_tags.get('audio_object', ''),
                    'AudioCodecAlt': stream_tags.get('audio_object', ''),
                    'AudioCodecExtra': stream_tags.get('audio_profile', ''),
                    'AudioCodecCombined': stream_tags.get('audio_profile', '') or stream_tags.get('audio_codec', ''),
                    'video_resolution': stream_tags.get('video_resolution', ''),
                    'video_codec': stream_tags.get('video_codec', ''),
                    'video_source': stream_tags.get('video_source', ''),
                    'video_hdr': stream_tags.get('hdr', ''),
                    'hdr_type': stream_tags.get('hdr_type', ''),
                    'audio_codec': stream_tags.get('audio_codec', ''),
                    'audio_channels': stream_tags.get('audio_channels', ''),
                    'audio_language': stream_tags.get('audio_language', ''),
                    'audio_profile': stream_tags.get('audio_profile', ''),
                    'audio_object': stream_tags.get('audio_object', ''),
                    'VideoPlayer.VideoResolution': stream_tags.get('video_resolution', ''),
                    'VideoPlayer.VideoCodec': stream_tags.get('video_codec', ''),
                    'VideoPlayer.VideoSource': stream_tags.get('video_source', ''),
                    'VideoPlayer.HdrType': stream_tags.get('hdr_type', ''),
                    'VideoPlayer.AudioCodec': stream_tags.get('audio_codec', ''),
                    'VideoPlayer.AudioChannels': stream_tags.get('audio_channels', ''),
                    'VideoPlayer.AudioLanguage': stream_tags.get('audio_language', ''),
                    'VideoPlayer.AudioProfile': stream_tags.get('audio_profile', ''),
                    'VideoInfo.VideoResolution': stream_tags.get('video_resolution', ''),
                    'VideoInfo.VideoCodec': stream_tags.get('video_codec', ''),
                    'VideoInfo.VideoSource': stream_tags.get('video_source', ''),
                    'VideoInfo.AudioCodec': stream_tags.get('audio_codec', ''),
                    'VideoInfo.AudioChannels': stream_tags.get('audio_channels', ''),
                    'VideoInfo.AudioLanguage': stream_tags.get('audio_language', ''),
                    'VideoInfo.AudioProfile': stream_tags.get('audio_profile', ''),
                    'media.videoresolution': stream_tags.get('video_resolution', ''),
                    'media.videocodec': stream_tags.get('video_codec', ''),
                    'media.videosource': stream_tags.get('video_source', ''),
                    'media.hdr': stream_tags.get('hdr', ''),
                    'media.hdrtype': stream_tags.get('hdr_type', ''),
                    'media.audiocodec': stream_tags.get('audio_codec', ''),
                    'media.audiochannels': stream_tags.get('audio_channels', ''),
                    'media.audiolanguage': stream_tags.get('audio_language', ''),
                    'media.audioprofile': stream_tags.get('audio_profile', ''),
                    'media.audioobject': stream_tags.get('audio_object', ''),
                    'audio.codec': stream_tags.get('audio_codec', ''),
                    'audio.channels': stream_tags.get('audio_channels', ''),
                    'audio.language': stream_tags.get('audio_language', ''),
                    'audio.profile': stream_tags.get('audio_profile', ''),
                    'audio.object': stream_tags.get('audio_object', ''),
                    'video.codec': stream_tags.get('video_codec', ''),
                    'video.resolution': stream_tags.get('video_resolution', ''),
                    'video.source': stream_tags.get('video_source', ''),
                    'video.hdr': stream_tags.get('hdr', ''),
                    'video.hdrtype': stream_tags.get('hdr_type', ''),
                    'HasAtmos': 'true' if stream_tags.get('audio_object', '').lower() == 'atmos' else '',
                    'AudioIsAtmos': 'true' if stream_tags.get('audio_object', '').lower() == 'atmos' else '',
                    'HasDTSX': 'true' if 'dts:x' in stream_tags.get('audio_profile', '').lower() else '',
                    'AudioIsDTSX': 'true' if 'dts:x' in stream_tags.get('audio_profile', '').lower() else '',
                }
                for prop_name, prop_value in prop_map.items():
                    if prop_value:
                        list_item.setProperty(prop_name, str(prop_value))

            if show_lookup_debug_ids:
                list_item.setProperty('debug.imdb_id', resolved_imdb_id)
                list_item.setProperty('debug.tmdb_id', resolved_tmdb_id)
                list_item.setProperty('debug.lookup_status', 'ok' if (resolved_imdb_id or resolved_tmdb_id) else 'missing')

            if is_playable:
                list_item.setProperty('IsPlayable', 'true')
                play_params = {
                    'action': 'play_trakt',
                    'url': f'plugin://plugin.video.vietmediaF?action=play&url={link}',
                    'imdb': resolved_imdb_id,
                    'tmdb': resolved_tmdb_id,
                    'title': effective_title,
                    'filename': name,
                    'year': effective_year,
                    'season': effective_season,
                    'episode': effective_episode,
                    'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
                }
                play_url = sys.argv[0] + '?' + urllib.parse.urlencode(play_params)
                list_items.append((play_url, list_item, False))
            else:
                if 'docs.google.com' in link:
                    browse_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'browse_gsheet', 'url': link})
                elif 'fshare.vn/folder' in link:
                    browse_url = sys.argv[0] + '?' + urllib.parse.urlencode({
                        'action': 'browse_fshare_folder',
                        'url': link,
                        'page_index': '0',
                        'folder_name': name,
                    })
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

    if page < total_pages:
        next_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'list_community', 'page': page + 1})
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=next_url, listitem=xbmcgui.ListItem(f'[Trang sau {page + 1}/{total_pages}]'), isFolder=True)

    if page > 1:
        prev_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'list_community', 'page': page - 1})
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=prev_url, listitem=xbmcgui.ListItem(f'[Trang truoc {page - 1}/{total_pages}]'), isFolder=True)

    xbmcplugin.endOfDirectory(addon_handle)
def show_fshare_links(movie_title, movie_year, imdb_id=None, tmdb_id=None,
                      season=None, episode=None, tvshowtitle=None,
                      include=None, exclude=None):
    """
    Lọc kết quả tìm kiếm Fshare bằng keyword match trên tên file.

    include : chuỗi các nhóm AND ngăn cách bằng ';', trong mỗi nhóm dùng '|' cho OR.
              Ví dụ: '2160p|4k|uhd;atmos|ddp;mkv|iso'
              -> (2160p OR 4k OR uhd) AND (atmos OR ddp) AND (mkv OR iso)
    exclude : chuỗi keyword ngăn cách bằng '|', bất kỳ keyword nào match thì loại.
              Ví dụ: 'hdcam|cam|telesync'

    Match theo token (tách tên file theo . - _ space) để tránh false positive.
    Fallback: nếu không có kết quả -> bỏ dần nhóm include từ cuối -> hiện tất cả.
    """
    content_type = 'movies' if not season else 'episodes'
    xbmcplugin.setContent(addon_handle, content_type)
    xbmcplugin.setPluginCategory(addon_handle, f'Links: {movie_title}')
    show_lookup_debug_ids = get_show_lookup_debug_ids()

    links = search_fshare(movie_title, movie_year, season=season, episode=episode)
    if not links:
        notify(f"Không tìm thấy link cho {movie_title}")
        xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
        return

    links.sort(key=lambda x: x.get('size', 0), reverse=True)

    # ------------------------------------------------------------------ #
    #  INCLUDE / EXCLUDE FILTER                                           #
    #  include='2160p|4k|uhd;atmos|ddp;mkv|iso'                          #
    #    ';' phan cach nhom AND, '|' phan cach gia tri OR trong nhom      #
    #  exclude='hdcam|cam|telesync'                                       #
    #    '|' phan cach keyword, bat ky keyword nao match thi loai         #
    #  Match theo token de tranh false positive (ts != TrueHD)            #
    #  Fallback: bo dan tung nhom include tu cuoi -> hien tat ca          #
    # ------------------------------------------------------------------ #

    def _tokenize(filename):
        """Tach ten file thanh set token lowercase de match chinh xac."""
        name = os.path.basename(filename or '').lower()
        raw = re.split(r'[\.\-_ ]+', name)
        tokens = set(t for t in raw if t)
        # Them full name (bo extension) de match chuoi co dau nhu 'web-dl', 'dts-hd'
        name_no_ext = re.sub(r'\.[a-z0-9]{2,4}$', '', name)
        tokens.add(name_no_ext)
        return tokens

    def _token_match(keyword, tokens, full_name):
        """Kiem tra keyword co match trong tokens hoac full_name khong."""
        kw = keyword.strip().lower()
        if not kw:
            return False
        if kw in tokens:
            return True
        if kw in full_name:
            return True
        return False

    def _parse_include_groups(include_param):
        """Phan tich include='A|B;C|D' thanh [['a','b'], ['c','d']]."""
        if not include_param:
            return []
        groups = []
        for group_str in str(include_param).split(';'):
            group_str = group_str.strip()
            if not group_str:
                continue
            keywords = [k.strip().lower() for k in group_str.split('|') if k.strip()]
            if keywords:
                groups.append(keywords)
        return groups

    def _parse_exclude_list(exclude_param):
        """Phan tich exclude='A|B|C' thanh ['a','b','c']."""
        if not exclude_param:
            return []
        return [k.strip().lower() for k in str(exclude_param).split('|') if k.strip()]

    def _item_matches(link_info, include_groups_check):
        """
        True neu khong bi exclude VA tat ca nhom include deu co it nhat 1 match.
        """
        fname  = link_info.get('title', '')
        full   = fname.lower()
        tokens = _tokenize(fname)

        # exclude: loai ngay neu bat ky keyword nao match
        for kw in exclude_list:
            if _token_match(kw, tokens, full):
                return False

        # include: AND giua cac nhom, OR trong moi nhom
        for group in include_groups_check:
            if not any(_token_match(kw, tokens, full) for kw in group):
                return False

        return True

    include_groups = _parse_include_groups(include)
    exclude_list   = _parse_exclude_list(exclude)
    has_filter     = bool(include_groups or exclude_list)

    if has_filter:
        # Buoc 1: thu filter day du
        filtered = [l for l in links if _item_matches(l, include_groups)]

        # Buoc 2: fallback - bo dan nhom include tu cuoi neu khong co ket qua
        fallback_label = ''
        current_groups = include_groups[:]
        while not filtered and current_groups:
            dropped = current_groups.pop()
            xbmc.log(
                f"show_fshare_links: no results, dropping include group {dropped}",
                level=xbmc.LOGINFO
            )
            filtered = [l for l in links if _item_matches(l, current_groups)]
            if filtered:
                fallback_label = '|'.join(dropped)

        # Buoc 3: van trang → hien tat ca (van giu exclude)
        if not filtered:
            filtered = [l for l in links if _item_matches(l, [])]
            fallback_label = 'tat ca'

        # Buoc 4: van trang (exclude qua chat) → hien luon tat ca
        if not filtered:
            filtered = links
            fallback_label = 'tat ca (bo ca exclude)'

        if fallback_label and include_groups:
            req = ';'.join('|'.join(g) for g in include_groups)
            xbmcgui.Dialog().notification(
                'FShare Filter',
                f'Fallback: [{req}] -> {fallback_label}',
                xbmcgui.NOTIFICATION_INFO, 3500
            )

        links = filtered

    list_items = []
    lookup_cache = {}

    for i, link_info in enumerate(links):
        size = link_info.get('size', 0)
        size_str = f"{size/(1024**3):.2f} GB" if size >= 1024**3 else f"{size/(1024**2):.2f} MB"
        stream_tags = parse_stream_tags_from_filename(link_info.get('title', ''))
        identity_tags = parse_media_identity_from_filename(link_info.get('title', ''), movie_title)
        effective_season = str(season or identity_tags.get('season') or '')
        effective_episode = str(episode or identity_tags.get('episode') or '')
        is_episode_item = bool(effective_season and effective_episode)
        effective_tvshowtitle = tvshowtitle or identity_tags.get('tvshowtitle') or movie_title or identity_tags.get('title', '')
        effective_title = movie_title or identity_tags.get('display_title') or identity_tags.get('title') or link_info.get('title', '')
        effective_year = str(movie_year or identity_tags.get('year') or '')
        if is_episode_item:
            effective_title = f"{effective_tvshowtitle} S{effective_season.zfill(2)}E{effective_episode.zfill(2)}"
        resolved_imdb_id = str(imdb_id) if imdb_id else ''
        resolved_tmdb_id = str(tmdb_id) if tmdb_id else ''
        resolved_plot = ''
        resolved_rating = ''
        resolved_poster_path = ''

        lookup_key = f"{effective_title}|{effective_year}|{effective_tvshowtitle}|{effective_season}|{effective_episode}"
        if get_metadata_source() != 'none':
            if lookup_key not in lookup_cache:
                lookup_cache[lookup_key] = lookup_tmdb_metadata(
                    title=effective_title if not is_episode_item else '',
                    year=effective_year,
                    tvshowtitle=effective_tvshowtitle if is_episode_item else '',
                    season=effective_season,
                    episode=effective_episode,
                )
            tmdb_meta = lookup_cache.get(lookup_key, {})
            if tmdb_meta:
                resolved_imdb_id = tmdb_meta.get('imdb_id', '') or resolved_imdb_id
                resolved_tmdb_id = tmdb_meta.get('tmdb_id', '') or resolved_tmdb_id
                if is_episode_item:
                    effective_title = tmdb_meta.get('title', '') or effective_title
                    effective_tvshowtitle = tmdb_meta.get('tvshowtitle', '') or effective_tvshowtitle
                else:
                    effective_title = tmdb_meta.get('title', '') or effective_title
                    effective_year = tmdb_meta.get('year', '') or effective_year
        debug_label = ''
        if show_lookup_debug_ids:
            debug_parts = []
            if resolved_imdb_id:
                debug_parts.append(f"IMDb:{resolved_imdb_id}")
            if resolved_tmdb_id:
                debug_parts.append(f"TMDb:{resolved_tmdb_id}")
            if debug_parts:
                debug_label = f" [IDs: {' | '.join(debug_parts)}]"
            elif get_metadata_source() != 'none':
                debug_label = ' [IDs: not found]'  

        title_label = f"{i+1}: {link_info['title']}{debug_label} - ({size_str})"

        # URL gọi qua action play_trakt để set script.trakt.ids trước khi play
        play_params = {
            'action': 'play_trakt',
            'url': link_info['url'],
            'imdb': resolved_imdb_id,
            'tmdb': resolved_tmdb_id,
            'title': effective_title,
            'filename': link_info.get('title', ''),
            'year': effective_year,
            'season': effective_season,
            'episode': effective_episode,
            'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
        }
        play_url = sys.argv[0] + '?' + urllib.parse.urlencode(play_params)

        list_item = xbmcgui.ListItem(label=title_label, path=play_url)

        # Gán metadata vào ListItem
        info_tag = list_item.getVideoInfoTag()
        info_tag.setTitle(effective_title)

        if resolved_plot:
            info_tag.setPlot(resolved_plot)

        if effective_year and str(effective_year).isdigit():
            info_tag.setYear(int(effective_year))

        ids = {}
        if resolved_imdb_id:
            ids['imdb'] = resolved_imdb_id
            info_tag.setIMDBNumber(resolved_imdb_id)
        if resolved_tmdb_id:
            ids['tmdb'] = resolved_tmdb_id
        if ids:
            info_tag.setUniqueIDs(ids, 'imdb' if resolved_imdb_id else 'tmdb')

        if show_lookup_debug_ids:
            list_item.setProperty('debug.imdb_id', resolved_imdb_id)
            list_item.setProperty('debug.tmdb_id', resolved_tmdb_id)
            list_item.setProperty('debug.lookup_status', 'ok' if (resolved_imdb_id or resolved_tmdb_id) else 'missing')

        if is_episode_item:
            info_tag.setMediaType('episode')
            info_tag.setTvShowTitle(effective_tvshowtitle)
            info_tag.setSeason(int(effective_season))
            info_tag.setEpisode(int(effective_episode))
        else:
            info_tag.setMediaType('movie')

        if resolved_poster_path:
            list_item.setArt({'thumb': resolved_poster_path, 'poster': resolved_poster_path, 'fanart': resolved_poster_path, 'icon': resolved_poster_path})

        video_stream = stream_tags.get('video_stream', {})
        audio_stream = stream_tags.get('audio_stream', {})
        if video_stream:
            list_item.addStreamInfo('video', video_stream)
        if audio_stream:
            list_item.addStreamInfo('audio', audio_stream)

        prop_map = {
            'video_tag': stream_tags.get('video_tag', ''),
            'audio_tag': stream_tags.get('audio_tag', ''),
            'VideoResolution': stream_tags.get('video_resolution', ''),
            'VideoCodec': stream_tags.get('video_codec', ''),
            'VideoSource': stream_tags.get('video_source', ''),
            'VideoHDR': stream_tags.get('hdr', ''),
            'HdrType': stream_tags.get('hdr_type', ''),
            'AudioCodec': stream_tags.get('audio_codec', ''),
            'AudioChannels': stream_tags.get('audio_channels', ''),
            'AudioLanguage': stream_tags.get('audio_language', ''),
            'AudioProfile': stream_tags.get('audio_profile', ''),
            'AudioObject': stream_tags.get('audio_object', ''),
            'AudioAtmos': stream_tags.get('audio_object', ''),
            'AudioCodec2': stream_tags.get('audio_object', ''),
            'AudioCodecAlt': stream_tags.get('audio_object', ''),
            'AudioCodecExtra': stream_tags.get('audio_profile', ''),
            'AudioCodecCombined': stream_tags.get('audio_profile', '') or stream_tags.get('audio_codec', ''),
            'video_resolution': stream_tags.get('video_resolution', ''),
            'video_codec': stream_tags.get('video_codec', ''),
            'video_source': stream_tags.get('video_source', ''),
            'video_hdr': stream_tags.get('hdr', ''),
            'hdr_type': stream_tags.get('hdr_type', ''),
            'audio_codec': stream_tags.get('audio_codec', ''),
            'audio_channels': stream_tags.get('audio_channels', ''),
            'audio_language': stream_tags.get('audio_language', ''),
            'audio_profile': stream_tags.get('audio_profile', ''),
            'audio_object': stream_tags.get('audio_object', ''),
            'audio_codec2': stream_tags.get('audio_object', ''),
            'audio_codec_alt': stream_tags.get('audio_object', ''),
            'audio_codec_extra': stream_tags.get('audio_profile', ''),
            'audio_codec_combined': stream_tags.get('audio_profile', '') or stream_tags.get('audio_codec', ''),
            'media.hdr': stream_tags.get('hdr', ''),
            'media.source': stream_tags.get('video_source', ''),
            'media.hdrtype': stream_tags.get('hdr_type', ''),
            'media.audio': stream_tags.get('audio_codec', ''),
            'media.audioprofile': stream_tags.get('audio_profile', ''),
            'media.audioobject': stream_tags.get('audio_object', ''),
            'media.audio2': stream_tags.get('audio_object', ''),
            'media.audioextra': stream_tags.get('audio_profile', ''),
            'VideoPlayer.VideoResolution': stream_tags.get('video_resolution', ''),
            'VideoPlayer.VideoCodec': stream_tags.get('video_codec', ''),
            'VideoPlayer.AudioCodec': stream_tags.get('audio_codec', ''),
            'VideoPlayer.AudioChannels': stream_tags.get('audio_channels', ''),
            'VideoPlayer.HDRType': stream_tags.get('hdr', ''),
            'VideoPlayer.HdrType': stream_tags.get('hdr_type', ''),
            'VideoPlayer.AudioProfile': stream_tags.get('audio_profile', ''),
            'VideoPlayer.AudioObject': stream_tags.get('audio_object', ''),
            'VideoPlayer.AudioCodec2': stream_tags.get('audio_object', ''),
            'VideoPlayer.AudioCodecCombined': stream_tags.get('audio_profile', '') or stream_tags.get('audio_codec', ''),
            'VideoPlayer.VideoAspect': '',
            'VideoInfo.VideoResolution': stream_tags.get('video_resolution', ''),
            'VideoInfo.VideoCodec': stream_tags.get('video_codec', ''),
            'VideoInfo.AudioCodec': stream_tags.get('audio_codec', ''),
            'VideoInfo.AudioChannels': stream_tags.get('audio_channels', ''),
            'VideoInfo.HDRType': stream_tags.get('hdr', ''),
            'VideoInfo.HdrType': stream_tags.get('hdr_type', ''),
            'VideoInfo.AudioProfile': stream_tags.get('audio_profile', ''),
            'VideoInfo.AudioObject': stream_tags.get('audio_object', ''),
            'VideoInfo.AudioCodec2': stream_tags.get('audio_object', ''),
            'VideoInfo.AudioCodecCombined': stream_tags.get('audio_profile', '') or stream_tags.get('audio_codec', ''),
            'video.resolution': stream_tags.get('video_resolution', ''),
            'video.codec': stream_tags.get('video_codec', ''),
            'video.source': stream_tags.get('video_source', ''),
            'video.hdr': stream_tags.get('hdr', ''),
            'audio.codec': stream_tags.get('audio_codec', ''),
            'audio.channels': stream_tags.get('audio_channels', ''),
            'audio.language': stream_tags.get('audio_language', ''),
            'audio.profile': stream_tags.get('audio_profile', ''),
            'audio.object': stream_tags.get('audio_object', ''),
            'audio.codec2': stream_tags.get('audio_object', ''),
            'audio.codec_combined': stream_tags.get('audio_profile', '') or stream_tags.get('audio_codec', ''),
            'media.resolution': stream_tags.get('video_resolution', ''),
            'media.codec': stream_tags.get('video_codec', ''),
            'media.channels': stream_tags.get('audio_channels', ''),
        }
        for prop_name, prop_value in prop_map.items():
            if prop_value:
                list_item.setProperty(prop_name, str(prop_value))

        flag_props = {
            'HasAtmos': 'true' if stream_tags.get('audio_object') == 'Atmos' else '',
            'AudioIsAtmos': 'true' if stream_tags.get('audio_object') == 'Atmos' else '',
            'HasDTSX': 'true' if stream_tags.get('audio_codec') == 'DTS:X' or stream_tags.get('audio_profile') == 'DTS:X' else '',
            'AudioIsDTSX': 'true' if stream_tags.get('audio_codec') == 'DTS:X' or stream_tags.get('audio_profile') == 'DTS:X' else '',
        }
        for prop_name, prop_value in flag_props.items():
            if prop_value:
                list_item.setProperty(prop_name, prop_value)

        list_item.setProperty('IsPlayable', 'true')

        # Context menu
        strm_params = {
            'action': 'create_strm',
            'title': link_info['title'],
            'url': link_info['url'],
            'movie_title': effective_title,
            'movie_year': effective_year,
            'imdb': resolved_imdb_id,
            'tmdb': resolved_tmdb_id,
            'movie_plot': resolved_plot,
            'movie_rating': resolved_rating,
            'season': effective_season,
            'episode': effective_episode,
            'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
            'video_resolution': stream_tags.get('video_resolution', ''),
            'video_source': stream_tags.get('video_source', ''),
            'video_codec': stream_tags.get('video_codec', ''),
            'hdr': stream_tags.get('hdr', ''),
            'hdr_type': stream_tags.get('hdr_type', ''),
            'audio_codec': stream_tags.get('audio_codec', ''),
            'audio_channels': stream_tags.get('audio_channels', ''),
            'audio_language': stream_tags.get('audio_language', ''),
            'audio_profile': stream_tags.get('audio_profile', ''),
            'audio_object': stream_tags.get('audio_object', ''),
        }
        strm_url = sys.argv[0] + '?' + urllib.parse.urlencode(strm_params)

        # Context menu download
        dl_params = {
            'action': 'download_fshare',
            'title': link_info['title'],
            'url': link_info['url'],
            'fshare_url': link_info.get('fshare_url', ''),
            'movie_title': effective_title,
            'movie_year': effective_year,
            'imdb': resolved_imdb_id,
            'tmdb': resolved_tmdb_id,
            'movie_plot': resolved_plot,
            'movie_rating': resolved_rating,
            'season': effective_season,
            'episode': effective_episode,
            'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
            'video_resolution': stream_tags.get('video_resolution', ''),
            'video_source': stream_tags.get('video_source', ''),
            'video_codec': stream_tags.get('video_codec', ''),
            'hdr': stream_tags.get('hdr', ''),
            'hdr_type': stream_tags.get('hdr_type', ''),
            'audio_codec': stream_tags.get('audio_codec', ''),
            'audio_channels': stream_tags.get('audio_channels', ''),
            'audio_language': stream_tags.get('audio_language', ''),
            'audio_profile': stream_tags.get('audio_profile', ''),
            'audio_object': stream_tags.get('audio_object', ''),
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

    # Neu chua co ID va setting fetch_ids_on_play bat -> tra API ngay truoc khi play
    if get_fetch_ids_on_play() and not imdb_id and not tmdb_id and movie_title:
        xbmc.log(f"play_trakt: no IDs for '{movie_title}', fetching now...", level=xbmc.LOGINFO)
        try:
            meta = lookup_tmdb_metadata(
                title=movie_title if not (season and episode) else '',
                year=movie_year,
                tvshowtitle=tvshowtitle if (season and episode) else '',
                season=season,
                episode=episode,
                
            )
            if meta:
                imdb_id = imdb_id or meta.get('imdb_id', '')
                tmdb_id = tmdb_id or meta.get('tmdb_id', '')
                xbmc.log(f"play_trakt: fetched imdb={imdb_id} tmdb={tmdb_id}", level=xbmc.LOGINFO)
        except Exception as e:
            xbmc.log(f"play_trakt: ID fetch error: {e}", level=xbmc.LOGWARNING)

    # Set script.trakt.ids vao Window(10000)
    if imdb_id or tmdb_id:
        trakt_ids = {}
        if imdb_id:
            trakt_ids['imdb'] = str(imdb_id)
        if tmdb_id:
            trakt_ids['tmdb'] = str(tmdb_id)
        xbmcgui.Window(10000).setProperty('script.trakt.ids', _json.dumps(trakt_ids))
        xbmc.log(f"Set script.trakt.ids: {_json.dumps(trakt_ids)}", level=xbmc.LOGINFO)

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

        elif action == 'list_community':
            list_community(params.get('page', '1'))

        elif action == 'settings_menu':
            settings_menu()

        elif action == 'set_tmdb_api_key':
            prompt_text_setting('tmdb_api_key', 'Nhập TMDb API Key', get_local_setting('tmdb_api_key', ''))

        elif action == 'choose_strm_dir':
            choose_strm_directory()

        elif action == 'toggle_debug_ids':
            toggle_debug_setting()

        elif action == 'cycle_metadata_source':
            cycle_metadata_source()
            settings_menu()

        elif action == 'toggle_fetch_ids_on_play':
            toggle_bool_setting('fetch_ids_on_play', 'Tra IMDb/TMDb ID khi play')
            settings_menu()

        elif action == 'set_items_per_page':
            prompt_number_setting('community_items_per_page', 'Nhập số mục mỗi trang cộng đồng', get_community_items_per_page())

        elif action == 'set_cache_ttl':
            prompt_number_setting('community_cache_ttl', 'Nhập thời gian giữ cache cộng đồng (giây)', get_gsheet_cache_ttl())

        elif action == 'clear_gsheet_cache':
            clear_gsheet_cache()
            xbmcgui.Dialog().notification('Thành công', 'Đã xóa cache cộng đồng', time=3000)

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

        elif action == 'browse_fshare_folder':
            folder_url = params.get('url', '')
            if folder_url:
                browse_fshare_folder(
                    folder_url=folder_url,
                    page_index=params.get('page_index', '0'),
                    folder_name=params.get('folder_name', ''),
                )

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

            # Filter keyword tu do (tu player entry TMDb Helper)
            # include: 'A|B;C|D'  -> (A OR B) AND (C OR D)
            #   vi du: '2160p|4k|uhd;atmos|ddp;mkv|iso'
            # exclude: 'X|Y|Z'    -> loai file chua bat ky keyword X, Y, Z
            #   vi du: 'hdcam|cam|telesync'
            include = params.get('include') or None
            exclude = params.get('exclude') or None

            if movie_title:
                show_fshare_links(
                    movie_title,
                    movie_year,
                    imdb_id=imdb_id,
                    tmdb_id=tmdb_id,
                    season=season,
                    episode=episode,
                    tvshowtitle=tvshowtitle,
                    include=include,
                    exclude=exclude,
                )

        
        elif action == 'download_fshare':
            info = {
                'title': params.get('movie_title', ''),
                'year': params.get('movie_year', ''),
                'imdb_id': params.get('imdb_id', params.get('imdb', '')),
                'tmdb_id': params.get('tmdb', ''),
                'plot': params.get('movie_plot', ''),
                'rating': params.get('movie_rating', ''),
                'season': params.get('season', ''),
                'episode': params.get('episode', ''),
                'tvshowtitle': params.get('tvshowtitle', ''),
                'video_resolution': params.get('video_resolution', ''),
                'video_source': params.get('video_source', ''),
                'video_codec': params.get('video_codec', ''),
                'hdr': params.get('hdr', ''),
                'hdr_type': params.get('hdr_type', ''),
                'audio_codec': params.get('audio_codec', ''),
                'audio_channels': params.get('audio_channels', ''),
                'audio_language': params.get('audio_language', ''),
                'audio_profile': params.get('audio_profile', ''),
                'audio_object': params.get('audio_object', ''),
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
                'season': params.get('season', ''),
                'episode': params.get('episode', ''),
                'tvshowtitle': params.get('tvshowtitle', ''),
                'video_resolution': params.get('video_resolution', ''),
                'video_source': params.get('video_source', ''),
                'video_codec': params.get('video_codec', ''),
                'hdr': params.get('hdr', ''),
                'hdr_type': params.get('hdr_type', ''),
                'audio_codec': params.get('audio_codec', ''),
                'audio_channels': params.get('audio_channels', ''),
                'audio_language': params.get('audio_language', ''),
                'audio_profile': params.get('audio_profile', ''),
                'audio_object': params.get('audio_object', ''),
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

if __name__ == '__main__':
    router(sys.argv[2][1:])
