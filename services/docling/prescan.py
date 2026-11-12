"""Bounded, explainable preflight; no OCR, chat model, embeddings or storage writes."""
import os
import time

REVISION = "prescan-v1"


def _profile_worker(source,settings,output):
    try:
        output.put((True,prescan_document(source,settings)))
    except Exception as error:
        output.put((False,str(error)))


def isolated_prescan(source,settings):
    """Used by HTTP preview; conversion already has its own process watchdog."""
    import multiprocessing
    import queue
    context=multiprocessing.get_context("spawn")
    output=context.Queue(maxsize=1)
    process=context.Process(target=_profile_worker,args=(str(source),settings,output),daemon=True)
    process.start()
    try:
        try:
            ok,result=output.get(timeout=int(settings.get("prescan_budget_ms",2000))/1000+5)
        except queue.Empty as error:
            raise TimeoutError("Prescan process exceeded wall-time budget") from error
        if not ok:raise ValueError(result)
        return result
    finally:
        if process.is_alive():process.terminate()
        process.join(timeout=2)
        if process.is_alive():process.kill();process.join(timeout=2)
        output.cancel_join_thread();output.close()


def sample_pages(total, limit=12):
    if total < 0 or limit < 1:
        raise ValueError("Invalid sampling limits")
    if total <= limit:
        return list(range(1,total+1))
    # First pages support early availability; evenly spaced pages detect heterogeneity.
    selected = set(range(1,min(3,limit)+1))
    remaining = limit-len(selected)
    if remaining:
        selected.update(1+round(i*(total-1)/remaining) for i in range(1,remaining+1))
    return sorted(selected)


def prescan_document(source, settings):
    from adaptive_pipeline import profile_pdf, profile_docx, route_page
    began=time.monotonic()
    limit=int(settings.get("prescan_max_pages",12))
    budget=int(settings.get("prescan_budget_ms",2000))
    if not 1 <= limit <= 64 or not 1 <= budget <= 10000:
        raise ValueError("Prescan budget outside supported range")
    size=os.path.getsize(source)
    if size > settings["max_bytes"]:
        raise ValueError("File size limit exceeded")
    if str(source).lower().endswith(".pdf"):
        import pypdfium2 as pdfium
        with pdfium.PdfDocument(source) as document:
            total=len(document)
        if total > settings["max_pages"]:
            raise ValueError("PDF page limit exceeded")
        result=profile_pdf(source,settings | {"profile_page_numbers":sample_pages(total,limit),"profile_budget_ms":budget})
        for page in result["pages"]:
            parser,reasons=route_page(page)
            page["route"]={"parser":parser,"reasons":reasons}
        full=len(result["pages"])==total
        plan={"unit":"PAGE", "sampledPageCount":len(result["pages"]),"totalPages":total,
              "unscannedPolicy":"PROFILE_BEFORE_EXTRACTION", "pageWorkers":1,
              "resourcePolicy":"RECOMMENDATION_ONLY_EXISTING_GLOBAL_LIMITS_APPLY",
              "sampleRoutes":{p:sum(x["route"]["parser"]==p for x in result["pages"]) for p in ("FAST","DOCLING")}}
    elif str(source).lower().endswith(".docx"):
        result=profile_docx(source,settings)
        full=True
        plan={"unit":"DOCUMENT", "parser":"DOCLING", "nativeCandidate":result["parser"],
              "reason":"PRESERVE_DOCUMENT_CHECKPOINT_PROTOCOL", "pageWorkers":1}
    else:
        raise ValueError("Unsupported prescan format")
    return result | {"prescanRevision":REVISION,"bytes":size,"coverage":"FULL" if full else "SAMPLED",
                     "budgetMs":budget,"maxSamplePages":limit,"plan":plan,
                     "budgetSemantics":"PDF_COOPERATIVE_BETWEEN_PAGES;DOCX_BOUNDED_BY_XML_BYTES;OUTER_PROCESS_TIMEOUT_APPLIES",
                     "elapsedMs":round((time.monotonic()-began)*1000,3)}
