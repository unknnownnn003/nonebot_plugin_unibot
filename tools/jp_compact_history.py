"""Remove archival XML bytes after their source metadata has been indexed."""


def discard_xml(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='raw_xml'").fetchone():
        return
    # Both legacy indexes identify XML by the same primary key as sources.
    for table in ('raw_we', 'raw_song_index'):
        ddl = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (table,)).fetchone()[0]
        db.execute(f'ALTER TABLE {table} RENAME TO {table}_archive')
        db.execute(ddl.replace('REFERENCES raw_xml', 'REFERENCES sources'))
        db.execute(f'INSERT INTO {table} SELECT * FROM {table}_archive')
        db.execute(f'DROP TABLE {table}_archive')
    db.execute('DROP TABLE raw_xml')
