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
# Thư mục dữ liệu người dùng — không bị xóa khi update addon
ADDON_DATA_PATH = xbmcvfs.translatePath(f'special://userdata/addon_data/{ADDON_ID}')
os.makedirs(ADDON_DATA_PATH, exist_ok=True)

VIDEO_FILE_EXTENSIONS = (
    '.mkv', '.mp4', '.avi', '.wmv', '.iso', '.ts', '.m2ts', '.mov',
    '.mpg', '.mpeg', '.m4v', '.webm', '.flv', '.3gp', '.asf', '.vob',
    '.ogm', '.ogv', '.divx', '.xvid', '.rm', '.rmvb', '.qt', '.f4v',
    '.mts', '.tp', '.trp', '.tod', '.vro', '.mxf'
)

# URL tìm kiếm Fshare.vn (có thể cần điều chỉnh nếu Fshare thay đổi)
FSHARE_SEARCH_API_URL = "https://api.timfshare.com/v1/string-query-search?query="

SETTINGS_FILE          = os.path.join(ADDON_DATA_PATH, 'settings.json')
TMDB_LOOKUP_CACHE_FILE = os.path.join(ADDON_DATA_PATH, 'tmdb_lookup_cache.json')
GSHEET_CACHE_FILE      = os.path.join(ADDON_DATA_PATH, 'gsheet_cache.json')
GSHEET_CACHE_TTL = 300

SEARCH_HISTORY_FILE    = os.path.join(ADDON_DATA_PATH, 'search_history.json')
PLAY_HISTORY_FILE      = os.path.join(ADDON_DATA_PATH, 'play_history.json')
HISTORY_MAX            = 15


def notify(msg, title='Fshare', duration=3000, sound=True):
    """Hiển thị thông báo nhanh trong Kodi."""
    xbmcgui.Dialog().notification(title, msg, time=duration, sound=sound)

# ---------------------------------------------------------------------------
# HISTORY — lịch sử tìm kiếm & lịch sử xem
# ---------------------------------------------------------------------------

def _format_history_time(ts):
    """Chuyển Unix timestamp thành chuỗi thân thiện: Hôm nay HH:MM / Hôm qua HH:MM / N ngày trước / DD/MM/YYYY."""
    try:
        import datetime
        now   = datetime.datetime.now()
        dt    = datetime.datetime.fromtimestamp(ts)
        delta = (now.date() - dt.date()).days
        hhmm  = dt.strftime('%H:%M')
        if delta == 0:
            return f'Hôm nay {hhmm}'
        elif delta == 1:
            return f'Hôm qua {hhmm}'
        elif delta <= 6:
            return f'{delta} ngày trước'
        else:
            return dt.strftime('%d/%m/%Y')
    except Exception:
        return ''


def _format_size(size_bytes):
    """Trả về chuỗi dung lượng dễ đọc: 19.4 GB / 850 MB."""
    try:
        b = int(size_bytes or 0)
        if b >= 1024 ** 3:
            return f'{b / (1024 ** 3):.1f} GB'
        elif b >= 1024 ** 2:
            return f'{b / (1024 ** 2):.0f} MB'
        return ''
    except Exception:
        return ''


def load_search_history():
    """Trả về list[dict] từ search_history.json, [] nếu lỗi."""
    try:
        if os.path.exists(SEARCH_HISTORY_FILE):
            with open(SEARCH_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []


def save_search_history(query):
    """Prepend query vào đầu lịch sử, deduplicate, giới hạn HISTORY_MAX."""
    if not query or not query.strip():
        return
    query = query.strip()
    try:
        history = load_search_history()
        # Loại bỏ duplicate cũ (case-insensitive)
        history = [h for h in history if h.get('query', '').lower() != query.lower()]
        history.insert(0, {'query': query, 'time': int(time.time())})
        history = history[:HISTORY_MAX]
        with open(SEARCH_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        xbmc.log(f'save_search_history error: {e}', level=xbmc.LOGWARNING)


def clear_search_history():
    """Xóa toàn bộ lịch sử tìm kiếm."""
    try:
        if os.path.exists(SEARCH_HISTORY_FILE):
            os.remove(SEARCH_HISTORY_FILE)
    except Exception as e:
        xbmc.log(f'clear_search_history error: {e}', level=xbmc.LOGWARNING)


def load_play_history():
    """Trả về list[dict] từ play_history.json, [] nếu lỗi."""
    try:
        if os.path.exists(PLAY_HISTORY_FILE):
            with open(PLAY_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return []


def save_play_history(title='', year='', filename='', fshare_url='',
                      imdb_id='', tmdb_id='', poster_url='', size_bytes=0, plot=''):
    """Prepend entry vào đầu lịch sử xem, deduplicate theo fshare_url, giới hạn HISTORY_MAX."""
    if not fshare_url and not filename:
        return
    try:
        history = load_play_history()
        # Deduplicate theo fshare_url (nếu có), fallback theo filename
        if fshare_url:
            history = [h for h in history if h.get('fshare_url', '') != fshare_url]
        elif filename:
            history = [h for h in history if h.get('filename', '') != filename]

        # Build subtitle: "filename · size" — KHÔNG nhúng thời gian vào đây,
        # thời gian sẽ được tính lại động mỗi lần hiển thị từ field 'time'.
        _subtitle_parts = []
        if filename:
            _subtitle_parts.append(filename)
        if size_bytes and int(size_bytes) > 0:
            _subtitle_parts.append(_format_size(int(size_bytes)))
        _subtitle = ' · '.join(p for p in _subtitle_parts if p)

        # full_plot = subtitle + dòng trắng + plot gốc (nếu có)
        if plot and plot.strip():
            full_plot = f"{_subtitle}\n\n{plot.strip()}" if _subtitle else plot.strip()
        else:
            full_plot = _subtitle

        entry = {
            'title':      title or '',
            'year':       str(year or ''),
            'filename':   filename or '',
            'fshare_url': fshare_url or '',
            'imdb_id':    imdb_id or '',
            'tmdb_id':    tmdb_id or '',
            'poster_url': poster_url or '',
            'plot':       full_plot,
            'size_bytes': int(size_bytes or 0),
            'time':       int(time.time()),
        }
        history.insert(0, entry)
        history = history[:HISTORY_MAX]
        with open(PLAY_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
        xbmc.log(
            f"save_play_history: '{title or filename}' fshare={bool(fshare_url)}",
            level=xbmc.LOGINFO
        )
    except Exception as e:
        xbmc.log(f'save_play_history error: {e}', level=xbmc.LOGWARNING)


def clear_play_history():
    """Xóa toàn bộ lịch sử xem."""
    try:
        if os.path.exists(PLAY_HISTORY_FILE):
            os.remove(PLAY_HISTORY_FILE)
    except Exception as e:
        xbmc.log(f'clear_play_history error: {e}', level=xbmc.LOGWARNING)


def list_play_history():
    """
    Hiển thị danh sách lịch sử xem gần đây.
    - Bấm vào item → play thẳng qua fshare_url (nếu còn) hoặc search lại.
    - Context menu: 'Tìm lại link mới' để search lại dù link còn sống.
    """
    xbmcplugin.setPluginCategory(addon_handle, 'Lịch sử xem gần đây')
    xbmcplugin.setContent(addon_handle, 'movies')

    history = load_play_history()

    # Nút xóa — đặt trên cùng
    clear_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'clear_play_history'})
    clear_item = xbmcgui.ListItem('[🗑 Xóa lịch sử xem]')
    clear_item.setArt({'icon': 'DefaultAddonProgram.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=clear_url, listitem=clear_item, isFolder=False)

    if not history:
        empty_item = xbmcgui.ListItem('(Chưa có lịch sử xem)')
        xbmcplugin.addDirectoryItem(handle=addon_handle, url='', listitem=empty_item, isFolder=False)
        xbmcplugin.endOfDirectory(addon_handle)
        return

    list_items = []
    for entry in history:
        title      = entry.get('title', '')
        year       = entry.get('year', '')
        filename   = entry.get('filename', '')
        fshare_url = entry.get('fshare_url', '')
        imdb_id    = entry.get('imdb_id', '')
        tmdb_id    = entry.get('tmdb_id', '')
        poster_url = entry.get('poster_url', '')
        size_bytes = entry.get('size_bytes', 0)
        plot_text  = entry.get('plot', '')
        ts         = entry.get('time', 0)

        # Label chính: "Title (Year)" hoặc filename nếu không có title
        if title:
            label = f'{title} ({year})' if year else title
        else:
            label = filename or '(không rõ)'

        # Luôn tính lại chuỗi thời gian từ ts tại thời điểm hiển thị
        # (tránh "Hôm nay" bị đóng băng từ lúc save).
        time_str = _format_history_time(ts) if ts else ''

        if not plot_text:
            # Entry cũ chưa có plot — build từ đầu
            parts = []
            if filename:
                parts.append(filename)
            size_str = _format_size(size_bytes)
            if size_str:
                parts.append(size_str)
            if time_str:
                parts.append(time_str)
            plot_text = ' · '.join(parts)
        else:
            # Entry mới đã có plot — ghép thời gian động vào đầu
            if time_str:
                plot_text = f"{time_str}\n\n{plot_text}"

        li = xbmcgui.ListItem(label=label)
        info_tag = li.getVideoInfoTag()
        info_tag.setTitle(title or filename)
        if plot_text:
            info_tag.setPlot(plot_text)
        if year and str(year).isdigit():
            info_tag.setYear(int(year))
        ids = {}
        if imdb_id:
            ids['imdb'] = imdb_id
            info_tag.setIMDBNumber(imdb_id)
        if tmdb_id:
            ids['tmdb'] = tmdb_id
        if ids:
            info_tag.setUniqueIDs(ids, 'imdb' if imdb_id else 'tmdb')
        info_tag.setMediaType('movie')

        if poster_url:
            li.setArt({'poster': poster_url, 'thumb': poster_url})

        li.setProperty('IsPlayable', 'true')

        # Action chính: play thẳng nếu có fshare_url, fallback search lại
        if fshare_url:
            play_params = {
                'action':    'play_fshare_direct',
                'url':       fshare_url,
                'imdb':      imdb_id,
                'tmdb':      tmdb_id,
                'title':     title,
                'year':      year,
                'filename':  filename,
            }
            play_url = sys.argv[0] + '?' + urllib.parse.urlencode(play_params)
        else:
            # Không có fshare_url → search lại theo title/filename
            search_again_params = {
                'action': 'search_fshare',
                'title':  title or filename,
                'year':   year,
                'imdb':   imdb_id,
                'tmdb':   tmdb_id,
            }
            play_url = sys.argv[0] + '?' + urllib.parse.urlencode(search_again_params)

        # Context menu: tìm lại link mới
        search_new_params = {
            'action': 'search_fshare',
            'title':  title or filename,
            'year':   year,
            'imdb':   imdb_id,
            'tmdb':   tmdb_id,
        }
        search_new_url = sys.argv[0] + '?' + urllib.parse.urlencode(search_new_params)
        li.addContextMenuItems([
            ('🔍 Tìm lại link mới', f'Container.Update({search_new_url})'),
        ])

        list_items.append((play_url, li, False))

    xbmcplugin.addDirectoryItems(addon_handle, list_items)
    xbmcplugin.endOfDirectory(addon_handle)


def list_search_history():
    """
    Hiển thị danh sách lịch sử tìm kiếm.
    Dùng trong action 'search_manual' — hiện trước keyboard nếu có lịch sử.
    Mỗi item bấm vào sẽ chạy search ngay với query đã lưu.
    """
    xbmcplugin.setPluginCategory(addon_handle, 'Tìm kiếm Fshare')
    xbmcplugin.setContent(addon_handle, 'files')

    # Nút nhập tìm kiếm mới — đặt trên cùng
    new_search_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'search_manual_keyboard'})
    new_item = xbmcgui.ListItem('[🔍 Nhập tìm kiếm mới]')
    new_item.setArt({'icon': 'DefaultAddonsSearch.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=new_search_url, listitem=new_item, isFolder=True)

    history = load_search_history()

    if not history:
        xbmcplugin.endOfDirectory(addon_handle)
        return

    # Nút xóa lịch sử tìm kiếm
    clear_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'clear_search_history'})
    clear_item = xbmcgui.ListItem('[🗑 Xóa lịch sử tìm kiếm]')
    clear_item.setArt({'icon': 'DefaultAddonProgram.png'})
    xbmcplugin.addDirectoryItem(handle=addon_handle, url=clear_url, listitem=clear_item, isFolder=False)

    list_items = []
    for entry in history:
        query    = entry.get('query', '')
        ts       = entry.get('time', 0)
        if not query:
            continue
        time_str = _format_history_time(ts)
        label    = query
        plot_text = time_str if time_str else ''

        li = xbmcgui.ListItem(label=label)
        info_tag = li.getVideoInfoTag()
        info_tag.setTitle(query)
        if plot_text:
            info_tag.setPlot(plot_text)
        li.setArt({'icon': 'DefaultAddonsSearch.png'})

        run_params = {'action': 'run_search_history', 'query': query}
        run_url = sys.argv[0] + '?' + urllib.parse.urlencode(run_params)
        list_items.append((run_url, li, True))

    xbmcplugin.addDirectoryItems(addon_handle, list_items)
    xbmcplugin.endOfDirectory(addon_handle)


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


def get_autoplay_notify():
    value = get_local_setting('autoplay_notify', True)
    if isinstance(value, bool):
        return value
    return str(value).lower() != 'false'


def get_autoplay_notify_duration():
    try:
        value = int(get_local_setting('autoplay_notify_duration', 5000) or 5000)
        return max(1000, value)
    except Exception:
        return 5000


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


def get_effective_source_for_play():
    """Tra ve nguon metadata hieu qua khi play.
    Neu 'metadata khi duyet' dang tat ('none'), van ep sang 'tmdb' de Trakt
    scrobble va skin hien dung thong tin — play luon can ID.
    Thu tu uu tien: tmdb (co key) > omdb (co key) > tmdb (fallback khong key).
    """
    actual = get_metadata_source()
    if actual != 'none':
        return actual
    # Browse tat — chon nguon tot nhat con dung duoc khi play
    if get_tmdb_api_key():
        return 'tmdb'
    if get_omdb_api_key():
        return 'omdb'
    return 'tmdb'  # fallback: thu TMDb du chua chac co key


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


def _make_setting_item(label, plot):
    """Tạo ListItem cho settings menu với label và plot mô tả."""
    item = xbmcgui.ListItem(label)
    item.getVideoInfoTag().setPlot(plot)
    return item


def settings_menu():
    xbmcplugin.setPluginCategory(addon_handle, 'Cài đặt')
    xbmcplugin.setContent(addon_handle, 'files')

    def add(url, item, is_folder=False):
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=url, listitem=item, isFolder=is_folder)

    # =========================================================
    # NHÓM 1 — TÀI KHOẢN FSHARE
    # =========================================================
    fshare_user = get_local_setting('fshare_username', '') or '(chưa nhập)'
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'set_fshare_credentials'}),
        _make_setting_item(
            f'[Tài khoản Fshare: {fshare_user}]',
            'Nhập email và mật khẩu tài khoản Fshare.vn của bạn.\n'
            'Addon dùng thông tin này để tự đăng nhập và lấy link download trực tiếp.\n'
            'Bắt buộc phải có tài khoản Fshare VIP để xem phim.\n'
            'Email phải nhập đầy đủ (VD: ten@gmail.com).'
        )
    )
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'fshare_relogin'}),
        _make_setting_item(
            '[🔄 Đăng nhập lại Fshare]',
            'Xóa session cũ và đăng nhập lại Fshare ngay bây giờ.\n'
            'Dùng khi bấm play báo lỗi hoặc không lấy được link — thường do token hết hạn.\n'
            'Addon sẽ tự thử đăng nhập lại khi cần, nhưng nếu vẫn lỗi thì bấm mục này.'
        )
    )

    # =========================================================
    # NHÓM 2 — API KEYS
    # =========================================================
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'set_tmdb_api_key'}),
        _make_setting_item(
            '[TMDb API Key]',
            'API Key của The Movie Database (TMDb) — dùng để tra plot, poster, rating.\n'
            'Lấy miễn phí tại: themoviedb.org → Tài khoản → Cài đặt → API.\n\n'
            'Thứ tự ưu tiên:\n'
            '1. Key nhập tại đây (nếu có)\n'
            '2. Key của addon TMDb Helper (tự động đọc nếu bạn đã cài)\n'
            '3. Không tra được nếu cả hai đều trống.\n\n'
            'Nếu đã cài TMDb Helper và nó đang hoạt động bình thường,\n'
            'bạn không cần nhập key ở đây.'
        )
    )

    # =========================================================
    # NHÓM 3 — METADATA KHI DUYỆT DANH SÁCH
    # =========================================================
    source_labels = {'none': 'Tắt (nhanh nhất)', 'tmdb': 'TMDb', 'omdb': 'OMDb'}
    current_source = get_metadata_source()
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'cycle_metadata_source'}),
        _make_setting_item(
            f'[Metadata khi duyệt danh sách: {source_labels[current_source]}]',
            'Kiểm soát việc tra thông tin phim khi hiển thị danh sách link.\n'
            'Mỗi file được tra riêng — các file cùng phim dùng cache, không gọi API lặp lại.\n\n'
            'Tắt: Không tra API — danh sách hiện nhanh nhất, không có poster/plot.\n'
            'TMDb: Tra đầy đủ — plot, poster, rating. Với TV show: tra thêm plot từng tập.\n'
            '       Cần TMDb API Key. Chậm hơn với nhiều file.\n'
            'OMDb: Tra cơ bản — chỉ lấy IMDb ID. Nhanh hơn TMDb. Cần OMDb API Key.\n\n'
            'Lưu ý: Tắt mục này KHÔNG ảnh hưởng đến việc tra metadata khi play.\n'
            'Bấm để chuyển vòng giữa các lựa chọn.'
        )
    )

    # =========================================================
    # NHÓM 4 — METADATA KHI PLAY
    # =========================================================
    ids_on_play_state = 'Bật' if get_fetch_ids_on_play() else 'Tắt'
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'toggle_fetch_ids_on_play'}),
        _make_setting_item(
            f'[Tra metadata & ID khi play: {ids_on_play_state}]',
            'Kiểm soát việc tra thông tin phim ngay trước khi phát video.\n\n'
            'Khi Bật: Nếu link chưa có TMDb ID hoặc IMDb ID (ví dụ từ tìm kiếm thủ công), '
            'addon sẽ tự tra API để lấy đủ thông tin trước khi phát.\n'
            'Lợi ích: Trakt scrobble hoạt động đúng, skin hiện metadata chính xác.\n\n'
            'Khi Tắt: Chỉ dùng thông tin đã có sẵn từ lúc duyệt danh sách.\n\n'
            'Hoạt động độc lập với "Metadata khi duyệt" — kể cả khi duyệt tắt, '
            'play vẫn có thể tra nếu mục này đang Bật.'
        )
    )

    # =========================================================
    # NHÓM 5 — CỘNG ĐỒNG CHIA SẺ
    # =========================================================
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'change_gsheet'}),
        _make_setting_item(
            '[Google Sheet ID — Danh sách cộng đồng]',
            'Nhập ID hoặc URL của Google Sheet chứa danh sách link Fshare cộng đồng.\n'
            'Sheet phải được chia sẻ công khai (Anyone with the link can view).\n'
            'Có thể dán URL đầy đủ hoặc chỉ phần ID trong đường dẫn.'
        )
    )
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'set_items_per_page'}),
        _make_setting_item(
            '[Số mục mỗi trang — Danh sách cộng đồng]',
            'Số lượng phim hiển thị trên mỗi trang trong danh sách cộng đồng.\n'
            'Giá trị nhỏ (10–20): chuyển trang nhanh hơn.\n'
            'Giá trị lớn (50–100): ít phải chuyển trang hơn nhưng tải lâu hơn.'
        )
    )
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'set_cache_ttl'}),
        _make_setting_item(
            '[Thời gian giữ cache — Danh sách cộng đồng]',
            'Số giây addon giữ lại dữ liệu Google Sheet trước khi tải lại từ internet.\n'
            'Giá trị cao (600+): chuyển trang nhanh, ít tốn băng thông, nhưng cập nhật chậm.\n'
            'Giá trị thấp (60–120): dữ liệu luôn mới nhất nhưng tải lại thường xuyên hơn.\n'
            'Mặc định: 300 giây (5 phút).'
        )
    )
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'clear_gsheet_cache'}),
        _make_setting_item(
            '[🗑 Xóa cache danh sách cộng đồng]',
            'Xóa dữ liệu Google Sheet đã lưu tạm trong máy.\n'
            'Dùng khi danh sách cộng đồng bị cũ hoặc muốn tải lại ngay lập tức\n'
            'mà không cần chờ hết thời gian cache.'
        )
    )

    # =========================================================
    # NHÓM 6 — FILE & THƯ MỤC
    # =========================================================
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'choose_strm_dir'}),
        _make_setting_item(
            '[Thư mục lưu file .strm và download]',
            'Chọn thư mục mặc định để lưu file .strm và file download từ Fshare.\n'
            'File .strm là shortcut nhỏ trỏ tới link Fshare — có thể thêm vào thư viện Kodi.\n'
            'Nếu chưa chọn, addon sẽ hỏi mỗi lần tạo file.'
        )
    )

    # =========================================================
    # NHÓM 7 — DEBUG / NÂNG CAO
    # =========================================================
    debug_state = 'Bật' if get_show_lookup_debug_ids() else 'Tắt'
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'toggle_debug_ids'}),
        _make_setting_item(
            f'[Hiện ID debug trong danh sách: {debug_state}]',
            'Hiện IMDb ID và TMDb ID ngay trên tên mỗi link trong danh sách.\n'
            'Dùng để kiểm tra addon đã nhận đúng ID chưa trước khi play.\n'
            'Tắt khi không cần — danh sách trông gọn hơn.'
        )
    )

    # =========================================================
    # NHÓM 8 — AUTO PLAY
    # =========================================================
    autoplay_notify_state = 'Bật' if get_autoplay_notify() else 'Tắt'
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'toggle_autoplay_notify'}),
        _make_setting_item(
            f'[Thông báo file được chọn khi auto play: {autoplay_notify_state}]',
            'Hiện thông báo tên file và dung lượng ngay sau khi auto_play_fshare chọn link.\n'
            'Tiêu đề: tên file đầy đủ. Nội dung: dung lượng + tier filter đã khớp.\n'
            'Bật để xác nhận addon đang play đúng file mong muốn.\n'
            'Tắt nếu không cần thông báo — play thẳng không gián đoạn.'
        )
    )
    add(
        sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'set_autoplay_notify_duration'}),
        _make_setting_item(
            f'[Thời gian hiện thông báo auto play: {get_autoplay_notify_duration() // 1000}s]',
            'Số giây hiển thị thông báo tên file khi auto play.\n'
            'Nhập theo mili giây (1000 = 1 giây). Tối thiểu 1000ms.\n'
            'Mặc định: 5000ms (5 giây) — đủ để đọc tên file dài.'
        )
    )

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


# get_trakt_client_id / lookup_trakt_tmdb_id đã bị xóa.
# Trakt scrobble dùng script.trakt.ids Window property với tmdb hoặc imdb — chỉ cần 1 trong 2.


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


TMDB_IMAGE_BASE = 'https://image.tmdb.org/t/p/'

def lookup_tmdb_movie(api_key, title, year=None):
    """Tra TMDb: search lay tmdb_id + plot + poster + fanart + rating (vi-VN uu tien)."""
    if not api_key or not title:
        return {}

    params = {'api_key': api_key, 'query': title, 'language': 'vi-VN'}
    if year:
        params['year'] = str(year)

    resp = requests.get('https://api.themoviedb.org/3/search/movie', params=params, timeout=5)
    resp.raise_for_status()
    results = resp.json().get('results', [])

    # Fallback sang en-US neu vi-VN khong co ket qua
    if not results:
        params['language'] = 'en-US'
        resp = requests.get('https://api.themoviedb.org/3/search/movie', params=params, timeout=5)
        resp.raise_for_status()
        results = resp.json().get('results', [])

    if not results:
        return {}

    movie = results[0]
    movie_id = movie.get('id')
    if not movie_id:
        return {}

    poster_path = movie.get('poster_path') or ''
    fanart_path = movie.get('backdrop_path') or ''

    return {
        'mediatype': 'movie',
        'title':   movie.get('title', title),
        'year':    (movie.get('release_date', '') or '')[:4],
        'tmdb_id': str(movie_id),
        'imdb_id': '',
        'plot':    movie.get('overview', '') or '',
        'rating':  str(movie.get('vote_average', '') or ''),
        'poster':  f"{TMDB_IMAGE_BASE}w500{poster_path}" if poster_path else '',
        'fanart':  f"{TMDB_IMAGE_BASE}w1280{fanart_path}" if fanart_path else '',
    }


def lookup_tmdb_episode(api_key, tvshowtitle, season, episode, force_source=None):
    """Tra TMDb: search show -> lay show_id + episode details (plot, still, poster, fanart).
    Request 1: search/tv -> show_id, show poster/fanart
    Request 2: tv/{show_id}/season/{s}/episode/{e} -> episode plot + still
    force_source: neu truyen vao, dung gia tri nay thay vi doc setting (tranh bi 'none' block).
    """
    if not api_key or not tvshowtitle:
        return {}

    # Request 1: tim show
    params = {'api_key': api_key, 'query': tvshowtitle, 'language': 'vi-VN'}
    resp = requests.get('https://api.themoviedb.org/3/search/tv', params=params, timeout=5)
    resp.raise_for_status()
    results = resp.json().get('results', [])

    if not results:
        params['language'] = 'en-US'
        resp = requests.get('https://api.themoviedb.org/3/search/tv', params=params, timeout=5)
        resp.raise_for_status()
        results = resp.json().get('results', [])

    if not results:
        return {}

    show = results[0]
    show_id = show.get('id')
    if not show_id:
        return {}

    show_poster  = show.get('poster_path') or ''
    show_fanart  = show.get('backdrop_path') or ''
    show_name    = show.get('name', tvshowtitle)
    show_year    = (show.get('first_air_date', '') or '')[:4]

    # Request 2: lay episode plot + still.
    # Dieu kien: effective_source == 'tmdb' (du la tu setting hay force_source khi play).
    # Truoc day chi doc get_metadata_source() truc tiep → bi block khi browse='none'.
    effective_source = force_source or get_metadata_source()
    ep_plot  = ''
    ep_still = ''
    if effective_source == 'tmdb':
        try:
            ep_resp = requests.get(
                f'https://api.themoviedb.org/3/tv/{show_id}/season/{season}/episode/{episode}',
                params={'api_key': api_key, 'language': 'vi-VN'},
                timeout=5
            )
            if ep_resp.status_code == 200:
                ep_data  = ep_resp.json()
                ep_plot  = ep_data.get('overview', '') or ''
                ep_still = ep_data.get('still_path') or ''
                if not ep_plot:
                    ep_resp2 = requests.get(
                        f'https://api.themoviedb.org/3/tv/{show_id}/season/{season}/episode/{episode}',
                        params={'api_key': api_key, 'language': 'en-US'},
                        timeout=5
                    )
                    if ep_resp2.status_code == 200:
                        ep_plot = ep_resp2.json().get('overview', '') or ''
        except Exception as e:
            xbmc.log(f"lookup_tmdb_episode: episode detail fetch error: {e}", level=xbmc.LOGWARNING)

    thumb = f"{TMDB_IMAGE_BASE}w300{ep_still}" if ep_still else (
            f"{TMDB_IMAGE_BASE}w500{show_poster}" if show_poster else '')

    return {
        'mediatype':    'episode',
        'title':        f"{show_name} S{int(season):02d}E{int(episode):02d}",
        'tvshowtitle':  show_name,
        'year':         show_year,
        'season':       str(season),
        'episode':      str(episode),
        'tmdb_id':      str(show_id),
        'imdb_id':      '',
        'plot':         ep_plot,
        'rating':       str(show.get('vote_average', '') or ''),
        'poster':       f"{TMDB_IMAGE_BASE}w500{show_poster}" if show_poster else '',
        'fanart':       f"{TMDB_IMAGE_BASE}w1280{show_fanart}" if show_fanart else '',
        'thumb':        thumb,
    }


def fetch_tmdb_details_by_id(tmdb_id, season=None, episode=None):
    """
    Tra TMDb trực tiếp bằng tmdb_id — không cần search, 1 API call duy nhất.
    Dùng khi đã có tmdb_id (từ TMDb Helper hoặc browse) nhưng chưa có plot/poster.

    Movie:   GET /movie/{tmdb_id}
    Episode: GET /tv/{tmdb_id}/season/{s}/episode/{e}  (+ /tv/{tmdb_id} cho poster/fanart show)

    Trả về dict cùng format với lookup_tmdb_movie / lookup_tmdb_episode.
    Trả về {} nếu không có API key hoặc lỗi.
    """
    if not tmdb_id:
        return {}

    api_key = get_tmdb_api_key()
    if not api_key:
        return {}

    is_episode = bool(season and episode)
    params_vi  = {'api_key': api_key, 'language': 'vi-VN'}
    params_en  = {'api_key': api_key, 'language': 'en-US'}

    try:
        if not is_episode:
            # ── Movie ──────────────────────────────────────────────────────
            resp = requests.get(
                f'https://api.themoviedb.org/3/movie/{tmdb_id}',
                params=params_vi, timeout=5
            )
            resp.raise_for_status()
            d = resp.json()

            # Fallback en-US nếu overview rỗng
            if not d.get('overview'):
                resp2 = requests.get(
                    f'https://api.themoviedb.org/3/movie/{tmdb_id}',
                    params=params_en, timeout=5
                )
                if resp2.status_code == 200:
                    d2 = resp2.json()
                    if d2.get('overview'):
                        d['overview'] = d2['overview']

            poster_path = d.get('poster_path') or ''
            fanart_path = d.get('backdrop_path') or ''

            # external_ids để lấy imdb_id
            imdb_id = d.get('imdb_id', '') or ''

            return {
                'mediatype': 'movie',
                'title':     d.get('title', '') or d.get('original_title', ''),
                'year':      (d.get('release_date', '') or '')[:4],
                'tmdb_id':   str(tmdb_id),
                'imdb_id':   imdb_id,
                'plot':      d.get('overview', '') or '',
                'rating':    str(d.get('vote_average', '') or ''),
                'poster':    f"{TMDB_IMAGE_BASE}w500{poster_path}"  if poster_path else '',
                'fanart':    f"{TMDB_IMAGE_BASE}w1280{fanart_path}" if fanart_path else '',
            }

        else:
            # ── Episode ────────────────────────────────────────────────────
            # Request 1: show details (poster, fanart, tên show, năm)
            show_resp = requests.get(
                f'https://api.themoviedb.org/3/tv/{tmdb_id}',
                params=params_vi, timeout=5
            )
            show_resp.raise_for_status()
            show = show_resp.json()

            show_name   = show.get('name', '') or show.get('original_name', '')
            show_year   = (show.get('first_air_date', '') or '')[:4]
            show_poster = show.get('poster_path') or ''
            show_fanart = show.get('backdrop_path') or ''

            # Request 2: episode details (plot, still)
            ep_plot  = ''
            ep_still = ''
            ep_resp = requests.get(
                f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}',
                params=params_vi, timeout=5
            )
            if ep_resp.status_code == 200:
                ep = ep_resp.json()
                ep_plot  = ep.get('overview', '') or ''
                ep_still = ep.get('still_path') or ''

                # Fallback en-US nếu plot rỗng
                if not ep_plot:
                    ep_resp2 = requests.get(
                        f'https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}',
                        params=params_en, timeout=5
                    )
                    if ep_resp2.status_code == 200:
                        ep_plot = ep_resp2.json().get('overview', '') or ''

            thumb = (f"{TMDB_IMAGE_BASE}w300{ep_still}"  if ep_still else
                     f"{TMDB_IMAGE_BASE}w500{show_poster}" if show_poster else '')

            return {
                'mediatype':   'episode',
                'title':       f"{show_name} S{int(season):02d}E{int(episode):02d}",
                'tvshowtitle': show_name,
                'year':        show_year,
                'season':      str(season),
                'episode':     str(episode),
                'tmdb_id':     str(tmdb_id),
                'imdb_id':     '',
                'plot':        ep_plot,
                'rating':      str(show.get('vote_average', '') or ''),
                'poster':      f"{TMDB_IMAGE_BASE}w500{show_poster}"  if show_poster else '',
                'fanart':      f"{TMDB_IMAGE_BASE}w1280{show_fanart}" if show_fanart else '',
                'thumb':       thumb,
            }

    except Exception as e:
        xbmc.log(f"fetch_tmdb_details_by_id({tmdb_id}): {e}", level=xbmc.LOGWARNING)
        return {}


def lookup_tmdb_metadata(title=None, year=None, tvshowtitle=None, season=None, episode=None,
                         force_source=None):
    """Tra cuu ID tu TMDb hoac OMDb tuy theo setting.
    - TMDb Helper player: truyen san tmdb/imdb qua URL -> khong goi ham nay.
    - List cong dong / search manual: chua co ID -> goi ham nay.
      Neu OMDb chi tra duoc imdb_id, tu dong convert sang tmdb_id qua Trakt API
      de skin ah2 / TMDb Helper hien duoc poster/plot dung.
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
                    data = lookup_tmdb_episode(api_key, tvshowtitle, season, episode,
                                               force_source=force_source)
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

    # imdb_id (OMDb) hoac tmdb_id (TMDb) la du cho Trakt scrobble qua script.trakt.ids.
    # Khong can convert qua Trakt API.

    if data:
        cache[cache_key] = data
        save_tmdb_lookup_cache(cache)

    return data or {}

def apply_stream_props(list_item, stream_tags):
    """Gán tất cả stream properties vào ListItem từ stream_tags dict.
    Dùng chung cho browse_fshare_folder, show_fshare_links và list_community.
    """
    if not stream_tags:
        return

    video_stream = stream_tags.get('video_stream', {})
    audio_stream = stream_tags.get('audio_stream', {})
    if video_stream:
        list_item.addStreamInfo('video', video_stream)
    if audio_stream:
        list_item.addStreamInfo('audio', audio_stream)

    # --- Set HdrType vào VideoInfoTag ---
    # ListItem.HdrType / VideoPlayer.HdrType (dùng trong Image_HDR_Codec, Image_OSD_HDR_Codec)
    # chỉ đọc từ VideoInfoTag.setHdrType() — setProperty() KHÔNG hoạt động cho các infolabel đó.
    hdr_type = stream_tags.get('hdr_type', '')
    if hdr_type:
        try:
            list_item.getVideoInfoTag().setHdrType(hdr_type)
        except Exception:
            pass  # Kodi build cũ không có setHdrType — fallback về setProperty bên dưới

    st = stream_tags
    prop_map = {
        'video_tag':                    st.get('video_tag', ''),
        'audio_tag':                    st.get('audio_tag', ''),
        'VideoResolution':              st.get('video_resolution', ''),
        'VideoCodec':                   st.get('video_codec', ''),
        'VideoSource':                  st.get('video_source', ''),
        'VideoHDR':                     st.get('hdr', ''),
        'HdrType':                      st.get('hdr_type', ''),
        'AudioCodec':                   st.get('audio_codec', ''),
        'AudioChannels':                st.get('audio_channels', ''),
        'AudioLanguage':                st.get('audio_language', ''),
        # AH2 đọc flag ngôn ngữ qua ListItem.Property(AudioLanguage.1), (AudioLanguage.2)...
        # audio_language có thể là chuỗi "VieSub TM ENG" -> tách thành từng slot đánh số
        **{f'AudioLanguage.{i+1}': lang
           for i, lang in enumerate((st.get('audio_language', '') or '').split())
           if lang},
        'AudioProfile':                 st.get('audio_profile', ''),
        'AudioObject':                  st.get('audio_object', ''),
        'AudioAtmos':                   st.get('audio_object', ''),
        'AudioCodec2':                  st.get('audio_object', ''),
        'AudioCodecAlt':                st.get('audio_object', ''),
        'AudioCodecExtra':              st.get('audio_profile', ''),
        'AudioCodecCombined':           st.get('audio_profile', ''),
        'VideoPlayer.AudioCodec':       st.get('audio_codec', ''),
        'VideoPlayer.AudioChannels':    st.get('audio_channels', ''),
        'VideoPlayer.AudioProfile':     st.get('audio_profile', ''),
        'VideoPlayer.AudioObject':      st.get('audio_object', ''),
        'VideoPlayer.AudioCodec2':      st.get('audio_object', ''),
        'VideoPlayer.AudioCodecCombined': st.get('audio_profile', '') or st.get('audio_codec', ''),
        'VideoPlayer.VideoResolution':  st.get('video_resolution', ''),
        'VideoPlayer.VideoCodec':       st.get('video_codec', ''),
        'VideoPlayer.VideoSource':      st.get('video_source', ''),
        'VideoPlayer.HdrType':          st.get('hdr_type', ''),
        'VideoPlayer.HDRType':          st.get('hdr', ''),
        'VideoInfo.VideoResolution':    st.get('video_resolution', ''),
        'VideoInfo.VideoCodec':         st.get('video_codec', ''),
        'VideoInfo.VideoSource':        st.get('video_source', ''),
        'VideoInfo.AudioCodec':         st.get('audio_codec', ''),
        'VideoInfo.AudioChannels':      st.get('audio_channels', ''),
        'VideoInfo.AudioLanguage':      st.get('audio_language', ''),
        'VideoInfo.AudioProfile':       st.get('audio_profile', ''),
        'VideoInfo.AudioObject':        st.get('audio_object', ''),
        'VideoInfo.AudioCodec2':        st.get('audio_object', ''),
        'VideoInfo.AudioCodecCombined': st.get('audio_profile', '') or st.get('audio_codec', ''),
        'VideoInfo.HDRType':            st.get('hdr', ''),
        'VideoInfo.HdrType':            st.get('hdr_type', ''),
        'media.videoresolution':        st.get('video_resolution', ''),
        'media.videocodec':             st.get('video_codec', ''),
        'media.videosource':            st.get('video_source', ''),
        'media.hdr':                    st.get('hdr', ''),
        'media.hdrtype':                st.get('hdr_type', ''),
        'media.audiocodec':             st.get('audio_codec', ''),
        'media.audiochannels':          st.get('audio_channels', ''),
        'media.audiolanguage':          st.get('audio_language', ''),
        'media.audioprofile':           st.get('audio_profile', ''),
        'media.audioobject':            st.get('audio_object', ''),
        'media.resolution':             st.get('video_resolution', ''),
        'media.codec':                  st.get('video_codec', ''),
        'media.channels':               st.get('audio_channels', ''),
        'media.hdr':                    st.get('hdr', ''),
        'media.source':                 st.get('video_source', ''),
        'media.audio':                  st.get('audio_codec', ''),
        'media.audioprofile':           st.get('audio_profile', ''),
        'media.audioobject':            st.get('audio_object', ''),
        'media.audio2':                 st.get('audio_object', ''),
        'audio.codec':                  st.get('audio_codec', ''),
        'audio.channels':               st.get('audio_channels', ''),
        'audio.language':               st.get('audio_language', ''),
        'audio.profile':                st.get('audio_profile', ''),
        'audio.object':                 st.get('audio_object', ''),
        'audio.codec2':                 st.get('audio_object', ''),
        'audio.codec_combined':         st.get('audio_profile', '') or st.get('audio_codec', ''),
        'video.codec':                  st.get('video_codec', ''),
        'video.resolution':             st.get('video_resolution', ''),
        'video.source':                 st.get('video_source', ''),
        'video.hdr':                    st.get('hdr', ''),
        'video.hdrtype':                st.get('hdr_type', ''),
        'audio_codec_combined':         st.get('audio_profile', '') or st.get('audio_codec', ''),
        'HasAtmos':    'true' if st.get('audio_object', '').lower() == 'atmos' else '',
        'IsAtmos': 'true' if st.get('audio_object', '').lower() == 'atmos' else '',
        'AudioIsAtmos':'true' if st.get('audio_object', '').lower() == 'atmos' else '',
        'HasDTSX':     'true' if 'dts:x' in st.get('audio_profile', '').lower() else '',
        'AudioIsDTSX': 'true' if 'dts:x' in st.get('audio_profile', '').lower() else '',
    }
    for prop_name, prop_value in prop_map.items():
        if prop_value:
            list_item.setProperty(prop_name, str(prop_value))




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
        (r'DOLBY[.\- ]?VISION|DO?VI|(?<![A-Z])DV(?![A-Z0-9])', 'DV'),
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

    # DTS:X phải có X không theo sau bởi số (tránh nhầm DTS.x265, DTS.x264 → X265/X264 sau upper())
    if re.search(r'DTS[.\- ]?X(?!\d)', normalized):
        audio_profile = 'DTS:X'

    audio_codec_patterns = [
        (r'TRUEHD', 'TrueHD'),
        (r'DTS[.\- ]?X(?!\d)', 'DTS:X'),
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
    Hiển thị lịch sử tìm kiếm trước, cho phép chọn lại hoặc nhập mới.
    Nếu chưa có lịch sử, mở keyboard ngay.
    """
    history = load_search_history()
    if history:
        list_search_history()
    else:
        search_fshare_manual_keyboard()


def search_fshare_manual_keyboard():
    """
    Mở keyboard để nhập từ khóa tìm kiếm mới, lưu vào lịch sử.
    """
    keyboard = xbmc.Keyboard('', 'Nhập tên phim cần tìm trên Fshare')
    keyboard.doModal()
    if keyboard.isConfirmed():
        query = keyboard.getText().strip()
        if query:
            save_search_history(query)
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


def download_fshare(fshare_url, title, url, movie_info=None):
    """
    Download file Fshare về cùng thư mục với file .strm.
    Dùng token/session từ VietmediaF để lấy direct link.
    Tên file giống quy tắc đặt tên .strm nhưng giữ extension gốc.
    """
    import threading

    # --- Lấy token/session từ fshare tích hợp ---
    def get_fshare_token():
        return fshare_check_session()

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
            link = f'plugin://plugin.video.myimdbfshare?action=play_fshare_direct&url={furl}'
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
                'action': 'play_fshare_direct',
                'url':    furl,
                'title':  name,
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
# ---------------------------------------------------------------------------
# FSHARE AUTH — tích hợp trực tiếp, không phụ thuộc plugin.video.vietmediaF
# ---------------------------------------------------------------------------
FSHARE_LOGIN_API      = 'https://api.fshare.vn/api/user/login'
FSHARE_DOWNLOAD_API   = 'https://api.fshare.vn/api/session/download'
FSHARE_FILEOPS_GET    = 'https://api.fshare.vn/api/fileops/get'
FSHARE_USER_AGENT     = 'kodivietmediaf-K58W6U'
FSHARE_APP_KEY        = 'dMnqMMZMUnN5YpvKENaEhdQQ5jxDqddt'


def fshare_get_credentials():
    """Lấy username/password từ settings của addon này."""
    username = get_local_setting('fshare_username', '')
    password = get_local_setting('fshare_password', '')
    return username.strip(), password.strip()


def fshare_prompt_credentials():
    """
    Hỏi user nhập email + password Fshare ngay trong Kodi (không cần vào Settings).
    Lưu vào settings và trả về (username, password).
    Trả về ('', '') nếu user hủy.
    """
    # Nhập email
    kb_user = xbmc.Keyboard('', 'Nhập email Fshare (VD: ten@gmail.com)')
    kb_user.doModal()
    if not kb_user.isConfirmed():
        return '', ''
    username = kb_user.getText().strip()
    if not username or '@' not in username:
        xbmcgui.Dialog().notification('Fshare', 'Email không hợp lệ', time=3000)
        return '', ''

    # Nhập password (ẩn ký tự)
    kb_pass = xbmc.Keyboard('', 'Nhập mật khẩu Fshare', True)
    kb_pass.doModal()
    if not kb_pass.isConfirmed():
        return '', ''
    password = kb_pass.getText().strip()
    if not password:
        xbmcgui.Dialog().notification('Fshare', 'Mật khẩu không được để trống', time=3000)
        return '', ''

    set_local_setting('fshare_username', username)
    set_local_setting('fshare_password', password)
    xbmc.log(f"Fshare credentials saved for: {username}", level=xbmc.LOGINFO)
    return username, password


def fshare_login():
    """Đăng nhập Fshare, lưu token/session_id vào settings, trả về (token, session_id)."""
    username, password = fshare_get_credentials()
    if not username or not password:
        # Hỏi inline thay vì chỉ thông báo
        xbmcgui.Dialog().notification('Fshare', 'Chưa có tài khoản — vui lòng nhập', time=2000)
        xbmc.sleep(500)
        username, password = fshare_prompt_credentials()
        if not username or not password:
            return '', ''
    payload = json.dumps({
        'app_key':    FSHARE_APP_KEY,
        'user_email': username,
        'password':   password,
    })
    headers = {'cache-control': 'no-cache', 'User-Agent': FSHARE_USER_AGENT}
    try:
        r = requests.post(FSHARE_LOGIN_API, data=payload, headers=headers, verify=False, timeout=15)
        data = r.json()
        if r.status_code == 200:
            token      = data.get('token', '')
            session_id = data.get('session_id', '')
            set_local_setting('fshare_token',      token)
            set_local_setting('fshare_session_id', session_id)
            set_local_setting('fshare_timelog',    str(int(time.time())))
            xbmc.log(f"Fshare login OK: {username}", level=xbmc.LOGINFO)
            return token, session_id
        else:
            msg = data.get('msg', f'HTTP {r.status_code}')
            notify(f'Đăng nhập Fshare thất bại: {msg}')
            xbmc.log(f"Fshare login failed {r.status_code}: {msg}", level=xbmc.LOGERROR)
    except Exception as e:
        xbmc.log(f"Fshare login error: {e}", level=xbmc.LOGERROR)
        notify(f'Lỗi đăng nhập Fshare: {e}')
    return '', ''


# FSHARE_SESSION_TTL: số giây coi session còn hợp lệ kể từ lần login cuối.
# Fshare thực tế giữ session nhiều giờ; 6h là ngưỡng an toàn.
# Nếu token thực sự bị revoke sớm hơn, fshare_get_download_link sẽ tự phát hiện
# (location rỗng) và login lại — không cần HTTP check ở đây.
FSHARE_SESSION_TTL = 6 * 3600  # 6 giờ


def fshare_check_session():
    """
    Trả về (token, session_id) hợp lệ.
    Dùng timestamp thay HTTP check: nếu login trong vòng FSHARE_SESSION_TTL giây
    → tin tưởng session còn hạn, dùng ngay mà không gọi HTTP.
    Nếu hết TTL hoặc chưa có token → login lại.
    Trường hợp token bị revoke sớm bất thường: fshare_get_download_link
    tự phát hiện (location rỗng) và retry login — không cần kiểm tra ở đây.
    """
    token      = get_local_setting('fshare_token', '')
    session_id = get_local_setting('fshare_session_id', '')

    if token and session_id:
        try:
            login_time = int(get_local_setting('fshare_timelog', '0') or 0)
            elapsed    = int(time.time()) - login_time
            if elapsed < FSHARE_SESSION_TTL:
                xbmc.log(
                    f"Fshare session reused (age {elapsed//60}m/{FSHARE_SESSION_TTL//60}m)",
                    level=xbmc.LOGDEBUG
                )
                return token, session_id
            xbmc.log(
                f"Fshare session TTL exceeded ({elapsed//60}m) — re-login",
                level=xbmc.LOGINFO
            )
        except Exception:
            pass  # Không parse được timelog → cứ login lại cho chắc

    xbmc.log("Fshare session missing or expired — login", level=xbmc.LOGINFO)
    return fshare_login()


def fshare_logout():
    """Xóa token/session_id/timelog đã lưu, buộc login lại lần sau."""
    set_local_setting('fshare_token', '')
    set_local_setting('fshare_session_id', '')
    set_local_setting('fshare_timelog', '0')
    xbmc.log("Fshare: logged out (token + timelog cleared)", level=xbmc.LOGINFO)


def fshare_relogin():
    """Xóa session cũ rồi đăng nhập lại ngay, thông báo kết quả."""
    fshare_logout()
    token, session_id = fshare_login()
    if token and session_id:
        xbmcgui.Dialog().notification('Fshare', 'Đăng nhập lại thành công', time=3000)
    else:
        xbmcgui.Dialog().notification('Fshare', 'Đăng nhập thất bại — kiểm tra tài khoản', time=4000)


def fshare_get_download_link(fshare_url, token=None, session_id=None):
    """
    Resolve fshare URL thành CDN link trực tiếp.
    Trả về CDN URL (str) hoặc '' nếu thất bại.
    Tự động force re-login và retry 1 lần nếu token expired (location rỗng hoặc lỗi auth).
    """
    if not token or not session_id:
        token, session_id = fshare_check_session()
    if not token or not session_id:
        return ''

    url_with_share = fshare_url
    if '?' not in url_with_share:
        url_with_share += '?share=8805984'
    else:
        url_with_share += '&share=8805984'

    def _do_post(tok, sid):
        payload = json.dumps({'zipflag': 0, 'url': url_with_share, 'password': '', 'token': tok})
        headers = {
            'Content-Type': 'application/json',
            'User-Agent':   FSHARE_USER_AGENT,
            'Cookie':       f'session_id={sid}',
        }
        r = requests.post(FSHARE_DOWNLOAD_API, data=payload, headers=headers,
                          verify=False, timeout=20)
        data = r.json()
        return r.status_code, data.get('location', '')

    try:
        status, cdn_url = _do_post(token, session_id)
        if status == 200 and cdn_url:
            xbmc.log(f"Fshare CDN OK: {fshare_url[:60]}", level=xbmc.LOGINFO)
            return cdn_url

        # Token expired: 200 nhưng location rỗng, hoặc lỗi auth
        xbmc.log(
            f"Fshare download API {status}: location empty — forcing re-login",
            level=xbmc.LOGWARNING
        )
        # Xóa token cũ trước khi re-login để tránh vòng lặp
        set_local_setting('fshare_token', '')
        set_local_setting('fshare_session_id', '')

        token, session_id = fshare_login()
        if not token or not session_id:
            xbmc.log("Fshare re-login failed", level=xbmc.LOGERROR)
            return ''

        # Retry lần 2
        status2, cdn_url2 = _do_post(token, session_id)
        if status2 == 200 and cdn_url2:
            xbmc.log(f"Fshare CDN OK (after re-login): {fshare_url[:60]}", level=xbmc.LOGINFO)
            return cdn_url2

        xbmc.log(f"Fshare download API retry {status2}: still no CDN url", level=xbmc.LOGERROR)
    except Exception as e:
        xbmc.log(f"Fshare get_download_link error: {e}", level=xbmc.LOGERROR)
    return ''


# Alias để các hàm cũ dùng get_vietmediaf_fshare_credentials() vẫn hoạt động
def get_vietmediaf_fshare_credentials():
    return fshare_check_session()


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
    """Login Fshare trực tiếp (không cần VietmediaF)."""
    return fshare_login()


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

    # Sắp xếp: file/folder mới upload lên đầu, giữ nguyên thứ tự nếu không có trường created
    items.sort(key=lambda x: x.get('created', 0) or 0, reverse=True)

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

            # Nếu là file nhưng tên không có extension video → thử lấy tên thật từ Fshare API
            if not is_folder and not any(name.lower().endswith(ext) for ext in VIDEO_FILE_EXTENSIONS):
                real_name = get_fshare_file_realname(item_url)
                if real_name:
                    xbmc.log(f"browse_fshare_folder: real_name '{name}' -> '{real_name}'", level=xbmc.LOGDEBUG)
                    name = real_name

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
                    resolved_plot         = resolved_meta.get('plot', '') or ''
                    resolved_rating       = resolved_meta.get('rating', '') or ''
                    resolved_poster_path  = resolved_meta.get('poster', '') or ''
                    resolved_fanart       = resolved_meta.get('fanart', '') or ''
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
                apply_stream_props(list_item, stream_tags)

            if show_lookup_debug_ids:
                list_item.setProperty('debug.imdb_id', resolved_imdb_id)
                list_item.setProperty('debug.tmdb_id', resolved_tmdb_id)
                list_item.setProperty('debug.lookup_status', 'ok' if (resolved_imdb_id or resolved_tmdb_id) else 'missing')

            list_item.setProperty('IsPlayable', 'true')

            # Skin dùng StreamFileNameAndPath để hiện icon source/codec khi browse
            if name:
                list_item.setProperty('StreamFileNameAndPath', name)
                # setPath(name) để skin đọc được ListItem.FileNameAndPath = tên file
                # URL thật nằm ở addDirectoryItem(url=play_url) nên không bị override
                list_item.setPath(name)

            # Kodi nhớ ListItem đã build đủ metadata — play_fshare_direct chỉ cần CDN url
            play_params = {
                'action':      'play_fshare_direct',
                'url':         item_url,
                'imdb':        resolved_imdb_id,
                'tmdb':        resolved_tmdb_id,
                'title':       effective_title,
                'year':        effective_year,
                'season':      effective_season,
                'episode':     effective_episode,
                'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
                'filename':    name,
            }
            play_url = sys.argv[0] + '?' + urllib.parse.urlencode(play_params)

            # Context menu: .strm và Download cho tất cả file Fshare
            strm_params = {
                'action':      'create_strm',
                'title':       name,
                'url':         item_url,
                'movie_title': effective_title,
                'movie_year':  effective_year,
                'imdb':        resolved_imdb_id,
                'tmdb':        resolved_tmdb_id,
                'season':      effective_season,
                'episode':     effective_episode,
                'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
                'video_resolution': stream_tags.get('video_resolution', ''),
                'video_source':     stream_tags.get('video_source', ''),
                'video_codec':      stream_tags.get('video_codec', ''),
                'hdr':              stream_tags.get('hdr', ''),
                'hdr_type':         stream_tags.get('hdr_type', ''),
                'audio_codec':      stream_tags.get('audio_codec', ''),
                'audio_channels':   stream_tags.get('audio_channels', ''),
                'audio_language':   stream_tags.get('audio_language', ''),
                'audio_profile':    stream_tags.get('audio_profile', ''),
                'audio_object':     stream_tags.get('audio_object', ''),
            }
            strm_url = sys.argv[0] + '?' + urllib.parse.urlencode(strm_params)

            dl_params = {
                'action':      'download_fshare',
                'title':       name,
                'url':         item_url,
                'fshare_url':  item_url,
                'movie_title': effective_title,
                'movie_year':  effective_year,
                'imdb':        resolved_imdb_id,
                'tmdb':        resolved_tmdb_id,
                'season':      effective_season,
                'episode':     effective_episode,
                'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
                'video_resolution': stream_tags.get('video_resolution', ''),
                'video_source':     stream_tags.get('video_source', ''),
                'video_codec':      stream_tags.get('video_codec', ''),
                'hdr':              stream_tags.get('hdr', ''),
                'hdr_type':         stream_tags.get('hdr_type', ''),
                'audio_codec':      stream_tags.get('audio_codec', ''),
                'audio_channels':   stream_tags.get('audio_channels', ''),
                'audio_language':   stream_tags.get('audio_language', ''),
                'audio_profile':    stream_tags.get('audio_profile', ''),
                'audio_object':     stream_tags.get('audio_object', ''),
            }
            dl_url = sys.argv[0] + '?' + urllib.parse.urlencode(dl_params)

            list_item.addContextMenuItems([
                ('💾 Tạo file .strm', f'RunPlugin({strm_url})'),
                ('⬇️ Download về máy', f'RunPlugin({dl_url})'),
            ])

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

    # --- Lịch sử xem gần đây (chỉ hiện nếu có dữ liệu) ---
    if load_play_history():
        history_url = sys.argv[0] + '?' + urllib.parse.urlencode({'action': 'list_play_history'})
        history_item = xbmcgui.ListItem('[⏱ Lịch sử xem gần đây]')
        history_item.setArt({'icon': 'DefaultAddonVideo.png'})
        xbmcplugin.addDirectoryItem(handle=addon_handle, url=history_url, listitem=history_item, isFolder=True)

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
                # Lưu filename để skin có thể dùng property thay vì FileNameAndPath URL
                # Skin sẽ dùng: ListItem.Property(StreamFileNameAndPath)
                list_item.setProperty('StreamFileNameAndPath', parse_name)
                # setPath(tên file) để skin đọc được ListItem.FileNameAndPath
                list_item.setPath(parse_name)
                apply_stream_props(list_item, stream_tags)

            if show_lookup_debug_ids:
                list_item.setProperty('debug.imdb_id', resolved_imdb_id)
                list_item.setProperty('debug.tmdb_id', resolved_tmdb_id)
                list_item.setProperty('debug.lookup_status', 'ok' if (resolved_imdb_id or resolved_tmdb_id) else 'missing')

            if is_playable:
                list_item.setProperty('IsPlayable', 'true')
                # Kodi Way: ListItem đã build đủ metadata — play_fshare_direct chỉ resolve CDN
                play_params = {
                    'action':      'play_fshare_direct',
                    'url':         link,
                    'imdb':        resolved_imdb_id,
                    'tmdb':        resolved_tmdb_id,
                    'title':       effective_title,
                    'year':        effective_year,
                    'season':      effective_season,
                    'episode':     effective_episode,
                    'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
                    'filename':    real_name if real_name and is_video_file else name,
                }
                play_url = sys.argv[0] + '?' + urllib.parse.urlencode(play_params)
                
                # Context menu: Create .strm và Download (cho tất cả file Fshare playable)
                xbmc.log(f"list_community DEBUG: name='{name}' is_video_file={is_video_file} fshare_in_link={'fshare.vn' in link}", level=xbmc.LOGINFO)
                if is_playable and 'fshare.vn' in link:
                    xbmc.log(f"list_community: Adding context menu for '{name}'", level=xbmc.LOGINFO)
                    strm_params = {
                        'action': 'create_strm',
                        'title': parse_name,
                        'url': link,
                    }
                    strm_url = sys.argv[0] + '?' + urllib.parse.urlencode(strm_params)
                    
                    dl_params = {
                        'action': 'download_fshare',
                        'title': parse_name,
                        'url': play_url,
                        'fshare_url': link,
                    }
                    dl_url = sys.argv[0] + '?' + urllib.parse.urlencode(dl_params)
                    
                    list_item.addContextMenuItems([
                        ('💾 Tạo file .strm', f'RunPlugin({strm_url})'),
                        ('⬇️ Download về máy', f'RunPlugin({dl_url})')
                    ])
                else:
                    xbmc.log(f"list_community: Context menu NOT added - is_video_file={is_video_file}, fshare_in_link={'fshare.vn' in link}", level=xbmc.LOGINFO)
                
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
                    browse_url = f'plugin://plugin.video.myimdbfshare?action=play_fshare_direct&url={link}'
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

    include : chuỗi các nhóm AND ngăn cách bằng ';', trong mỗi nhóm dùng ',' cho OR.
              Ví dụ: '2160p,4k,uhd;atmos,ddp;mkv,iso'
              -> (2160p OR 4k OR uhd) AND (atmos OR ddp) AND (mkv OR iso)
    exclude : chuỗi keyword ngăn cách bằng ',', bất kỳ keyword nào match thì loại.
              Ví dụ: 'hdcam,cam,telesync'

    Match theo token (tách tên file theo . - _ space) để tránh false positive.
    Fallback: nếu không có kết quả -> bỏ dần nhóm include từ cuối -> hiện tất cả.
    """
    # ------------------------------------------------------------------
    # ĐỌC METADATA TỪ TMDB HELPER SERVICE MONITOR
    # Đọc ngay lúc này (trước endOfDirectory) rồi encode vào URL từng link.
    # Không dùng Window property nữa để tránh race condition.
    # ------------------------------------------------------------------
    content_type = 'movies' if not season else 'episodes'
    xbmcplugin.setContent(addon_handle, content_type)
    xbmcplugin.setPluginCategory(addon_handle, f'Links: {movie_title}')
    show_lookup_debug_ids = get_show_lookup_debug_ids()
    do_lookup = get_metadata_source() != 'none'

    # Đọc Window context của TMDb Helper một lần — chỉ dùng làm seed ban đầu
    # nếu file khớp đúng phim TMDb Helper đang focus.
    # Không gán cứng cho toàn bộ vòng lặp.
    tmdb_ctx = {}
    if tmdb_id and do_lookup:
        tmdb_ctx = read_tmdbhelper_context(tmdb_id=tmdb_id)

    links = search_fshare(movie_title, movie_year, season=season, episode=episode)
    if not links:
        notify(f"Không tìm thấy link cho {movie_title}")
        xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
        return

    links.sort(key=lambda x: x.get('size', 0), reverse=True)

    # ------------------------------------------------------------------ #
    #  INCLUDE / EXCLUDE FILTER                                           #
    #  include='2160p,4k,uhd;atmos,ddp;mkv,iso'                          #
    #    ';' phan cach nhom AND, ',' phan cach gia tri OR trong nhom      #
    #  exclude='hdcam,cam,telesync'                                       #
    #    ',' phan cach keyword, bat ky keyword nao match thi loai         #
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
        """Phan tich include='A,B;C,D' thanh [['a','b'], ['c','d']].
        ';' phan cach nhom AND, ',' phan cach OR trong nhom.
        """
        if not include_param:
            return []
        groups = []
        for group_str in str(include_param).split(';'):
            group_str = group_str.strip()
            if not group_str:
                continue
            keywords = [k.strip().lower() for k in group_str.split(',') if k.strip()]
            if keywords:
                groups.append(keywords)
        return groups

    def _parse_exclude_list(exclude_param):
        """Phan tich exclude='A,B,C' thanh ['a','b','c']."""
        if not exclude_param:
            return []
        return [k.strip().lower() for k in str(exclude_param).split(',') if k.strip()]

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
        filtered = [l for l in links if _item_matches(l, include_groups)]

        if not filtered:
            # Không có kết quả khớp filter → báo rõ, không fallback âm thầm
            req_str = ''
            if include_groups:
                req_str = 'include=[' + ';'.join('|'.join(g) for g in include_groups) + ']'
            if exclude_list:
                req_str += (' ' if req_str else '') + 'exclude=[' + '|'.join(exclude_list) + ']'
            xbmcgui.Dialog().notification(
                'FShare: Không tìm thấy',
                f'Không có file khớp filter: {req_str}',
                xbmcgui.NOTIFICATION_WARNING, 4000
            )
            xbmc.log(f"show_fshare_links: filter returned 0 results for: {req_str}", level=xbmc.LOGINFO)
            xbmcplugin.endOfDirectory(addon_handle, succeeded=False)
            return

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
        # Khi search manual (không có imdb_id/tmdb_id), movie_title là query string
        # người dùng nhập — không nên dùng làm title cho từng file.
        # Ưu tiên: identity_tags từ tên file > movie_title (query) > tên file thô.
        is_manual_search = not (imdb_id or tmdb_id)
        if is_manual_search:
            effective_title = (
                identity_tags.get('display_title')
                or identity_tags.get('title')
                or movie_title
                or link_info.get('title', '')
            )
        else:
            effective_title = (
                movie_title
                or identity_tags.get('display_title')
                or identity_tags.get('title')
                or link_info.get('title', '')
            )
        effective_year = str(movie_year or identity_tags.get('year') or '')
        if is_episode_item:
            effective_title = f"{effective_tvshowtitle} S{effective_season.zfill(2)}E{effective_episode.zfill(2)}"
        resolved_imdb_id = str(imdb_id) if imdb_id else ''
        resolved_tmdb_id = str(tmdb_id) if tmdb_id else ''
        resolved_plot        = ''
        resolved_rating      = ''
        resolved_poster_path = ''
        resolved_fanart      = ''

        # --- Validate tmdb_id từ TMDb Helper với effective_title của file ---
        # Nếu tên file parse ra phim khác với phim TMDb Helper đang focus
        # → reset tmdb_id, lookup riêng theo effective_title để tránh scrobble nhầm.
        if resolved_tmdb_id and not is_episode_item:
            file_title_clean  = re.sub(r'\s+', ' ', effective_title.lower().strip())
            movie_title_clean = re.sub(r'\s+', ' ', (movie_title or '').lower().strip())
            # Chấp nhận khác nhau nhỏ (năm, dấu câu) nhưng nếu tên quá khác → lookup riêng
            if file_title_clean and movie_title_clean and file_title_clean != movie_title_clean:
                # Kiểm tra xem effective_title có chứa movie_title không (partial match)
                if movie_title_clean not in file_title_clean and file_title_clean not in movie_title_clean:
                    xbmc.log(
                        f"show_fshare_links: title mismatch '{effective_title}' vs '{movie_title}' "
                        f"— resetting tmdb_id for per-file lookup",
                        level=xbmc.LOGINFO
                    )
                    resolved_tmdb_id = ''
                    resolved_imdb_id = ''

        # --- Lookup metadata riêng cho từng file ---
        # Cache theo effective_title+year — cùng phim chỉ gọi API 1 lần dù nhiều file.
        if do_lookup:
            lookup_title_key = (tvshowtitle or effective_title or movie_title or '').lower()
            lookup_year_key  = effective_year or movie_year or ''
            lookup_key = f"{lookup_title_key}|{lookup_year_key}|{effective_season}|{effective_episode}"

            # Nếu tmdb_id còn hợp lệ và Window context có đủ data → dùng luôn, không gọi API
            if resolved_tmdb_id and tmdb_ctx:
                resolved_plot        = tmdb_ctx.get('plot', '')
                resolved_rating      = tmdb_ctx.get('rating', '')
                resolved_poster_path = tmdb_ctx.get('poster', '')
                resolved_fanart      = tmdb_ctx.get('fanart', '')

            # Nếu thiếu bất kỳ field nào hoặc tmdb_id đã bị reset → lookup API
            if not (resolved_tmdb_id and resolved_plot and resolved_poster_path):
                if lookup_key not in lookup_cache:
                    lookup_cache[lookup_key] = lookup_tmdb_metadata(
                        title       = effective_title if not is_episode_item else '',
                        year        = effective_year or movie_year,
                        tvshowtitle = effective_tvshowtitle if is_episode_item else '',
                        season      = effective_season,
                        episode     = effective_episode,
                    )
                tmdb_meta = lookup_cache.get(lookup_key, {})
                if tmdb_meta:
                    resolved_imdb_id     = tmdb_meta.get('imdb_id', '') or resolved_imdb_id
                    resolved_tmdb_id     = tmdb_meta.get('tmdb_id', '') or resolved_tmdb_id
                    resolved_plot        = tmdb_meta.get('plot', '')        or resolved_plot
                    resolved_rating      = tmdb_meta.get('rating', '')      or resolved_rating
                    resolved_poster_path = tmdb_meta.get('poster', '')      or resolved_poster_path
                    resolved_fanart      = tmdb_meta.get('fanart', '')      or resolved_fanart
                    if is_episode_item:
                        effective_title       = tmdb_meta.get('title', '')       or effective_title
                        effective_tvshowtitle = tmdb_meta.get('tvshowtitle', '') or effective_tvshowtitle
                    else:
                        effective_title = tmdb_meta.get('title', '') or effective_title
                        effective_year  = tmdb_meta.get('year', '')  or effective_year
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

        # URL gọi thẳng play_fshare_direct — 1 hop, không qua play_trakt
        fshare_url_clean = link_info.get('fshare_url') or ''
        if not fshare_url_clean:
            # fallback: extract từ plugin URL nếu fshare_url chưa được set
            raw_url = link_info.get('url', '')
            m = re.search(r'url=(https?://(?:www\.)?fshare\.vn/[^\s&\[\]]+)', raw_url)
            if m:
                fshare_url_clean = urllib.parse.unquote(m.group(1))

        play_params = {
            'action':      'play_fshare_direct',
            'url':         fshare_url_clean,
            'imdb':        resolved_imdb_id,
            'tmdb':        resolved_tmdb_id,
            'title':       effective_title,
            'year':        effective_year,
            'season':      effective_season,
            'episode':     effective_episode,
            'tvshowtitle': effective_tvshowtitle if is_episode_item else '',
            'filename':    link_info.get('title', ''),
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

        if resolved_poster_path or resolved_fanart:
            art = {}
            if resolved_poster_path:
                art['thumb']  = resolved_poster_path
                art['poster'] = resolved_poster_path
                art['icon']   = resolved_poster_path
            if resolved_fanart:
                art['fanart'] = resolved_fanart
            # Art bổ sung từ TMDb Helper nếu có
            for art_key in ('clearlogo', 'clearart', 'landscape', 'discart', 'banner'):
                val = tmdb_ctx.get(art_key, '')
                if val:
                    art[art_key] = val
            list_item.setArt(art)

        apply_stream_props(list_item, stream_tags)

        # Skin dùng StreamFileNameAndPath để hiện icon source/codec khi browse
        _fname = link_info.get('title', '')
        if _fname:
            list_item.setProperty('StreamFileNameAndPath', _fname)
            # setPath(tên file) để skin đọc được ListItem.FileNameAndPath
            # URL thật nằm ở addDirectoryItem(url=play_url) nên không bị override
            list_item.setPath(_fname)

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


# ---------------------------------------------------------------------------
# TMDB HELPER CONTEXT — đọc metadata TMDb Helper đã fetch sẵn từ Window(Home)
# Service Monitor populate TMDbHelper.ListItem.* ngay khi user focus/bấm play.
# Addon đọc thẳng từ đây thay vì gọi thêm API — nhanh hơn, đúng ngôn ngữ hơn.
# ---------------------------------------------------------------------------

def read_tmdbhelper_context(tmdb_id=None, wait_ms=800):
    """
    Đọc metadata TMDb Helper Service Monitor đang giữ trong Window(Home).
    Chỉ dùng khi được gọi từ TMDb Helper (có tmdb_id từ params).

    Đợi tối đa wait_ms ms nếu Service Monitor đang update.
    Trả về dict với plot/poster/fanart/rating/title — rỗng nếu không có gì.
    """
    win = xbmcgui.Window(10000)

    # Đợi Service Monitor xong nếu đang busy (tối đa wait_ms)
    steps = wait_ms // 100
    for _ in range(steps):
        if not win.getProperty('TMDbHelper.IsUpdating'):
            break
        xbmc.sleep(100)

    # Map các property TMDb Helper Service Monitor → key nội bộ
    # Tham khảo: wiki/Detailed-Item — prefix TMDbHelper.ListItem.*
    prop_map = {
        'plot':       'TMDbHelper.ListItem.Plot',
        'title':      'TMDbHelper.ListItem.Title',
        'rating':     'TMDbHelper.ListItem.Rating',
        'votes':      'TMDbHelper.ListItem.Votes',
        'poster':     'TMDbHelper.ListItem.thumb',
        'fanart':     'TMDbHelper.ListItem.fanart',
        'clearlogo':  'TMDbHelper.ListItem.clearlogo',
        'clearart':   'TMDbHelper.ListItem.clearart',
        'landscape':  'TMDbHelper.ListItem.landscape',
        'discart':    'TMDbHelper.ListItem.discart',
        'banner':     'TMDbHelper.ListItem.banner',
        'tmdb_id':    'TMDbHelper.ListItem.UniqueId.tmdb',
        'imdb_id':    'TMDbHelper.ListItem.UniqueId.imdb',
    }

    ctx = {}
    for key, prop in prop_map.items():
        value = win.getProperty(prop) or ''
        if value:
            ctx[key] = value

    # Validate tmdb_id khớp để tránh đọc nhầm context của item khác
    if tmdb_id and ctx.get('tmdb_id'):
        if str(ctx['tmdb_id']) != str(tmdb_id):
            xbmc.log(
                f"read_tmdbhelper_context: tmdb_id mismatch "
                f"expected={tmdb_id} got={ctx.get('tmdb_id')} — discarding",
                level=xbmc.LOGWARNING
            )
            return {}

    if ctx:
        xbmc.log(
            f"read_tmdbhelper_context: got plot={bool(ctx.get('plot'))} "
            f"poster={bool(ctx.get('poster'))} fanart={bool(ctx.get('fanart'))} "
            f"tmdb_id={ctx.get('tmdb_id')}",
            level=xbmc.LOGINFO
        )
    else:
        xbmc.log("read_tmdbhelper_context: no properties found in Window(Home)", level=xbmc.LOGINFO)

    return ctx


# save_tmdbhelper_context / load_tmdbhelper_context / clear_tmdbhelper_context
# đã bị xóa — Kodi Way: metadata sống trong ListItem, không cần truyền qua URL.


# ---------------------------------------------------------------------------
# PLAY VIA TMDB HELPER — dùng cho list cộng đồng / manual search
# Lưu fshare_url vào Window property, redirect sang TMDb Helper để lấy
# metadata tiếng Việt đầy đủ. TMDb Helper gọi ngược action=search_fshare
# và addon dùng pending_url để skip search, play thẳng.
# ---------------------------------------------------------------------------
FSHARE_PENDING_KEY = 'myimdbfshare.pending.{tmdb_id}'

def play_via_tmdb_helper(fshare_url, tmdb_id, imdb_id='', title='', year='',
                          season=None, episode=None, tvshowtitle=None):
    """
    Redirect sang TMDb Helper để lấy metadata VN đầy đủ.
    Lưu fshare_url vào Window(10000) với key theo tmdb_id để tránh race condition.
    Nếu không có tmdb_id, fallback play trực tiếp.
    """
    if not tmdb_id:
        xbmc.log("play_via_tmdb_helper: no tmdb_id, fallback to play_fshare_direct", level=xbmc.LOGINFO)
        play_fshare_direct(
            fshare_url  = fshare_url,
            imdb_id     = imdb_id,
            tmdb_id     = '',
            title       = title,
            year        = year,
            season      = season,
            episode     = episode,
            tvshowtitle = tvshowtitle,
        )
        return

    tmdb_type = 'tv' if (season and episode) else 'movie'
    params = {'info': 'play', 'tmdb_id': str(tmdb_id), 'tmdb_type': tmdb_type}
    if season:
        params['season']  = str(season)
    if episode:
        params['episode'] = str(episode)

    tmdb_url = 'plugin://plugin.video.themoviedb.helper/?' + urllib.parse.urlencode(params)
    xbmc.log(f"play_via_tmdb_helper: launching {tmdb_url}", level=xbmc.LOGINFO)
    xbmc.executebuiltin(f'RunPlugin({tmdb_url})')


def play_fshare_direct(fshare_url, imdb_id='', tmdb_id='', title='', year='',
                       season=None, episode=None, tvshowtitle=None, filename=''):
    """
    Resolve fshare URL → CDN link → setResolvedUrl.

    Chiến lược metadata (Hướng 2 + 3):
    - Nếu browse đã tra (có imdb/tmdb_id): dùng lại, không gọi API thêm.
    - Nếu browse tắt (không có ID) và fetch_ids_on_play=True: tra đầy đủ ngay tại đây.
    - Build ListItem đầy đủ cho setResolvedUrl (Hướng 2 — một số skin đọc được).
    - Sau khi player bắt đầu, gọi updateInfoTag() để đảm bảo skin nào cũng thấy (Hướng 3).
    """
    handle = int(sys.argv[1])

    # ------------------------------------------------------------------ #
    # 1. Extract fshare URL thuần nếu vẫn còn dạng plugin://
    # ------------------------------------------------------------------ #
    if fshare_url.startswith('plugin://'):
        decoded = urllib.parse.unquote_plus(fshare_url)
        match = re.search(r'url=(https?://(?:www\.)?fshare\.vn/[^\s&\[\]]+)', decoded)
        if match:
            fshare_url = urllib.parse.unquote(match.group(1))
        else:
            notify('Không tìm được fshare URL')
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
            return

    if not fshare_url or 'fshare.vn' not in fshare_url:
        notify('URL Fshare không hợp lệ')
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    # ------------------------------------------------------------------ #
    # 2. Resolve CDN link
    # ------------------------------------------------------------------ #
    cdn_url = fshare_get_download_link(fshare_url)
    if not cdn_url:
        notify('Không lấy được CDN link từ Fshare')
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    xbmc.log(f"play_fshare_direct: resolved {fshare_url[:60]} → CDN OK", level=xbmc.LOGINFO)

    # ------------------------------------------------------------------ #
    # 3. Tra metadata đầy đủ trước setResolvedUrl để Kore và các client
    #    JSON-RPC thấy plot/poster/fanart ngay khi play bắt đầu.
    #
    #    Ưu tiên:
    #    a) Đã có tmdb_id → fetch_tmdb_details_by_id (1 call trực tiếp, nhanh)
    #    b) Chưa có ID + fetch_ids_on_play=True → lookup_tmdb_metadata (search)
    # ------------------------------------------------------------------ #
    meta = {}
    if tmdb_id:
        try:
            meta = fetch_tmdb_details_by_id(
                tmdb_id = tmdb_id,
                season  = season,
                episode = episode,
            )
            if meta:
                imdb_id = imdb_id or meta.get('imdb_id', '')
                title   = title   or meta.get('title', title)
                year    = year    or meta.get('year',  year)
                xbmc.log(
                    f"play_fshare_direct: fetch_by_id tmdb={tmdb_id} "
                    f"plot={bool(meta.get('plot'))} poster={bool(meta.get('poster'))}",
                    level=xbmc.LOGINFO
                )
        except Exception as e:
            xbmc.log(f"play_fshare_direct: fetch_by_id error: {e}", level=xbmc.LOGWARNING)

    if not meta and get_fetch_ids_on_play() and not imdb_id and not tmdb_id and title:
        try:
            force_source = get_effective_source_for_play()
            meta = lookup_tmdb_metadata(
                title        = title if not (season and episode) else '',
                year         = year,
                tvshowtitle  = tvshowtitle if (season and episode) else '',
                season       = season,
                episode      = episode,
                force_source = force_source,
            )
            if meta:
                imdb_id = imdb_id or meta.get('imdb_id', '')
                tmdb_id = tmdb_id or meta.get('tmdb_id', '')
                title   = title   or meta.get('title',   title)
                year    = year    or meta.get('year',    year)
                xbmc.log(
                    f"play_fshare_direct: search meta — "
                    f"imdb={imdb_id} tmdb={tmdb_id} "
                    f"plot={bool(meta.get('plot'))} poster={bool(meta.get('poster'))}",
                    level=xbmc.LOGINFO
                )
        except Exception as e:
            xbmc.log(f"play_fshare_direct: metadata fetch error: {e}", level=xbmc.LOGWARNING)

    # ------------------------------------------------------------------ #
    # 4. Set script.trakt.ids vào Window(10000) cho Trakt scrobble
    # ------------------------------------------------------------------ #
    if imdb_id or tmdb_id:
        trakt_ids = {}
        if imdb_id: trakt_ids['imdb'] = str(imdb_id)
        if tmdb_id: trakt_ids['tmdb'] = str(tmdb_id)
        xbmcgui.Window(10000).setProperty('script.trakt.ids', json.dumps(trakt_ids))
        xbmc.log(f"play_fshare_direct: set script.trakt.ids={json.dumps(trakt_ids)}", level=xbmc.LOGINFO)

    # ------------------------------------------------------------------ #
    # 5. Build ListItem đầy đủ metadata (Hướng 2)
    #    Một số skin và Kodi build đọc metadata từ resolved ListItem.
    #    Khi browse đã tra đầy đủ: meta={} nhưng imdb/tmdb_id đã có trong params.
    #    Khi browse tắt: meta chứa plot/poster/fanart vừa tra ở bước 3.
    # ------------------------------------------------------------------ #
    is_episode = bool(season and episode)
    list_item = xbmcgui.ListItem(label=title or '', path=cdn_url)
    list_item.setProperty('IsPlayable', 'true')
    list_item.setMimeType('video/mp4')

    # ------------------------------------------------------------------ #
    # Tên file gốc để skin check FileNameAndPath (atmos/dtsx/bluray/4K...)
    # Dùng filename để parse stream tags, nhưng KHÔNG ghép vào CDN URL
    # để tránh URL encoding issues với Fshare server (HTTP 400 error).
    # ------------------------------------------------------------------ #
    _filename = (filename or os.path.basename((fshare_url or '').split('?')[0])).strip()
    
    # Lưu filename vào property để skin có thể dùng thay vì FileNameAndPath
    # Skin sẽ dùng: ListItem.Property(StreamFileNameAndPath)
    if _filename:
        list_item.setProperty('StreamFileNameAndPath', _filename)

    # Parse và apply stream tags (audio codec, video codec, HDR, channels...)
    # apply_stream_props cũng gọi setHdrType() vào VideoInfoTag để
    # Image_HDR_Codec / Image_OSD_HDR_Codec hoạt động đúng.
    if _filename:
        stream_tags = parse_stream_tags_from_filename(_filename)
        apply_stream_props(list_item, stream_tags)

    info_tag = list_item.getVideoInfoTag()
    info_tag.setTitle(title or '')

    plot   = meta.get('plot', '')
    poster = meta.get('poster', '')
    fanart = meta.get('fanart', '')
    thumb  = meta.get('thumb', '') or poster
    rating = meta.get('rating', '')

    if plot:
        info_tag.setPlot(plot)
    if rating:
        try:
            info_tag.setRating(float(rating))
        except (ValueError, TypeError):
            pass
    if year and str(year).isdigit():
        info_tag.setYear(int(year))

    ids = {}
    if imdb_id:
        ids['imdb'] = str(imdb_id)
        info_tag.setIMDBNumber(str(imdb_id))
    if tmdb_id:
        ids['tmdb'] = str(tmdb_id)
    if ids:
        info_tag.setUniqueIDs(ids, 'imdb' if imdb_id else 'tmdb')

    if is_episode:
        info_tag.setMediaType('episode')
        info_tag.setTvShowTitle(tvshowtitle or title or '')
        try:
            info_tag.setSeason(int(season))
            info_tag.setEpisode(int(episode))
        except (ValueError, TypeError):
            pass
    else:
        info_tag.setMediaType('movie')

    art = {}
    if poster: art['poster'] = poster
    if thumb:  art['thumb']  = thumb
    if fanart: art['fanart'] = fanart
    if art:
        list_item.setArt(art)

    # ------------------------------------------------------------------ #
    # 6. Lưu lịch sử xem
    # ------------------------------------------------------------------ #
    try:
        _hist_filename = (filename or os.path.basename((fshare_url or '').split('?')[0])).strip()
        save_play_history(
            title      = title or '',
            year       = year  or '',
            filename   = _hist_filename,
            fshare_url = fshare_url or '',
            imdb_id    = imdb_id   or '',
            tmdb_id    = tmdb_id   or '',
            poster_url = poster    or '',
            plot       = plot      or '',
            size_bytes = 0,
        )
    except Exception as _e:
        xbmc.log(f'play_fshare_direct: save_play_history error: {_e}', level=xbmc.LOGWARNING)

    # ------------------------------------------------------------------ #
    # 7. setResolvedUrl — trả CDN link + metadata về Kodi
    # ------------------------------------------------------------------ #
    xbmcplugin.setResolvedUrl(handle, True, listitem=list_item)

    # ------------------------------------------------------------------ #
    # 8. updateInfoTag() sau khi player bắt đầu (Hướng 3)
    #    Đảm bảo skin hiện đúng plot/poster/IDs trong Now Playing,
    #    kể cả các skin không đọc metadata từ resolved ListItem.
    #    Chỉ chạy khi có metadata mới cần update (meta không rỗng).
    # ------------------------------------------------------------------ #
    if meta and (plot or poster or imdb_id or tmdb_id):
        try:
            player = xbmc.Player()
            # Đợi player bắt đầu phát — tối đa 5 giây (50 x 100ms)
            for _ in range(50):
                if player.isPlaying():
                    break
                xbmc.sleep(100)

            if player.isPlaying():
                player.updateInfoTag(list_item)
                xbmc.log(
                    f"play_fshare_direct: updateInfoTag OK — "
                    f"plot={bool(plot)} poster={bool(poster)} "
                    f"imdb={imdb_id} tmdb={tmdb_id}",
                    level=xbmc.LOGINFO
                )
            else:
                xbmc.log("play_fshare_direct: updateInfoTag skipped — player not started", level=xbmc.LOGWARNING)
        except Exception as e:
            xbmc.log(f"play_fshare_direct: updateInfoTag error: {e}", level=xbmc.LOGWARNING)


def auto_play_fshare(title, year='', imdb_id='', tmdb_id='',
                     season=None, episode=None, tvshowtitle=None,
                     include=None, exclude=None, size_gb=None):
    """
    TMDb Helper Resolver Player — tự động search Fshare và play link tốt nhất.

    Khác search_fshare (hiện danh sách để user chọn), hàm này:
      1. Search Fshare lấy toàn bộ link
      2. Apply include/exclude filter (từ players.json của TMDb Helper)
      3. Apply size_gb filter (lọc theo khoảng dung lượng GB)
      4. Sort: file lớn nhất lên đầu (chất lượng cao nhất)
      5. Chọn link đầu tiên → play thẳng qua play_fshare_direct()
      6. Metadata: đọc từ TMDb Helper Window context (đã fetch sẵn, không tốn API)

    players.json mẫu:
      Movie:   "plugin://plugin.video.myimdbfshare/?action=auto_play_fshare
                &title={originaltitle}&year={year}&imdb={imdb}&tmdb={tmdb}
                &include=1080p,2160p&exclude=hdcam,cam&size_gb=5-25"
      Episode: "plugin://plugin.video.myimdbfshare/?action=auto_play_fshare
                &title={showname}&year={year}&imdb={imdb}&tmdb={tmdb}
                &season={season}&episode={episode}&size_gb=0-8"

    include  : 'A,B;C,D'  → (A OR B) AND (C OR D) — phân cách nhóm bằng ';', OR bằng ','
    exclude  : 'X,Y,Z'    → loại file chứa bất kỳ keyword X, Y, hoặc Z
    size_gb  : 'min-max'  → chỉ giữ file trong khoảng [min, max] GB
                            dùng 0 để bỏ qua một đầu: '0-20' (≤20 GB), '10-0' (≥10 GB)
                            không truyền hoặc '0-0' = không lọc size
    """
    handle = int(sys.argv[1])
    is_episode = bool(season and episode)

    xbmc.log(
        f"[auto_play_fshare] title={title!r} year={year!r} "
        f"imdb={imdb_id!r} tmdb={tmdb_id!r} "
        f"season={season!r} episode={episode!r} "
        f"include={include!r} exclude={exclude!r} size_gb={size_gb!r}",
        level=xbmc.LOGINFO
    )

    # ------------------------------------------------------------------ #
    # 1. Search Fshare
    # ------------------------------------------------------------------ #
    links = search_fshare(title, year, season=season, episode=episode)
    if not links:
        notify(f"Không tìm thấy link: {title}", duration=4000)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    # ------------------------------------------------------------------ #
    # 2. Apply include / exclude filter (tái dùng logic từ show_fshare_links)
    # ------------------------------------------------------------------ #
    def _tokenize(filename):
        name = os.path.basename(filename or '').lower()
        raw = re.split(r'[\.\-_ ]+', name)
        tokens = set(t for t in raw if t)
        tokens.add(re.sub(r'\.[a-z0-9]{2,4}$', '', name))
        return tokens

    def _token_match(keyword, tokens, full_name):
        kw = keyword.strip().lower()
        return kw and (kw in tokens or kw in full_name)

    def _parse_include_groups(tier_str):
        """Parse một tier thành list of OR-groups: 'A,B;C,D' → [['a','b'],['c','d']]
        ';' phân cách nhóm AND, ',' phân cách OR trong nhóm.
        """
        if not tier_str:
            return []
        groups = []
        for g in str(tier_str).split(';'):
            g = g.strip()
            if g:
                kws = [k.strip().lower() for k in g.split(',') if k.strip()]
                if kws:
                    groups.append(kws)
        return groups

    def _parse_include_tiers(param):
        """
        Parse chuỗi include có nhiều tầng ưu tiên, phân cách bằng '~~'.
        Ví dụ: '2160p,4k;atmos~~1080p;dts~~720p'
          → tier 1: [['2160p','4k'],['atmos']]
          → tier 2: [['1080p'],['dts']]
          → tier 3: [['720p']]
        Tương thích ngược: nếu không có '~~', coi như 1 tier duy nhất.
        Dùng '~~' thay vì '||' vì '||' bị shell interpret khi TMDb Helper build URL.
        Dùng ',' thay vì '|' cho OR vì '|' bị Kodi cắt trong URL params.
        """
        if not param:
            return []
        tiers = []
        for tier_str in str(param).split('~~'):
            tier_str = tier_str.strip()
            if tier_str:
                groups = _parse_include_groups(tier_str)
                if groups:
                    tiers.append(groups)
        return tiers

    def _parse_exclude_list(param):
        """Phan tich exclude='A,B,C' thanh ['a','b','c']."""
        if not param:
            return []
        return [k.strip().lower() for k in str(param).split(',') if k.strip()]

    include_tiers = _parse_include_tiers(include)
    exclude_list  = _parse_exclude_list(exclude)
    matched_tier  = 0  # 0 = không có filter, >0 = tier đã khớp

    def _is_excluded(link_info):
        fname  = link_info.get('title', '')
        full   = fname.lower()
        tokens = _tokenize(fname)
        return any(_token_match(kw, tokens, full) for kw in exclude_list)

    def _matches_groups(link_info, groups):
        """Kiểm tra link_info có thỏa mãn tất cả các AND-group không."""
        fname  = link_info.get('title', '')
        full   = fname.lower()
        tokens = _tokenize(fname)
        return all(any(_token_match(kw, tokens, full) for kw in group) for group in groups)

    if include_tiers or exclude_list:
        # Áp exclude trước — loại bỏ vĩnh viễn trước khi thử bất kỳ tier nào
        candidates = [l for l in links if not _is_excluded(l)]

        matched      = None
        matched_tier = -1

        for i, groups in enumerate(include_tiers):
            tier_result = [l for l in candidates if _matches_groups(l, groups)]
            if tier_result:
                matched      = tier_result
                matched_tier = i + 1
                break

        if matched:
            if matched_tier > 1:
                tier_desc = ' || '.join(
                    ';'.join('|'.join(g) for g in t) for t in include_tiers
                )
                xbmc.log(
                    f"auto_play_fshare: tier 1–{matched_tier - 1} matched 0, "
                    f"using tier {matched_tier}",
                    level=xbmc.LOGINFO
                )
                notify(f"Tier 1–{matched_tier - 1} không khớp, dùng tier {matched_tier}", duration=3000, sound=False)
            links = matched

        elif include_tiers:
            # Tất cả tier đều trống → dừng hẳn, không fallback bừa
            tier_desc = ' || '.join(
                ';'.join('|'.join(g) for g in t) for t in include_tiers
            )
            xbmc.log(
                f"auto_play_fshare: all {len(include_tiers)} tier(s) matched 0 results "
                f"(include=[{tier_desc}] exclude=[{'|'.join(exclude_list)}])",
                level=xbmc.LOGWARNING
            )
            notify(
                f"Không tìm thấy link sau {len(include_tiers)} tier filter — dừng play",
                duration=4500,
                sound=False
            )
            xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
            return

        else:
            # Chỉ có exclude, không có include — dùng candidates đã lọc
            # Nếu exclude lọc sạch toàn bộ thì dùng list gốc còn hơn không play được
            links = candidates if candidates else links

    # ------------------------------------------------------------------ #
    # 3. Filter size_gb: chỉ giữ file trong khoảng [min_gb, max_gb]
    #    Cú pháp: 'min-max' tính bằng GB, dùng 0 để bỏ qua một đầu.
    #    Ví dụ: '5-25' (5–25 GB), '10-0' (≥10 GB), '0-20' (≤20 GB)
    # ------------------------------------------------------------------ #
    _size_min_gb = 0.0
    _size_max_gb = 0.0
    if size_gb:
        try:
            _parts = str(size_gb).split('-')
            if len(_parts) == 2:
                _size_min_gb = float(_parts[0]) if _parts[0].strip() else 0.0
                _size_max_gb = float(_parts[1]) if _parts[1].strip() else 0.0
        except (ValueError, TypeError):
            xbmc.log(f"auto_play_fshare: invalid size_gb={size_gb!r}, skipping size filter", level=xbmc.LOGWARNING)
            _size_min_gb = _size_max_gb = 0.0

    if _size_min_gb > 0 or _size_max_gb > 0:
        _bytes_per_gb = 1024 ** 3
        def _in_size_range(link_info):
            sz_gb = link_info.get('size', 0) / _bytes_per_gb
            if _size_min_gb > 0 and sz_gb < _size_min_gb:
                return False
            if _size_max_gb > 0 and sz_gb > _size_max_gb:
                return False
            return True

        size_filtered = [l for l in links if _in_size_range(l)]
        if size_filtered:
            xbmc.log(
                f"auto_play_fshare: size_gb={size_gb!r} → "
                f"{len(size_filtered)}/{len(links)} file(s) trong khoảng",
                level=xbmc.LOGINFO
            )
            links = size_filtered
        else:
            xbmc.log(
                f"auto_play_fshare: size_gb={size_gb!r} không khớp file nào — bỏ filter size",
                level=xbmc.LOGWARNING
            )
            notify(f"Không có file trong khoảng {size_gb} GB — bỏ qua filter size", duration=3500, sound=False)
            # Không return, giữ nguyên links để vẫn play được

    # ------------------------------------------------------------------ #
    # 4. Sort: file lớn nhất lên đầu (proxy cho chất lượng cao nhất)
    # ------------------------------------------------------------------ #
    links.sort(key=lambda x: x.get('size', 0), reverse=True)
    best = links[0]

    best_title   = best.get('title', '')
    best_size_gb = best.get('size', 0) / (1024 ** 3)
    xbmc.log(
        f"auto_play_fshare: selected '{best_title}' "
        f"({best_size_gb:.2f} GB) "
        f"from {len(links)} candidate(s)",
        level=xbmc.LOGINFO
    )

    if get_autoplay_notify():
        # Hiện: size · tier N: <include của tier đã matched> · size range · excl: exclude · N ứng viên
        parts = [f'{best_size_gb:.2f} GB']
        if include_tiers and matched_tier > 0:
            # Chỉ lấy groups của tier đã matched (index = matched_tier - 1)
            matched_groups = include_tiers[matched_tier - 1]
            include_str = ';'.join(','.join(g) for g in matched_groups)
            # Chỉ hiện nhãn TN nếu có nhiều hơn 1 tier (để biết đã fallback)
            tier_label = f'T{matched_tier}: ' if len(include_tiers) > 1 else ''
            parts.append(f'{tier_label}{include_str}')
        if (_size_min_gb > 0 or _size_max_gb > 0) and size_gb:
            parts.append(f'size: {size_gb} GB')
        if exclude_list:
            parts.append(f'excl: {",".join(exclude_list)}')
        parts.append(f'{len(links)} ứng viên')
        body = ' · '.join(parts)
        xbmcgui.Dialog().notification(
            best_title,
            body,
            time=get_autoplay_notify_duration(),
            sound=False
        )

    # ------------------------------------------------------------------ #
    # 4. Tra metadata đầy đủ trước setResolvedUrl
    #    Ưu tiên: fetch_tmdb_details_by_id (tmdb_id trực tiếp, 1 call)
    #    Fallback: read_tmdbhelper_context (Window properties, không tốn API)
    # ------------------------------------------------------------------ #
    tmdb_ctx = {}
    meta     = {}

    if tmdb_id:
        try:
            meta = fetch_tmdb_details_by_id(
                tmdb_id = tmdb_id,
                season  = season,
                episode = episode,
            )
            xbmc.log(
                f"auto_play_fshare: fetch_by_id tmdb={tmdb_id} "
                f"plot={bool(meta.get('plot'))} poster={bool(meta.get('poster'))}",
                level=xbmc.LOGINFO
            )
        except Exception as e:
            xbmc.log(f"auto_play_fshare: fetch_by_id error: {e}", level=xbmc.LOGWARNING)

    # Fallback: đọc Window context TMDb Helper (không tốn API call)
    if not meta or not meta.get('plot'):
        if tmdb_id or imdb_id:
            tmdb_ctx = read_tmdbhelper_context(tmdb_id=tmdb_id)

    # Merge: meta từ API ưu tiên, bổ sung art từ TMDb Helper context nếu thiếu
    ctx_plot   = meta.get('plot', '')   or tmdb_ctx.get('plot', '')
    ctx_poster = meta.get('poster', '') or tmdb_ctx.get('poster', '')
    ctx_fanart = meta.get('fanart', '') or tmdb_ctx.get('fanart', '')
    ctx_rating = meta.get('rating', '') or tmdb_ctx.get('rating', '')
    ctx_title  = meta.get('title', '')  or tmdb_ctx.get('title', '') or title
    # Art bổ sung chỉ có từ TMDb Helper context (clearlogo, clearart...)
    ctx_extra_art = {k: tmdb_ctx.get(k, '') for k in
                     ('clearlogo', 'clearart', 'landscape', 'discart', 'banner')
                     if tmdb_ctx.get(k)}

    # ------------------------------------------------------------------ #
    # 5. Resolve CDN link
    # ------------------------------------------------------------------ #
    fshare_url = best.get('fshare_url', '')
    if not fshare_url:
        raw_url = best.get('url', '')
        m = re.search(r'url=(https?://(?:www\.)?fshare\.vn/[^\s&\[\]]+)', raw_url)
        if m:
            fshare_url = urllib.parse.unquote(m.group(1))

    if not fshare_url or 'fshare.vn' not in fshare_url:
        notify('Không tìm được fshare URL hợp lệ')
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    cdn_url = fshare_get_download_link(fshare_url)
    if not cdn_url:
        notify('Không lấy được CDN link từ Fshare')
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return

    xbmc.log(f"auto_play_fshare: CDN resolved OK", level=xbmc.LOGINFO)

    # ------------------------------------------------------------------ #
    # 6. Set script.trakt.ids cho Trakt scrobble
    # ------------------------------------------------------------------ #
    if imdb_id or tmdb_id:
        trakt_ids = {}
        if imdb_id: trakt_ids['imdb'] = str(imdb_id)
        if tmdb_id: trakt_ids['tmdb'] = str(tmdb_id)
        xbmcgui.Window(10000).setProperty('script.trakt.ids', json.dumps(trakt_ids))
        xbmc.log(f"auto_play_fshare: set script.trakt.ids={json.dumps(trakt_ids)}", level=xbmc.LOGINFO)

    # ------------------------------------------------------------------ #
    # 7. Build ListItem đầy đủ metadata
    # ------------------------------------------------------------------ #
    list_item = xbmcgui.ListItem(label=ctx_title, path=cdn_url)
    list_item.setProperty('IsPlayable', 'true')
    list_item.setMimeType('video/mp4')

    info_tag = list_item.getVideoInfoTag()
    info_tag.setTitle(ctx_title)
    if ctx_plot:
        info_tag.setPlot(ctx_plot)
    if ctx_rating:
        try:
            info_tag.setRating(float(ctx_rating))
        except (ValueError, TypeError):
            pass
    if year and str(year).isdigit():
        info_tag.setYear(int(year))

    ids = {}
    if imdb_id:
        ids['imdb'] = str(imdb_id)
        info_tag.setIMDBNumber(str(imdb_id))
    if tmdb_id:
        ids['tmdb'] = str(tmdb_id)
    if ids:
        info_tag.setUniqueIDs(ids, 'imdb' if imdb_id else 'tmdb')

    if is_episode:
        info_tag.setMediaType('episode')
        info_tag.setTvShowTitle(tvshowtitle or title or '')
        try:
            info_tag.setSeason(int(season))
            info_tag.setEpisode(int(episode))
        except (ValueError, TypeError):
            pass
    else:
        info_tag.setMediaType('movie')

    art = {}
    if ctx_poster: art['poster'] = ctx_poster; art['thumb'] = ctx_poster
    if ctx_fanart: art['fanart'] = ctx_fanart
    for art_key, art_val in ctx_extra_art.items():
        art[art_key] = art_val
    if art:
        list_item.setArt(art)

    # ------------------------------------------------------------------ #
    # 8. Write metadata vào TMDbHelper.ListItem.* Window properties TRƯỚC
    #    setResolvedUrl.
    #
    #    Kore KHÔNG đọc plot từ Player.GetItem (JSON-RPC trả về type=unknown,
    #    plot="" với resolver flow — Kodi không merge VideoInfoTag vào playlist
    #    item của TMDb Helper). Kore đọc plot qua XBMC.GetInfoLabels với key
    #    TMDbHelper.ListItem.Plot — chính là các property TMDb Helper Service
    #    Monitor ghi vào Window(10000).
    #
    #    Ghi đè các key này bằng metadata đã fetch (tiếng Việt nếu có) trước
    #    setResolvedUrl để Kore luôn thấy plot dù query ở thời điểm nào.
    # ------------------------------------------------------------------ #
    try:
        _win = xbmcgui.Window(10000)
        # Các key này khớp với prop_map trong read_tmdbhelper_context()
        # và là key Kore/Yatse đọc qua GetInfoLabels
        if ctx_plot:
            _win.setProperty('TMDbHelper.ListItem.Plot',             ctx_plot)
        if ctx_title:
            _win.setProperty('TMDbHelper.ListItem.Title',            ctx_title)
        if ctx_poster:
            _win.setProperty('TMDbHelper.ListItem.thumb',            ctx_poster)
        if ctx_fanart:
            _win.setProperty('TMDbHelper.ListItem.fanart',           ctx_fanart)
        if ctx_rating:
            _win.setProperty('TMDbHelper.ListItem.Rating',           str(ctx_rating))
        if tmdb_id:
            _win.setProperty('TMDbHelper.ListItem.UniqueId.tmdb',    str(tmdb_id))
        if imdb_id:
            _win.setProperty('TMDbHelper.ListItem.UniqueId.imdb',    str(imdb_id))
        xbmc.log(
            f"auto_play_fshare: pre-push TMDbHelper.ListItem.* OK "
            f"plot={bool(ctx_plot)} title={bool(ctx_title)}",
            level=xbmc.LOGINFO
        )
    except Exception as _we:
        xbmc.log(f"auto_play_fshare: pre-push TMDbHelper.ListItem error: {_we}", level=xbmc.LOGWARNING)

    # ------------------------------------------------------------------ #
    # 8b. setResolvedUrl — trả CDN + metadata về TMDb Helper NGAY
    # ------------------------------------------------------------------ #
    xbmcplugin.setResolvedUrl(handle, True, listitem=list_item)

    # ------------------------------------------------------------------ #
    # 9. Background: đợi player start, updateInfoTag + save_play_history.
    #    Metadata đã đầy đủ từ bước 4 (fetch_tmdb_details_by_id).
    #    Toàn bộ phần này chạy SAU setResolvedUrl nên không block TMDb Helper.
    #
    #    updateInfoTag được gọi 2 lần:
    #      - Lần 1: ngay khi player bắt đầu phát (skin Kodi đọc được)
    #      - Lần 2: sau thêm 3 giây (phòng trường hợp Kore query muộn hơn bình thường)
    #    Dùng Monitor.waitForAbort() thay vì polling xbmc.sleep() để tránh
    #    giữ thread khi Kodi đang shutdown.
    # ------------------------------------------------------------------ #
    try:
        monitor = xbmc.Monitor()
        player  = xbmc.Player()

        # Đợi player thực sự bắt đầu phát — tối đa 10 giây
        _waited = 0
        while _waited < 10000 and not monitor.abortRequested():
            if player.isPlaying():
                break
            monitor.waitForAbort(0.1)
            _waited += 100

        if player.isPlaying():
            # Update skin Kodi — đọc từ player internal state, không từ Window properties
            player.updateInfoTag(list_item)
            xbmc.log("auto_play_fshare: updateInfoTag OK", level=xbmc.LOGINFO)
        else:
            xbmc.log("auto_play_fshare: updateInfoTag skipped — player not started within 10s", level=xbmc.LOGWARNING)

        # Lưu lịch sử xem sau khi player đã start
        try:
            save_play_history(
                title      = ctx_title or title or '',
                year       = year or '',
                filename   = best_title,
                fshare_url = fshare_url,
                imdb_id    = imdb_id or '',
                tmdb_id    = tmdb_id or '',
                poster_url = ctx_poster,
                plot       = ctx_plot,
                size_bytes = best.get('size', 0),
            )
        except Exception as _he:
            xbmc.log(f'auto_play_fshare: save_play_history error: {_he}', level=xbmc.LOGWARNING)

    except Exception as e:
        xbmc.log(f"auto_play_fshare: post-play background error: {e}", level=xbmc.LOGWARNING)


def set_trakt_ids_and_play(play_url, imdb_id, tmdb_id, movie_title, movie_year, season=None, episode=None, tvshowtitle=None):
    import json as _json

    # Neu chua co ID va setting fetch_ids_on_play bat -> tra API ngay truoc khi play
    if get_fetch_ids_on_play() and not imdb_id and not tmdb_id and movie_title:
        xbmc.log(f"play_trakt: no IDs for '{movie_title}', fetching now...", level=xbmc.LOGINFO)
        try:
            force_source = get_effective_source_for_play()
            meta = lookup_tmdb_metadata(
                title=movie_title if not (season and episode) else '',
                year=movie_year,
                tvshowtitle=tvshowtitle if (season and episode) else '',
                season=season,
                episode=episode,
                force_source=force_source,
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

        elif action == 'search_manual_keyboard':
            search_fshare_manual_keyboard()

        elif action == 'run_search_history':
            query = params.get('query', '')
            if query:
                show_fshare_links(query, '')

        elif action == 'list_play_history':
            list_play_history()

        elif action == 'clear_search_history':
            clear_search_history()
            xbmcgui.Dialog().notification('Tìm kiếm', 'Đã xóa lịch sử tìm kiếm', time=2500)
            xbmc.executebuiltin('Container.Refresh')

        elif action == 'clear_play_history':
            clear_play_history()
            xbmcgui.Dialog().notification('Lịch sử xem', 'Đã xóa lịch sử xem', time=2500)
            xbmc.executebuiltin('Container.Refresh')

        elif action == 'list_community':
            list_community(params.get('page', '1'))

        elif action == 'settings_menu':
            settings_menu()

        elif action == 'set_tmdb_api_key':
            prompt_text_setting('tmdb_api_key', 'Nhập TMDb API Key', get_local_setting('tmdb_api_key', ''))

        elif action == 'set_fshare_credentials':
            username, password = fshare_prompt_credentials()
            if username:
                xbmcgui.Dialog().notification('Fshare', f'Đã lưu: {username}', time=3000)
            settings_menu()

        elif action == 'fshare_relogin':
            fshare_relogin()
            settings_menu()

        elif action == 'debug_tmdbhelper_props':
            # Dump toàn bộ TMDbHelper.* properties trong Window(Home) ra log
            win = xbmcgui.Window(10000)
            # Các prefix cần kiểm tra
            prefixes = [
                'TMDbHelper.ListItem.',
                'TMDbHelper.Player.',
                'TMDbHelper.IsUpdating',
                'TMDbHelper.Service',
            ]
            # Danh sách property name đã biết từ wiki
            known_props = [
                'TMDbHelper.IsUpdating',
                'TMDbHelper.ListItem.Plot',
                'TMDbHelper.ListItem.Title',
                'TMDbHelper.ListItem.Rating',
                'TMDbHelper.ListItem.Votes',
                'TMDbHelper.ListItem.thumb',
                'TMDbHelper.ListItem.fanart',
                'TMDbHelper.ListItem.poster',
                'TMDbHelper.ListItem.clearlogo',
                'TMDbHelper.ListItem.clearart',
                'TMDbHelper.ListItem.landscape',
                'TMDbHelper.ListItem.discart',
                'TMDbHelper.ListItem.banner',
                'TMDbHelper.ListItem.UniqueId.tmdb',
                'TMDbHelper.ListItem.UniqueId.imdb',
                'TMDbHelper.ListItem.UniqueId.tvdb',
                'TMDbHelper.ListItem.Year',
                'TMDbHelper.ListItem.Genre',
                'TMDbHelper.ListItem.Studio',
                'TMDbHelper.ListItem.Premiered',
                'TMDbHelper.ListItem.MPAA',
                'TMDbHelper.ListItem.Director',
                'TMDbHelper.ListItem.Duration',
                'TMDbHelper.ListItem.Tagline',
                'TMDbHelper.ListItem.Status',
                'TMDbHelper.ListItem.TVShowTitle',
                'TMDbHelper.ListItem.Season',
                'TMDbHelper.ListItem.Episode',
                'TMDbHelper.Player.Plot',
                'TMDbHelper.Player.Title',
                'TMDbHelper.Player.thumb',
                'TMDbHelper.Player.fanart',
                'TMDbHelper.Player.UniqueId.tmdb',
                'TMDbHelper.Player.UniqueId.imdb',
                'TMDbHelper.Player.Rating',
            ]
            found = []
            empty = []
            for prop in known_props:
                val = win.getProperty(prop) or ''
                if val:
                    # Cắt ngắn URL dài
                    display = val[:120] + '...' if len(val) > 120 else val
                    found.append(f"  [SET]   {prop} = {display}")
                else:
                    empty.append(f"  [EMPTY] {prop}")

            xbmc.log("=" * 70, level=xbmc.LOGINFO)
            xbmc.log("DEBUG: TMDbHelper Window(Home) Properties Dump", level=xbmc.LOGINFO)
            xbmc.log(f"  Found {len(found)} non-empty / {len(empty)} empty properties", level=xbmc.LOGINFO)
            xbmc.log("--- NON-EMPTY ---", level=xbmc.LOGINFO)
            for line in found:
                xbmc.log(line, level=xbmc.LOGINFO)
            xbmc.log("--- EMPTY ---", level=xbmc.LOGINFO)
            for line in empty:
                xbmc.log(line, level=xbmc.LOGINFO)
            xbmc.log("=" * 70, level=xbmc.LOGINFO)

            summary = f"{len(found)} props có giá trị / {len(empty)} rỗng. Xem kodi.log để biết chi tiết."
            xbmcgui.Dialog().ok('Debug TMDbHelper Props', summary)

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

        elif action == 'toggle_autoplay_notify':
            toggle_bool_setting('autoplay_notify', 'Thông báo file auto play')
            settings_menu()

        elif action == 'set_autoplay_notify_duration':
            prompt_number_setting('autoplay_notify_duration', 'Nhập thời gian thông báo auto play (mili giây)', get_autoplay_notify_duration())
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
            xbmc.log(
                f"[search_fshare] title={movie_title!r} year={movie_year!r} "
                f"imdb={imdb_id!r} tmdb={tmdb_id!r} "
                f"season={params.get('season')!r} episode={params.get('episode')!r}",
                level=xbmc.LOGINFO
            )

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

        elif action == 'auto_play_fshare':
            # TMDb Helper Resolver Player — search Fshare và play link tốt nhất tự động.
            # Config players.json:
            #   Movie:   action=auto_play_fshare&title={originaltitle}&year={year}
            #            &imdb={imdb}&tmdb={tmdb}&include=1080p,2160p&exclude=hdcam,cam
            #            &size_gb=5-25
            #   Episode: action=auto_play_fshare&title={showname}&year={year}
            #            &imdb={imdb}&tmdb={tmdb}&season={season}&episode={episode}
            #            &size_gb=0-8
            #   size_gb: 'min-max' GB, dùng 0 để bỏ qua một đầu (vd: '10-0' = ≥10 GB)
            movie_title = params.get('title', '')
            if movie_title:
                auto_play_fshare(
                    title       = movie_title,
                    year        = params.get('year', ''),
                    imdb_id     = params.get('imdb', ''),
                    tmdb_id     = params.get('tmdb', ''),
                    season      = params.get('season') or None,
                    episode     = params.get('episode') or None,
                    tvshowtitle = params.get('tvshowtitle') or params.get('title', '') or None,
                    include     = params.get('include') or None,
                    exclude     = params.get('exclude') or None,
                    size_gb     = params.get('size_gb') or None,
                )
            else:
                xbmc.log("auto_play_fshare: missing title param", level=xbmc.LOGWARNING)
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())

        elif action == 'play_fshare_direct':
            play_fshare_direct(
                fshare_url  = urllib.parse.unquote_plus(params.get('url', '')),
                imdb_id     = params.get('imdb', ''),
                tmdb_id     = params.get('tmdb', ''),
                title       = params.get('title', ''),
                year        = params.get('year', ''),
                season      = params.get('season') or None,
                episode     = params.get('episode') or None,
                tvshowtitle = params.get('tvshowtitle') or None,
                filename    = params.get('filename', ''),
            )

        elif action == 'play_via_tmdb_helper':
            fshare_url = params.get('url', '')
            if fshare_url:
                play_via_tmdb_helper(
                    fshare_url  = fshare_url,
                    tmdb_id     = params.get('tmdb', ''),
                    imdb_id     = params.get('imdb', ''),
                    title       = params.get('title', ''),
                    year        = params.get('year', ''),
                    season      = params.get('season') or None,
                    episode     = params.get('episode') or None,
                    tvshowtitle = params.get('tvshowtitle') or None,
                )

    else:
        # Nếu không có tham số, hiển thị menu gốc
        main_menu()

if __name__ == '__main__':
    router(sys.argv[2][1:])
