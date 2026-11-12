"""Measure the production RAG task from submit to first streamed model token.

Uses the isolated holdout account and documents. Secrets and answer text are never
written to the report or stdout.
"""
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "evaluation/rag-quality/outputs/completion-holdout-20261014"
API = "http://127.0.0.1:9090"
REPORT = ROOT / "server/target/rag-first-token-20261015.json"


def request(path, body=None, token=None, timeout=360):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(API + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code} {error.read().decode('utf-8', 'replace')[:300]}") from None
    if result.get("code") != 0:
        raise RuntimeError(result.get("message", "API rejected request"))
    return result.get("data")


def refresh_session():
    private = json.loads((RUN / ".session.json").read_text(encoding="utf-8"))
    login = request("/user/login", {"username": private["username"], "password": private["password"]})
    if int(login["userInfo"]["id"]) != int(private["owner"]):
        raise RuntimeError("Evaluation owner changed")
    return login["token"]


def stream_events(task_id, token, started):
    req = urllib.request.Request(
        f"{API}/documents/query-tasks/{task_id}/events?after=0",
        headers={"Authorization": "Bearer " + token, "Accept": "text/event-stream"},
    )
    milestones = {}
    event_name = "message"
    data_lines = []
    with urllib.request.urlopen(req, timeout=360) as response:
        while True:
            raw = response.readline()
            if not raw:
                raise RuntimeError("SSE ended before a terminal event")
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line == "" and data_lines:
                elapsed = time.perf_counter() - started
                payload = json.loads("\n".join(data_lines))
                if event_name == "stage":
                    milestones.setdefault(payload.get("stage", "UNKNOWN"), elapsed)
                elif event_name == "delta":
                    milestones.setdefault("FIRST_TOKEN", elapsed)
                elif event_name == "complete":
                    milestones["COMPLETE"] = elapsed
                    milestones["answerChars"] = len(payload.get("answer", ""))
                    milestones["citations"] = len(payload.get("citations", []))
                    return milestones
                elif event_name == "error":
                    milestones["FAILED"] = elapsed
                    milestones["failureType"] = payload.get("failureType")
                    milestones["message"] = payload.get("message")
                    return milestones
                elif event_name == "task_status" and payload.get("state") in {"FAILED", "CANCELLED"}:
                    milestones["FAILED"] = elapsed
                    milestones["message"] = payload.get("error_message")
                    return milestones
                event_name, data_lines = "message", []


def run_case(token, case):
    started = time.perf_counter()
    task = request("/knowledge/query", {
        "documentIds": [case["documentId"]],
        "question": case["question"],
        "requestId": str(uuid.uuid4()),
        "limit": 100,
    }, token)
    accepted = time.perf_counter() - started
    result = stream_events(task["id"], token, started)
    result.update({
        "case": case["case"],
        "language": case["language"],
        "question": case["question"],
        "acceptedSeconds": round(accepted, 3),
    })
    if "FIRST_TOKEN" in result:
        result["firstTokenSeconds"] = round(result["FIRST_TOKEN"], 3)
    if "GENERATING" in result and "FIRST_TOKEN" in result:
        result["modelFirstTokenSeconds"] = round(result["FIRST_TOKEN"] - result["GENERATING"], 3)
    if "RETRIEVING" in result and "GENERATING" in result:
        result["retrievalSeconds"] = round(result["GENERATING"] - result["RETRIEVING"], 3)
    if "COMPLETE" in result:
        result["completeSeconds"] = round(result["COMPLETE"], 3)
    for key in ["PREPARING", "RETRIEVING", "GENERATING", "VERIFYING", "FIRST_TOKEN", "COMPLETE", "FAILED"]:
        if key in result and isinstance(result[key], float):
            result[key] = round(result[key], 3)
    return result


def main():
    token = refresh_session()
    ingestion = json.loads((RUN / "ingestion.json").read_text(encoding="utf-8"))
    cases = [
        {"case": "zh_numeric", "language": "zh", "documentId": ingestion["pbc-2024q4"]["document_id"],
         "question": "2024年末社会融资规模存量和M2同比增速分别是多少？"},
        {"case": "zh_multi_fact", "language": "zh", "documentId": ingestion["pbc-2024q4"]["document_id"],
         "question": "2024年全年降准几次、合计多少个百分点，并提供了多少长期流动性？"},
        {"case": "en_protocol", "language": "en", "documentId": ingestion["rfc9110"]["document_id"],
         "question": "What does HTTP 405 mean and which response header must the origin server generate?"},
    ]
    results = []
    for case in cases:
        result = run_case(token, case)
        results.append(result)
        print(json.dumps({k: result.get(k) for k in ["case", "acceptedSeconds", "retrievalSeconds",
              "modelFirstTokenSeconds", "firstTokenSeconds", "completeSeconds", "failureType", "message"]
              if result.get(k) is not None}, ensure_ascii=False), flush=True)
    successful = [item for item in results if "firstTokenSeconds" in item]
    report = {"measuredAt": "2026-10-15", "method": "sequential production API + received SSE timestamps",
              "results": results, "successful": len(successful)}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(REPORT), "successful": len(successful)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
