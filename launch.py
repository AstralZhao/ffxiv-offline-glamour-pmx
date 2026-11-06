"""Record startup failures before importing the native client dependencies."""
from datetime import datetime
import os
from pathlib import Path
import sys
sys.dont_write_bytecode=True
import traceback

ROOT=Path(__file__).resolve().parents[1]
from app_paths import paths,migrate,verify_source_release
LOG=paths().derived('logs')/'client-startup.log'


def log(text):
    LOG.parent.mkdir(parents=True,exist_ok=True)
    with LOG.open('a',encoding='utf8')as file:
        file.write(f'\n{datetime.now().isoformat(timespec="seconds")} pid={os.getpid()}\n{text}\n')


def main():
    os.chdir(ROOT)
    try:
        verify_source_release()
        if sys.argv[1:]!=['--startup-check']:migrate()
        log('Starting native client. Python: '+sys.executable)
        import qt_client as client
        if sys.argv[1:]==['--startup-check']:
            # Hidden diagnostic of the actual CMD/interpreter/import/Qt
            # startup route. No game, visible client or GPU work is required.
            from runtime_identity import compiled_manifest
            from runtime_files import native_static_assets
            compiled_manifest();native_static_assets()
            client.startup_check()
            log('STARTUP_CHECK_OK');print('STARTUP_CHECK_OK');return 0
        result=client.main()
        log('Client closed normally.');return int(result or 0)
    except Exception:
        detail=traceback.format_exc()
        try:log(detail)
        except OSError:pass
        print(detail,file=sys.stderr)
        print('Client startup failed. Details: '+str(LOG),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
