import sqlite3
conn = sqlite3.connect('data/digest.db')
rows = conn.execute("SELECT name, transcript_priority FROM sources WHERE type='podcast' AND active=1 ORDER BY transcript_priority, name").fetchall()
for name, priority in rows:
    print(f'{priority:15s} {name}')
