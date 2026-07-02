import json
import pandas as pd
from pathlib import Path
from datetime import date

# 1. Define paths safely
data_file = Path("data/latest.csv")
history_path = Path("processed/history.json")

# Ensure the processed folder exists
history_path.parent.mkdir(parents=True, exist_ok=True)

if not data_file.exists():
    print("No new data found in data/latest.csv. Skipping updates.")
    exit(0)

# 2. Read the newly uploaded CSV
df = pd.read_csv(data_file)

new_snapshot = {
    "date": str(date.today()),
    "source_file": "latest.csv",
    "records": df.to_dict(orient="records")
}

# 3. Read existing history or start fresh
if history_path.exists() and history_path.stat().st_size > 0:
    try:
        history = json.loads(history_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        history = []
else:
    history = []

# 4. Append new snapshot and write back to disk
history.append(new_snapshot)
history_path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding='utf-8')
print("Successfully appended latest data to history.json!")
