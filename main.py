import os
import sys
import webview
import threading
import subprocess
import time

# To allow dev server testing, we can point URL to Vite dev server, or local built assets.
DEV_MODE = os.environ.get("DEV_MODE", "1") == "1"
VITE_DEV_SERVER = "http://localhost:5173"

from core.api import Api
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

window_ref = None

class ExtensionRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress default HTTP server logs to keep the console clean
        pass

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path.startswith('/api/check'):
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
            check_url = query.get('url', [''])[0]
            
            exists = False
            if check_url:
                from core.database import get_connection
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM games WHERE f95_url = ?", (check_url,))
                exists = cursor.fetchone() is not None
                conn.close()
            
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"exists": exists}).encode())
        elif self.path.startswith('/api/open'):
            from urllib.parse import urlparse, parse_qs
            query = parse_qs(urlparse(self.path).query)
            game_url = query.get('url', [''])[0]
            
            if window_ref:
                try:
                    window_ref.on_top = True
                    window_ref.on_top = False
                    # Tell the frontend to open the edit modal for this game
                    if game_url:
                        safe_url = json.dumps(game_url)
                        js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-open', {{ detail: {{ url: {safe_url} }} }}));"
                        window_ref.evaluate_js(js_code)
                except Exception:
                    pass
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"success": true}')
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/add':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                payload = json.loads(post_data.decode('utf-8'))
                
                # We have the payload. Send it to the Vue frontend via PyWebView javascript evaluation
                if window_ref:
                    # Escape the payload to be safe to inject as a JS string argument
                    safe_json = json.dumps(payload).replace('\\', '\\\\').replace('"', '\\"')
                    js_code = f"window.dispatchEvent(new CustomEvent('wlib-extension-add', {{ detail: JSON.parse(\"{safe_json}\") }}));"
                    window_ref.evaluate_js(js_code)
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"success": true}')
            except Exception as e:
                self.send_response(400)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

def start_extension_server():
    try:
        server = HTTPServer(('localhost', 8183), ExtensionRequestHandler)
        print("Starting extension HTTP receiver on port 8183...")
        server.serve_forever()
    except Exception as e:
        print(f"Failed to start extension HTTP server: {e}")

def start_vite_dev_server():
    print("Starting Vite dev server...")
    # Change dir to the 'ui' folder relative to main.py
    ui_dir = os.path.join(os.path.dirname(__file__), 'ui')
    if os.path.exists(ui_dir):
        # We start process and let it run
        return subprocess.Popen(
            ["npm", "run", "dev"], 
            cwd=ui_dir,
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
    return None

def main():
    global window_ref, DEV_MODE
    api = Api()
    
    url = "ui/dist/index.html"
    vite_process = None
    
    import shutil
    missing_deps = []
    for dep in ['wine', 'winetricks', 'npm']:
        if shutil.which(dep) is None:
            missing_deps.append(dep)
            
    if missing_deps:
        # Show a user-friendly error dialog in the webview
        html_content = f"""
        <html>
        <body style="background-color: #101014; color: white; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0;">
            <div style="background-color: #1a1a20; padding: 30px; border-radius: 10px; border: 1px solid #2d2d34; max-width: 500px; text-align: center;">
                <h2 style="color: #ff4a4a;">Missing Dependencies</h2>
                <p>wLib requires the following system packages to function correctly:</p>
                <ul style="text-align: left; background: #25252e; padding: 15px 30px; border-radius: 5px;">
                    {''.join([f'<li><b>{d}</b></li>' for d in missing_deps])}
                </ul>
                <p style="color: #a0a0a0; font-size: 14px;">Please install them using your system package manager and restart wLib.</p>
            </div>
        </body>
        </html>
        """
        import urllib.parse
        url = "data:text/html," + urllib.parse.quote(html_content)
        DEV_MODE = False # Disable dev mode if we're just showing an error
    
    if DEV_MODE:
        url = VITE_DEV_SERVER
        vite_process = start_vite_dev_server()
        # Give Vite a second to start
        time.sleep(2)

    window = webview.create_window(
        'wLib - Game Manager', 
        url,
        js_api=api,
        width=1200,
        height=800,
        background_color='#101014'
    )
    window_ref = window
    api.set_window(window)
    
    # Start the extension background server in a daemon thread
    threading.Thread(target=start_extension_server, daemon=True).start()
    
    # Start the PyWebView UI loop
    # We set debug=False so it doesn't open the Web Inspector automatically
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wlib.png')
    webview.start(debug=False, http_server=True, icon=icon_path if os.path.exists(icon_path) else None)
    
    # Cleanup Vite server when window closes
    if vite_process:
        vite_process.terminate()

if __name__ == '__main__':
    main()
