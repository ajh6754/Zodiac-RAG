import os
import csv
import json
import glob
import chromadb

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def load_mined() -> list:
  rules = []
  for path in glob.glob(os.path.join(REPO, "ruleJsonFiles", "*", "*FICL.json")):
    resource = os.path.basename(os.path.dirname(path))
    with open(path, encoding="utf-8") as f:
      for group in json.load(f).values():
        for entry in group:
          rules.append((resource, entry[1]))
  return rules

def load_validated() -> list:
  rules = []
  with open(os.path.join(REPO, "dataset", "sampledChecks.csv"), encoding="utf-8") as f:
    for row in csv.DictReader(f):
      rules.append((row["Resource Type"], row["Plain Text"]))
  return rules

class RuleStore:
  def __init__(self, kb: str = "mined"):
    rules = load_mined() if kb == "mined" else load_validated()
    rules = list(dict.fromkeys(rules))  # remove duplicates

    self.collection = chromadb.Client().create_collection(name=kb)
    for i in range(0, len(rules), 2000):
      batch = rules[i:i + 2000]
      self.collection.add(
        documents=[text for _, text in batch],
        metadatas=[{"resource": resource} for resource, _ in batch],
        ids=[f"rule_{i + j}" for j in range(len(batch))]
      )

  def retrieve(self, prompt: str, resources: list, top_k: int = 15) -> list:
    """Returns the rules for the task's resource types that are most similar to the prompt."""
    results = self.collection.query(
      query_texts=[prompt],
      n_results=top_k,
      where={"resource": {"$in": resources}}
    )
    return results["documents"][0]
