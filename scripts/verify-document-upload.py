"""Authenticated synthetic upload checks. Never calls a chat/generation endpoint."""
import concurrent.futures
import hashlib
import importlib.util
import json
import time
import uuid
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "evaluation" / "upload-verification-20261023"
spec = importlib.util.spec_from_file_location("pilot", ROOT / "scripts/run-rag-quality-pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def pdf(padding=0):
    # Keep enough native text to avoid invoking the project's sparse-page OCR fallback.
    content = b"BT /F1 12 Tf 72 720 Td (Verified asynchronous document upload test.) Tj 0 -20 Td "
    content += b"(This synthetic fixture checks hashing, recovery, and asynchronous processing.) Tj ET\n"
    content += b"%" + b" " * padding + b"\n"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream"]
    data = bytearray(b"%PDF-1.7\n"); offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]: data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def main():
    session = pilot.load_session(RUN)
    headers = {"Authorization": "Bearer " + session["token"]}
    checks = []

    def call(path, expected=200, **kwargs):
        assert "/chat" not in path and "/query" not in path
        response = requests.request(kwargs.pop("method", "GET"), "http://127.0.0.1:9090" + path,
                                    headers=headers, timeout=30, **kwargs)
        assert response.status_code == expected, (path, response.status_code, response.text[:200])
        return response.json().get("data")

    def init(data, name, expected_hash=None, base=None):
        return call("/documents/uploads/init", method="POST", json={"filename": name, "sizeBytes": len(data),
                    "sha256": expected_hash or hashlib.sha256(data).hexdigest(), "knowledgeBaseId": base})

    def part(upload, index, data, digest=None, expected=200):
        return call("/documents/upload-chunk", expected=expected, method="POST",
                    data={"uploadId": upload, "chunkIndex": index, "sha256": digest or hashlib.sha256(data).hexdigest()},
                    files={"file": ("part.bin", data, "application/octet-stream")})

    # Each run gets fresh content while preserving a private test-account credential file.
    source = pdf(5 * 1024 * 1024 + 1000) + ("\n% " + uuid.uuid4().hex + "\n").encode()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        initialized = list(pool.map(lambda i: init(source, f"parallel-{i}.pdf"), range(8)))
    ids = {entry["uploadId"] for entry in initialized}
    assert len(ids) == 1
    upload = ids.pop(); checks.append("eight_concurrent_initializations_one_task")
    size = initialized[0]["chunkSizeBytes"]
    assert initialized[0]["totalChunks"] == 2
    part(upload, 0, source[:size], digest="0" * 64, expected=400)
    assert call("/documents/upload-status?uploadId=" + upload) == []
    checks.append("bad_part_hash_rejected_without_receipt")
    part(upload, 0, source[:size]); part(upload, 0, source[:size])
    assert call("/documents/upload-status?uploadId=" + upload) == [0]
    part(upload, 0, source[:size], digest="0" * 64, expected=409)
    checks.append("duplicate_part_idempotent_changed_hash_conflict")
    resumed = init(source, "renamed-without-local-receipt.pdf")
    assert resumed["mode"] == "RESUME" and resumed["uploadId"] == upload and resumed["completedChunks"] == [0]
    checks.append("renamed_resume_uses_server_hash_and_completed_parts")
    part(upload, 1, source[size:])
    started = time.monotonic()
    accepted = call("/documents/complete-upload?uploadId=" + upload, expected=202, method="POST")
    accepted_ms = round((time.monotonic() - started) * 1000)
    assert accepted["status"] in ["QUEUED", "PROCESSING", "COMPLETED"]
    call("/documents/complete-upload?uploadId=" + upload, expected=202, method="POST")
    checks.append("completion_accepted_asynchronously_and_is_idempotent")
    for _ in range(90):
        state = call("/documents/" + upload)
        assert state["status"] != "FAILED", state
        if state["status"] == "COMPLETED": break
        time.sleep(1)
    else: raise AssertionError("Synthetic one-page PDF did not finish in 90 seconds")
    instant = init(source, "renamed-completed.pdf")
    assert instant["mode"] == "INSTANT" and instant["document"]["id"] == upload
    checks.append("valid_hash_verified_then_parsed_and_reused_instantly")

    # A small second upload declares a wrong complete-file hash but correct part hashes.
    other = pdf() + ("\n% " + uuid.uuid4().hex + "\n").encode()
    corrupt = init(other, "invalid-whole-hash.pdf", "0" * 64)
    part(corrupt["uploadId"], 0, other)
    call("/documents/complete-upload?uploadId=" + corrupt["uploadId"], expected=202, method="POST")
    for _ in range(40):
        failed = call("/documents/" + corrupt["uploadId"])
        if failed["status"] == "FAILED": break
        time.sleep(.5)
    else: raise AssertionError("Wrong whole hash was not rejected asynchronously")
    assert failed["stage"] == "VERIFY_FAILED" and failed["totalBlocks"] == 0
    call("/documents/" + corrupt["uploadId"] + "/retry", expected=409, method="POST")
    checks.append("wrong_whole_hash_blocks_parsing_and_manual_retry")
    report = {"checksPassed": checks, "checkCount": len(checks), "llmCalls": 0,
              "sourceBytes": len(source), "parts": 2, "completionAcceptedMs": accepted_ms,
              "documentStatus": state["status"], "documentBlocks": state["totalBlocks"],
              "backend": "http://127.0.0.1:9090", "testOwner": session["owner"],
              "scope": "isolated synthetic account; no existing user files changed"}
    pilot.save(RUN / "http-smoke.json", report)
    print(json.dumps(report, ensure_ascii=True))


if __name__ == "__main__": main()
