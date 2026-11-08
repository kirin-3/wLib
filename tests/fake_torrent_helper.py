# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in torrent helper for supervisor tests; behaviour comes from FAKE_MODE."""
import json
import os
import sys
import time

MODE = os.environ.get("FAKE_MODE", "complete")
STATE = os.environ.get("FAKE_STATE", "")
LOG = os.environ.get("FAKE_LOG", "")


def emit(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def note(line):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def launches():
    count = 0
    if STATE:
        try:
            count = int(open(STATE).read() or 0)
        except OSError:
            pass
        open(STATE, "w").write(str(count + 1))
    return count


def main():
    n = launches()
    if MODE == "noimport":
        emit({"event": "error", "message": "Torrent downloads are unavailable in this build", "code": "engine_missing"})
        sys.exit(3)
    jobs = {}
    adds = 0
    peers = int(os.environ.get("FAKE_PEERS", "2"))
    for line in sys.stdin:
        cmd = json.loads(line)
        note(json.dumps({k: v for k, v in cmd.items()}))
        name = cmd["cmd"]
        if name == "hello":
            time.sleep(float(os.environ.get("FAKE_READY_DELAY", "0")))
            emit({"event": "ready", "port": 1})
        elif name == "add":
            if MODE == "crash_always" or (MODE == "crash_once" and n == 0):
                sys.exit(1)
            if MODE == "badpaths":
                emit({"event": "error", "id": cmd["id"], "message": "x", "code": "unsafe_paths"})
                continue
            # error_once: the first torrent added hits a write error.
            jobs[cmd["id"]] = {"dir": cmd["dir"], "paused": cmd["paused"], "done": 0.0, "error": MODE == "error_once" and adds == 0}
            adds += 1
        elif name in ("pause", "resume"):
            if cmd["id"] not in jobs:
                emit({"event": "error", "id": cmd["id"], "message": "Bad command"})
                continue
            jobs[cmd["id"]]["paused"] = name == "pause"
        elif name in ("remove", "stop_seeding"):
            jobs.pop(cmd["id"], None)
            emit({"event": "removed", "id": cmd["id"]})
        elif name == "shutdown":
            emit({"event": "bye"})
            return
        # progress every command round; the fake also advances on a timer below
        if MODE == "complete":
            for job_id, job in list(jobs.items()):
                if not job["paused"] and job["done"] < 1.0:
                    job["done"] = 1.0
                    data = os.path.join(job["dir"], "data")
                    os.makedirs(data, exist_ok=True)
                    open(os.path.join(data, "game.zip"), "wb").write(b"PK")
                    emit({"event": "status", "torrents": [{"id": job_id, "state": "seeding", "size": 100, "downloaded": 100, "down": 0, "up": 0, "peers": peers, "seeds": 1, "uploaded": 0, "seeding_seconds": 0, "finished": True, "paused": False, "progress": 1.0, "tracker": ""}]})
                    emit({"event": "finished", "id": job_id})
        elif MODE in ("stalled", "crash_once", "crash_always", "error_once"):
            emit({"event": "status", "torrents": [
                {"id": job_id, "state": "error" if job["error"] else "downloading", "size": 100, "downloaded": 30, "down": int(os.environ.get("FAKE_SPEED", "0")), "up": 0, "peers": peers, "seeds": 0, "uploaded": 0, "seeding_seconds": 0, "finished": False, "paused": job["paused"], "progress": 0.3, "tracker": ""}
                for job_id, job in jobs.items()]})


main()
