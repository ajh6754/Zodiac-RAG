import os
import re
import sys
import csv
import json
import hcl2
import shutil
import tempfile
import subprocess
from collections import defaultdict
from dotenv import load_dotenv, find_dotenv

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

def plan(folder: str) -> tuple:
  # Runs terraform plan on a copy of main.tf and then saves the output as single plan.txt file
  saved = os.path.join(folder, "plan.txt")
  if not os.path.exists(saved): # make sure it's not been run already
    work = tempfile.mkdtemp()
    shutil.copy(os.path.join(folder, "main.tf"), work)
    init = subprocess.run(["terraform", "init", "-input=false", "-no-color"], cwd=work, capture_output=True, text=True, encoding="utf-8")
    output = init.stdout + init.stderr
    if init.returncode == 0:
      result = subprocess.run(["terraform", "plan", "-input=false", "-no-color", "-lock=false"], cwd=work, capture_output=True, text=True, encoding="utf-8")
      output = result.stdout + result.stderr
    shutil.rmtree(work, ignore_errors=True)
    with open(saved, "w", encoding="utf-8") as f:
      f.write(output)

  with open(saved, encoding="utf-8") as f:
    output = f.read()
  errors = re.findall(r"Error: (.*)", output)
  if not errors:
    return "pass", ""
  if all(e.startswith("No value for required variable") for e in errors):
    return "needs_input", ""
  return "fail", errors[0]

def main():
  run_dir = sys.argv[1]
  core, per_task, labeled = load_labels()
  rows = []
  unlabeled = set()

  # Plan needs both terraform and an Azure login/subscription (from .env)
  load_dotenv(find_dotenv())
  os.environ["ARM_RESOURCE_PROVIDER_REGISTRATIONS"] = "none"  # IMPORTANT - don't let plan change anything on the subscription
  os.environ.setdefault("TF_PLUGIN_CACHE_DIR", os.path.join(HERE, ".tfcache"))  # download the Azure provider once
  os.makedirs(os.environ["TF_PLUGIN_CACHE_DIR"], exist_ok=True)
  run_plan = shutil.which("terraform") is not None
  if not run_plan:
    print("terraform not found, skipping plan")

  for folder, _, files in os.walk(run_dir):
    if "main.tf" not in files:
      continue
    model, task, condition, rep = os.path.relpath(folder, run_dir).split(os.sep)
    row = {"model": model, "task": task, "condition": condition, "rep": rep}

    if not parses(os.path.join(folder, "main.tf")):
      print(f"{model} {task} {condition} {rep}: did not parse")
      rows.append({**row, "parsed": False, "plan": "fail", "plan_error": "did not parse", "failed": "", "core_failed": "", "failed_ids": "", "core_failed_ids": ""})
      continue

    plan_result, plan_error = plan(folder) if run_plan else ("skipped", "")
    failed = failed_checks(folder)
    core_failed = failed & (core | per_task[task])
    unlabeled |= failed - labeled
    print(f"{model} {task} {condition} {rep}: plan {plan_result}, {len(failed)} failed, {len(core_failed)} core")
    rows.append({
      **row, "parsed": True, "plan": plan_result, "plan_error": plan_error,
      "failed": len(failed), "core_failed": len(core_failed),
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
  print("\nmodel / condition: files, did not parse, plan ok (pass or needs input), avg failed (all), avg failed (core), files with 0 core failures")
  for (model, condition), group in sorted(groups.items()):
    ok = [r for r in group if r["parsed"]]
    plan_ok = sum(1 for r in group if r["plan"] in ("pass", "needs_input"))
    if not ok:
      print(f"  {model} / {condition}: {len(group)}, {len(group)}, 0, none parsed")
      continue
    avg_all = sum(r["failed"] for r in ok) / len(ok)
    avg_core = sum(r["core_failed"] for r in ok) / len(ok)
    clean = sum(1 for r in ok if r["core_failed"] == 0)
    print(f"  {model} / {condition}: {len(group)}, {len(group) - len(ok)}, {plan_ok}, {avg_all:.1f}, {avg_core:.2f}, {clean}")

if __name__ == "__main__":
  main()
