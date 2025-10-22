from flask import Flask, request
from flask_socketio import SocketIO, join_room, leave_room, emit
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --- ONE-ON-ONE CHAT LOGIC ---
@socketio.on("join")
def handle_join(data):
    room = data["room"]
    join_room(room)
    emit("message", {"sender": "System", "text": "User joined the chat"}, room=room)

@socketio.on("send_message")
def handle_send_message(data):
    room = data["room"]
    emit("message", {"sender": data["sender"], "text": data["text"]}, room=room)

@app.route("/")
def home():
    return "Socket.IO One-on-One Chat Server is running!"

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
