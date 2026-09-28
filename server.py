#!/usr/bin/env python3
import os, json, hashlib, hmac, secrets, tempfile, shutil, subprocess, threading, time
import base64, re, ssl
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime

PORT      = int(os.environ.get('PORT', 3344))
BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / 'data'
PW_ZIP    = DATA_DIR / 'spw.zip'
CFG_FILE  = DATA_DIR / 'config.json'
BACKUP_DIR= DATA_DIR / 'backups'
PUB_DIR   = BASE_DIR / 'public'

APP_VERSION  = '1.1.2'
APP_PATH     = Path(__file__).resolve()
SERVICE_NAME = os.environ.get('SPW_SERVICE', 'spw')
GITHUB_RAW   = 'https://raw.githubusercontent.com/hirogura/spw/main/'
UPDATE_FILES = ['server.py', 'public/index.html', 'public/app.js', 'public/style.css']

# ---------- sync ----------
# 別PCの spw と1日1回・片方向で同期する（予備機用途・同期先は1箇所のみ）。
# 同期対象は暗号化ファイル spw.zip のみ（AES256暗号化済みのため復号不要・
# マスターパスワードは両PCで同一であることが前提）。config.json は同期しない。
SYNC_CFG_FILE   = DATA_DIR / 'sync_config.json'
SYNC_ROLE_SOURCE = 'source'
SYNC_ROLE_DEST   = 'destination'
SYNC_MAX_BYTES   = 60 * 1024 * 1024
DEFAULT_SYNC_CONFIG = {
  'role': SYNC_ROLE_SOURCE, 'peer': '', 'peer_name': '',
  'sync_time': '03:00', 'last_sync': '', 'last_result': '',
}
_SYNC_RUN_LOCK = threading.Lock()

DATA_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

sessions = {}  # token -> password
sessions_lock = threading.Lock()

MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css; charset=utf-8',
  '.js':   'application/javascript; charset=utf-8',
  '.json': 'application/json',
  '.ico':  'image/x-icon',
  '.svg':  'image/svg+xml',
}

# ---------- config ----------
def load_config():
  try:
    return json.loads(CFG_FILE.read_text())
  except:
    return {}

def save_config(cfg):
  tmp_path = CFG_FILE.with_suffix('.json.tmp')
  tmp_path.write_text(json.dumps(cfg, indent=2))
  os.replace(tmp_path, CFG_FILE)

# ---------- password hash ----------
def hash_password(password):
  salt = secrets.token_hex(16)
  dk = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=64)
  return {'salt': salt, 'hash': dk.hex()}

def verify_password(password, stored):
  try:
    dk = hashlib.scrypt(password.encode(), salt=stored['salt'].encode(), n=16384, r=8, p=1, dklen=64)
  except Exception:
    return False
  return hmac.compare_digest(dk.hex(), stored.get('hash', ''))

# ---------- zip ----------
def validate_password_data(data):
  if not isinstance(data, dict):
    return False
  cats = data.get('categories')
  if not isinstance(cats, list):
    return False
  for cat in cats:
    if not isinstance(cat, dict):
      return False
    if not isinstance(cat.get('id'), str) or not isinstance(cat.get('name'), str):
      return False
    cards = cat.get('cards')
    if not isinstance(cards, list):
      return False
    for card in cards:
      if not isinstance(card, dict):
        return False
      if not isinstance(card.get('id'), str) or not isinstance(card.get('name'), str):
        return False
      fields = card.get('fields', [])
      if not isinstance(fields, list):
        return False
      for f in fields:
        if not isinstance(f, dict):
          return False
        if not isinstance(f.get('key', ''), str) or not isinstance(f.get('value', ''), str):
          return False
  return True

def save_encrypted(data, password):
  # NOTE: os.replace は同一FS内でしか使えないため、tmp を DATA_DIR 配下
  # (PW_ZIP と同一FS) に作ることで /tmp が別FS(tmpfs)でも EXDEV にならない。
  tmp = Path(tempfile.mkdtemp(dir=str(DATA_DIR)))
  try:
    jf = tmp / 'passwords.json'
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp_zip = tmp / 'spw.zip.tmp'
    subprocess.run(
      ['7z', 'a', '-tzip', '-mem=AES256', f'-p{password}', str(tmp_zip), str(jf)],
      check=True, capture_output=True, timeout=30
    )
    try:
      os.replace(tmp_zip, PW_ZIP)
    except OSError:
      # 万が一別FSになった場合のフォールバック (コピー+削除)
      shutil.move(str(tmp_zip), str(PW_ZIP))
  finally:
    shutil.rmtree(tmp, ignore_errors=True)

def load_encrypted(password):
  if not PW_ZIP.exists():
    return {'categories': []}
  tmp = Path(tempfile.mkdtemp())
  try:
    subprocess.run(
      ['7z', 'x', f'-p{password}', f'-o{tmp}', str(PW_ZIP), '-y'],
      check=True, capture_output=True, timeout=30
    )
    jf = tmp / 'passwords.json'
    return json.loads(jf.read_text()) if jf.exists() else {'categories': []}
  except:
    return {'categories': []}
  finally:
    shutil.rmtree(tmp, ignore_errors=True)

# ---------- sync ----------
def load_sync_config():
  cfg = dict(DEFAULT_SYNC_CONFIG)
  try:
    data = json.loads(SYNC_CFG_FILE.read_text())
    if isinstance(data, dict):
      for k in DEFAULT_SYNC_CONFIG:
        if isinstance(data.get(k), str):
          cfg[k] = data[k]
  except Exception:
    pass
  if cfg.get('role') not in (SYNC_ROLE_SOURCE, SYNC_ROLE_DEST):
    cfg['role'] = SYNC_ROLE_SOURCE
  if not re.match(r'^([01]\d|2[0-3]):[0-5]\d$', cfg.get('sync_time') or ''):
    cfg['sync_time'] = DEFAULT_SYNC_CONFIG['sync_time']
  return cfg

def save_sync_config(cfg):
  tmp = SYNC_CFG_FILE.with_suffix('.json.tmp')
  tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
  os.replace(tmp, SYNC_CFG_FILE)

def valid_sync_time(s):
  return isinstance(s, str) and re.match(r'^([01]\d|2[0-3]):[0-5]\d$', s) is not None

def is_valid_peer_url(u):
  if not isinstance(u, str) or not u or len(u) > 500:
    return False
  try:
    from urllib.parse import urlsplit
    p = urlsplit(u)
    return p.scheme in ('http', 'https') and bool(p.hostname)
  except Exception:
    return False

def tailscale_status():
  try:
    r = subprocess.run(['tailscale', 'status', '--json'],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
      return None
    d = json.loads(r.stdout or '{}')
    return d if isinstance(d, dict) else None
  except Exception:
    return None

def get_serve_port():
  # Tailscale Serve の公開ポートを特定（127.0.0.1:PORT へのプロキシを探す）
  try:
    r = subprocess.run(['tailscale', 'serve', 'status', '--json'],
                       capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
      d = json.loads(r.stdout or '{}')
      web = d.get('Web') or {}
      want = f'http://127.0.0.1:{PORT}'
      for key, val in web.items():
        try:
          proxy = ((val.get('Handlers') or {}).get('/') or {}).get('Proxy', '')
          if proxy.rstrip('/') == want and ':' in key:
            return str(key.rsplit(':', 1)[1])
        except Exception:
          continue
  except Exception:
    pass
  return str(PORT)

def get_self_base_url():
  d = tailscale_status()
  try:
    if d:
      dns = ((d.get('Self') or {}).get('DNSName') or '').rstrip('.')
      if dns:
        return f'https://{dns}:{get_serve_port()}'
  except Exception:
    pass
  return ''

def get_self_host_name():
  d = tailscale_status()
  try:
    if d:
      return ((d.get('Self') or {}).get('HostName') or '')
  except Exception:
    pass
  return ''

def get_sync_peer_list():
  d = tailscale_status()
  if not d:
    return []
  peers = d.get('Peer') or {}
  self_dns = (((d.get('Self') or {}).get('DNSName')) or '').rstrip('.')
  port = get_serve_port()
  out = []
  for p in peers.values():
    if not isinstance(p, dict):
      continue
    dns = (p.get('DNSName') or '').rstrip('.')
    if not dns or dns == self_dns:
      continue
    name = p.get('HostName') or dns
    ips = p.get('TailscaleIPs') or []
    out.append({'name': name, 'dns': dns, 'url': f'https://{dns}:{port}',
                'ip': ips[0] if ips else '', 'os': p.get('OS') or '',
                'online': bool(p.get('Online'))})
  out.sort(key=lambda x: (not x['online'], x['name'].lower()))
  return out

def _http_get_json(url, timeout=60):
  ctx = ssl.create_default_context()
  ctx.check_hostname = False
  ctx.verify_mode = ssl.CERT_NONE
  req = urllib.request.Request(url, headers={'User-Agent': 'spw-sync'})
  with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
    return json.loads(resp.read().decode('utf-8'))

def _http_post_json(url, payload, timeout=60):
  ctx = ssl.create_default_context()
  ctx.check_hostname = False
  ctx.verify_mode = ssl.CERT_NONE
  body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
  req = urllib.request.Request(url, data=body,
                               headers={'User-Agent': 'spw-sync',
                                        'Content-Type': 'application/json'})
  with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
    text = resp.read().decode('utf-8') or '{}'
    try:
      return json.loads(text)
    except ValueError:
      return {}

def build_sync_bundle():
  if not PW_ZIP.exists():
    return {'version': 1, 'exported_at': datetime.now().isoformat(timespec='seconds'),
            'spw_zip': None}
  raw = PW_ZIP.read_bytes()
  if len(raw) > SYNC_MAX_BYTES:
    raise ValueError('データが大きすぎます')
  return {'version': 1, 'exported_at': datetime.now().isoformat(timespec='seconds'),
          'spw_zip': base64.b64encode(raw).decode('ascii')}

def apply_sync_bundle(bundle):
  if not isinstance(bundle, dict):
    raise ValueError('invalid bundle')
  content = bundle.get('spw_zip')
  if not isinstance(content, str) or not content:
    raise ValueError('同期元にデータがありません')
  try:
    raw = base64.b64decode(content, validate=True)
  except Exception:
    raise ValueError('invalid bundle')
  if len(raw) > SYNC_MAX_BYTES or raw[:2] != b'PK':
    raise ValueError('invalid bundle')
  tmp = DATA_DIR / 'spw.zip.tmp'
  tmp.write_bytes(raw)
  try:
    os.replace(tmp, PW_ZIP)
  except OSError:
    shutil.move(str(tmp), str(PW_ZIP))

def mark_sync_result(ok, message):
  try:
    cfg = load_sync_config()
    if ok:
      cfg['last_sync'] = datetime.now().isoformat(timespec='seconds')
    cfg['last_result'] = str(message or '')[:300]
    save_sync_config(cfg)
  except Exception:
    pass

def sync_push_to_peer(peer_url):
  if not is_valid_peer_url(peer_url):
    raise ValueError('同期先が未設定です')
  bundle = build_sync_bundle()
  if not bundle.get('spw_zip'):
    raise ValueError('同期するデータがありません（先にパスワードを保存してください）')
  try:
    _http_post_json(peer_url.rstrip('/') + '/api/sync/import', bundle, timeout=60)
  except Exception as e:
    raise RuntimeError(f'送信に失敗しました: {e}')
  mark_sync_result(True, f"同期しました（送信・{bundle['exported_at']}）")
  return bundle.get('exported_at', '')

def sync_pull_from_peer(peer_url):
  if not is_valid_peer_url(peer_url):
    raise ValueError('同期元が未設定です')
  try:
    bundle = _http_get_json(peer_url.rstrip('/') + '/api/sync/export', timeout=60)
  except Exception as e:
    raise RuntimeError(f'取得に失敗しました: {e}')
  apply_sync_bundle(bundle)
  exported_at = bundle.get('exported_at', '') if isinstance(bundle, dict) else ''
  mark_sync_result(True, f'同期しました（受信・{exported_at}）')
  return exported_at

def sync_scheduler_tick(now=None):
  cfg = load_sync_config()
  peer = (cfg.get('peer') or '').strip()
  if not peer or not is_valid_peer_url(peer):
    return False
  if not valid_sync_time(cfg.get('sync_time') or ''):
    return False
  now = now or datetime.now()
  if now.strftime('%H:%M') < cfg['sync_time']:
    return False
  last = cfg.get('last_sync') or ''
  if len(last) >= 10 and last[:10] == now.date().isoformat():
    return False
  if not _SYNC_RUN_LOCK.acquire(blocking=False):
    return False
  try:
    if cfg.get('role') == SYNC_ROLE_SOURCE:
      sync_push_to_peer(peer)
    elif cfg.get('role') == SYNC_ROLE_DEST:
      sync_pull_from_peer(peer)
    else:
      return False
    return True
  except Exception as e:
    mark_sync_result(False, f'自動同期に失敗しました: {e}')
    return False
  finally:
    try:
      _SYNC_RUN_LOCK.release()
    except RuntimeError:
      pass

def sync_scheduler_loop():
  while True:
    try:
      time.sleep(30)
      sync_scheduler_tick()
    except Exception:
      continue

# ---------- HTTP handler ----------
class Handler(BaseHTTPRequestHandler):
  def log_message(self, fmt, *args):
    pass  # アクセスログ抑制

  def send_json(self, code, obj):
    body = json.dumps(obj, ensure_ascii=False).encode()
    self.send_response(code)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', str(len(body)))
    self.end_headers()
    try:
      self.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
      pass

  def send_bytes(self, code, body, content_type, filename=None):
    self.send_response(code)
    self.send_header('Content-Type', content_type)
    self.send_header('Content-Length', str(len(body)))
    if filename:
      safe_name = filename.replace('"', '').replace('\r', '').replace('\n', '')
      self.send_header('Content-Disposition', f'attachment; filename="{safe_name}"')
    self.end_headers()
    try:
      self.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
      pass

  def auth_token(self):
    return self.headers.get('x-auth-token', '')

  def get_session_password(self):
    token = self.auth_token()
    with sessions_lock:
      s = sessions.get(token)
    return s['password'] if s else None

  def read_json(self, max_bytes=10 * 1024 * 1024):
    length = int(self.headers.get('Content-Length', 0) or 0)
    if not length:
      return {}
    if length > max_bytes:
      raise ValueError('Request body too large')
    raw = self.rfile.read(length)
    try:
      return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
      raise ValueError('Invalid JSON')

  def serve_static(self, path):
    # パストラバーサル対策: PUB_DIR 配下のみ許可
    rel = path.lstrip('/').split('?')[0].split('#')[0]
    p = (PUB_DIR / rel).resolve()
    try:
      p.relative_to(PUB_DIR.resolve())
    except ValueError:
      self.send_json(403, {'error': 'Forbidden'})
      return
    if not p.is_file():
      p = PUB_DIR / 'index.html'
    ext = p.suffix.lower()
    mime = MIME.get(ext, 'application/octet-stream')
    body = p.read_bytes()
    self.send_bytes(200, body, mime)

  def do_GET(self):
    parsed = urlparse(self.path)
    path = parsed.path

    if path == '/api/auth/status':
      cfg = load_config()
      self.send_json(200, {'hasPassword': bool(cfg.get('passwordHash'))})

    elif path == '/api/version':
      self.send_json(200, {'version': APP_VERSION})

    elif path == '/api/sync/config':
      cfg = load_sync_config()
      cfg['self_url'] = get_self_base_url()
      cfg['self_name'] = get_self_host_name()
      self.send_json(200, cfg)

    elif path == '/api/sync/peers':
      self.send_json(200, {'peers': get_sync_peer_list(),
                           'self_url': get_self_base_url(),
                           'self_name': get_self_host_name()})

    elif path == '/api/sync/export':
      try:
        self.send_json(200, build_sync_bundle())
      except ValueError as e:
        self.send_json(400, {'error': str(e)})

    elif path == '/api/passwords':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'})
        return
      self.send_json(200, load_encrypted(pw))

    else:
      self.serve_static(path if path != '/' else '/index.html')

  def do_POST(self):
    parsed = urlparse(self.path)
    path = parsed.path

    if path == '/api/auth/setup':
      cfg = load_config()
      if cfg.get('passwordHash'):
        self.send_json(400, {'error': 'Password already set'}); return
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'error': str(e)}); return
      pw = body.get('password', '') if isinstance(body, dict) else ''
      if not isinstance(pw, str) or len(pw) < 4:
        self.send_json(400, {'error': 'Password must be at least 4 characters'}); return
      cfg['passwordHash'] = hash_password(pw)
      save_config(cfg)
      token = secrets.token_hex(32)
      with sessions_lock:
        sessions[token] = {'password': pw}
      self.send_json(200, {'success': True, 'token': token})

    elif path == '/api/auth/login':
      cfg = load_config()
      if not cfg.get('passwordHash'):
        self.send_json(400, {'error': 'No password set'}); return
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'error': str(e)}); return
      pw = body.get('password', '') if isinstance(body, dict) else ''
      if not pw:
        self.send_json(400, {'error': 'Password required'}); return
      if not verify_password(pw, cfg['passwordHash']):
        self.send_json(401, {'error': 'Wrong password'}); return
      token = secrets.token_hex(32)
      with sessions_lock:
        sessions[token] = {'password': pw}
      self.send_json(200, {'success': True, 'token': token})

    elif path == '/api/auth/logout':
      token = self.auth_token()
      with sessions_lock:
        sessions.pop(token, None)
      self.send_json(200, {'success': True})

    elif path == '/api/auth/change-password':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'error': str(e)}); return
      cur = body.get('currentPassword', '') if isinstance(body, dict) else ''
      new = body.get('newPassword', '') if isinstance(body, dict) else ''
      cfg = load_config()
      if not cfg.get('passwordHash') or not verify_password(cur, cfg['passwordHash']):
        self.send_json(401, {'error': 'Current password is wrong'}); return
      if not isinstance(new, str) or len(new) < 4:
        self.send_json(400, {'error': 'New password must be at least 4 characters'}); return
      data = load_encrypted(pw)
      try:
        save_encrypted(data, new)
      except Exception as e:
        self.send_json(500, {'error': f'Failed to re-encrypt data: {e}'}); return
      cfg['passwordHash'] = hash_password(new)
      save_config(cfg)
      token = self.auth_token()
      with sessions_lock:
        if token in sessions:
          sessions[token]['password'] = new
      self.send_json(200, {'success': True})

    elif path == '/api/passwords':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'error': str(e)}); return
      if not validate_password_data(body):
        self.send_json(400, {'error': 'Invalid data format'}); return
      try:
        save_encrypted(body, pw)
      except Exception as e:
        self.send_json(500, {'error': f'Failed to save: {e}'}); return
      self.send_json(200, {'success': True})

    elif path == '/api/export':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      data = load_encrypted(pw)
      tmp = Path(tempfile.mkdtemp())
      try:
        jf = tmp / 'passwords.json'
        jf.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        ts = datetime.now().strftime('%Y-%m-%dT%H-%M-%S')
        filename = f'passwords-{ts}.zip'
        zippath = tmp / filename
        subprocess.run(
          ['7z', 'a', '-tzip', '-mem=AES256', f'-p{pw}', str(zippath), str(jf)],
          check=True, capture_output=True, timeout=30
        )
        body = zippath.read_bytes()
        self.send_bytes(200, body, 'application/zip', filename)
      except Exception as e:
        self.send_json(500, {'error': str(e)})
      finally:
        shutil.rmtree(tmp, ignore_errors=True)

    elif path == '/api/import':
      pw = self.get_session_password()
      if pw is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'error': str(e)}); return
      file_pw = body.get('password', '') if isinstance(body, dict) else ''
      file_data = body.get('fileData', '') if isinstance(body, dict) else ''
      if not file_pw or not file_data:
        self.send_json(400, {'error': 'Password and file required'}); return
      import base64
      import binascii
      tmp = Path(tempfile.mkdtemp())
      try:
        try:
          raw = base64.b64decode(file_data, validate=True)
        except (binascii.Error, ValueError):
          self.send_json(400, {'error': 'Invalid file encoding'}); return
        if len(raw) > 50 * 1024 * 1024:
          self.send_json(400, {'error': 'File too large'}); return
        arc = tmp / 'import.zip'
        arc.write_bytes(raw)
        out = tmp / 'out'
        out.mkdir()
        subprocess.run(
          ['7z', 'x', f'-p{file_pw}', f'-o{out}', str(arc), '-y'],
          check=True, capture_output=True, timeout=30
        )
        jf = out / 'passwords.json'
        if not jf.exists():
          self.send_json(400, {'error': 'passwords.json not found in archive'}); return
        try:
          imported = json.loads(jf.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
          self.send_json(400, {'error': 'Invalid passwords.json in archive'}); return
        if not validate_password_data(imported):
          self.send_json(400, {'error': 'Invalid data format in archive'}); return
        try:
          save_encrypted(imported, pw)
        except Exception as e:
          self.send_json(500, {'error': f'Failed to save: {e}'}); return
        self.send_json(200, {'success': True})
      except subprocess.CalledProcessError:
        self.send_json(400, {'error': 'Wrong password or corrupt file'})
      except Exception as e:
        self.send_json(500, {'error': 'Wrong password or corrupt file'})
      finally:
        shutil.rmtree(tmp, ignore_errors=True)

    elif path == '/api/admin/restart':
      if self.get_session_password() is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      def _restart():
        time.sleep(0.8)
        subprocess.run(['systemctl', 'restart', SERVICE_NAME],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
      threading.Thread(target=_restart, daemon=True).start()
      self.send_json(200, {'success': True})

    elif path == '/api/admin/update':
      if self.get_session_password() is None:
        self.send_json(401, {'error': 'Unauthorized'}); return
      try:
        fetched = {}
        for rel in UPDATE_FILES:
          req = urllib.request.Request(GITHUB_RAW + rel, headers={'User-Agent': 'spw-updater'})
          with urllib.request.urlopen(req, timeout=30) as resp:
            fetched[rel] = resp.read().decode('utf-8')
        if not fetched['server.py'].startswith('#!') or '</html>' not in fetched['public/index.html']:
          raise ValueError('ダウンロードしたファイルが不正です')
        changed = False
        for rel, new_text in fetched.items():
          try:
            cur_text = (BASE_DIR / rel).read_text()
          except Exception:
            cur_text = ''
          if cur_text != new_text:
            changed = True
            break
        if not changed:
          self.send_json(200, {'success': True, 'updated': False}); return
        compile(fetched['server.py'], 'server.py', 'exec')
        new_paths = {}
        for rel, new_text in fetched.items():
          tmp_path = BASE_DIR / (rel + '.new')
          tmp_path.parent.mkdir(parents=True, exist_ok=True)
          tmp_path.write_text(new_text)
          new_paths[rel] = tmp_path
        for rel, tmp_path in new_paths.items():
          dest = BASE_DIR / rel
          try:
            mode = dest.stat().st_mode
          except FileNotFoundError:
            mode = 0o644
          os.replace(tmp_path, dest)
          # 実行ビットを維持 (server.py が +x の場合は維持)
          if rel == 'server.py' and (mode & 0o111):
            os.chmod(dest, mode)
      except Exception as e:
        self.send_json(500, {'success': False, 'error': str(e)}); return
      def _apply():
        time.sleep(1.0)
        subprocess.run(['systemctl', 'restart', SERVICE_NAME],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
      threading.Thread(target=_apply, daemon=True).start()
      self.send_json(200, {'success': True, 'updated': True})

    elif path == '/api/sync/config':
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'ok': False, 'error': str(e)}); return
      if not isinstance(body, dict):
        self.send_json(400, {'ok': False, 'error': 'bad request'}); return
      role = body.get('role', '')
      peer = (body.get('peer', '') or '').strip() if isinstance(body.get('peer', ''), str) else ''
      peer_name = body.get('peer_name', '')
      peer_name = peer_name.strip()[:100] if isinstance(peer_name, str) else ''
      sync_time = body.get('sync_time', '')
      sync_time = sync_time.strip() if isinstance(sync_time, str) else ''
      if role not in (SYNC_ROLE_SOURCE, SYNC_ROLE_DEST):
        self.send_json(400, {'ok': False, 'error': '同期元・同期先のいずれかを指定してください'}); return
      if peer and not is_valid_peer_url(peer):
        self.send_json(400, {'ok': False, 'error': '同期先のURLが不正です'}); return
      if not valid_sync_time(sync_time):
        self.send_json(400, {'ok': False, 'error': '同期時刻は HH:MM 形式で指定してください'}); return
      cfg = load_sync_config()
      cfg['role'] = role; cfg['peer'] = peer
      cfg['peer_name'] = peer_name; cfg['sync_time'] = sync_time
      save_sync_config(cfg)
      # 相手側の役割を反対にそろえる（相手が旧バージョン等で失敗しても保存自体は成功扱い）
      peer_notified, peer_message = False, ''
      if peer and body.get('notify_peer', True):
        opposite = SYNC_ROLE_DEST if role == SYNC_ROLE_SOURCE else SYNC_ROLE_SOURCE
        try:
          _http_post_json(peer.rstrip('/') + '/api/sync/role',
                           {'role': opposite, 'peer_url': get_self_base_url(),
                            'peer_name': get_self_host_name(),
                            'sync_time': sync_time}, timeout=10)
          peer_notified = True
          peer_message = '相手側を「%s」に切り替え、同期時刻（%s）を共有しました' % ('同期先' if opposite == SYNC_ROLE_DEST else '同期元', sync_time)
        except Exception as e:
          peer_message = f'相手側への通知に失敗しました（相手のSPWを最新版に更新してください）: {e}'
      self.send_json(200, {'ok': True, 'peer_notified': peer_notified,
                           'peer_message': peer_message})

    elif path == '/api/sync/role':
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'ok': False, 'error': str(e)}); return
      if not isinstance(body, dict):
        self.send_json(400, {'ok': False, 'error': 'bad request'}); return
      role = body.get('role', '')
      peer_url = body.get('peer_url', '')
      peer_url = peer_url.strip() if isinstance(peer_url, str) else ''
      peer_name = body.get('peer_name', '')
      peer_name = peer_name.strip()[:100] if isinstance(peer_name, str) else ''
      if role not in (SYNC_ROLE_SOURCE, SYNC_ROLE_DEST):
        self.send_json(400, {'ok': False, 'error': 'invalid role'}); return
      if peer_url and not is_valid_peer_url(peer_url):
        self.send_json(400, {'ok': False, 'error': 'invalid peer_url'}); return
      cfg = load_sync_config()
      cfg['role'] = role
      if peer_url:
        cfg['peer'] = peer_url; cfg['peer_name'] = peer_name
      # 同期時刻も共有する（旧バージョンからは送られてこないため任意扱い）。
      # 形式が正しい場合のみ反映し、不正な値では役割の更新を妨げない。
      sync_time = body.get('sync_time', '')
      sync_time = sync_time.strip() if isinstance(sync_time, str) else ''
      if valid_sync_time(sync_time):
        cfg['sync_time'] = sync_time
      save_sync_config(cfg)
      self.send_json(200, {'ok': True, 'role': role, 'sync_time': cfg.get('sync_time', '')})

    elif path == '/api/sync/import':
      try:
        body = self.read_json(max_bytes=SYNC_MAX_BYTES)
      except ValueError as e:
        self.send_json(413, {'ok': False, 'error': str(e)}); return
      try:
        apply_sync_bundle(body)
      except ValueError as e:
        self.send_json(400, {'ok': False, 'error': str(e)}); return
      except Exception as e:
        self.send_json(500, {'ok': False, 'error': f'反映に失敗しました: {e}'}); return
      exported_at = body.get('exported_at', '') if isinstance(body, dict) else ''
      mark_sync_result(True, f'同期しました（受信・{exported_at}）')
      self.send_json(200, {'ok': True})

    elif path == '/api/sync/run':
      cfg = load_sync_config()
      peer = (cfg.get('peer') or '').strip()
      if not peer:
        self.send_json(400, {'ok': False, 'error': '同期相手が未設定です。先に相手を選択して保存してください'}); return
      if not _SYNC_RUN_LOCK.acquire(blocking=False):
        self.send_json(409, {'ok': False, 'error': '同期を実行中です。しばらく待ってください'}); return
      try:
        if cfg.get('role') == SYNC_ROLE_SOURCE:
          exported_at = sync_push_to_peer(peer)
          self.send_json(200, {'ok': True, 'direction': 'push',
                               'message': f'同期先へ送信しました（{exported_at}）'})
        elif cfg.get('role') == SYNC_ROLE_DEST:
          exported_at = sync_pull_from_peer(peer)
          self.send_json(200, {'ok': True, 'direction': 'pull',
                               'message': f'同期元から取得しました（{exported_at}）'})
        else:
          self.send_json(400, {'ok': False, 'error': '役割が不正です'})
      except (ValueError, RuntimeError) as e:
        mark_sync_result(False, f'手動同期に失敗しました: {e}')
        self.send_json(500, {'ok': False, 'error': str(e)})
      except Exception as e:
        mark_sync_result(False, f'手動同期に失敗しました: {e}')
        self.send_json(500, {'ok': False, 'error': f'同期に失敗しました: {e}'})
      finally:
        try:
          _SYNC_RUN_LOCK.release()
        except RuntimeError:
          pass

    elif path == '/api/sync/unlink':
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'ok': False, 'error': str(e)}); return
      if not isinstance(body, dict):
        self.send_json(400, {'ok': False, 'error': 'bad request'}); return
      peer_url = body.get('peer_url', '')
      peer_url = peer_url.strip().rstrip('/') if isinstance(peer_url, str) else ''
      cfg = load_sync_config()
      unlinked = False
      if peer_url and (cfg.get('peer') or '').rstrip('/') == peer_url:
        cfg['peer'] = ''; cfg['peer_name'] = ''
        cfg['last_result'] = '相手側で同期が停止されたため、相手指定を解除しました'
        save_sync_config(cfg)
        unlinked = True
      self.send_json(200, {'ok': True, 'unlinked': unlinked})

    elif path == '/api/sync/stop':
      try:
        body = self.read_json()
      except ValueError as e:
        self.send_json(400, {'ok': False, 'error': str(e)}); return
      if not isinstance(body, dict):
        body = {}
      cfg = load_sync_config()
      old_peer = (cfg.get('peer') or '').strip()
      cfg['peer'] = ''; cfg['peer_name'] = ''
      cfg['last_result'] = f"同期を停止しました（{datetime.now().isoformat(timespec='seconds')}）"
      save_sync_config(cfg)
      peer_notified, peer_message = False, ''
      if old_peer and body.get('notify_peer', True):
        try:
          r = _http_post_json(old_peer.rstrip('/') + '/api/sync/unlink',
                              {'peer_url': get_self_base_url()}, timeout=10)
          peer_notified = bool(isinstance(r, dict) and r.get('unlinked'))
          peer_message = '相手側の同期設定も解除しました' if peer_notified else '相手側への通知は届きましたが、相手の相手指定は既に外れていました'
        except Exception as e:
          peer_message = f'相手側への通知に失敗しました（相手のSPWを最新版に更新するか、相手側でも停止してください）: {e}'
      self.send_json(200, {'ok': True, 'peer_notified': peer_notified,
                           'peer_message': peer_message})

    else:
      self.send_json(404, {'error': 'Not found'})

from http.server import ThreadingHTTPServer
threading.Thread(target=sync_scheduler_loop, daemon=True).start()
# Tailscale Serve が TLS 終端用に <tailscale IP>:PORT をバインドできるよう、
# アプリは 127.0.0.1 のみで待機する (LAN からの平文HTTP直アクセスは不可)。
print(f'SPW Password Manager running on http://127.0.0.1:{PORT}')
server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
server.serve_forever()
