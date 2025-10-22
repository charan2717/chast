import os
import sqlite3
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# --- CONFIG ---
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
DB_FILE = "chat.db"

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Users
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            online_status INTEGER
        )
    ''')
    # Messages
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT,
            sender TEXT,
            text TEXT,
            timestamp TEXT,
            type TEXT
        )
    ''')
    # Friend requests
    c.execute('''
        CREATE TABLE IF NOT EXISTS friend_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT,
            receiver TEXT,
            status TEXT
        )
    ''')
    # Friends
    c.execute('''
        CREATE TABLE IF NOT EXISTS friends (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1 TEXT,
            user2 TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# --- AUTH ---
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    username = data["username"]
    password = data["password"]
    password_hash = generate_password_hash(password)
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT INTO users (username, password_hash, online_status) VALUES (?, ?, 0)",
                  (username, password_hash))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "Username already exists"})

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    username = data["username"]
    password = data["password"]
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if row and check_password_hash(row[0], password):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid credentials"})

# --- FRIEND SYSTEM ---
@app.route("/send_friend_request", methods=["POST"])
def send_friend_request():
    data = request.json
    sender = data["sender"]
    receiver = data["receiver"]
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Check if already friends
    c.execute("SELECT * FROM friends WHERE (user1=? AND user2=?) OR (user1=? AND user2=?)",
              (sender, receiver, receiver, sender))
    if c.fetchone():
        conn.close()
        return jsonify({"success": False, "error": "Already friends"})
    # Check if request exists
    c.execute("SELECT * FROM friend_requests WHERE sender=? AND receiver=? AND status='pending'",
              (sender, receiver))
    if c.fetchone():
        conn.close()
        return jsonify({"success": False, "error": "Request already sent"})
    c.execute("INSERT INTO friend_requests (sender, receiver, status) VALUES (?,?,?)",
              (sender, receiver, "pending"))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/get_friend_requests/<username>")
def get_friend_requests(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT sender, id FROM friend_requests WHERE receiver=? AND status='pending'", (username,))
    requests = [{"id": r[1], "sender": r[0]} for r in c.fetchall()]
    conn.close()
    return jsonify(requests)

@app.route("/respond_friend_request", methods=["POST"])
def respond_friend_request():
    data = request.json
    request_id = data["request_id"]
    action = data["action"]  # accept/reject
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT sender, receiver FROM friend_requests WHERE id=?", (request_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return jsonify({"success": False, "error": "Request not found"})
    sender, receiver = row
    if action=="accept":
        # Add to friends
        c.execute("INSERT INTO friends (user1,user2) VALUES (?,?)", (sender, receiver))
        c.execute("UPDATE friend_requests SET status='accepted' WHERE id=?", (request_id,))
    else:
        c.execute("UPDATE friend_requests SET status='rejected' WHERE id=?", (request_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/get_friends/<username>")
def get_friends(username):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user1,user2 FROM friends WHERE user1=? OR user2=?", (username, username))
    friends = [f[1] if f[0]==username else f[0] for f in c.fetchall()]
    conn.close()
    return jsonify(friends)

# --- SOCKET.IO EVENTS ---
@socketio.on("join")
def handle_join(data):
    room = data["room"]
    username = data["username"]
    join_room(room)
    # Set online
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET online_status=1 WHERE username=?", (username,))
    conn.commit()
    conn.close()
    emit("user_status", get_online_users(), room=room)
    # Send chat history
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT sender,text,timestamp,type FROM messages WHERE room_id=? ORDER BY id ASC", (room,))
    messages = [{"sender": s, "text": t, "timestamp": ts, "type": tp} for s,t,ts,tp in c.fetchall()]
    conn.close()
    emit("chat_history", messages)

@socketio.on("send_message")
def handle_send_message(data):
    room = data["room"]
    sender = data["sender"]
    text = data["text"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Save
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO messages (room_id,sender,text,timestamp,type) VALUES (?,?,?,?,?)",
              (room,sender,text,timestamp,"text"))
    conn.commit()
    conn.close()
    emit("message", {"sender": sender, "text": text, "type":"text","timestamp":timestamp}, room=room)

@socketio.on("typing")
def handle_typing(data):
    room = data["room"]
    username = data["username"]
    emit("typing", {"username": username}, room=room, include_self=False)

@socketio.on("disconnect_user")
def handle_disconnect(data):
    username = data["username"]
    room = data["room"]
    leave_room(room)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET online_status=0 WHERE username=?", (username,))
    conn.commit()
    conn.close()
    emit("user_status", get_online_users(), room=room)

def get_online_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT username FROM users WHERE online_status=1")
    users = [u[0] for u in c.fetchall()]
    conn.close()
    return {"online_users": users}

# --- FILE UPLOAD ---
@app.route("/upload", methods=["POST"])
def upload_file():
    file = request.files["file"]
    room = request.form["room"]
    sender = request.form["sender"]
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO messages (room_id,sender,text,timestamp,type) VALUES (?,?,?,?,?)",
              (room,sender,filename,timestamp,"file"))
    conn.commit()
    conn.close()
    socketio.emit("message", {"sender": sender, "text": filename, "type":"file","timestamp":timestamp}, room=room)
    return jsonify({"success": True, "filename": filename})

@app.route("/uploads/<filename>")
def serve_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route("/")
def home():
    return "Social Chat Server Running!"

if __name__=="__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
