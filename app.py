import os
import json
import time
import re
import threading
import logging
from flask import Flask, jsonify, request, send_from_directory, render_template
from flask_cors import CORS
import dropbox
from dropbox.exceptions import ApiError
from dropbox.files import FileMetadata, FolderMetadata

# ─── Config ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('photo-studio')

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

DROPBOX_ACCESS_TOKEN = os.environ.get('DROPBOX_ACCESS_TOKEN', '')
DROPBOX_REFRESH_TOKEN = os.environ.get('DROPBOX_REFRESH_TOKEN', '')
DROPBOX_APP_KEY = os.environ.get('DROPBOX_APP_KEY', '')
DROPBOX_APP_SECRET = os.environ.get('DROPBOX_APP_SECRET', '')
DROPBOX_BASE_PATH = os.environ.get('DROPBOX_BASE_PATH', '/Ecom_Photos')

# S3 URLs for regular inventory images (public, no auth needed)
S3_INVENTORY_URL = 'https://nauticaslimfit.s3.us-east-2.amazonaws.com/ALL+INVENTORY+Photos/PHOTOS+INVENTORY'
S3_OVERRIDE_URL = 'https://nauticaslimfit.s3.us-east-2.amazonaws.com/ALL+INVENTORY+Photos/STYLE+OVERRIDES'

# ─── Brand Mapping ────────────────────────────────────────────────────────────
# Maps Dropbox folder names → brand metadata
# This handles the inconsistent naming in Dropbox vs the standard brand_abbr system
FOLDER_BRAND_MAP = {
    'BEN SHERMAN':          {'abbr': 'BEN',    'prefix': 'BE', 'full': 'Ben Sherman'},
    'CHAPS':                {'abbr': 'CHAPS',  'prefix': 'CH', 'full': 'Chaps'},
    'DKNY':                 {'abbr': 'DKNY',   'prefix': 'DK', 'full': 'DKNY'},
    'EDDIE BAUER':          {'abbr': 'EB',     'prefix': 'EB', 'full': 'Eddie Bauer'},
    'GEOFFREY BEENE':       {'abbr': 'BEENE',  'prefix': 'GB', 'full': 'Geoffrey Beene'},
    'JNY':                  {'abbr': 'JNY',    'prefix': 'JN', 'full': 'Jones New York'},
    'LUCKY BRAND':          {'abbr': 'LUCKY',  'prefix': 'LB', 'full': 'Lucky Brand'},
    'NAUTICA':              {'abbr': 'NAUTICA','prefix': 'NA', 'full': 'Nautica'},
    'NICOLE MILLER  by andrea': {'abbr': 'NICOLE', 'prefix': 'NM', 'full': 'Nicole Miller'},
    'NICOLE MILLER by andrea':  {'abbr': 'NICOLE', 'prefix': 'NM', 'full': 'Nicole Miller'},
    'Nicolle Miller Trims': {'abbr': 'NICOLE', 'prefix': 'NM', 'full': 'Nicole Miller Trims'},
    'Nine West':            {'abbr': 'NW',     'prefix': 'NW', 'full': 'Nine West'},
    'REEBOK':               {'abbr': 'REEBOK', 'prefix': 'RB', 'full': 'Reebok'},
    'TAYON':                {'abbr': 'TAYION', 'prefix': 'TA', 'full': 'Tayion'},
    'US POLO':              {'abbr': 'USPA',   'prefix': 'US', 'full': 'U.S. Polo Assn.'},
    'VINCE CAMUTO':         {'abbr': 'VINCE',  'prefix': 'VC', 'full': 'Vince Camuto'},
    'Von Dutch':            {'abbr': 'VD',     'prefix': 'VD', 'full': 'Von Dutch'},
    'KARL LAGERFELD':       {'abbr': 'KL',     'prefix': 'KL', 'full': 'Karl Lagerfeld Paris'},
}

# S3 brand folder names (for inventory images) — matches inventory index pattern
S3_FOLDER_MAP = {
    'EB': 'EDDIE+BAUER', 'USPA': 'US+POLO', 'VINCE': 'VINCE+CAMUTO',
    'LUCKY': 'LUCKY+BRAND', 'BEN': 'BEN+SHERMAN', 'BEENE': 'GEOFFREY+BEENE',
    'NICOLE': 'NICOLE+MILLER', 'TAYION': 'TAYON', 'VD': 'Von+Dutch',
    'KL': 'KARL+LAGERFELD',
}

# Brand logos (same as inventory index)
BRAND_LOGOS = {
    'NAUTICA': 'https://versamens.com/wp-content/uploads/2025/07/nautica-logo-1-1-1024x576.png',
    'DKNY':    'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T210144.119-1024x576.png',
    'EB':      'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T141044.111-1-1024x576.png',
    'REEBOK':  'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-100-1-1024x576.png',
    'VINCE':   'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T140302.980-1-1024x576.png',
    'BEN':     'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T140546.875-1-1024x576.png',
    'USPA':    'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T141256.597-2-1024x576.png',
    'CHAPS':   'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T203105.646-1024x576.png',
    'LUCKY':   'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T142102.500-2-1024x576.png',
    'JNY':     'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T200647.521-1024x576.png',
    'BEENE':   'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T203625.911-1024x576.png',
    'NICOLE':  'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T203949.948-1024x576.png',
    'TAYION':  'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T202043.389-1024x576.png',
    'VD':      'https://versamens.com/wp-content/uploads/2025/02/Untitled-design-2025-02-03T205306.479-1024x576.png',
    'KL':      'https://nauticaslimfit.s3.us-east-2.amazonaws.com/ALL+INVENTORY+Photos/Brand+Logos/klp-wht-blue-back-1-1024x576.png',
}

# ─── State ────────────────────────────────────────────────────────────────────
photo_index = {}
scan_status = {
    'scanning': False,
    'last_scan': None,
    'progress': '',
    'brands': 0,
    'styles': 0,
    'images': 0,
    'error': None,
}
link_cache = {}       # dropbox_path → (temp_url, expiry_timestamp)
LINK_TTL = 3 * 3600   # 3 hours (Dropbox links last 4h)
scan_lock = threading.Lock()

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.bmp'}

# Folders inside Ecom_Photos that are NOT brands — skip during scan
IGNORE_FOLDERS = {
    'updated photos', 'overstock pictures', 'belk dropship photos',
    'old photos', 'archive', 'temp', 'test',
}

# ─── Dropbox Client ──────────────────────────────────────────────────────────
def get_dbx():
    """Create a Dropbox client, preferring refresh token for auto-renewal."""
    if DROPBOX_REFRESH_TOKEN and DROPBOX_APP_KEY and DROPBOX_APP_SECRET:
        return dropbox.Dropbox(
            oauth2_refresh_token=DROPBOX_REFRESH_TOKEN,
            app_key=DROPBOX_APP_KEY,
            app_secret=DROPBOX_APP_SECRET,
        )
    elif DROPBOX_ACCESS_TOKEN:
        return dropbox.Dropbox(DROPBOX_ACCESS_TOKEN)
    else:
        raise RuntimeError('No Dropbox credentials configured. Set DROPBOX_ACCESS_TOKEN or DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY + DROPBOX_APP_SECRET.')


# ─── Path Parsing ─────────────────────────────────────────────────────────────
STYLE_CODE_RE = re.compile(r'^([A-Z]{2})[-_](\d+(?:[-_]\d+)?)$', re.IGNORECASE)

def resolve_brand(folder_name):
    """Resolve a Dropbox folder name to brand metadata."""
    # Exact match
    if folder_name in FOLDER_BRAND_MAP:
        return FOLDER_BRAND_MAP[folder_name]
    # Case-insensitive match
    for k, v in FOLDER_BRAND_MAP.items():
        if k.upper() == folder_name.upper():
            return v
    # Partial match (folder name starts with known brand)
    for k, v in FOLDER_BRAND_MAP.items():
        if folder_name.upper().startswith(k.upper()):
            return v
    return None


def parse_image_entry(path_lower, path_display):
    """Parse a Dropbox file path into structured photo metadata.
    
    Expected patterns:
      /Ecom_Photos/BRAND/GHOST/PREFIX - 72dpi/STYLE_CODE/filename.jpg
      /Ecom_Photos/BRAND/MODEL/PREFIX - 72dpi/STYLE_CODE/filename.jpg
      /Ecom_Photos/BRAND/STYLE_CODE/filename.jpg  (no GHOST/MODEL split)
      /Ecom_Photos/BRAND/subfolder/.../filename.jpg (catch-all)
    """
    base = DROPBOX_BASE_PATH.lower().rstrip('/')
    rel = path_display
    # Get relative path after base
    idx = path_lower.find(base.lower())
    if idx < 0:
        return None
    rel = path_display[idx + len(base):].strip('/')
    parts = rel.split('/')
    
    if len(parts) < 2:
        return None
    
    brand_folder = parts[0]
    filename = parts[-1]
    name_no_ext = os.path.splitext(filename)[0].upper()
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in IMAGE_EXTS:
        return None
    
    brand_info = resolve_brand(brand_folder)
    
    # Skip non-brand folders
    if brand_folder.lower() in IGNORE_FOLDERS:
        return None
    
    # Determine photo type (ghost/model)
    photo_type = 'other'
    middle_parts = [p.upper() for p in parts[1:-1]]
    for mp in middle_parts:
        if 'GHOST' in mp:
            photo_type = 'ghost'
            break
        elif 'MODEL' in mp:
            photo_type = 'model'
            break
    
    # Determine DPI
    dpi = 72
    for mp in middle_parts:
        if '300' in mp and 'DPI' in mp.upper():
            dpi = 300
    
    # Find style code — look for folder matching XX_### pattern
    style_code = None
    for p in parts[1:-1]:
        m = STYLE_CODE_RE.match(p.replace(' ', ''))
        if m:
            style_code = p.upper().replace(' ', '')
            break
    
    # If no style folder found, try to extract from filename
    if not style_code:
        # e.g., BE_001_FRONT.jpg → style = BE_001
        # Try to find PREFIX_NUMBER pattern at start of filename
        fn_match = re.match(r'^([A-Z]{2}[-_]\d+(?:[-_]\d+)?)', name_no_ext)
        if fn_match:
            style_code = fn_match.group(1).replace('-', '_')
    
    if not style_code:
        return None
    
    # Normalize style code: ensure underscore separator
    style_code = style_code.replace('-', '_').upper()
    # Ensure consistent format: PREFIX_PADDED_NUMBER (pad to 3 digits like inventory index)
    sc_match = re.match(r'^([A-Z]{2})_(\d+(?:_\d+)?)$', style_code)
    if sc_match:
        prefix = sc_match.group(1)
        num_part = sc_match.group(2)
        # Only pad simple numbers (not compound like 470_001)
        if '_' not in num_part:
            num_part = num_part.zfill(3)  # pad to 3 digits: 1→001, 22→022, 140→140
        style_code = f"{prefix}_{num_part}"
    
    # Determine angle from filename
    angle = 'unknown'
    name_upper = name_no_ext.upper()
    for tag in ['FRONT3', 'FRONT2', 'FRONT', 'BACK2', 'BACK', 'SIDE2', 'SIDE',
                'DETAIL2', 'DETAIL', 'CLOSEUP', 'CLOSE', 'CLOSE_UP', 'FULL',
                'FLAT', 'COLLAR', 'CUFF', 'POCKET', 'INTERIOR', 'LABEL',
                'HANG', 'FOLD', 'LIFESTYLE', 'ANGLE', 'TOP', 'BOTTOM']:
        if tag in name_upper:
            angle = tag.lower()
            break
    # If still unknown, try to detect numbered variants (e.g., _01, _02 at end)
    if angle == 'unknown':
        num_suffix = re.search(r'_(\d{1,2})$', name_no_ext)
        if num_suffix:
            angle = f"view {num_suffix.group(1)}"
    
    return {
        'brand_folder': brand_folder,
        'brand_info': brand_info,
        'photo_type': photo_type,
        'dpi': dpi,
        'style_code': style_code,
        'angle': angle,
        'filename': filename,
        'path': path_display if path_display.startswith('/') else '/' + path_display,
        'path_lower': path_lower,
    }


# ─── Dropbox Scanner ─────────────────────────────────────────────────────────
def scan_dropbox():
    """Recursively scan the Ecom_Photos Dropbox folder and build the index."""
    global photo_index, scan_status
    
    if scan_status['scanning']:
        log.info('Scan already in progress, skipping.')
        return
    
    with scan_lock:
        scan_status['scanning'] = True
        scan_status['error'] = None
        scan_status['progress'] = 'Connecting to Dropbox...'
    
    try:
        dbx = get_dbx()
        log.info(f'Starting Dropbox scan at: {DROPBOX_BASE_PATH}')
        
        new_index = {}
        total_images = 0
        total_styles = set()
        
        # Recursive listing
        scan_status['progress'] = 'Scanning folders...'
        result = dbx.files_list_folder(DROPBOX_BASE_PATH, recursive=True)
        entries = list(result.entries)
        
        while result.has_more:
            result = dbx.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)
        
        log.info(f'Found {len(entries)} total entries in Dropbox')
        scan_status['progress'] = f'Processing {len(entries)} entries...'
        
        # Process only files (not folders)
        file_entries = [e for e in entries if isinstance(e, FileMetadata)]
        log.info(f'Found {len(file_entries)} files')
        
        for entry in file_entries:
            parsed = parse_image_entry(entry.path_lower, entry.path_display)
            if not parsed:
                continue
            
            brand_folder = parsed['brand_folder']
            brand_info = parsed['brand_info']
            style_code = parsed['style_code']
            photo_type = parsed['photo_type']
            
            # Initialize brand in index
            if brand_folder not in new_index:
                new_index[brand_folder] = {
                    'info': brand_info or {
                        'abbr': brand_folder[:4].upper(),
                        'prefix': brand_folder[:2].upper(),
                        'full': brand_folder,
                    },
                    'styles': {},
                }
            
            brand_data = new_index[brand_folder]
            
            # Initialize style
            if style_code not in brand_data['styles']:
                brand_data['styles'][style_code] = {
                    'ghost': [],
                    'model': [],
                    'other': [],
                }
                total_styles.add(style_code)
            
            style_data = brand_data['styles'][style_code]
            
            # Add image entry (prefer 72dpi for web display)
            img_entry = {
                'path': parsed['path'],
                'filename': parsed['filename'],
                'angle': parsed['angle'],
                'dpi': parsed['dpi'],
            }
            
            style_data[photo_type].append(img_entry)
            total_images += 1
        
        # Sort images within each style by angle for consistent display
        angle_order = {'front': 0, 'front2': 1, 'front3': 2, 'back': 3, 'side': 4, 'detail': 5, 'close': 6, 'full': 7, 'flat': 8, 'unknown': 9}
        for brand in new_index.values():
            for style in brand['styles'].values():
                for ptype in ['ghost', 'model', 'other']:
                    style[ptype].sort(key=lambda x: (x['dpi'], angle_order.get(x['angle'], 99)))
        
        # Deduplicate: if both 72dpi and 300dpi exist for same angle, keep 72dpi for display
        for brand in new_index.values():
            for style in brand['styles'].values():
                for ptype in ['ghost', 'model', 'other']:
                    seen_angles = {}
                    deduped = []
                    for img in style[ptype]:
                        key = (img['angle'], img['dpi'])
                        angle_key = img['angle']
                        if angle_key not in seen_angles:
                            seen_angles[angle_key] = img
                            deduped.append(img)
                        elif img['dpi'] < seen_angles[angle_key]['dpi']:
                            # Replace with lower DPI (faster loading)
                            deduped = [x for x in deduped if x['angle'] != angle_key]
                            deduped.append(img)
                            seen_angles[angle_key] = img
                        else:
                            # Keep the 300dpi as hi-res option
                            img['_hires'] = True
                            deduped.append(img)
                    style[ptype] = deduped
        
        photo_index = new_index
        
        with scan_lock:
            scan_status['scanning'] = False
            scan_status['last_scan'] = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())
            scan_status['brands'] = len(new_index)
            scan_status['styles'] = len(total_styles)
            scan_status['images'] = total_images
            scan_status['progress'] = 'Complete'
        
        log.info(f'Scan complete: {len(new_index)} brands, {len(total_styles)} styles, {total_images} images')
        
    except Exception as e:
        log.error(f'Scan error: {e}', exc_info=True)
        with scan_lock:
            scan_status['scanning'] = False
            scan_status['error'] = str(e)
            scan_status['progress'] = f'Error: {e}'


# ─── Temp Link Generation ────────────────────────────────────────────────────
def get_temp_link(dbx_path):
    """Get a temporary download link for a Dropbox file, with caching."""
    now = time.time()
    if dbx_path in link_cache:
        url, expiry = link_cache[dbx_path]
        if now < expiry:
            return url
    
    try:
        dbx = get_dbx()
        result = dbx.files_get_temporary_link(dbx_path)
        url = result.link
        link_cache[dbx_path] = (url, now + LINK_TTL)
        return url
    except Exception as e:
        log.error(f'Failed to get temp link for {dbx_path}: {e}')
        return None


def get_temp_links_batch(paths):
    """Get temporary links for multiple paths."""
    results = {}
    for p in paths:
        url = get_temp_link(p)
        if url:
            results[p] = url
    return results


# ─── API Routes ───────────────────────────────────────────────────────────────

@app.route('/')
def serve_frontend():
    return send_from_directory('templates', 'index.html')


@app.route('/api/status')
def api_status():
    return jsonify(scan_status)


@app.route('/api/scan', methods=['POST'])
def api_scan():
    """Trigger a Dropbox scan (runs in background thread)."""
    if scan_status['scanning']:
        return jsonify({'status': 'already_scanning', **scan_status})
    t = threading.Thread(target=scan_dropbox, daemon=True)
    t.start()
    return jsonify({'status': 'scan_started'})


@app.route('/api/browse')
def api_browse():
    """Browse Dropbox folders to discover the correct path. Usage: /api/browse?path=/"""
    browse_path = request.args.get('path', '').strip()
    if not browse_path:
        browse_path = ''  # root
    try:
        dbx = get_dbx()
        result = dbx.files_list_folder(browse_path if browse_path else '', recursive=False)
        items = []
        for entry in result.entries:
            items.append({
                'name': entry.name,
                'path': entry.path_display,
                'type': 'folder' if isinstance(entry, FolderMetadata) else 'file',
            })
        items.sort(key=lambda x: (x['type'] == 'file', x['name'].lower()))
        return jsonify({'path': browse_path or '/', 'items': items, 'count': len(items)})
    except Exception as e:
        return jsonify({'error': str(e), 'path': browse_path}), 400


@app.route('/api/index')
def api_index():
    """Return the full photo index (lightweight — paths only, no temp links)."""
    # Build a serializable summary
    summary = {}
    for brand_folder, brand_data in photo_index.items():
        info = brand_data['info']
        styles = {}
        for style_code, style_data in brand_data['styles'].items():
            styles[style_code] = {
                'ghost': len([i for i in style_data['ghost'] if not i.get('_hires')]),
                'model': len([i for i in style_data['model'] if not i.get('_hires')]),
                'other': len([i for i in style_data['other'] if not i.get('_hires')]),
                'total': sum(len(style_data[t]) for t in ['ghost', 'model', 'other']),
            }
        summary[brand_folder] = {
            'info': info,
            'logo': BRAND_LOGOS.get(info.get('abbr', ''), ''),
            'style_count': len(styles),
            'image_count': sum(s['total'] for s in styles.values()),
            'styles': styles,
        }
    return jsonify({
        'brands': summary,
        'scan_status': scan_status,
        's3_inventory_url': S3_INVENTORY_URL,
        's3_override_url': S3_OVERRIDE_URL,
        's3_folder_map': S3_FOLDER_MAP,
    })


@app.route('/api/images/<path:brand_folder>/<style_code>')
def api_style_images(brand_folder, style_code):
    """Get all images for a specific style with temporary download links."""
    style_code = style_code.upper()
    
    brand_data = photo_index.get(brand_folder)
    if not brand_data:
        # Try case-insensitive match
        for k, v in photo_index.items():
            if k.upper() == brand_folder.upper():
                brand_data = v
                brand_folder = k
                break
    
    if not brand_data:
        return jsonify({'error': 'Brand not found'}), 404
    
    style_data = brand_data['styles'].get(style_code)
    if not style_data:
        return jsonify({'error': 'Style not found'}), 404
    
    # Generate temp links for all images
    result = {'ghost': [], 'model': [], 'other': []}
    for ptype in ['ghost', 'model', 'other']:
        for img in style_data[ptype]:
            url = get_temp_link(img['path'])
            if url:
                result[ptype].append({
                    'url': url,
                    'filename': img['filename'],
                    'angle': img['angle'],
                    'dpi': img['dpi'],
                    'path': img['path'],
                })
    
    return jsonify({
        'brand': brand_data['info'],
        'style_code': style_code,
        'images': result,
    })


@app.route('/api/links', methods=['POST'])
def api_links():
    """Batch generate temporary links for a list of Dropbox paths."""
    data = request.get_json()
    paths = data.get('paths', [])
    if not paths:
        return jsonify({'error': 'No paths provided'}), 400
    
    # Limit batch size
    paths = paths[:50]
    links = get_temp_links_batch(paths)
    return jsonify({'links': links})


@app.route('/api/search')
def api_search():
    """Search the photo index. Returns matching styles with their brand info.
    
    Query parsing:
      - "NAUTICA" → all Nautica styles
      - "201" → any style with 201 in the number
      - "NA_201" or "NA 201" → specific style
      - "NA_201, BE_001" → multiple styles
      - "201, 205, 340" → multiple numbers across all brands
    """
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'results': []})
    
    results = []
    
    # Split by comma for multi-style queries
    terms = [t.strip().upper() for t in q.split(',') if t.strip()]
    
    for term in terms:
        # Normalize: replace spaces with underscores for style matching
        term_norm = term.replace(' ', '_').replace('-', '_')
        
        # Check if it's a brand name
        is_brand_search = False
        for brand_folder, brand_data in photo_index.items():
            info = brand_data['info']
            if (term.upper() == brand_folder.upper() or
                term.upper() == info.get('abbr', '').upper() or
                term.upper() == info.get('full', '').upper() or
                term.upper() == info.get('prefix', '').upper()):
                # Return all styles for this brand
                for style_code, style_data in brand_data['styles'].items():
                    results.append({
                        'brand_folder': brand_folder,
                        'brand_info': info,
                        'style_code': style_code,
                        'ghost_count': len([i for i in style_data['ghost'] if not i.get('_hires')]),
                        'model_count': len([i for i in style_data['model'] if not i.get('_hires')]),
                        'other_count': len([i for i in style_data['other'] if not i.get('_hires')]),
                        'logo': BRAND_LOGOS.get(info.get('abbr', ''), ''),
                    })
                is_brand_search = True
                break
        
        if is_brand_search:
            continue
        
        # Check if it's a full style code (e.g., NA_201, BE_001)
        style_match = re.match(r'^([A-Z]{2})[_\s]*(\d+(?:[_]\d+)?)$', term_norm)
        if style_match:
            prefix = style_match.group(1)
            number = style_match.group(2)
            # Search for exact or close match
            for brand_folder, brand_data in photo_index.items():
                for style_code, style_data in brand_data['styles'].items():
                    sc_parts = style_code.split('_', 1)
                    if len(sc_parts) == 2:
                        sc_prefix, sc_num = sc_parts
                        # Match prefix and number (with flexible zero-padding)
                        if sc_prefix == prefix and (sc_num == number or sc_num.lstrip('0') == number.lstrip('0') or sc_num == number.zfill(3)):
                            results.append({
                                'brand_folder': brand_folder,
                                'brand_info': brand_data['info'],
                                'style_code': style_code,
                                'ghost_count': len([i for i in style_data['ghost'] if not i.get('_hires')]),
                                'model_count': len([i for i in style_data['model'] if not i.get('_hires')]),
                                'other_count': len([i for i in style_data['other'] if not i.get('_hires')]),
                                'logo': BRAND_LOGOS.get(brand_data['info'].get('abbr', ''), ''),
                            })
            continue
        
        # It's just a number — search across all brands
        if term.isdigit():
            for brand_folder, brand_data in photo_index.items():
                for style_code, style_data in brand_data['styles'].items():
                    sc_parts = style_code.split('_', 1)
                    if len(sc_parts) == 2:
                        sc_num = sc_parts[1]
                        if sc_num == term or sc_num.lstrip('0') == term.lstrip('0') or sc_num == term.zfill(3):
                            results.append({
                                'brand_folder': brand_folder,
                                'brand_info': brand_data['info'],
                                'style_code': style_code,
                                'ghost_count': len([i for i in style_data['ghost'] if not i.get('_hires')]),
                                'model_count': len([i for i in style_data['model'] if not i.get('_hires')]),
                                'other_count': len([i for i in style_data['other'] if not i.get('_hires')]),
                                'logo': BRAND_LOGOS.get(brand_data['info'].get('abbr', ''), ''),
                            })
            continue
        
        # Fuzzy: search style codes that contain the term
        for brand_folder, brand_data in photo_index.items():
            for style_code, style_data in brand_data['styles'].items():
                if term_norm in style_code or term in style_code:
                    results.append({
                        'brand_folder': brand_folder,
                        'brand_info': brand_data['info'],
                        'style_code': style_code,
                        'ghost_count': len([i for i in style_data['ghost'] if not i.get('_hires')]),
                        'model_count': len([i for i in style_data['model'] if not i.get('_hires')]),
                        'other_count': len([i for i in style_data['other'] if not i.get('_hires')]),
                        'logo': BRAND_LOGOS.get(brand_data['info'].get('abbr', ''), ''),
                    })
    
    # Deduplicate by style_code + brand
    seen = set()
    deduped = []
    for r in results:
        key = f"{r['brand_folder']}:{r['style_code']}"
        if key not in seen:
            seen.add(key)
            deduped.append(r)
    
    # Sort: by brand then style code
    deduped.sort(key=lambda x: (x['brand_info'].get('full', ''), x['style_code']))
    
    return jsonify({'results': deduped, 'query': q, 'count': len(deduped)})


# ─── Startup ──────────────────────────────────────────────────────────────────
def startup_scan():
    """Auto-scan on startup after a short delay."""
    time.sleep(3)
    if DROPBOX_ACCESS_TOKEN or DROPBOX_REFRESH_TOKEN:
        log.info('Starting initial Dropbox scan...')
        scan_dropbox()
    else:
        log.warning('No Dropbox credentials — skipping auto-scan.')

# Start background scan on startup
threading.Thread(target=startup_scan, daemon=True).start()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
