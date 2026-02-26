with open('core/launcher.py', 'r') as f:
    content = f.read()

old_thread = """                            now = time.time()
                            delta = int(now - last_saved_time)
                            last_saved_time = now
                            on_exit_callback(delta)
                            
                        now = time.time()
                        delta = int(now - last_saved_time)
                        if delta > 0:
                            on_exit_callback(delta)"""

new_thread = """                            now = time.time()
                            delta = int(now - last_saved_time)
                            last_saved_time = now
                            on_exit_callback(delta, is_final=False)
                            
                        now = time.time()
                        delta = int(now - last_saved_time)
                        if delta > 0:
                            on_exit_callback(delta, is_final=True)
                        else:
                            on_exit_callback(0, is_final=True)"""

content = content.replace(old_thread, new_thread)
with open('core/launcher.py', 'w') as f:
    f.write(content)
