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
    # Users table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password_hash TEXT,
            online_status INTEGER
        )
    ''')
    # Rooms table
    c.execute('''
        CREATE TABLE IF NOT EXISTS rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT UNIQUE,
            room_name TEXT,
            password_hash TEXT,
            created_by TEXT,
            created_at TEXT
        )
    ''')
    # Messages table
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
    conn.commit()
    conn.close()

init_db()

# --- HELPERS ---
def room_exists_and_password_ok(room_id: str, password: str) -> (bool, str):
    """
    Returns (True, "") if room exists and password matches.
    Returns (False, error_message) otherwise.
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM rooms WHERE room_id=?", (room_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return False, "Room not found"
    stored_hash = row[0]
    if check_password_hash(stored_hash, password):
        return True, ""
    return False, "Incorrect password"

# --- USER ROUTES ---
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"success": False, "error": "username and password required"}), 400

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
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        return jsonify({"success": False, "error": "username and password required"}), 400

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if row and check_password_hash(row[0], password):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid credentials"})

# --- ROOM ROUTES ---
@app.route("/create_room", methods=["POST"])
def create_room():
    """
    Request JSON:
    {
        "room_id": "room123",      # unique identifier
        "room_name": "My Room",
        "password": "secret",
        "created_by": "alice"
    }
    """
    data = request.json or {}
    room_id = data.get("room_id")
    room_name = data.get("room_name", room_id)
    password = data.get("password")
    created_by = data.get("created_by", "unknown")

    if not room_id or password is None:
        return jsonify({"success": False, "error": "room_id and password are required"}), 400

    password_hash = generate_password_hash(password)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            "INSERT INTO rooms (room_id, room_name, password_hash, created_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (room_id, room_name, password_hash, created_by, created_at)
        )
        conn.commit()
        conn.close()
        return jsonify({"success": True, "message": "Room created"})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "Room ID already exists"})

@app.route("/join_room", methods=["POST"])
def join_room_api():
    """
    Validate room + password before attempting a socket join.
    Request JSON:
    { "room_id": "...", "password": "..." }
    """
    data = request.json or {}
    room_id = data.get("room_id")
    password = data.get("password")
    if not room_id or password is None:
        return jsonify({"success": False, "error": "room_id and password required"}), 400

    ok, err = room_exists_and_password_ok(room_id, password)
    if ok:
        return jsonify({"success": True, "message": "Access granted"})
    else:
        return jsonify({"success": False, "error": err})

@app.route("/rooms/<room_id>", methods=["GET"])
def get_room_info(room_id):
    """
    Public info about a room (not returning password hash).
    """
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT room_id, room_name, created_by, created_at FROM rooms WHERE room_id=?", (room_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return jsonify({"success": False, "error": "Room not found"}), 404
    room_info = {
        "room_id": row[0],
        "room_name": row[1],
        "created_by": row[2],
        "created_at": row[3]
    }
    return jsonify({"success": True, "room": room_info})

# --- SOCKET.IO EVENTS ---
@socketio.on("join")
def handle_join(data):
    """
    Socket join requires server-side validation. The client should either:
    1) Call POST /join_room first and then emit this event with room & password,
       or
    2) Emit this event with room & password and the server will validate here too.

    Expected data:
    { "room": "room123", "username": "alice", "password": "secret" }
    """
    room = data.get("room")
    username = data.get("username")
    password = data.get("password")

    if not room or not username or password is None:
        emit("error", {"error": "room, username and password are required for join"})
        return

    ok, err = room_exists_and_password_ok(room, password)
    if not ok:
        emit("error", {"error": f"Cannot join room: {err}"})
        return

    # Now allow socket to join
    join_room(room)

    # Set user online
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET online_status=1 WHERE username=?", (username,))
    conn.commit()
    conn.close()

    emit("message", {"sender": "System", "text": f"{username} joined the chat", "type":"text"}, room=room)
    emit("user_status", get_online_users(), room=room)

    # Send chat history for this room
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT sender, text, timestamp, type FROM messages WHERE room_id=? ORDER BY id ASC", (room,))
    messages = [{"sender": s, "text": t, "timestamp": ts, "type": tp} for s, t, ts, tp in c.fetchall()]
    conn.close()
    emit("chat_history", messages)

@socketio.on("send_message")
def handle_send_message(data):
    room = data.get("room")
    sender = data.get("sender")
    text = data.get("text")

    if not room or not sender or text is None:
        emit("error", {"error": "room, sender, and text are required to send a message"})
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Save message
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO messages (room_id, sender, text, timestamp, type) VALUES (?, ?, ?, ?, ?)",
              (room, sender, text, timestamp, "text"))
    conn.commit()
    conn.close()
    emit("message", {"sender": sender, "text": text, "type":"text", "timestamp": timestamp}, room=room)

@socketio.on("typing")
def handle_typing(data):
    room = data.get("room")
    username = data.get("username")
    if not room or not username:
        return
    emit("typing", {"username": username}, room=room, include_self=False)

@socketio.on("disconnect_user")
def handle_disconnect(data):
    username = data.get("username")
    room = data.get("room")
    if room:
        leave_room(room)
    if username:
        # Set user offline
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE users SET online_status=0 WHERE username=?", (username,))
        conn.commit()
        conn.close()
        # Inform room(s) the user left
        if room:
            emit("message", {"sender": "System", "text": f"{username} left the chat", "type":"text"}, room=room)
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
    """
    Form-data expected:
      file: <file>
      room: <room_id>
      sender: <username>
      password: <room_password>   # optional, server will validate if present
    """
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400
    file = request.files["file"]
    room = request.form.get("room")
    sender = request.form.get("sender")
    password = request.form.get("password")  # optional but recommended

    if not room or not sender:
        return jsonify({"success": False, "error": "room and sender are required"}), 400

    # If password provided, validate before allowing upload
    if password is not None:
        ok, err = room_exists_and_password_ok(room, password)
        if not ok:
            return jsonify({"success": False, "error": f"Room validation failed: {err}"}), 403

    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Save to DB
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO messages (room_id, sender, text, timestamp, type) VALUES (?, ?, ?, ?, ?)",
              (room, sender, filename, timestamp, "file"))
    conn.commit()
    conn.close()
    socketio.emit("message", {"sender": sender, "text": filename, "type":"file", "timestamp": timestamp}, room=room)
    return jsonify({"success": True, "filename": filename})

@app.route("/uploads/<filename>")
def serve_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# --- RUN SERVER ---
@app.route("/")
def home():
    return "Full-featured Chat Server Running!"

if __name__ == "__main__":
    # Runs the Socket.IO server. Set debug=True if you want auto-reload during development.
    socketio.run(app, host="0.0.0.0", port=5000)
