import sqlite3
import json
import os

DB_FILE = "papers.db"
JSON_FILE = "paper_database.json"

# 初始化数据库
conn = sqlite3.connect(DB_FILE)
c = conn.cursor()

# 文献表
c.execute('''CREATE TABLE IF NOT EXISTS papers
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT,
              author TEXT,
              year TEXT,
              filename TEXT)''')

# 用户表
c.execute('''CREATE TABLE IF NOT EXISTS users
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT UNIQUE,
              password TEXT,
              api_key TEXT)''')

conn.commit()

# 如果有JSON文件，导入数据
if os.path.exists(JSON_FILE):
    with open(JSON_FILE, "r", encoding="utf-8") as f:
        papers = json.load(f)

    for p in papers:
        c.execute("INSERT INTO papers (title, author, year, filename) VALUES (?, ?, ?, ?)",
                  (p.get("title", "未知"), p.get("author", "未知"), p.get("year", "未知"), p.get("filename", "")))

    conn.commit()
    print(f"已导入 {len(papers)} 篇文献到数据库")
else:
    print("没有找到 paper_database.json 文件")

conn.close()
print("数据库初始化完成")
