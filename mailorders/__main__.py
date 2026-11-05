import sys
import threading
import webbrowser

from . import config, db, worker
from .app import app


def main():
    db.init()
    port = int(config.load()['port'])
    worker.start()
    if '--no-browser' not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    print(f'דשבורד הזמנות: http://localhost:{port}   (לסגירה: לסגור את החלון)')
    app.run(host='127.0.0.1', port=port, threaded=True, use_reloader=False)


main()
