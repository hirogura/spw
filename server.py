#!/usr/bin/env python3
import os, json, hashlib, hmac, secrets, tempfile, shutil, subprocess, threading, time
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime

PORT      = int(os.environ.get('PORT', 3345))
BASE_DIR  = Path(__file__).parent
DATA_DIR  = BASE_DIR / 'data'
PW_ZIP    = DATA_DIR / 'spw.zip'
CFG_FILE  = DATA_DIR / 'config.json'
BACKUP_DIR= DATA_DIR / 'backups'
PUB_DIR   = BASE_DIR / 'public'

APP_VERSION  = '1.0.2'
APP_PATH     = Path(__file__).resolve()
SERVICE_NAME = os.environ.get('SPW_SERVICE', 'spw')
GITHUB_RAW   = 'https://raw.githubusercontent.com/hirogura/spw/main/'
UPDATE_FILES = ['server.py', 'public/index.html', 'public/app.js', 'public/style.css']

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
  tmp = Path(tempfile.mkdtemp())
  try:
    jf = tmp / 'passwords.json'
    jf.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp_zip = tmp / 'spw.zip.tmp'
    subprocess.run(
      ['7z', 'a', '-tzip', '-mem=AES256', f'-p{password}', str(tmp_zip), str(jf)],
      check=True, capture_output=True, timeout=30
    )
    os.replace(tmp_zip, PW_ZIP)
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

  def read_json(self):
    length = int(self.headers.get('Content-Length', 0) or 0)
    if not length:
      return {}
    if length > 10 * 1024 * 1024:
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

    else:
      self.send_json(404, {'error': 'Not found'})

from http.server import ThreadingHTTPServer
print(f'SPW Password Manager running on http://0.0.0.0:{PORT}')
server = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
server.serve_forever()
