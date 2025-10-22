import os
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

# --- LOAD ENV ---
load_dotenv()

# --- CONFIG ---
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# --- DATABASE SETUP ---
DB_URL = os.getenv("DATABASE_URL")  # Put your CockroachDB URL in .env
engine = create_engine(DB_URL, echo=True, pool_pre_ping=True)
Base = declarative_base()
Session = sessionmaker(bind=engine)
session = Session()

# --- DATABASE MODELS ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True)
    password_hash = Column(String(255))
    online_status = Column(Integer, default=0)

class FriendRequest(Base):
    __tablename__ = "friend_requests"
    id = Column(Integer, primary_key=True)
    sender = Column(String(50))
    receiver = Column(String(50))
    status = Column(String(20), default="pending")

class Friend(Base):
    __tablename__ = "friends"
    id = Column(Integer, primary_key=True)
    user1 = Column(String(50))
    user2 = Column(String(50))

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    room_id = Column(String(100))
    sender = Column(String(50))
    text = Column(Text)
    timestamp = Column(String(50))
    type = Column(String(20), default="text")

Base.metadata.create_all(engine)

# --- AUTH ---
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    username = data["username"]
    password = data["password"]
    password_hash = generate_password_hash(password)
    if session.query(User).filter_by(username=username).first():
        return jsonify({"success": False, "error": "Username already exists"})
    user = User(username=username, password_hash=password_hash)
    session.add(user)
    session.commit()
    return jsonify({"success": True})

@app.route("/login", methods=["POST"])
def login():
    data = request.json
    user = session.query(User).filter_by(username=data["username"]).first()
    if user and check_password_hash(user.password_hash, data["password"]):
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid credentials"})

# --- FRIEND SYSTEM ---
@app.route("/send_friend_request", methods=["POST"])
def send_friend_request():
    data = request.json
    sender, receiver = data["sender"], data["receiver"]
    # Already friends?
    if session.query(Friend).filter(
        ((Friend.user1==sender) & (Friend.user2==receiver)) |
        ((Friend.user1==receiver) & (Friend.user2==sender))
    ).first():
        return jsonify({"success": False, "error": "Already friends"})
    # Request exists?
    if session.query(FriendRequest).filter_by(sender=sender, receiver=receiver, status="pending").first():
        return jsonify({"success": False, "error": "Request already sent"})
    fr = FriendRequest(sender=sender, receiver=receiver)
    session.add(fr)
    session.commit()
    return jsonify({"success": True})

@app.route("/get_friend_requests/<username>")
def get_friend_requests(username):
    requests = session.query(FriendRequest).filter_by(receiver=username, status="pending").all()
    return jsonify([{"id": r.id, "sender": r.sender} for r in requests])

@app.route("/respond_friend_request", methods=["POST"])
def respond_friend_request():
    data = request.json
    fr = session.query(FriendRequest).filter_by(id=data["request_id"]).first()
    if not fr:
        return jsonify({"success": False, "error": "Request not found"})
    if data["action"]=="accept":
        f = Friend(user1=fr.sender, user2=fr.receiver)
        session.add(f)
        fr.status = "accepted"
    else:
        fr.status = "rejected"
    session.commit()
    return jsonify({"success": True})

@app.route("/get_friends/<username>")
def get_friends(username):
    friends = session.query(Friend).filter(
        (Friend.user1==username) | (Friend.user2==username)
    ).all()
    return jsonify([f.user2 if f.user1==username else f.user1 for f in friends])

# --- SOCKET.IO EVENTS ---
@socketio.on("join")
def handle_join(data):
    room, username = data["room"], data["username"]
    join_room(room)
    user = session.query(User).filter_by(username=username).first()
    if user:
        user.online_status = 1
        session.commit()
    emit("user_status", get_online_users(), room=room)
    # Send chat history
    messages = session.query(Message).filter_by(room_id=room).order_by(Message.id.asc()).all()
    emit("chat_history", [{"sender": m.sender, "text": m.text, "timestamp": m.timestamp, "type": m.type} for m in messages])

@socketio.on("send_message")
def handle_send_message(data):
    room, sender, text = data["room"], data["sender"], data["text"]
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    m = Message(room_id=room, sender=sender, text=text, type="text", timestamp=timestamp)
    session.add(m)
    session.commit()
    emit("message", {"sender": sender, "text": text, "type":"text","timestamp":timestamp}, room=room)

@socketio.on("typing")
def handle_typing(data):
    emit("typing", {"username": data["username"]}, room=data["room"], include_self=False)

@socketio.on("disconnect_user")
def handle_disconnect(data):
    username, room = data["username"], data["room"]
    leave_room(room)
    user = session.query(User).filter_by(username=username).first()
    if user:
        user.online_status = 0
        session.commit()
    emit("user_status", get_online_users(), room=room)

def get_online_users():
    users = session.query(User).filter_by(online_status=1).all()
    return {"online_users": [u.username for u in users]}

# --- FILE UPLOAD ---
@app.route("/upload", methods=["POST"])
def upload_file():
    file = request.files["file"]
    room, sender = request.form["room"], request.form["sender"]
    filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    m = Message(room_id=room, sender=sender, text=filename, type="file", timestamp=timestamp)
    session.add(m)
    session.commit()
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
