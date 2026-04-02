import sqlite3
conn = sqlite3.connect('data/digest.db')
cur = conn.execute("""
    UPDATE articles SET needs_transcription = 0
    WHERE needs_transcription = 1
    AND (content IS NULL OR content = '')
""")
conn.commit()
print(f'Cleared {cur.rowcount} articles from Whisper queue')
conn.close()
