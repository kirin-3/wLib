import re

with open('core/api.py', 'r') as f:
    content = f.read()

# Replace on_exit signature and logic
old_on_exit = """        def on_exit(delta):
            try:
                from core.database import update_playtime

                update_playtime(game_id, delta)
                import webview

                if webview.windows:
                    webview.windows[0].evaluate_js(
                        "window.dispatchEvent(new CustomEvent('wlib-refresh-library'))"
                    )
            except Exception as e:
                print(f"Failed to update playtime for game {game_id}: {e}")"""

new_on_exit = """        def on_exit(delta, is_final=True):
            try:
                from core.database import update_playtime

                update_playtime(game_id, delta)
                import webview

                if is_final and webview.windows:
                    webview.windows[0].evaluate_js(
                        "window.dispatchEvent(new CustomEvent('wlib-refresh-library'))"
                    )
            except Exception as e:
                print(f"Failed to update playtime for game {game_id}: {e}")"""

content = content.replace(old_on_exit, new_on_exit)
with open('core/api.py', 'w') as f:
    f.write(content)
