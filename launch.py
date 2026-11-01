"""Record startup failures before importing the native client dependencies."""
from datetime import datetime
import os
from pathlib import Path
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
LOG=ROOT/'artifacts/demo/client-startup.log'


def log(text):
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open('a',encoding='utf8')as file:
        file.write(f'\n{datetime.now().isoformat(timespec="seconds")} pid={os.getpid()}\n{text}\n')


def main():
    os.chdir(ROOT)
    try:
        log('Starting native client. Python: '+sys.executable)
        import client
        if sys.argv[1:]==['--startup-check']:
            # Hidden diagnostic of the actual CMD/interpreter/import/Tk
            # startup route. No game, visible client or GPU work is required.
            root=client.tk.Tk()
            try:root.withdraw();root.update_idletasks()
            finally:root.destroy()
            log('STARTUP_CHECK_OK');print('STARTUP_CHECK_OK');return 0
        client.main()
        log('Client closed normally.');return 0
    except Exception:
        detail=traceback.format_exc()
        try:log(detail)
        except OSError:pass
        print(detail,file=sys.stderr)
        print('Client startup failed. Details: '+str(LOG),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
