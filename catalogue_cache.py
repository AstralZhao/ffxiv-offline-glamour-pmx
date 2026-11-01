"""Portable, source-keyed equipment folder index; no personal or GPU state."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import uuid

VERSION=1
SLOTS=('Head','Body','Gloves','Legs','Feet','Ears','Neck','Wrists','FingerL')


def ensure_catalogue_cache(catalog):
    catalog=Path(catalog).resolve();path=catalog.with_name('equipment-cache.sqlite')
    fingerprint=hashlib.sha256(catalog.read_bytes()).hexdigest()
    expected={'version':str(VERSION),'catalog_sha256':fingerprint}
    if path.is_file():
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True))as conn:
                if dict(conn.execute('SELECT key,value FROM metadata'))==expected:
                    conn.execute('SELECT item_id,model_set,group_count FROM model_folders LIMIT 1').fetchall()
                    return path
        except sqlite3.DatabaseError:pass
    # Build separately so an interrupted build cannot expose a partial index.
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with closing(sqlite3.connect(temporary,uri=True))as conn,conn:
            conn.execute('ATTACH DATABASE ? AS source',(catalog.as_uri()+'?mode=ro',))
            conn.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            conn.executemany('INSERT INTO metadata VALUES(?,?)',expected.items())
            conn.execute('''CREATE TABLE model_folders(
                slot TEXT NOT NULL,model_set INTEGER NOT NULL,item_id INTEGER NOT NULL,
                name TEXT NOT NULL,icon_id INTEGER,dye_count INTEGER,level INTEGER,
                category_name TEXT,model_main INTEGER,group_count INTEGER NOT NULL,
                PRIMARY KEY(slot,model_set))''')
            conn.execute('''INSERT INTO model_folders
                SELECT slot,model_main & 65535,item_id,name,icon_id,dye_count,level,category_name,model_main,group_count
                FROM (SELECT *,ROW_NUMBER() OVER(PARTITION BY slot,(model_main & 65535)
                    ORDER BY dye_count DESC,item_id DESC) AS rank,
                    COUNT(*) OVER(PARTITION BY slot,(model_main & 65535)) AS group_count
                    FROM source.equipment WHERE slot IN ('''+','.join('?'for _ in SLOTS)+')) WHERE rank=1',SLOTS)
        temporary.replace(path)
    finally:
        if temporary.exists():temporary.unlink()
    return path
