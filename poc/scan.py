import os
import sys
import csv
import json
import hcl2
import subprocess
from collections import defaultdict

# Example: python scan.py results/examplerun

HERE = os.path.dirname(os.path.abspath(__file__))

def load_labels() -> tuple:
  core = set()
  per_task = defaultdict(set)
  labeled = set()
  with open(os.path.join(HERE, "check_labels.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
      labeled.add(row["check_id"])
      if row["label"] == "core":
        core.add(row["check_id"])
      elif row["label"] == "task":
        per_task[row["task"]].add(row["check_id"])
  return core, per_task, labeled

def parses(path: str) -> bool:
  try:
    with open(path, encoding="utf-8") as f:
      hcl2.load(f)
    return True
  except Exception:
    return False

def failed_checks(directory: str) -> set:
  result = subprocess.run(
    [sys.executable, "-m", "checkov.main", "-d", directory, "--framework", "terraform", "-o", "json", "--quiet", "--compact"],
    capture_output=True, text=True, encoding="utf-8"
  )
  data = json.loads(result.stdout) if result.stdout.strip() else {}
  if isinstance(data, list):
    data = data[0]
  # No "results" means that Checkov had zero checks for these resource types
  return {check["check_id"] for check in data.get("results", {}).get("failed_checks", [])}

def main():
  run_dir = sys.argv[1]
  core, per_task, labeled = load_labels()
  rows = []
  unlabeled = set()

  for folder, _, files in os.walk(run_dir):
    if "main.tf" not in files:
      continue
    model, task, condition, rep = os.path.relpath(folder, run_dir).split(os.sep)
    row = {"model": model, "task": task, "condition": condition, "rep": rep}

    if not parses(os.path.join(folder, "main.tf")):
      print(f"{model} {task} {condition} {rep}: did not parse")
      rows.append({**row, "parsed": False, "failed": "", "core_failed": "", "failed_ids": "", "core_failed_ids": ""})
      continue

    failed = failed_checks(folder)
    core_failed = failed & (core | per_task[task])
    unlabeled |= failed - labeled
    print(f"{model} {task} {condition} {rep}: {len(failed)} failed, {len(core_failed)} core")
    rows.append({
      **row, "parsed": True, "failed": len(failed), "core_failed": len(core_failed),
      "failed_ids": " ".join(sorted(failed)), "core_failed_ids": " ".join(sorted(core_failed))
    })

  with open(os.path.join(run_dir, "checkov.csv"), "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=rows[0].keys())
    writer.writeheader()
    writer.writerows(rows)

  if unlabeled:
    print("\nAdd these to check_labels.csv:", " ".join(sorted(unlabeled)))

  # Summary
  groups = defaultdict(list)
  for row in rows:
    groups[(row["model"], row["condition"])].append(row)
  print("\nmodel / condition: files, did not parse, avg failed (all), avg failed (core), files with 0 core failures")
  for (model, condition), group in sorted(groups.items()):
    ok = [r for r in group if r["parsed"]]
    if not ok:
      print(f"  {model} / {condition}: {len(group)}, {len(group)}, none parsed")
      continue
    avg_all = sum(r["failed"] for r in ok) / len(ok)
    avg_core = sum(r["core_failed"] for r in ok) / len(ok)
    clean = sum(1 for r in ok if r["core_failed"] == 0)
    print(f"  {model} / {condition}: {len(group)}, {len(group) - len(ok)}, {avg_all:.1f}, {avg_core:.2f}, {clean}")

if __name__ == "__main__":
  main()
