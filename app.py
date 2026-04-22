import os
import re
import json
import sqlite3
import hashlib
import requests
from flask import Flask, request, jsonify, session
from werkzeug.utils import secure_filename
from pypdf import PdfReader

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = 'uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

DB_FILE = "papers.db"

# 支持的AI模型配置
AI_MODELS = {
    "deepseek": {
        "name": "DeepSeek",
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "key_prefix": "sk-",
        "get_key_url": "https://platform.deepseek.com"
    },
    "zhipu": {
        "name": "智谱AI (GLM)",
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash",
        "key_prefix": "",
        "get_key_url": "https://open.bigmodel.cn"
    },
    "qwen": {
        "name": "通义千问",
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-turbo",
        "key_prefix": "sk-",
        "get_key_url": "https://dashscope.console.aliyun.com"
    },
    "doubao": {
        "name": "豆包",
        "url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        "model": "doubao-pro-32k",
        "key_prefix": "",
        "get_key_url": "https://console.volcengine.com/ark"
    }
}

# ----------------------
# 初始化数据库
# ----------------------
def init_db():
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
                  api_key TEXT,
                  api_provider TEXT DEFAULT 'deepseek',
                  is_admin INTEGER DEFAULT 0)''')

    # 检查是否需要添加新字段
    c.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in c.fetchall()]
    if 'is_admin' not in columns:
        c.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
    if 'api_provider' not in columns:
        c.execute("ALTER TABLE users ADD COLUMN api_provider TEXT DEFAULT 'deepseek'")

    # 创建默认管理员账号
    c.execute("SELECT * FROM users WHERE username = 'admin'")
    if not c.fetchone():
        c.execute("INSERT INTO users (username, password, api_key, api_provider, is_admin) VALUES (?, ?, ?, ?, 1)",
                  ('admin', hash_password('admin123'), '', 'deepseek'))

    conn.commit()
    conn.close()

init_db()

# ----------------------
# 密码加密
# ----------------------
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ----------------------
# 用户相关函数
# ----------------------
def get_user_by_username(username):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None

def create_user(username, password):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password, api_key, is_admin) VALUES (?, ?, ?, 0)",
                  (username, hash_password(password), ''))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False

def update_user_api_key(username, api_key, api_provider='deepseek'):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET api_key = ?, api_provider = ? WHERE username = ?", (api_key, api_provider, username))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, username, is_admin, api_provider FROM users")
    rows = c.fetchall()
    conn.close()
    return [{"id": r["id"], "username": r["username"], "is_admin": r["is_admin"], "api_provider": r["api_provider"]} for r in rows]

def delete_user_by_id(user_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM users WHERE id = ? AND is_admin = 0", (user_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0

# ----------------------
# 文献相关函数
# ----------------------
def load_papers():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT title, author, year, filename FROM papers")
    rows = c.fetchall()
    conn.close()
    return [{"title": r["title"], "author": r["author"], "year": r["year"], "filename": r["filename"]} for r in rows]

def add_paper_to_db(paper):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO papers (title, author, year, filename) VALUES (?, ?, ?, ?)",
              (paper["title"], paper["author"], paper["year"], paper.get("filename", "")))
    conn.commit()
    conn.close()

def update_paper_in_db(index, paper):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM papers ORDER BY id LIMIT 1 OFFSET ?", (index,))
    row = c.fetchone()
    if row:
        c.execute("UPDATE papers SET title=?, author=?, year=?, filename=? WHERE id=?",
                  (paper["title"], paper["author"], paper["year"], paper.get("filename", ""), row[0]))
        conn.commit()
    conn.close()

def delete_paper_from_db(index):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id FROM papers ORDER BY id LIMIT 1 OFFSET ?", (index,))
    row = c.fetchone()
    if row:
        c.execute("DELETE FROM papers WHERE id=?", (row[0],))
        conn.commit()
    conn.close()

# ----------------------
# AI相关函数
# ----------------------
def call_ai(prompt, api_key, provider='deepseek'):
    """调用AI API，支持多个模型"""
    if not api_key or provider not in AI_MODELS:
        return None

    config = AI_MODELS[provider]
    try:
        headers = {
            "Content-Type": "application/json"
        }

        # 不同模型的认证方式
        if provider == "zhipu":
            headers["Authorization"] = f"Bearer {api_key}"
        elif provider == "doubao":
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": config["model"],
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1
        }

        response = requests.post(
            config["url"],
            headers=headers,
            json=payload,
            timeout=60
        )

        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"]
        else:
            print(f"API错误 ({provider}): {response.status_code} - {response.text}")
    except Exception as e:
        print(f"API调用失败 ({provider}): {e}")
    return None

# 保留旧函数名兼容
def call_deepseek(prompt, api_key):
    return call_ai(prompt, api_key, 'deepseek')

def extract_info_by_ai(text, api_key, provider='deepseek'):
    try:
        prompt = f"""请从以下学术论文首页文本中提取信息，以JSON格式返回：
{{
    "title": "论文标题",
    "author": "第一作者姓名",
    "year": "发表年份"
}}

注意：
1. 标题通常是最大字号、最显眼的文字
2. 作者名通常在标题下方
3. 年份可能在作者信息中

文本内容：
{text[:3000]}"""

        content = call_ai(prompt, api_key, provider)
        if content:
            json_match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
    except Exception as e:
        print(f"AI提取失败: {e}")
    return None

def ai_check_duplicate(new_info, existing_info, api_key, provider='deepseek'):
    try:
        prompt = f"""请判断以下两篇学术论文是否为同一篇文献：

新文献：
- 标题：{new_info.get('title', '')}
- 作者：{new_info.get('author', '')}
- 年份：{new_info.get('year', '')}

已有文献：
- 标题：{existing_info.get('title', '')}
- 作者：{existing_info.get('author', '')}
- 年份：{existing_info.get('year', '')}

请只回答JSON格式：
{{"is_duplicate": true/false, "reason": "简短理由"}}"""

        content = call_ai(prompt, api_key, provider)
        if content:
            json_match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return result.get('is_duplicate', False)
    except Exception as e:
        print(f"AI查重失败: {e}")
    return False

def extract_text_from_pdf(pdf_path):
    try:
        reader = PdfReader(pdf_path)
        if len(reader.pages) > 0:
            return reader.pages[0].extract_text()
    except:
        pass
    return ""

def extract_info_from_pdf(pdf_path, filename, api_key, provider='deepseek'):
    try:
        text = extract_text_from_pdf(pdf_path)
        ai_info = extract_info_by_ai(text, api_key, provider) if api_key else None

        if ai_info:
            if ai_info.get('year') == '未知' or not ai_info.get('year'):
                year_match = re.search(r'(20\d{2})', filename)
                ai_info['year'] = year_match.group(1) if year_match else "未知"
            return {
                "title": ai_info.get('title', '未知'),
                "author": ai_info.get('author', '未知'),
                "year": ai_info.get('year', '未知'),
                "filename": filename
            }

        # 无API Key时用传统方法
        reader = PdfReader(pdf_path)
        info = reader.metadata
        title = info.title if info and info.title else None
        author = info.author if info and info.author else None

        year_match = re.search(r'(20\d{2})', filename)
        year = year_match.group(1) if year_match else "未知"

        if not title and len(reader.pages) > 0:
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            for line in lines[:15]:
                if len(line) > 15 and not line.lower().startswith(('abstract', 'keywords', 'introduction', 'http', 'www', 'doi')):
                    title = line
                    break

        if not author:
            author_match = re.match(r'^[\d\s]*([A-Za-z一-鿿]+)', filename)
            author = author_match.group(1) if author_match else "未知"

        return {
            "title": title or filename.replace('.pdf', ''),
            "author": author,
            "year": year,
            "filename": filename
        }
    except Exception as e:
        return None

def check_duplicate(new_info, paper_list, api_key, provider='deepseek'):
    if not new_info or not new_info.get('title'):
        return False, None

    new_title = new_info['title']
    new_clean = re.sub(r'[^\w]', '', new_title.lower()).strip()
    candidates = []

    for paper in paper_list:
        p_title = paper.get("title", "")
        p_clean = re.sub(r'[^\w]', '', p_title.lower()).strip()

        if new_clean and p_clean:
            if new_clean in p_clean or p_clean in new_clean:
                candidates.append(paper)
                continue
            common = set(new_clean) & set(p_clean)
            rate = len(common) / max(len(new_clean), len(p_clean)) * 100
            if rate >= 60:
                candidates.append(paper)

    # 有API Key时用AI确认
    if api_key:
        for paper in candidates:
            if ai_check_duplicate(new_info, paper, api_key, provider):
                return True, paper
    else:
        # 无API Key时直接返回第一个候选
        if candidates:
            return True, candidates[0]

    return False, None

# ----------------------
# 登录页面
# ----------------------
@app.route('/login')
def login_page():
    return '''
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>登录 - 组会文献查重系统</title>
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}
body{
    min-height:100vh;
    display:flex;
    align-items:center;
    justify-content:center;
    font-family:'Nunito',-apple-system,sans-serif;
    background:linear-gradient(135deg,#fce4ec 0%,#e3f2fd 100%);
    margin:0;
    padding:20px
}
.login-box{
    background:white;
    padding:40px;
    border-radius:20px;
    box-shadow:0 10px 40px rgba(0,0,0,0.1);
    width:100%;
    max-width:400px
}
.login-title{
    text-align:center;
    color:#e91e63;
    font-size:1.8em;
    margin-bottom:30px
}
.form-group{
    margin-bottom:20px
}
.form-label{
    display:block;
    margin-bottom:8px;
    color:#666;
    font-weight:600
}
.form-input{
    width:100%;
    padding:12px 15px;
    border:2px solid #fce4ec;
    border-radius:10px;
    font-size:1em;
    transition:border-color 0.3s
}
.form-input:focus{
    outline:none;
    border-color:#e91e63
}
.submit-btn{
    width:100%;
    padding:14px;
    background:linear-gradient(135deg,#e91e63,#f48fb1);
    color:white;
    border:none;
    border-radius:50px;
    cursor:pointer;
    font-weight:700;
    font-size:1.1em;
    margin-top:10px
}
.submit-btn:hover{
    opacity:0.9
}
.switch-link{
    text-align:center;
    margin-top:20px;
    color:#666
}
.switch-link a{
    color:#e91e63;
    text-decoration:none;
    font-weight:600
}
.error-msg{
    background:#ffebee;
    color:#c62828;
    padding:10px;
    border-radius:8px;
    margin-bottom:15px;
    text-align:center;
    display:none
}
</style>
</head>
<body>
<div class="login-box">
    <h1 class="login-title">📚 组会文献查重</h1>
    <div class="error-msg" id="errorMsg"></div>
    <form id="loginForm">
        <div class="form-group">
            <label class="form-label">用户名</label>
            <input type="text" class="form-input" id="username" required>
        </div>
        <div class="form-group">
            <label class="form-label">密码</label>
            <input type="password" class="form-input" id="password" required>
        </div>
        <button type="submit" class="submit-btn">登录</button>
    </form>
    <div class="switch-link">
        还没有账号？<a href="/register">立即注册</a>
    </div>
</div>
<script>
document.getElementById('loginForm').addEventListener('submit', async function(e){
    e.preventDefault();
    let username = document.getElementById('username').value;
    let password = document.getElementById('password').value;

    let res = await fetch('/api/login', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password})
    });
    let data = await res.json();

    if(data.success){
        window.location.href = '/';
    } else {
        document.getElementById('errorMsg').textContent = data.error;
        document.getElementById('errorMsg').style.display = 'block';
    }
});
</script>
</body>
</html>
'''

# ----------------------
# 注册页面
# ----------------------
@app.route('/register')
def register_page():
    return '''
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>注册 - 组会文献查重系统</title>
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}
body{
    min-height:100vh;
    display:flex;
    align-items:center;
    justify-content:center;
    font-family:'Nunito',-apple-system,sans-serif;
    background:linear-gradient(135deg,#fce4ec 0%,#e3f2fd 100%);
    margin:0;
    padding:20px
}
.login-box{
    background:white;
    padding:40px;
    border-radius:20px;
    box-shadow:0 10px 40px rgba(0,0,0,0.1);
    width:100%;
    max-width:400px
}
.login-title{
    text-align:center;
    color:#e91e63;
    font-size:1.8em;
    margin-bottom:30px
}
.form-group{
    margin-bottom:20px
}
.form-label{
    display:block;
    margin-bottom:8px;
    color:#666;
    font-weight:600
}
.form-input{
    width:100%;
    padding:12px 15px;
    border:2px solid #fce4ec;
    border-radius:10px;
    font-size:1em;
    transition:border-color 0.3s
}
.form-input:focus{
    outline:none;
    border-color:#e91e63
}
.submit-btn{
    width:100%;
    padding:14px;
    background:linear-gradient(135deg,#e91e63,#f48fb1);
    color:white;
    border:none;
    border-radius:50px;
    cursor:pointer;
    font-weight:700;
    font-size:1.1em;
    margin-top:10px
}
.submit-btn:hover{
    opacity:0.9
}
.switch-link{
    text-align:center;
    margin-top:20px;
    color:#666
}
.switch-link a{
    color:#e91e63;
    text-decoration:none;
    font-weight:600
}
.error-msg{
    background:#ffebee;
    color:#c62828;
    padding:10px;
    border-radius:8px;
    margin-bottom:15px;
    text-align:center;
    display:none
}
.success-msg{
    background:#e8f5e9;
    color:#2e7d32;
    padding:10px;
    border-radius:8px;
    margin-bottom:15px;
    text-align:center;
    display:none
}
</style>
</head>
<body>
<div class="login-box">
    <h1 class="login-title">📚 注册账号</h1>
    <div class="error-msg" id="errorMsg"></div>
    <div class="success-msg" id="successMsg"></div>
    <form id="registerForm">
        <div class="form-group">
            <label class="form-label">用户名</label>
            <input type="text" class="form-input" id="username" required>
        </div>
        <div class="form-group">
            <label class="form-label">密码</label>
            <input type="password" class="form-input" id="password" required>
        </div>
        <div class="form-group">
            <label class="form-label">确认密码</label>
            <input type="password" class="form-input" id="password2" required>
        </div>
        <button type="submit" class="submit-btn">注册</button>
    </form>
    <div class="switch-link">
        已有账号？<a href="/login">立即登录</a>
    </div>
</div>
<script>
document.getElementById('registerForm').addEventListener('submit', async function(e){
    e.preventDefault();
    let username = document.getElementById('username').value;
    let password = document.getElementById('password').value;
    let password2 = document.getElementById('password2').value;

    if(password !== password2){
        document.getElementById('errorMsg').textContent = '两次密码不一致';
        document.getElementById('errorMsg').style.display = 'block';
        return;
    }

    let res = await fetch('/api/register', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username, password})
    });
    let data = await res.json();

    if(data.success){
        document.getElementById('successMsg').textContent = '注册成功！即将跳转...';
        document.getElementById('successMsg').style.display = 'block';
        setTimeout(() => window.location.href = '/login', 1500);
    } else {
        document.getElementById('errorMsg').textContent = data.error;
        document.getElementById('errorMsg').style.display = 'block';
    }
});
</script>
</body>
</html>
'''

# ----------------------
# API接口：登录
# ----------------------
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username', '')
    password = data.get('password', '')

    user = get_user_by_username(username)
    if user and user['password'] == hash_password(password):
        session['user'] = username
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "用户名或密码错误"})

# ----------------------
# API接口：注册
# ----------------------
@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if len(username) < 2:
        return jsonify({"success": False, "error": "用户名至少2个字符"})
    if len(password) < 4:
        return jsonify({"success": False, "error": "密码至少4个字符"})

    if create_user(username, password):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "用户名已存在"})

# ----------------------
# API接口：登出
# ----------------------
@app.route('/api/logout')
def api_logout():
    session.pop('user', None)
    return jsonify({"success": True})

# ----------------------
# API接口：获取/设置API Key和模型
# ----------------------
@app.route('/api/user/apikey', methods=['GET', 'POST'])
def api_user_apikey():
    if 'user' not in session:
        return jsonify({"error": "not logged in"}), 401

    if request.method == 'GET':
        user = get_user_by_username(session['user'])
        return jsonify({
            "api_key": user.get('api_key', ''),
            "api_provider": user.get('api_provider', 'deepseek')
        })

    data = request.json
    api_key = data.get('api_key', '')
    api_provider = data.get('api_provider', 'deepseek')
    update_user_api_key(session['user'], api_key, api_provider)
    return jsonify({"success": True})

# API接口：获取支持的AI模型列表
# ----------------------
@app.route('/api/models')
def api_models():
    return jsonify(AI_MODELS)

# ----------------------
# 主页面
# ----------------------
@app.route('/')
def index():
    if 'user' not in session:
        return '''
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>组会文献查重系统</title>
<style>
body{min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:-apple-system,sans-serif;background:linear-gradient(135deg,#fce4ec 0%,#e3f2fd 100%);margin:0}
.welcome-box{text-align:center;background:white;padding:60px;border-radius:20px;box-shadow:0 10px 40px rgba(0,0,0,0.1)}
h1{color:#e91e63;font-size:2.5em;margin-bottom:20px}
p{color:#666;margin-bottom:30px}
.btn{display:inline-block;padding:15px 40px;background:linear-gradient(135deg,#e91e63,#f48fb1);color:white;text-decoration:none;border-radius:50px;font-weight:700;margin:5px}
</style>
</head>
<body>
<div class="welcome-box">
    <h1>📚 组会文献查重系统</h1>
    <p>请登录后使用</p>
    <a href="/login" class="btn">登录</a>
    <a href="/register" class="btn" style="background:linear-gradient(135deg,#7c4dff,#b388ff)">注册</a>
</div>
</body>
</html>
'''

    user = get_user_by_username(session['user'])
    return f'''
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>组会文献查重系统</title>
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box}}
body{{max-width:1000px;margin:0 auto;font-family:'Nunito',-apple-system,sans-serif;padding:30px 20px;background:linear-gradient(135deg,#fce4ec 0%,#e3f2fd 100%);min-height:100vh}}
.header{{text-align:center;margin-bottom:30px}}
.header h1{{color:#e91e63;font-size:2.2em;margin:0;display:flex;align-items:center;justify-content:center;gap:10px}}
.header p{{color:#666;margin-top:8px;font-size:1.1em}}
.count-badge{{display:inline-block;background:linear-gradient(135deg,#e91e63,#f48fb1);color:white;padding:8px 20px;border-radius:50px;font-weight:700;margin-top:15px;box-shadow:0 4px 15px rgba(233,30,99,0.3)}}
.user-bar{{display:flex;justify-content:space-between;align-items:center;background:white;padding:15px 25px;border-radius:15px;margin-bottom:20px;box-shadow:0 4px 15px rgba(0,0,0,0.05)}}
.user-info{{display:flex;align-items:center;gap:15px}}
.user-name{{font-weight:700;color:#333}}
.api-status{{font-size:0.9em;padding:5px 12px;border-radius:20px}}
.api-set{{background:#e8f5e9;color:#2e7d32}}
.api-notset{{background:#fff3e0;color:#ef6c00}}
.logout-btn{{padding:8px 20px;background:#f5f5f5;color:#666;border:none;border-radius:20px;cursor:pointer;font-weight:600}}
.logout-btn:hover{{background:#eee}}
.upload-section{{background:white;border-radius:20px;padding:35px;margin:25px 0;box-shadow:0 10px 40px rgba(0,0,0,0.1);text-align:center}}
.upload-area{{border:3px dashed #f48fb1;padding:40px;border-radius:15px;background:#fce4ec20;transition:all 0.3s}}
.upload-area.dragover{{border-color:#e91e63;background:#fce4ec60}}
.upload-icon{{font-size:50px;margin-bottom:15px}}
.file-input{{display:none}}
.file-label{{display:inline-block;padding:12px 30px;background:linear-gradient(135deg,#e91e63,#f48fb1);color:white;border-radius:50px;cursor:pointer;font-weight:600;transition:transform 0.2s,box-shadow 0.2s;box-shadow:0 4px 15px rgba(233,30,99,0.3)}}
.file-label:hover{{transform:translateY(-2px);box-shadow:0 6px 20px rgba(233,30,99,0.4)}}
.file-name{{margin-top:15px;color:#666;font-size:0.95em}}
.file-list{{margin-top:15px;text-align:left;max-height:150px;overflow-y:auto}}
.file-item{{padding:8px 12px;background:#f5f5f5;margin:5px 0;border-radius:8px;font-size:0.9em;display:flex;justify-content:space-between;align-items:center}}
.file-item .remove-btn{{color:#e53935;cursor:pointer;font-weight:bold}}
.check-btn{{margin-top:20px;padding:14px 40px;background:linear-gradient(135deg,#7c4dff,#b388ff);color:white;border:none;border-radius:50px;cursor:pointer;font-weight:700;font-size:1.1em;transition:transform 0.2s,box-shadow 0.2s;box-shadow:0 4px 15px rgba(124,77,255,0.3)}}
.check-btn:hover{{transform:translateY(-2px);box-shadow:0 6px 20px rgba(124,77,255,0.4)}}
.check-btn:disabled{{background:#ccc;cursor:not-allowed;transform:none;box-shadow:none}}
.progress-section{{margin-top:25px;display:none}}
.progress-bar{{height:10px;background:#f5f5f5;border-radius:10px;overflow:hidden}}
.progress-fill{{height:100%;background:linear-gradient(135deg,#e91e63,#f48fb1);width:0%;transition:width 0.3s}}
.progress-text{{margin-top:10px;color:#666;font-size:0.9em}}
.result-section{{margin-top:25px;display:none}}
.result-item{{padding:20px;margin:10px 0;border-radius:12px;text-align:left}}
.result-duplicate{{background:linear-gradient(135deg,#ffebee,#ffcdd2);border-left:5px solid #e53935}}
.result-new{{background:linear-gradient(135deg,#e8f5e9,#c8e6c9);border-left:5px solid #43a047}}
.result-title{{font-weight:700;font-size:1.1em;margin-bottom:8px}}
.result-meta{{color:#666;font-size:0.9em}}
.result-match{{background:white;padding:10px;border-radius:8px;margin-top:10px;font-size:0.9em}}
.add-selected-btn{{margin-top:20px;padding:12px 35px;background:linear-gradient(135deg,#00c853,#69f0ae);color:white;border:none;border-radius:50px;cursor:pointer;font-weight:700;display:none}}
.paper-list-section{{margin-top:40px;background:white;border-radius:20px;padding:30px;box-shadow:0 10px 40px rgba(0,0,0,0.1)}}
.paper-list-header{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:20px}}
.paper-list-header h2{{color:#e91e63;margin:0;font-size:1.5em}}
.add-paper-btn{{padding:10px 20px;background:linear-gradient(135deg,#00c853,#69f0ae);color:white;border:none;border-radius:50px;cursor:pointer;font-weight:600;font-size:0.9em}}
.search-input{{width:100%;max-width:400px;padding:12px 20px;border:2px solid #fce4ec;border-radius:50px;font-size:1em;transition:border-color 0.3s;margin-bottom:20px}}
.search-input:focus{{outline:none;border-color:#e91e63}}
.paper-item{{background:linear-gradient(135deg,#fce4ec10,#e3f2fd10);padding:15px 20px;margin:12px 0;border-radius:12px;border-left:4px solid #e91e63;transition:transform 0.2s,box-shadow 0.2s;position:relative}}
.paper-item:hover{{transform:translateX(5px);box-shadow:0 4px 15px rgba(233,30,99,0.1)}}
.paper-title{{font-weight:700;color:#333;font-size:1.05em;padding-right:80px}}
.paper-meta{{color:#888;font-size:0.9em;margin-top:6px}}
.paper-actions{{position:absolute;right:15px;top:50%;transform:translateY(-50%);display:flex;gap:8px}}
.edit-btn,.delete-btn{{padding:6px 12px;border:none;border-radius:20px;cursor:pointer;font-size:0.85em;font-weight:600}}
.edit-btn{{background:#e3f2fd;color:#1976d2}}
.edit-btn:hover{{background:#bbdefb}}
.delete-btn{{background:#ffebee;color:#e53935}}
.delete-btn:hover{{background:#ffcdd2}}
.empty-msg{{text-align:center;color:#aaa;padding:30px}}
.loading{{text-align:center;padding:20px;color:#e91e63}}
.modal{{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:1000;justify-content:center;align-items:center}}
.modal.show{{display:flex}}
.modal-content{{background:white;padding:30px;border-radius:20px;width:90%;max-width:500px;box-shadow:0 20px 60px rgba(0,0,0,0.3)}}
.modal-title{{color:#e91e63;margin:0 0 20px 0;font-size:1.3em}}
.modal-input{{width:100%;padding:12px 15px;border:2px solid #fce4ec;border-radius:10px;font-size:1em;margin-bottom:15px}}
.modal-input:focus{{outline:none;border-color:#e91e63}}
.modal-label{{display:block;margin-bottom:5px;color:#666;font-weight:600}}
.modal-btns{{display:flex;gap:10px;justify-content:flex-end;margin-top:20px}}
.modal-btn{{padding:10px 25px;border:none;border-radius:50px;cursor:pointer;font-weight:600}}
.modal-cancel{{background:#f5f5f5;color:#666}}
.modal-save{{background:linear-gradient(135deg,#e91e63,#f48fb1);color:white}}
</style>
</head>
<body>
<div class="header">
    <h1><span>📚</span> 组会文献查重系统</h1>
    <p>支持批量上传PDF，AI智能识别与查重</p>
    <div class="count-badge">已收录 <span id="count">...</span> 篇文献</div>
</div>

<div class="user-bar">
    <div class="user-info">
        <span class="user-name">👤 {session['user']}</span>
        <span class="api-status" id="apiStatus">检查中...</span>
        {'<a href="/admin" style="margin-left:15px;color:#e91e63;font-weight:600">🔧 管理后台</a>' if user.get('is_admin') else ''}
    </div>
    <button class="logout-btn" onclick="location.href='/api/logout'">退出登录</button>
</div>

<div class="upload-section">
    <div class="upload-area" id="uploadArea">
        <div class="upload-icon">📄</div>
        <input type="file" id="pdfFiles" class="file-input" accept=".pdf" multiple>
        <label for="pdfFiles" class="file-label">选择PDF文件（可多选）</label>
        <div class="file-name" id="fileName">支持拖拽上传，可一次选择多个文件</div>
        <div class="file-list" id="fileList"></div>
    </div>
    <button class="check-btn" id="checkBtn" onclick="startBatchCheck()" disabled>开始批量查重</button>

    <div class="progress-section" id="progressSection">
        <div class="progress-bar">
            <div class="progress-fill" id="progressFill"></div>
        </div>
        <div class="progress-text" id="progressText">准备中...</div>
    </div>
</div>

<div class="result-section" id="resultSection">
    <div id="resultList"></div>
    <button class="add-selected-btn" id="addSelectedBtn" onclick="addSelectedPapers()">添加选中的新文献到数据库</button>
</div>

<div class="paper-list-section">
    <div class="paper-list-header">
        <h2>📖 已分享文献汇总</h2>
        <button class="add-paper-btn" onclick="showAddModal()">+ 手动添加</button>
    </div>
    <input type="text" id="searchInput" class="search-input" placeholder="搜索标题、作者或年份..." oninput="filterPapers()">
    <div id="paperList" class="loading">加载中...</div>
</div>

<div id="editModal" class="modal">
    <div class="modal-content">
        <h3 class="modal-title">编辑文献信息</h3>
        <label class="modal-label">标题</label>
        <input type="text" id="editTitle" class="modal-input">
        <label class="modal-label">作者</label>
        <input type="text" id="editAuthor" class="modal-input">
        <label class="modal-label">年份</label>
        <input type="text" id="editYear" class="modal-input">
        <input type="hidden" id="editIndex">
        <div class="modal-btns">
            <button class="modal-btn modal-cancel" onclick="closeModal()">取消</button>
            <button class="modal-btn modal-save" onclick="saveEdit()">保存</button>
        </div>
    </div>
</div>

<div id="addModal" class="modal">
    <div class="modal-content">
        <h3 class="modal-title">手动添加文献</h3>
        <label class="modal-label">标题</label>
        <input type="text" id="addTitle" class="modal-input" placeholder="请输入论文标题">
        <label class="modal-label">作者</label>
        <input type="text" id="addAuthor" class="modal-input" placeholder="请输入作者">
        <label class="modal-label">年份</label>
        <input type="text" id="addYear" class="modal-input" placeholder="请输入年份">
        <div class="modal-btns">
            <button class="modal-btn modal-cancel" onclick="closeAddModal()">取消</button>
            <button class="modal-btn modal-save" onclick="manualAdd()">添加</button>
        </div>
    </div>
</div>

<div id="apiKeyModal" class="modal">
    <div class="modal-content">
        <h3 class="modal-title">设置 AI 模型 API</h3>
        <p style="color:#666;font-size:0.9em;margin-bottom:15px">选择AI模型并输入对应的API Key，费用自理。</p>
        <label class="modal-label">选择模型</label>
        <select id="apiProvider" class="modal-input" style="cursor:pointer" onchange="updateModelInfo()">
            <option value="deepseek">DeepSeek</option>
            <option value="zhipu">智谱AI (GLM)</option>
            <option value="qwen">通义千问</option>
            <option value="doubao">豆包</option>
        </select>
        <div id="modelInfo" style="background:#f5f5f5;padding:10px;border-radius:8px;margin-bottom:15px;font-size:0.85em">
            <div><strong>获取地址：</strong><a id="modelUrl" href="https://platform.deepseek.com" target="_blank">platform.deepseek.com</a></div>
        </div>
        <label class="modal-label">API Key</label>
        <input type="text" id="apiKeyInput" class="modal-input" placeholder="输入API Key">
        <div class="modal-btns">
            <button class="modal-btn modal-cancel" onclick="closeApiKeyModal()">取消</button>
            <button class="modal-btn modal-save" onclick="saveApiKey()">保存</button>
        </div>
    </div>
</div>

<script>
let allPapers = [];
let selectedFiles = [];
let checkResults = [];
let userApiKey = '';
let userApiProvider = 'deepseek';

const modelUrls = {{
    'deepseek': 'https://platform.deepseek.com',
    'zhipu': 'https://open.bigmodel.cn',
    'qwen': 'https://dashscope.console.aliyun.com',
    'doubao': 'https://console.volcengine.com/ark'
}};

const modelNames = {{
    'deepseek': 'DeepSeek',
    'zhipu': '智谱AI',
    'qwen': '通义千问',
    'doubao': '豆包'
}};

function updateModelInfo(){{
    let provider = document.getElementById('apiProvider').value;
    document.getElementById('modelUrl').href = modelUrls[provider];
    document.getElementById('modelUrl').textContent = modelUrls[provider];
}}

// 检查API Key状态
async function checkApiStatus(){{
    let res = await fetch('/api/user/apikey');
    let data = await res.json();
    userApiKey = data.api_key || '';
    userApiProvider = data.api_provider || 'deepseek';

    let statusEl = document.getElementById('apiStatus');
    if(userApiKey){{
        statusEl.className = 'api-status api-set';
        statusEl.innerHTML = '✅ ' + modelNames[userApiProvider] + ' 已设置 <a href="#" onclick="showApiKeyModal()" style="margin-left:10px;color:#e91e63">修改</a>';
    }} else {{
        statusEl.className = 'api-status api-notset';
        statusEl.innerHTML = '⚠️ 未设置AI模型 <a href="#" onclick="showApiKeyModal()" style="margin-left:10px;color:#e91e63">设置</a>';
    }}
}}

function showApiKeyModal(){{
    document.getElementById('apiProvider').value = userApiProvider;
    document.getElementById('apiKeyInput').value = userApiKey;
    updateModelInfo();
    document.getElementById('apiKeyModal').classList.add('show');
}}

function closeApiKeyModal(){{
    document.getElementById('apiKeyModal').classList.remove('show');
}}

async function saveApiKey(){{
    let key = document.getElementById('apiKeyInput').value.trim();
    let provider = document.getElementById('apiProvider').value;
    let res = await fetch('/api/user/apikey', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{api_key: key, api_provider: provider}})
    }});
    let data = await res.json();
    if(data.success){{
        userApiKey = key;
        userApiProvider = provider;
        closeApiKeyModal();
        checkApiStatus();
    }}
}}

// 文件选择处理
document.getElementById('pdfFiles').addEventListener('change', function(e){{
    selectedFiles = Array.from(e.target.files);
    updateFileList();
}});

const uploadArea = document.getElementById('uploadArea');
uploadArea.addEventListener('dragover', function(e){{e.preventDefault();uploadArea.classList.add('dragover')}});
uploadArea.addEventListener('dragleave', function(){{uploadArea.classList.remove('dragover')}});
uploadArea.addEventListener('drop', function(e){{
    e.preventDefault();
    uploadArea.classList.remove('dragover');
    selectedFiles = Array.from(e.dataTransfer.files).filter(f => f.name.toLowerCase().endsWith('.pdf'));
    updateFileList();
}});

function updateFileList(){{
    let html = '';
    selectedFiles.forEach((f, i) => {{
        html += `<div class="file-item"><span>📄 ${{f.name}}</span><span class="remove-btn" onclick="removeFile(${{i}})">✕</span></div>`;
    }});
    document.getElementById('fileList').innerHTML = html;
    document.getElementById('checkBtn').disabled = selectedFiles.length === 0;
    document.getElementById('fileName').textContent = selectedFiles.length > 0 ? `已选择 ${{selectedFiles.length}} 个文件` : '支持拖拽上传，可一次选择多个文件';
}}

function removeFile(index){{selectedFiles.splice(index, 1);updateFileList()}}

async function loadCount(){{let r = await fetch('/count');let d = await r.json();document.getElementById('count').textContent = d.count}}
async function loadPapers(){{let r = await fetch('/papers');allPapers = await r.json();renderPapers(allPapers)}}

function renderPapers(papers){{
    let html = '';
    if(papers.length === 0){{
        html = '<div class="empty-msg">暂无文献记录</div>';
    }} else {{
        papers.forEach((p, i) => {{
            html += `<div class="paper-item"><div class="paper-title">${{i+1}}. ${{p.title}}</div><div class="paper-meta">作者：${{p.author}} | 年份：${{p.year}}</div><div class="paper-actions"><button class="edit-btn" onclick="editPaper(${{i}})">编辑</button><button class="delete-btn" onclick="deletePaper(${{i}})">删除</button></div></div>`;
        }});
    }}
    document.getElementById('paperList').innerHTML = html;
}}

function filterPapers(){{
    let keyword = document.getElementById('searchInput').value.toLowerCase();
    let filtered = allPapers.filter(p => p.title.toLowerCase().includes(keyword) || p.author.toLowerCase().includes(keyword) || p.year.includes(keyword));
    renderPapers(filtered);
}}

async function startBatchCheck(){{
    if(selectedFiles.length === 0) return;
    if(!userApiKey){{alert('请先设置API Key');showApiKeyModal();return}}

    document.getElementById('progressSection').style.display = 'block';
    document.getElementById('resultSection').style.display = 'none';
    document.getElementById('checkBtn').disabled = true;
    checkResults = [];

    for(let i = 0; i < selectedFiles.length; i++){{
        let file = selectedFiles[i];
        document.getElementById('progressText').textContent = `正在处理: ${{file.name}} (${{i+1}}/${{selectedFiles.length}})`;
        document.getElementById('progressFill').style.width = ((i+1) / selectedFiles.length * 100) + '%';

        let form = new FormData();
        form.append('pdf', file);
        try{{
            let res = await fetch('/upload-check', {{method:'POST', body:form}});
            let data = await res.json();
            checkResults.push({{filename: file.name, ...data}});
        }}catch(e){{
            checkResults.push({{filename: file.name, error: true}});
        }}
    }}
    showResults();
}}

function showResults(){{
    document.getElementById('progressSection').style.display = 'none';
    document.getElementById('resultSection').style.display = 'block';
    document.getElementById('checkBtn').disabled = false;

    let html = '';
    let newCount = 0;

    checkResults.forEach((r, i) => {{
        if(r.error){{
            html += `<div class="result-item" style="background:#fff3e0;border-left:5px solid #ff9800"><div class="result-title">⚠️ 处理失败</div><div class="result-meta">${{r.filename}}</div></div>`;
        }} else if(r.duplicate){{
            html += `<div class="result-item result-duplicate"><div class="result-title">❌ 已存在</div><div class="result-meta"><strong>${{r.title}}</strong><br>作者：${{r.full_info?.author || '未知'}} | 年份：${{r.full_info?.year || '未知'}}</div><div class="result-match">匹配到已有文献：<br>${{r.paper?.title || ''}}<br><small>作者：${{r.paper?.author || ''}} | 年份：${{r.paper?.year || ''}}</small></div></div>`;
        }} else {{
            newCount++;
            html += `<div class="result-item result-new"><input type="checkbox" id="check${{i}}" checked style="margin-right:10px"><div style="display:inline-block;vertical-align:top;width:calc(100% - 30px)"><div class="result-title">✅ 新文献</div><div class="result-meta"><strong>${{r.title}}</strong><br>作者：${{r.full_info?.author || '未知'}} | 年份：${{r.full_info?.year || '未知'}}</div></div></div>`;
        }}
    }});

    document.getElementById('resultList').innerHTML = html;
    document.getElementById('addSelectedBtn').style.display = newCount > 0 ? 'block' : 'none';
}}

async function addSelectedPapers(){{
    let toAdd = [];
    checkResults.forEach((r, i) => {{
        if(!r.error && !r.duplicate && document.getElementById('check'+i)?.checked){{
            toAdd.push(r.full_info);
        }}
    }});

    if(toAdd.length === 0){{alert('请至少选择一篇文献');return}}

    let res = await fetch('/batch-add-papers', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{papers: toAdd}})}});
    let data = await res.json();

    if(data.success){{
        alert(`成功添加 ${{data.added}} 篇文献`);
        document.getElementById('resultSection').style.display = 'none';
        selectedFiles = [];
        updateFileList();
        loadCount();
        loadPapers();
    }}
}}

function editPaper(index){{
    let paper = allPapers[index];
    document.getElementById('editTitle').value = paper.title;
    document.getElementById('editAuthor').value = paper.author;
    document.getElementById('editYear').value = paper.year;
    document.getElementById('editIndex').value = index;
    document.getElementById('editModal').classList.add('show');
}}

function closeModal(){{document.getElementById('editModal').classList.remove('show')}}

async function saveEdit(){{
    let index = parseInt(document.getElementById('editIndex').value);
    let updatedPaper = {{title: document.getElementById('editTitle').value, author: document.getElementById('editAuthor').value, year: document.getElementById('editYear').value, filename: allPapers[index].filename || ''}};
    let res = await fetch('/update-paper', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{index: index, paper: updatedPaper}})}});
    let data = await res.json();
    if(data.success){{closeModal();loadPapers()}}
}}

async function deletePaper(index){{
    if(!confirm('确定要删除这篇文献吗？')) return;
    let res = await fetch('/delete-paper', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{index: index}})}});
    let data = await res.json();
    if(data.success){{loadPapers();loadCount()}}
}}

function showAddModal(){{document.getElementById('addTitle').value = '';document.getElementById('addAuthor').value = '';document.getElementById('addYear').value = '';document.getElementById('addModal').classList.add('show')}}
function closeAddModal(){{document.getElementById('addModal').classList.remove('show')}}

async function manualAdd(){{
    let paper = {{title: document.getElementById('addTitle').value, author: document.getElementById('addAuthor').value, year: document.getElementById('addYear').value, filename: ''}};
    if(!paper.title){{alert('请输入标题');return}}
    let res = await fetch('/add-paper', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(paper)}});
    let data = await res.json();
    if(data.success){{closeAddModal();loadPapers();loadCount()}}
}}

checkApiStatus();
loadCount();
loadPapers();
</script>
</body>
</html>
'''

# ----------------------
# 接口：文献数量
# ----------------------
@app.route('/count')
def count():
    return jsonify(count=len(load_papers()))

# ----------------------
# 接口：文献列表
# ----------------------
@app.route('/papers')
def papers():
    return jsonify(load_papers())

# ----------------------
# 接口：上传PDF + 查重
# ----------------------
@app.route('/upload-check', methods=['POST'])
def upload_check():
    if 'user' not in session:
        return jsonify({"error": "not logged in"}), 401

    if 'pdf' not in request.files:
        return jsonify({"error": "no file"}), 400

    file = request.files['pdf']
    if file.filename == '':
        return jsonify({"error": "no selected file"}), 400

    filename = secure_filename(file.filename)
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(path)

    user = get_user_by_username(session['user'])
    api_key = user.get('api_key', '')
    api_provider = user.get('api_provider', 'deepseek')

    full_info = extract_info_from_pdf(path, filename, api_key, api_provider)
    os.remove(path)

    if not full_info:
        return jsonify({"error": "extract failed"}), 500

    papers = load_papers()
    dup, paper = check_duplicate(full_info, papers, api_key, api_provider)

    return jsonify({
        "title": full_info['title'],
        "duplicate": dup,
        "paper": paper,
        "full_info": full_info
    })

# ----------------------
# 接口：添加单篇文献
# ----------------------
@app.route('/add-paper', methods=['POST'])
def add_paper():
    if 'user' not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.json
    if not data or 'title' not in data:
        return jsonify({"error": "missing data"}), 400

    new_paper = {
        "title": data.get('title', '未知'),
        "author": data.get('author', '未知'),
        "year": data.get('year', '未知'),
        "filename": data.get('filename', '')
    }
    add_paper_to_db(new_paper)
    return jsonify({"success": True, "paper": new_paper})

# ----------------------
# 接口：批量添加文献
# ----------------------
@app.route('/batch-add-papers', methods=['POST'])
def batch_add_papers():
    if 'user' not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.json
    papers_to_add = data.get('papers', [])

    if not papers_to_add:
        return jsonify({"error": "no papers"}), 400

    added = 0
    for p in papers_to_add:
        if p and p.get('title'):
            add_paper_to_db({
                "title": p.get('title', '未知'),
                "author": p.get('author', '未知'),
                "year": p.get('year', '未知'),
                "filename": p.get('filename', '')
            })
            added += 1

    return jsonify({"success": True, "added": added})

# ----------------------
# 接口：更新文献
# ----------------------
@app.route('/update-paper', methods=['POST'])
def update_paper():
    if 'user' not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.json
    index = data.get('index')
    paper = data.get('paper')

    if index is None or not paper:
        return jsonify({"error": "missing data"}), 400

    papers = load_papers()
    if index < 0 or index >= len(papers):
        return jsonify({"error": "invalid index"}), 400

    update_paper_in_db(index, paper)
    return jsonify({"success": True})

# ----------------------
# 接口：删除文献
# ----------------------
@app.route('/delete-paper', methods=['POST'])
def delete_paper():
    if 'user' not in session:
        return jsonify({"error": "not logged in"}), 401

    data = request.json
    index = data.get('index')

    if index is None:
        return jsonify({"error": "missing index"}), 400

    papers = load_papers()
    if index < 0 or index >= len(papers):
        return jsonify({"error": "invalid index"}), 400

    delete_paper_from_db(index)
    return jsonify({"success": True})

# ----------------------
# 管理员页面
# ----------------------
@app.route('/admin')
def admin_page():
    if 'user' not in session:
        return '<script>alert("请先登录");location.href="/login"</script>'

    user = get_user_by_username(session['user'])
    if not user or not user.get('is_admin'):
        return '<script>alert("无权限访问");location.href="/"</script>'

    users = get_all_users()
    papers = load_papers()

    user_list_html = ''
    for u in users:
        if u['is_admin']:
            user_list_html += f'<tr><td>{u["username"]}</td><td><span style="color:#e91e63">管理员</span></td><td>-</td></tr>'
        else:
            user_list_html += f'<tr><td>{u["username"]}</td><td>普通用户</td><td><button onclick="deleteUser({u["id"]})" style="background:#ffebee;color:#e53935;border:none;padding:5px 15px;border-radius:5px;cursor:pointer">删除</button></td></tr>'

    paper_list_html = ''
    for i, p in enumerate(papers):
        paper_list_html += f'<tr><td>{i+1}</td><td>{p["title"][:60]}...</td><td>{p["author"]}</td><td>{p["year"]}</td></tr>'

    return f'''
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>管理员后台</title>
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box}}
body{{max-width:1000px;margin:0 auto;font-family:'Nunito',-apple-system,sans-serif;padding:30px 20px;background:linear-gradient(135deg,#fce4ec 0%,#e3f2fd 100%);min-height:100vh}}
h1{{color:#e91e63;text-align:center}}
h2{{color:#333;margin-top:30px}}
.back-btn{{display:inline-block;padding:10px 20px;background:#f5f5f5;color:#666;text-decoration:none;border-radius:20px;margin-bottom:20px}}
.back-btn:hover{{background:#eee}}
.section{{background:white;padding:25px;border-radius:15px;margin:20px 0;box-shadow:0 4px 15px rgba(0,0,0,0.05)}}
table{{width:100%;border-collapse:collapse;margin-top:15px}}
th,td{{padding:12px;text-align:left;border-bottom:1px solid #eee}}
th{{background:#f5f5f5;font-weight:600}}
tr:hover{{background:#fafafa}}
</style>
</head>
<body>
<a href="/" class="back-btn">← 返回主页</a>
<h1>🔧 管理员后台</h1>

<div class="section">
    <h2>👥 用户管理</h2>
    <table>
        <tr><th>用户名</th><th>角色</th><th>操作</th></tr>
        {user_list_html}
    </table>
</div>

<div class="section">
    <h2>📚 文献库 (共 {len(papers)} 篇)</h2>
    <table>
        <tr><th>#</th><th>标题</th><th>作者</th><th>年份</th></tr>
        {paper_list_html}
    </table>
</div>

<script>
async function deleteUser(userId){{
    if(!confirm('确定要删除这个用户吗？')) return;
    let res = await fetch('/admin/delete-user', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{user_id: userId}})
    }});
    let data = await res.json();
    if(data.success){{
        alert('删除成功');
        location.reload();
    }} else {{
        alert('删除失败：' + data.error);
    }}
}}
</script>
</body>
</html>
'''

# ----------------------
# 接口：管理员删除用户
# ----------------------
@app.route('/admin/delete-user', methods=['POST'])
def admin_delete_user():
    if 'user' not in session:
        return jsonify({"error": "not logged in"}), 401

    user = get_user_by_username(session['user'])
    if not user or not user.get('is_admin'):
        return jsonify({"error": "no permission"}), 403

    data = request.json
    user_id = data.get('user_id')

    if delete_user_by_id(user_id):
        return jsonify({"success": True})
    return jsonify({"error": "cannot delete admin or user not found"}), 400

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    print("文献查重系统已启动")
    app.run(host='0.0.0.0', port=port)
