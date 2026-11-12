"""Bounded subprocess conversion: emit batches and kill the task on hard timeout."""
import json
import logging
import multiprocessing
import queue
import time


def conversion_worker(source, settings, output):
    try:
        if settings.get("parse_mode") in {"AUTO", "HIGH_ACCURACY"}:
            from adaptive_pipeline import convert_adaptive
            convert_adaptive(source, settings, output)
            return
        if settings.get("parse_mode") == "DOCX_STREAM":
            from docx_stream import convert_docx_stream
            convert_docx_stream(source, settings, output)
            return
        if settings.get("parse_mode") == "FAST":
            from fast_pdf import convert_fast_pdf
            convert_fast_pdf(source, settings, output)
            return
        import pypdfium2 as pdfium
        from docling.datamodel.base_models import ConversionStatus
        from normalization import normalize
        pdf = source.lower().endswith(".pdf")
        total = 0
        if pdf:
            with pdfium.PdfDocument(source) as document:
                total = len(document)
            if total > settings["max_pages"]:
                raise ValueError("PDF 页数超过处理上限")
            if settings["resume_pages"] > total:
                raise ValueError("解析检查点超过文件页数")
        output.put({"type": "metadata", "totalPages": total})
        from ocr_config import create_converter
        converter = create_converter(settings)
        heading = settings["heading"].split(" / ") if settings["heading"] else []
        ranges = ([(start, min(total, start + settings["batch_pages"] - 1))
                   for start in range(settings["resume_pages"] + 1, total + 1, settings["batch_pages"])]
                  if pdf else ([] if settings["resume_batches"] else [(0, 0)]))
        chars = 0
        for start, end in ranges:
            kwargs = {"max_num_pages": settings["max_pages"], "max_file_size": settings["max_bytes"]}
            if pdf:
                kwargs["page_range"] = (start, end)
            result = converter.convert(source, **kwargs)
            if result.status != ConversionStatus.SUCCESS:
                raise RuntimeError("本批文档解析未完整完成，已成功批次将保留")
            parsed = normalize(result.document, heading, ocr_enabled=settings.get("ocr_enabled", False))
            if pdf and settings.get("table_mode") == "fast":
                parsed["warnings"].append("使用 Docling 快速表格模型；复杂表格的行列对应关系请核对原文。")
            if pdf and total > settings["batch_pages"]:
                parsed["warnings"].append("PDF 按页范围分批解析；跨批次的表格尚未自动合并，请核对相关原文。")
            heading = parsed.pop("headingState")
            chars += sum(len(block["text"]) for block in parsed["blocks"])
            if chars > settings["max_chars"]:
                raise ValueError("文档解析文本超过容量限制")
            output.put({"type": "batch", "startPage": start, "endPage": end,"headingState": heading,
                        "totalPages": total, "doclingDocument": result.document.export_to_dict(), **parsed})
        output.put({"type": "complete", "totalPages": total})
    except ValueError as error:
        output.put({"type": "error", "retryable": False, "failureType":"FILE_INVALID", "message": str(error)})
    except Exception:
        logging.exception("document_batch_conversion_failed")
        output.put({"type": "error", "retryable": True,"failureType":"PARSER_EXCEPTION",
                    "message": "Docling 批次解析失败，请检查文件、模型及服务日志；已成功批次可续跑"})


def task_events(source, settings):
    from boundary_stream import BoundaryAssembler
    try:
        # DOCX has stable body nodes rather than physical pages. Its parser owns
        # its checkpoint protocol; PDF page-tail state must never interpret it.
        assembler = None if settings.get("parse_mode") == "DOCX_STREAM" else BoundaryAssembler(settings)
    except ValueError as error:
        yield {"type": "error", "retryable": False, "failureType": "CHECKPOINT_INVALID", "message": str(error)}
        return
    context = multiprocessing.get_context("spawn")
    output = context.Queue(maxsize=2)
    process = context.Process(target=conversion_worker, args=(str(source), settings, output), daemon=True)
    process.start()
    deadline = time.monotonic() + settings["timeout"]
    last_heartbeat = 0
    try:
        while True:
            if time.monotonic() >= deadline:
                yield {"type": "error", "retryable": True,"failureType":"PARSER_TIMEOUT",
                       "message": "Docling 单批解析超时，任务进程已回收；重试将保留已完成批次"}
                return
            try:
                event = output.get(timeout=1)
            except queue.Empty:
                if not process.is_alive():
                    yield {"type": "error", "retryable": True, "message": "解析任务进程意外退出，已成功批次可续跑"}
                    return
                if time.monotonic() - last_heartbeat >= 3:
                    yield {"type": "heartbeat"}
                    last_heartbeat = time.monotonic()
                continue
            if event["type"] in {"batch", "metadata"}:
                deadline = time.monotonic() + settings["timeout"]
            try:
                yield event if assembler is None else assembler.process(event)
            except ValueError as error:
                yield {"type": "error", "retryable": False, "failureType": "CHECKPOINT_INVALID", "message": str(error)}
                return
            if event["type"] in {"error", "complete"}:
                return
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        output.cancel_join_thread()
        output.close()


def encode_event(event):
    return json.dumps(event, ensure_ascii=False) + "\n"
