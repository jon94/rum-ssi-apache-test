import time
import random
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/api/ping")
def ping():
    return jsonify({"status": "ok", "message": "pong", "service": "rum-demo-backend"})

@app.route("/api/slow")
def slow():
    delay = round(random.uniform(0.4, 1.2), 2)
    time.sleep(delay)
    return jsonify({"status": "ok", "message": "slow response", "latency_ms": int(delay * 1000)})

@app.route("/api/error")
def error():
    raise RuntimeError("Intentional backend error for RUM<>APM demo")

@app.errorhandler(500)
def handle_500(e):
    return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
