import os
import io
import json
import time
import re
import base64 as b64mod
import uuid
import threading
import logging
import zipfile
import urllib.request
from flask import Flask, jsonify, request, send_from_directory, render_template, Response
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
INVENTORY_API_URL = os.environ.get('INVENTORY_API_URL', 'https://versa-inventory-api.onrender.com')

# S3 URLs for regular inventory images (public, no auth needed)
S3_INVENTORY_URL = 'https://nauticaslimfit.s3.us-east-2.amazonaws.com/ALL+INVENTORY+Photos/PHOTOS+INVENTORY'
S3_OVERRIDE_URL = 'https://nauticaslimfit.s3.us-east-2.amazonaws.com/ALL+INVENTORY+Photos/STYLE+OVERRIDES'

# S3 extra folders to pull directly into the photo index
# Bucket listing is restricted (403), so we define known styles and probe for files.
S3_EXTRA_FOLDERS = [
    {
        'base_url': 'https://nauticaslimfit.s3.us-east-2.amazonaws.com/ALL+INVENTORY+Photos/PHOTOS+INVENTORY/Von+Dutch/Von+Dutch+Jewelry',
        'brand_abbr': 'VD',
        'brand_full': 'Von Dutch',
        'brand_folder': 'Von Dutch',
        'photo_type': 'other',
        # Known style numbers in this folder (from POs / known inventory)
        'styles': [
            'SSVDNP001C', 'SSVDNP002C', 'SSVDNP009R',
            'BVDNP003C', 'BVDNP004C', 'BVDNP005C', 'BVDNP006R',
            'BVDNP007R', 'BVDNP008R', 'BVDNP010BC', 'BVDNP010BLC',
            'BVDNP011BLC', 'BVDNP012BLC',
        ],
        'max_variants': 6,  # probe -1.jpg through -6.jpg per style
    },
]
S3_BUCKET_URL = 'https://nauticaslimfit.s3.us-east-2.amazonaws.com'

# SKU brand code → image prefix (same as inventory index extractImageCode)
BRAND_IMAGE_PREFIX = {
    "NAUTICA": "NA", "DKNY": "DK", "EB": "EB", "REEBOK": "RB", "VINCE": "VC",
    "BEN": "BE", "USPA": "US", "CHAPS": "CH", "LUCKY": "LB", "JNY": "JN",
    "BEENE": "GB", "NICOLE": "NM", "SHAQ": "SH", "TAYION": "TA", "STRAHAN": "MS",
    "VD": "VD", "VERSA": "VR", "CHEROKEE": "CK", "AMERICA": "AC", "BLO": "BL",
    "DN": "D9", "KL": "KL", "NE": "NE"
}

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
    'scanning': True,   # Start True so frontend polls on cold start
    'last_scan': None,
    'progress': 'Starting up...',
    'brands': 0,
    'styles': 0,
    'images': 0,
    'error': None,
}
link_cache = {}       # dropbox_path → (temp_url, expiry_timestamp)
LINK_TTL = 3 * 3600   # 3 hours (Dropbox links last 4h)
scan_lock = threading.Lock()
_scan_running = False  # Internal flag: is a scan thread actively running?

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.tif', '.tiff', '.webp', '.bmp'}

# Folders inside Ecom_Photos that are NOT brands — skip during scan
IGNORE_FOLDERS = {
    'updated photos', 'overstock pictures', 'belk dropship photos',
    'old photos', 'archive', 'temp', 'test',
}
# Also ignore folders that start with these prefixes (retailer-specific shoots)
IGNORE_PREFIXES = ('kohls ', 'macys ', 'nordstrom', 'walmart', 'bjs ')

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
    if brand_folder.lower().startswith(IGNORE_PREFIXES):
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
        if '300' in mp and 'DPI' in mp:
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
    # Extract ONLY prefix + first number group, pad to 3 digits
    # NA_201_10 → NA_201, BE_22_140 → BE_022, NA_207_4 → NA_207
    sc_match = re.match(r'^([A-Z]{2})[_](\d+)', style_code)
    if sc_match:
        prefix = sc_match.group(1)
        num_part = sc_match.group(2).zfill(3)
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
    global photo_index, scan_status, _scan_running
    
    if _scan_running:
        log.info('Scan already in progress, skipping.')
        return
    
    with scan_lock:
        _scan_running = True
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
            _scan_running = False
        
        log.info(f'Scan complete: {len(new_index)} brands, {len(total_styles)} styles, {total_images} images')
        
        # Merge inventory styles (in its own try/catch so it can't crash the scan)
        try:
            scan_status['progress'] = 'Merging inventory styles...'
            load_inventory_styles()
        except Exception as e:
            log.warning(f'Inventory merge failed (non-fatal): {e}')
        
        # Load S3 extra folders (e.g. Von Dutch Jewelry)
        try:
            scan_status['progress'] = 'Loading S3 extra folders...'
            _load_s3_extra_folders()
        except Exception as e:
            log.warning(f'S3 extra folders load failed (non-fatal): {e}')
        
        # Update counts after merge
        with scan_lock:
            scan_status['brands'] = len(photo_index)
            scan_status['styles'] = sum(len(b['styles']) for b in photo_index.values())
            scan_status['images'] = sum(
                sum(len(s[t]) for t in ['ghost', 'model', 'other'])
                for b in photo_index.values() for s in b['styles'].values()
            )
            scan_status['progress'] = 'Complete'
        
    except Exception as e:
        log.error(f'Scan error: {e}', exc_info=True)
        with scan_lock:
            scan_status['scanning'] = False
            scan_status['error'] = str(e)
            scan_status['progress'] = f'Error: {e}'
            _scan_running = False


# ─── Inventory Merge ──────────────────────────────────────────────────────────
def extract_image_code(sku, brand_abbr):
    """Convert a Versa SKU to an image code (same logic as inventory index)."""
    prefix = BRAND_IMAGE_PREFIX.get(brand_abbr, (brand_abbr or '')[:2])
    base_sku = sku.split('-')[0]
    numbers = re.findall(r'\d+', base_sku)
    if numbers:
        main_number = max(numbers, key=len)
        padded = main_number.zfill(3)
        return f"{prefix}_{padded}"
    return f"{prefix}_{base_sku}"


def load_inventory_styles():
    """Fetch inventory data to enrich existing photo index entries with brand names.
    
    Does NOT add new styles — only styles with actual photos (from Dropbox scan
    or /dropbox-photos) should appear. This just updates brand display names.
    """
    global photo_index
    if not INVENTORY_API_URL:
        return
    
    try:
        log.info(f'Fetching inventory data from {INVENTORY_API_URL}/inventory ...')
        req = urllib.request.Request(
            f'{INVENTORY_API_URL}/inventory',
            headers={'User-Agent': 'VersaPhotoStudio/1.0'}
        )
        resp = urllib.request.urlopen(req, timeout=30)
        raw = json.loads(resp.read())
        
        enriched = 0
        for brand_abbr, brand_data in raw.items():
            if not isinstance(brand_data, dict):
                continue
            
            full_name = brand_data.get('full_name', brand_abbr)
            
            # Find matching brand folder — only update, don't create
            for folder, bdata in photo_index.items():
                if bdata['info'].get('abbr', '').upper() == brand_abbr.upper():
                    if bdata['info'].get('full', '') == folder:
                        bdata['info']['full'] = full_name
                        enriched += 1
                    break
        
        log.info(f'Enriched {enriched} brand names from inventory API')
    
    except Exception as e:
        log.warning(f'Failed to fetch inventory styles: {e}')
    
    # Also fetch all image codes from the Dropbox-synced PHOTOS INVENTORY
    _load_dropbox_photo_codes()


# Reverse lookup: image prefix → brand abbr
PREFIX_TO_BRAND = {v: k for k, v in BRAND_IMAGE_PREFIX.items()}

def _load_dropbox_photo_codes():
    """Fetch image codes from inventory API's /dropbox-photos endpoint.
    
    These are all styles that have regular product photos in:
    Versa Share Files / PHOTOS INVENTORY (synced to S3).
    """
    global photo_index
    try:
        log.info(f'Fetching dropbox photo codes from {INVENTORY_API_URL}/dropbox-photos ...')
        req = urllib.request.Request(
            f'{INVENTORY_API_URL}/dropbox-photos',
            headers={'User-Agent': 'VersaPhotoStudio/1.0'}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        
        codes = data.get('codes', [])
        if not codes:
            log.info('No dropbox photo codes returned')
            return
        
        added = 0
        for code in codes:
            # Code format: "NA_001", "US_130", "BE_074"
            code = code.upper().strip()
            parts = code.split('_', 1)
            if len(parts) != 2:
                continue
            
            img_prefix, num = parts[0], parts[1]
            brand_abbr = PREFIX_TO_BRAND.get(img_prefix)
            if not brand_abbr:
                continue
            
            # Normalize: pad number to 3 digits
            style_code = f"{img_prefix}_{num.zfill(3)}"
            
            # Find matching brand folder
            brand_folder = None
            for folder, bdata in photo_index.items():
                if bdata['info'].get('abbr', '').upper() == brand_abbr.upper():
                    brand_folder = folder
                    break
            
            if not brand_folder:
                # Create brand entry
                brand_folder = brand_abbr
                photo_index[brand_folder] = {
                    'info': {
                        'abbr': brand_abbr,
                        'prefix': img_prefix,
                        'full': brand_abbr,
                    },
                    'styles': {},
                }
            
            if style_code not in photo_index[brand_folder]['styles']:
                photo_index[brand_folder]['styles'][style_code] = {
                    'ghost': [], 'model': [], 'other': []
                }
                added += 1
        
        log.info(f'Added {added} styles from dropbox photo codes '
                 f'(total styles now: {sum(len(b["styles"]) for b in photo_index.values())})')
    
    except Exception as e:
        log.warning(f'Failed to fetch dropbox photo codes: {e}')


# ─── S3 Extra Folder Loader ─────────────────────────────────────────────────
def _load_s3_extra_folders():
    """Load images from S3 extra folders (e.g. Von Dutch Jewelry) into the photo index.
    
    Bucket listing is restricted, so we probe known style numbers with HEAD
    requests to discover which image files actually exist.
    """
    global photo_index

    for folder_cfg in S3_EXTRA_FOLDERS:
        base_url = folder_cfg['base_url'].rstrip('/')
        brand_abbr = folder_cfg['brand_abbr']
        brand_full = folder_cfg['brand_full']
        brand_folder_name = folder_cfg['brand_folder']
        photo_type = folder_cfg.get('photo_type', 'other')
        known_styles = folder_cfg.get('styles', [])
        max_variants = folder_cfg.get('max_variants', 6)

        if not known_styles:
            continue

        try:
            log.info(f'S3 extra: probing {len(known_styles)} styles at {base_url}')

            # Ensure brand exists in the index
            target_folder = None
            for folder, bdata in photo_index.items():
                if bdata['info'].get('abbr', '').upper() == brand_abbr.upper():
                    target_folder = folder
                    break

            if not target_folder:
                target_folder = brand_folder_name
                photo_index[target_folder] = {
                    'info': {
                        'abbr': brand_abbr,
                        'prefix': BRAND_IMAGE_PREFIX.get(brand_abbr, brand_abbr[:2]),
                        'full': brand_full,
                    },
                    'styles': {},
                }

            brand_data = photo_index[target_folder]
            added_styles = 0
            added_images = 0

            for style in known_styles:
                style_upper = style.upper().strip()
                style_images = []

                # Probe: STYLE-1.jpg, STYLE-2.jpg, ... and also STYLE.jpg
                urls_to_try = []
                # Try with variant suffixes first
                for v in range(1, max_variants + 1):
                    urls_to_try.append((f'{base_url}/{style}-{v}.jpg', str(v)))
                # Also try without suffix
                urls_to_try.append((f'{base_url}/{style}.jpg', None))

                for url, variant in urls_to_try:
                    try:
                        req = urllib.request.Request(url, method='HEAD',
                                                     headers={'User-Agent': 'VersaPhotoStudio/1.0'})
                        resp = urllib.request.urlopen(req, timeout=5)
                        if resp.status == 200:
                            filename = os.path.basename(url)
                            if variant:
                                angle_map = {'1': 'front', '2': 'back', '3': 'side',
                                             '4': 'detail', '5': 'detail2', '6': 'detail3'}
                                angle = angle_map.get(variant, f'view {variant}')
                            else:
                                angle = 'front'
                            style_images.append({
                                'path': f's3://{style_upper}/{filename}',
                                'filename': filename,
                                'angle': angle,
                                'dpi': 72,
                                's3_url': url,
                            })
                    except urllib.error.HTTPError as e:
                        if e.code == 404:
                            continue  # file doesn't exist, expected
                        log.warning(f'S3 probe error for {url}: {e}')
                    except Exception:
                        continue  # timeout or other error, skip

                if style_images:
                    if style_upper not in brand_data['styles']:
                        brand_data['styles'][style_upper] = {
                            'ghost': [], 'model': [], 'other': []
                        }
                        added_styles += 1
                    brand_data['styles'][style_upper][photo_type].extend(style_images)
                    added_images += len(style_images)
                    log.info(f'  {style_upper}: found {len(style_images)} images')

            log.info(f'S3 extra probe done: {added_styles} styles, {added_images} images for {brand_full}')

        except Exception as e:
            log.error(f'S3 extra folder load FAILED: {e}', exc_info=True)


# ─── Temp Link Generation ────────────────────────────────────────────────────
_dbx_client = None
_dbx_client_lock = threading.Lock()

def get_dbx_cached():
    """Get a cached Dropbox client (avoids re-creating on every API call)."""
    global _dbx_client
    with _dbx_client_lock:
        if _dbx_client is None:
            _dbx_client = get_dbx()
        return _dbx_client

def reset_dbx_client():
    """Reset cached client (e.g., after auth error)."""
    global _dbx_client
    with _dbx_client_lock:
        _dbx_client = None

def get_temp_link(dbx_path):
    """Get a temporary download link for a Dropbox file, with caching."""
    now = time.time()
    if dbx_path in link_cache:
        url, expiry = link_cache[dbx_path]
        if now < expiry:
            return url
    
    # Try with cached client first, retry with fresh client on any auth/connection error
    for attempt in range(2):
        try:
            dbx = get_dbx_cached()
            result = dbx.files_get_temporary_link(dbx_path)
            url = result.link
            link_cache[dbx_path] = (url, now + LINK_TTL)
            return url
        except (ApiError, Exception) as e:
            err_str = str(e).lower()
            if attempt == 0 and ('expired' in err_str or 'auth' in err_str or 'token' in err_str or 'invalid' in err_str or isinstance(e, ConnectionError)):
                log.warning(f'Dropbox auth/connection error, refreshing client: {e}')
                reset_dbx_client()
                continue
            log.error(f'Failed to get temp link for {dbx_path}: {e}')
            return None
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


@app.route('/api/s3test')
def api_debug_s3():
    """Diagnostic: test S3 probing for Von Dutch Jewelry."""
    results = {'steps': [], 'probes': []}
    
    for folder_cfg in S3_EXTRA_FOLDERS:
        base_url = folder_cfg['base_url'].rstrip('/')
        styles = folder_cfg.get('styles', [])[:3]  # test first 3 styles only
        
        results['base_url'] = base_url
        results['total_known_styles'] = len(folder_cfg.get('styles', []))
        
        for style in styles:
            # Try STYLE-1.jpg
            url = f'{base_url}/{style}-1.jpg'
            try:
                req = urllib.request.Request(url, method='HEAD',
                                             headers={'User-Agent': 'VersaPhotoStudio/1.0'})
                resp = urllib.request.urlopen(req, timeout=5)
                results['probes'].append({
                    'style': style, 'url': url,
                    'status': resp.status, 'found': True
                })
            except urllib.error.HTTPError as e:
                results['probes'].append({
                    'style': style, 'url': url,
                    'status': e.code, 'found': False
                })
            except Exception as e:
                results['probes'].append({
                    'style': style, 'url': url,
                    'error': str(e), 'found': False
                })
    
    # Check what's in the Von Dutch index
    for folder, bdata in photo_index.items():
        if bdata['info'].get('abbr', '').upper() == 'VD':
            s3_styles = {}
            for sc, sd in bdata['styles'].items():
                s3_imgs = [img for img in sd.get('other', []) if img.get('s3_url')]
                if s3_imgs:
                    s3_styles[sc] = [img['s3_url'] for img in s3_imgs]
            results['von_dutch_index'] = {
                'folder': folder,
                'total_styles': len(bdata['styles']),
                's3_jewelry_styles': s3_styles,
            }
    
    return jsonify(results)


@app.route('/api/scan', methods=['POST'])
def api_scan():
    """Trigger a Dropbox scan (runs in background thread)."""
    if _scan_running:
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
    """Get all images for a specific style. Use ?links=false to skip Dropbox temp links (fast)."""
    style_code = style_code.upper()
    skip_links = request.args.get('links', 'true').lower() == 'false'
    
    brand_data = photo_index.get(brand_folder)
    if not brand_data:
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
    
    result = {'ghost': [], 'model': [], 'other': []}
    for ptype in ['ghost', 'model', 'other']:
        for img in style_data[ptype]:
            if skip_links:
                # Fast mode: return paths only, no Dropbox API calls
                result[ptype].append({
                    'url': img.get('s3_url', ''),  # S3 images have direct URL even in fast mode
                    'filename': img['filename'],
                    'angle': img['angle'],
                    'dpi': img['dpi'],
                    'path': img['path'],
                })
            elif img.get('s3_url'):
                # S3-sourced image — use direct public URL, no Dropbox needed
                result[ptype].append({
                    'url': img['s3_url'],
                    'filename': img['filename'],
                    'angle': img['angle'],
                    'dpi': img['dpi'],
                    'path': img['path'],
                })
            else:
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
    
    # Separate S3 paths (already have public URLs) from Dropbox paths
    links = {}
    dbx_paths = []
    for p in paths:
        if p.startswith('s3://'):
            # Look up the direct URL from the photo index
            for brand_data in photo_index.values():
                found = False
                for style_data in brand_data['styles'].values():
                    for ptype in ['ghost', 'model', 'other']:
                        for img in style_data[ptype]:
                            if img['path'] == p and img.get('s3_url'):
                                links[p] = img['s3_url']
                                found = True
                                break
                        if found:
                            break
                    if found:
                        break
                if found:
                    break
        else:
            dbx_paths.append(p)
    
    # Get Dropbox temp links for non-S3 paths
    if dbx_paths:
        links.update(get_temp_links_batch(dbx_paths))
    return jsonify({'links': links})


def parse_versa_sku(term):
    """Try to parse a full Versa SKU into a style code.
    
    SKU format: [0:2]=customer, [2:4]=brand, [4:6]=fabric, [6:9]=style#, [9:11]=fit, [11:12]=collar
    Example: AMNASU201SLS → brand=NA, style=201 → NA_201
             WLBE001SLBD → brand=BE, style=001 → BE_001
    
    Also handles SKUs with size suffix: AMNASU201SLS-1534 → NA_201
    """
    # Strip size suffix
    base = term.split('-')[0].strip().upper()
    
    # Must be at least 9 chars (2 customer + 2 brand + 2 fabric + 3 style)
    # Full SKU is 12 chars but some may be truncated
    if len(base) < 9:
        return None
    
    # Check if it looks like a SKU (letters + digits pattern)
    # Positions 6-8 must be digits (style number)
    if not base[6:9].isdigit():
        return None
    
    # Positions 2-3 must be a known brand prefix
    brand_prefix = base[2:4]
    known_prefixes = {'NA', 'DK', 'EB', 'RB', 'VC', 'BE', 'US', 'CH', 'LB', 
                      'JN', 'GB', 'NM', 'SH', 'TA', 'MS', 'VD', 'KL', 'CK', 'NW'}
    if brand_prefix not in known_prefixes:
        return None
    
    style_num = base[6:9]
    return f"{brand_prefix}_{style_num}"


@app.route('/api/search')
def api_search():
    """Search the photo index. Returns matching styles with their brand info.
    
    Query parsing:
      - "NAUTICA" → all Nautica styles
      - "201" → any style with 201 in the number
      - "NA_201" or "NA 201" → specific style
      - "NA_201, BE_001" → multiple styles
      - "201, 205, 340" → multiple numbers across all brands
      - "AMNASU201SLS" → full Versa SKU → NA_201
    """
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'results': []})
    
    results = []
    
    # Split by comma, newline, tab, or multiple spaces for multi-style queries
    raw_terms = re.split(r'[,\n\t]+|\s{2,}', q)
    
    # If we got a single term with spaces, check if it's space-separated numbers/codes
    # "201 207" → ["201", "207"], "NA 201 BE 001" → ["NA_201", "BE_001"]
    # but "Ben Sherman" stays as one term (brand name)
    if len(raw_terms) == 1 and ' ' in raw_terms[0].strip():
        parts = raw_terms[0].strip().split()
        reassembled = []
        i = 0
        is_list = True
        while i < len(parts):
            p = parts[i].upper()
            # Two-letter prefix + number in next part: "NA 201" → "NA_201"
            if len(p) == 2 and p.isalpha() and i + 1 < len(parts) and parts[i+1].isdigit():
                reassembled.append(f"{p}_{parts[i+1]}")
                i += 2
                continue
            if p.isdigit():
                reassembled.append(p)
                i += 1
                continue
            if re.match(r'^[A-Z]{2}[_]\d+$', p):
                reassembled.append(p)
                i += 1
                continue
            sku = parse_versa_sku(p)
            if sku:
                reassembled.append(p)
                i += 1
                continue
            # Doesn't look like a code — probably a brand name
            is_list = False
            break
        if is_list and reassembled:
            raw_terms = reassembled
    
    terms = [t.strip().upper() for t in raw_terms if t.strip()]
    
    # Pre-process: convert full Versa SKUs to style codes
    processed_terms = []
    for term in terms:
        converted = parse_versa_sku(term)
        if converted:
            processed_terms.append(converted)
        else:
            processed_terms.append(term)
    
    for term in processed_terms:
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


@app.route('/api/download-zip', methods=['POST'])
def api_download_zip():
    """Generate a ZIP file from selected Dropbox image paths.
    
    Expects JSON body:
    {
        "items": [
            {
                "style_code": "NA_201",
                "brand": "Nautica",
                "folder_name": "Custom Folder Name",  (optional)
                "paths": ["/path/to/img1.jpg", ...],
                "filenames": {"/path/to/img1.jpg": "Custom_Name.jpg"}  (optional)
            }
        ],
        "zip_name": "My_Export.zip"  (optional)
    }
    """
    data = request.get_json()
    items = data.get('items', [])
    custom_zip_name = data.get('zip_name', '')
    if not items:
        return jsonify({'error': 'No items provided'}), 400
    
    # Limit total images
    total_paths = sum(len(item.get('paths', [])) for item in items)
    if total_paths > 200:
        return jsonify({'error': f'Too many images ({total_paths}). Max 200 per download.'}), 400
    
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for item in items:
                style_code = item.get('style_code', 'unknown')
                brand = item.get('brand', '')
                paths = item.get('paths', [])
                urls = item.get('urls', {})       # path → direct URL for non-Dropbox images
                base64s = item.get('base64', {})   # path → data:image/... for override images
                custom_filenames = item.get('filenames', {})
                
                folder_name = item.get('folder_name', '').strip()
                if not folder_name:
                    folder_name = f"{brand} - {style_code}" if brand else style_code
                folder_name = re.sub(r'[<>:"/\\|?*]', '_', folder_name)
                
                for idx, dbx_path in enumerate(paths):
                    filename = custom_filenames.get(dbx_path, os.path.basename(dbx_path))
                    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
                    
                    try:
                        img_data = None
                        
                        # Base64 override image
                        if dbx_path.startswith('override://') and dbx_path in base64s:
                            b64 = base64s[dbx_path]
                            if ',' in b64:
                                b64 = b64.split(',', 1)[1]
                            img_data = b64mod.b64decode(b64)
                        
                        # S3 direct URL
                        elif dbx_path.startswith('s3://') and dbx_path in urls:
                            req = urllib.request.Request(urls[dbx_path], headers={'User-Agent': 'VersaPhotoStudio/1.0'})
                            resp = urllib.request.urlopen(req, timeout=15)
                            img_data = resp.read()
                        
                        # Regular Dropbox path
                        else:
                            url = get_temp_link(dbx_path)
                            if not url:
                                continue
                            req = urllib.request.Request(url, headers={'User-Agent': 'VersaPhotoStudio/1.0'})
                            resp = urllib.request.urlopen(req, timeout=15)
                            img_data = resp.read()
                        
                        if img_data:
                            zf.writestr(f"{folder_name}/{filename}", img_data)
                            if (idx + 1) % 10 == 0:
                                log.info(f'ZIP progress: {idx + 1}/{len(paths)} images for {style_code}')
                    except Exception as e:
                        log.warning(f'Failed to download {dbx_path}: {e}')
                        continue
        
        buf.seek(0)
        
        # Generate filename
        if custom_zip_name:
            zip_name = custom_zip_name if custom_zip_name.endswith('.zip') else custom_zip_name + '.zip'
        elif len(items) == 1:
            zip_name = f"{items[0].get('folder_name', items[0].get('style_code', 'photos'))}.zip"
        else:
            zip_name = f"Versa_Photos_{len(items)}_styles.zip"
        zip_name = re.sub(r'[<>:"/\\|?*]', '_', zip_name)
        
        return Response(
            buf.getvalue(),
            mimetype='application/zip',
            headers={'Content-Disposition': f'attachment; filename="{zip_name}"'}
        )
    
    except Exception as e:
        log.error(f'ZIP generation error: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


# ─── ZIP Jobs with Progress ──────────────────────────────────────────────────
zip_jobs = {}  # job_id → {status, progress, total, buf, error, zip_name}

@app.route('/api/zip/start', methods=['POST'])
def api_zip_start():
    """Start a background ZIP job. Returns job_id for progress polling."""
    data = request.get_json()
    items = data.get('items', [])
    custom_zip_name = data.get('zip_name', '')
    if not items:
        return jsonify({'error': 'No items'}), 400
    
    total_paths = sum(len(item.get('paths', [])) for item in items)
    if total_paths > 200:
        return jsonify({'error': f'Too many images ({total_paths}). Max 200.'}), 400
    
    job_id = uuid.uuid4().hex[:12]
    zip_jobs[job_id] = {
        'status': 'starting',
        'progress': 0,
        'total': total_paths,
        'buf': None,
        'error': None,
        'zip_name': '',
    }
    
    # Determine zip name
    if custom_zip_name:
        zn = custom_zip_name
    elif len(items) == 1:
        fn = items[0].get('folder_name', '').strip()
        zn = (fn or items[0].get('style_code', 'photos')) + '.zip'
    else:
        zn = f"Versa_Photos_{len(items)}_styles.zip"
    zip_jobs[job_id]['zip_name'] = re.sub(r'[<>:"/\\|?*]', '_', zn)
    
    threading.Thread(target=_build_zip, args=(job_id, items), daemon=True).start()
    return jsonify({'job_id': job_id, 'total': total_paths})


def _build_zip(job_id, items):
    """Background thread: build ZIP with progress updates."""
    job = zip_jobs[job_id]
    job['status'] = 'downloading'
    done = 0
    
    try:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for item in items:
                style_code = item.get('style_code', 'unknown')
                brand = item.get('brand', '')
                paths = item.get('paths', [])
                urls_map = item.get('urls', {})
                base64s = item.get('base64', {})
                custom_filenames = item.get('filenames', {})
                
                folder_name = item.get('folder_name', '').strip()
                if not folder_name:
                    folder_name = f"{brand} - {style_code}" if brand else style_code
                folder_name = re.sub(r'[<>:"/\\|?*]', '_', folder_name)
                
                for dbx_path in paths:
                    filename = custom_filenames.get(dbx_path, os.path.basename(dbx_path))
                    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
                    
                    try:
                        img_data = None
                        if dbx_path.startswith('override://') and dbx_path in base64s:
                            b64 = base64s[dbx_path]
                            if ',' in b64:
                                b64 = b64.split(',', 1)[1]
                            img_data = b64mod.b64decode(b64)
                        elif dbx_path.startswith('s3://') and dbx_path in urls_map:
                            req = urllib.request.Request(urls_map[dbx_path], headers={'User-Agent': 'VersaPhotoStudio/1.0'})
                            resp = urllib.request.urlopen(req, timeout=15)
                            img_data = resp.read()
                        else:
                            url = get_temp_link(dbx_path)
                            if not url:
                                done += 1
                                job['progress'] = done
                                continue
                            req = urllib.request.Request(url, headers={'User-Agent': 'VersaPhotoStudio/1.0'})
                            resp = urllib.request.urlopen(req, timeout=15)
                            img_data = resp.read()
                        
                        if img_data:
                            zf.writestr(f"{folder_name}/{filename}", img_data)
                    except Exception as e:
                        log.warning(f'ZIP job {job_id}: failed {dbx_path}: {e}')
                    
                    done += 1
                    job['progress'] = done
        
        job['buf'] = buf
        job['status'] = 'done'
    except Exception as e:
        log.error(f'ZIP job {job_id} error: {e}', exc_info=True)
        job['status'] = 'error'
        job['error'] = str(e)


@app.route('/api/zip/status/<job_id>')
def api_zip_status(job_id):
    job = zip_jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify({
        'status': job['status'],
        'progress': job['progress'],
        'total': job['total'],
        'error': job['error'],
        'zip_name': job['zip_name'],
    })


@app.route('/api/zip/download/<job_id>')
def api_zip_download(job_id):
    job = zip_jobs.get(job_id)
    if not job or job['status'] != 'done' or not job['buf']:
        return jsonify({'error': 'ZIP not ready'}), 404
    
    buf = job['buf']
    zn = job['zip_name']
    # Clean up job after download
    del zip_jobs[job_id]
    
    return Response(
        buf.getvalue(),
        mimetype='application/zip',
        headers={'Content-Disposition': f'attachment; filename="{zn}"'}
    )


# ─── AI Assistant ─────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
AI_MODEL = 'claude-sonnet-4-20250514'

AI_SYSTEM_PROMPT = """You are the Versa Photo Studio AI assistant. You help users manage professional product photography for Versa Group's men's apparel brands.

You can:
1. Search for styles by brand, number, or SKU
2. Analyze product images to identify what they show (front/back/side view, model/ghost shot, close-up, etc.)
3. Rename images in the user's cart based on what you see in them
4. Answer questions about the platform

When asked to rename images, use the rename_images tool. Analyze each image carefully — look at the angle, whether it shows a model or flat/ghost garment, and describe it with clear names like "Front", "Back", "Side", "Detail", "Close-up", etc.

When multiple images of the same type exist (e.g., two front views), number them: "Front 1", "Front 2".

For model shots, identify the pose: is the model facing forward (Front), turned sideways (Side), or showing the back (Back)?
For ghost/flat shots: identify if it's the front of the garment, back, a detail shot, collar, cuffs, etc.

Keep your responses concise and helpful."""

@app.route('/api/ai/chat', methods=['POST'])
def api_ai_chat():
    """AI chat endpoint — proxies to Anthropic Claude API with vision support."""
    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'AI not configured. Set ANTHROPIC_API_KEY env var.'}), 503
    
    data = request.get_json()
    messages = data.get('messages', [])
    
    if not messages:
        return jsonify({'error': 'No messages'}), 400
    
    # Build API request
    tools = [
        {
            "name": "rename_images",
            "description": "Rename images in the user's cart. Provide a list of renames: original filename → new filename.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "renames": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "style_code": {"type": "string", "description": "Style code e.g. NA_201"},
                                "original_filename": {"type": "string", "description": "Current filename"},
                                "new_name": {"type": "string", "description": "New filename (without extension)"}
                            },
                            "required": ["style_code", "original_filename", "new_name"]
                        },
                        "description": "List of rename operations"
                    }
                },
                "required": ["renames"]
            }
        },
        {
            "name": "search_styles",
            "description": "Search for styles in the Photo Studio. Returns matching styles with image counts.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query — brand name, style number, or SKU"}
                },
                "required": ["query"]
            }
        }
    ]
    
    api_body = {
        "model": AI_MODEL,
        "max_tokens": 2048,
        "system": AI_SYSTEM_PROMPT,
        "messages": messages,
        "tools": tools,
    }
    
    try:
        req_data = json.dumps(api_body).encode('utf-8')
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=req_data,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            method='POST'
        )
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        return jsonify(result)
    
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        log.error(f'Anthropic API error {e.code}: {error_body}')
        return jsonify({'error': f'AI API error: {e.code}', 'details': error_body}), 502
    except Exception as e:
        log.error(f'AI chat error: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/api/ai/analyze', methods=['POST'])
def api_ai_analyze():
    """Analyze images using Claude vision. Accepts image URLs and returns descriptions."""
    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'AI not configured'}), 503
    
    data = request.get_json()
    images = data.get('images', [])  # [{url, filename, style_code}]
    instruction = data.get('instruction', 'Describe each image and suggest a descriptive filename.')
    
    if not images:
        return jsonify({'error': 'No images'}), 400
    
    # Build vision message content
    content = []
    for img in images[:20]:  # Max 20 images per call
        url = img.get('url', '')
        if url.startswith('data:'):
            # Base64 image
            media_type = url.split(';')[0].split(':')[1] if ';' in url else 'image/jpeg'
            b64_data = url.split(',')[1] if ',' in url else url
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type, "data": b64_data}
            })
        elif url:
            content.append({
                "type": "image",
                "source": {"type": "url", "url": url}
            })
        content.append({
            "type": "text",
            "text": f"Image {len([c for c in content if c['type']=='image'])}: {img.get('filename', 'unknown')} (Style: {img.get('style_code', '?')})"
        })
    
    content.append({"type": "text", "text": f"\nInstruction: {instruction}\n\nFor each image, respond with a JSON array of objects: [{{\"filename\": \"original.jpg\", \"suggested_name\": \"Descriptive Name\", \"description\": \"brief description\"}}]"})
    
    api_body = {
        "model": AI_MODEL,
        "max_tokens": 2048,
        "messages": [{"role": "user", "content": content}],
    }
    
    try:
        req_data = json.dumps(api_body).encode('utf-8')
        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=req_data,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            method='POST'
        )
        resp = urllib.request.urlopen(req, timeout=60)
        result = json.loads(resp.read())
        
        # Extract text response
        text = ''
        for block in result.get('content', []):
            if block.get('type') == 'text':
                text += block['text']
        
        # Try to parse JSON from response
        suggestions = []
        try:
            import re as _re
            json_match = _re.search(r'\[.*\]', text, _re.DOTALL)
            if json_match:
                suggestions = json.loads(json_match.group())
        except:
            pass
        
        return jsonify({'text': text, 'suggestions': suggestions})
    
    except Exception as e:
        log.error(f'AI analyze error: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


# ─── Share Storage ────────────────────────────────────────────────────────────
share_store = {}  # share_id → {created, items: [{style_code, brand, brand_folder, images: [{path, filename}]}]}
SHARE_MAX_AGE = 30 * 24 * 3600  # 30 days

@app.route('/api/share', methods=['POST'])
def api_create_share():
    """Save a cart selection for sharing. Returns a short share ID."""
    data = request.get_json()
    items = data.get('items', [])
    if not items:
        return jsonify({'error': 'No items'}), 400
    
    share_id = uuid.uuid4().hex[:10]
    share_store[share_id] = {
        'created': time.time(),
        'items': items,
    }
    
    # Cleanup old shares
    now = time.time()
    stale = [k for k, v in share_store.items() if now - v['created'] > SHARE_MAX_AGE]
    for k in stale:
        del share_store[k]
    
    log.info(f'Created share {share_id} with {len(items)} styles')
    return jsonify({'share_id': share_id, 'url': f'/share?id={share_id}'})


@app.route('/api/share/<share_id>')
def api_get_share(share_id):
    """Retrieve a saved share by ID."""
    share = share_store.get(share_id)
    if not share:
        return jsonify({'error': 'Share link not found or expired'}), 404
    return jsonify(share)


@app.route('/share')
def serve_share():
    """Serve the frontend in share mode."""
    return send_from_directory('templates', 'index.html')


# ─── Startup ──────────────────────────────────────────────────────────────────
def startup_scan():
    """Auto-scan on startup after a short delay."""
    time.sleep(1)
    if DROPBOX_ACCESS_TOKEN or DROPBOX_REFRESH_TOKEN:
        log.info('Starting initial Dropbox scan...')
        try:
            # Reset the startup scanning flag so scan_dropbox doesn't skip
            scan_status['scanning'] = False
            scan_dropbox()
        except Exception as e:
            log.error(f'Startup scan crashed: {e}', exc_info=True)
            with scan_lock:
                scan_status['scanning'] = False
                scan_status['error'] = str(e)
                scan_status['progress'] = f'Startup error: {e}'
    else:
        log.warning('No Dropbox credentials — skipping auto-scan.')
        with scan_lock:
            scan_status['scanning'] = False
            scan_status['progress'] = 'No credentials configured'

# ─── Self-Healing Startup & Recovery ─────────────────────────────────────────
_startup_done = False
_last_auto_rescan = 0
_last_cache_clean = 0
AUTO_RESCAN_INTERVAL = 4 * 3600  # Re-scan every 4 hours to refresh Dropbox tokens
CACHE_CLEAN_INTERVAL = 1800      # Clean stale link cache every 30 min

@app.before_request
def ensure_index():
    """Self-healing: auto-scan on first request, and recover if index is lost."""
    global _startup_done, _last_auto_rescan, _last_cache_clean
    now = time.time()
    
    if not _startup_done:
        _startup_done = True
        _last_auto_rescan = now
        threading.Thread(target=startup_scan, daemon=True).start()
        return
    
    # Auto-recovery: if index is empty and no scan running, trigger rescan
    if not photo_index and not _scan_running and not scan_status.get('scanning'):
        log.warning('Photo index is empty — triggering auto-recovery scan')
        _last_auto_rescan = now
        threading.Thread(target=startup_scan, daemon=True).start()
        return
    
    # Periodic refresh: re-scan every 4 hours to keep Dropbox tokens fresh
    if photo_index and not _scan_running and (now - _last_auto_rescan) > AUTO_RESCAN_INTERVAL:
        log.info('Periodic rescan triggered (keeping Dropbox tokens fresh)')
        _last_auto_rescan = now
        threading.Thread(target=startup_scan, daemon=True).start()
    
    # Periodic link cache cleanup — evict expired entries
    if (now - _last_cache_clean) > CACHE_CLEAN_INTERVAL:
        _last_cache_clean = now
        stale = [k for k, (_, exp) in link_cache.items() if now > exp]
        for k in stale:
            del link_cache[k]
        if stale:
            log.info(f'Cleaned {len(stale)} stale link cache entries')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
