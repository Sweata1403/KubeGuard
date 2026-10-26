"""Print a Markdown handover report from the persistent incident journal."""
import datetime
import os
import sqlite3

if __name__ == "__main__":
    db = sqlite3.connect(os.getenv("JOURNAL", "/data/incidents.db"))
    print("# KubeGuard incident handover\n")
    for key, status, detail, updated in db.execute("SELECT key, status, detail, updated FROM incidents ORDER BY updated DESC"):
        stamp = datetime.datetime.fromtimestamp(updated, datetime.timezone.utc).isoformat()
        print(f"- **{status}** — {stamp} — `{key}`: {detail}")
    db.close()
