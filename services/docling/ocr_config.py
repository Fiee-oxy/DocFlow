"""Local Chinese/English OCR; never calls a chat model."""
def create_converter(settings):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode, TesseractCliOcrOptions
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    enabled = settings.get("ocr_enabled", False)
    options = PdfPipelineOptions(do_ocr=enabled, do_table_structure=True)
    if enabled:
        options.ocr_options = TesseractCliOcrOptions(lang=settings.get("ocr_languages", ["chi_sim", "eng"]))
    options.table_structure_options.mode = TableFormerMode(settings.get("table_mode", "fast"))
    options.accelerator_options = AcceleratorOptions(num_threads=settings["threads"])
    options.document_timeout = max(1, settings["timeout"] - 15)
    return DocumentConverter(allowed_formats=[InputFormat.PDF, InputFormat.DOCX],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
