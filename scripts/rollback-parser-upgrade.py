"""Restore retained pre-upgrade parser paths; preserve data and applied migrations."""
from pathlib import Path
import datetime,hashlib,json,re,zipfile

ROOT=Path(__file__).resolve().parents[1]
BACKUP=ROOT/'.rollback-backups'/datetime.datetime.now().strftime('parser-upgrade-%Y%m%d-%H%M%S')
BACKUP.mkdir(parents=True)
files=[p for base in ['server/src','services','scripts'] for p in (ROOT/base).rglob('*')
       if p.is_file() and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pdf'}]
files += [ROOT/p for p in ['server/pom.xml','.env','.env.example','docker-compose.yml','docker-compose.local.yml','.gitignore']]
with zipfile.ZipFile(BACKUP/'before.zip','w',zipfile.ZIP_DEFLATED) as z:
    for p in files:
        if p.exists():z.write(p,p.relative_to(ROOT))
changes=[]
def edit(name,fn):
    p=ROOT/name;before=p.read_text(encoding='utf-8');after=fn(before)
    if after!=before:
        p.write_text(after,encoding='utf-8');changes.append(name)
def cut(text,start,end):
    a=text.index(start);b=text.index(end,a);return text[:a]+text[b:]

def workflow(s):
    s=cut(s,'    @Value("${document.parsing.adaptive-enabled:false}")','    public DocumentWorkflow(')
    s=s.replace('Set.of("FAST", "DOCLING","AUTO","STANDARD","DEEP","NATIVE")','Set.of("FAST", "DOCLING")')
    s=cut(s,'                        if(event.path("documentIR").isObject()){','                        switch (event.path("type").asText())')
    s=s.replace('                            case "audit" -> { }\n','')
    s=s.replace('case "metadata" -> {repository.parserProgress(id, event.path("totalPages").asInt());repository.stage(id,"PARSING");}',
                'case "metadata" -> repository.parserProgress(id, event.path("totalPages").asInt());')
    s=cut(s,'                                if(event.has("documentIR")){','                                int total = event.path("totalPages").asInt()')
    s=cut(s,'                    if("NATIVE".equals(mode)){','                } finally { Files.deleteIfExists(file); }')
    s=s.replace('                } finally { Files.deleteIfExists(file); }','                    parser.stream(file, doc.filename(), cursor[0], cursor[1], heading, mode, consumer);\n                } finally { Files.deleteIfExists(file); }')
    s=cut(s,'                if(adaptiveEnabled){','                if (error instanceof InterruptedException)')
    s=cut(s,'        if(adaptiveEnabled){','        if(!doc.filename().toLowerCase(Locale.ROOT).endsWith(".pdf"))return "DOCLING";')
    return s
edit('server/src/main/java/com/example/server/document/DocumentWorkflow.java',workflow)

def uploads(s):
    s=cut(s,'    @Value("${document.parsing.adaptive-enabled:false}")','    public DocumentUploadService(')
    s=re.sub(r'        if\(!adaptiveEnabled.*?\n','',s)
    s=re.sub(r'                if\(adaptiveEnabled&&nativeParser!=null\).*?\n','',s)
    s=s.replace('!com.example.server.document.parsing.NativeDocumentParser.supported(name)',
                '!name.toLowerCase(Locale.ROOT).matches(".+\\\\.(pdf|docx)")')
    s=s.replace('不支持的文档格式，DOC 请先转换为 DOCX','仅支持 PDF 和 DOCX 文档')
    s=s.replace('filename.toLowerCase(Locale.ROOT).matches(".+\\\\.(docx|xlsx|pptx)")','filename.toLowerCase(Locale.ROOT).endsWith(".docx")')
    a=s.index('                String ext=com.example.server.document.parsing.NativeDocumentParser.extension(filename);')
    b=s.index('                long expanded = 0;',a)
    s=s[:a]+'''                if (zip.getEntry("word/document.xml") == null || zip.getEntry("[Content_Types].xml") == null)
                    throw new IllegalArgumentException("文件内容不是有效 DOCX");
'''+s[b:]
    return s
edit('server/src/main/java/com/example/server/document/DocumentUploadService.java',uploads)
edit('server/src/main/java/com/example/server/document/DoclingClient.java',lambda s:cut(s,'                    if(event.has("failureType")){','                    if (!event.path("retryable")'))
edit('server/src/main/java/com/example/server/config/ThreadPoolConfig.java',lambda s:cut(s,'    @Bean("documentNativeParserExecutor")','    @Bean("documentQueryExecutor")'))
edit('server/pom.xml',lambda s:re.sub(r'^.*<dependency><groupId>org\.(?:apache\.poi|apache\.tika|jsoup)</groupId>.*\n','',s,flags=re.M))
edit('server/src/main/resources/application.properties',lambda s:re.sub(r'^document\.parsing\..*\n','',s,flags=re.M))

def parser_app(s):
    s=s.replace('import parse_metrics\n','')
    s=s.replace('"tableMode": TABLE_MODE,\n            "irSchemaVersion":"1.0", "adaptiveModes":["AUTO","FAST","STANDARD","DEEP"],\n            "adaptiveEnabledByDefault":os.getenv("DOCUMENT_ADAPTIVE_PARSING_ENABLED","false").lower()=="true"}',
                '"tableMode": TABLE_MODE, "parseModes": ["FAST", "DOCLING"]}')
    s=s.replace('{"AUTO", "FAST", "STANDARD", "DEEP", "DOCLING"}','{"FAST", "DOCLING"}')
    s=s.replace('        # AUTO may upgrade into Docling, so it must obey the heavy-worker limit.\n','')
    s=s.replace('        parse_metrics.active(1)\n','').replace('            parse_metrics.active(-1)\n','').replace('                parse_metrics.observe(event)\n','')
    s=cut(s,'            self.settings.update(adaptive_enabled=','        except Exception:')
    s=cut(s,'@app.post("/preflight")','@app.post("/parse")')
    return s
edit('services/docling/app.py',parser_app)
edit('services/docling/pipeline.py',lambda s:cut(s,'        if settings.get("adaptive_enabled")','        if settings.get("parse_mode") == "FAST":'))

def normalize(s):
    s=s.replace('if not text and label != "picture":','if not text:')
    s=cut(s,'        attributes = {}','        blocks.append({')
    s=s.replace('            "bbox": bbox, "attributes": attributes,\n','')
    return s
edit('services/docling/normalization.py',normalize)
def ocr(s):
    s=s.split('\n# Per isolated process cache.')[0].rstrip()+'\n'
    s=s.replace('PdfPipelineOptions(do_ocr=enabled, do_table_structure=settings.get("table_structure", True))','PdfPipelineOptions(do_ocr=enabled, do_table_structure=True)')
    s=s.replace('    options.do_formula_enrichment = settings.get("formula_enrichment", False)\n','')
    s=s.replace('    options.do_picture_description = settings.get("picture_description", False)\n','')
    return s
edit('services/docling/ocr_config.py',ocr)

def retrieval(s):
    s=cut(s,'    ir = batch.get("documentIR")','        from docling_core.types.doc import DoclingDocument')
    s=s.replace('        from docling_core.types.doc import DoclingDocument','    if batch.get("doclingDocument"):\n        from docling_core.types.doc import DoclingDocument',1)
    s=s.replace('if request.merge_peers and not ir:','if request.merge_peers:')
    s=cut(s,'        if ir:\n','    return result\n')
    s=s.replace('"DOCUMENT_IR" if request.batch.get("documentIR") else ','')
    return s
edit('services/retrieval/app.py',retrieval)
edit('services/docling/Dockerfile',lambda s:s.replace('document_ir.py adaptive_pdf.py adaptive_pipeline.py parse_metrics.py ',''))
edit('services/retrieval/Dockerfile',lambda s:s.replace('COPY app.py ir_chunking.py ./','COPY app.py ./'))
edit('services/docling/requirements.txt',lambda s:re.sub(r'^PyMuPDF==.*\n?','',s,flags=re.M))

prefixes=('DOCUMENT_ADAPTIVE_PARSING_','DOCUMENT_PARSING_','DOCUMENT_NATIVE_PARSE_','DOCUMENT_PREFLIGHT_',
          'DOCUMENT_PARSE_AUTO_UPGRADE','DOCUMENT_PARSE_ENABLED_MODES','DOCUMENT_TABLE_STRUCTURE_ENABLED',
          'DOCUMENT_FORMULA_ENRICHMENT_ENABLED','DOCUMENT_PICTURE_DESCRIPTION_ENABLED')
def env(s):
    return '\n'.join(line for line in s.splitlines() if not line.strip().startswith(prefixes)
                     and 'Opt-in parser architecture upgrade' not in line
                     and 'Configure these variables on the Python parser service' not in line)+'\n'
edit('.env',env);edit('.env.example',env)
edit('docker-compose.yml',env)
edit('.gitignore',lambda s:s if '/.rollback-backups/' in s else s.rstrip()+'\n/.rollback-backups/\n')

# Historical IR JSON exports, audit reads, cancellation guards, and Flyway V12
# deliberately remain compatible with already-persisted documents. They cannot
# select the removed ingestion or chunking implementations.
retire=[
 'server/src/main/java/com/example/server/document/parsing/DocumentIR.java',
 'server/src/main/java/com/example/server/document/parsing/NativeDocumentParser.java',
 'server/src/main/java/com/example/server/document/parsing/NativeParseExecutor.java',
 'server/src/main/java/com/example/server/document/parsing/ParseQualityException.java',
 'server/src/test/java/com/example/server/document/parsing/NativeDocumentParserTest.java',
 'server/src/test/java/com/example/server/document/parsing/NativeParseExecutorTest.java',
 'server/src/test/java/com/example/server/document/parsing/NativeParserBenchmarkTest.java',
 'services/docling/document_ir.py','services/docling/adaptive_pdf.py',
 'services/docling/adaptive_pipeline.py','services/docling/parse_metrics.py',
 'services/docling/test_adaptive_parser.py','services/docling/test_adaptive_api.py',
 'services/retrieval/ir_chunking.py','services/retrieval/test_ir_chunking.py']
for name in retire:
    p=(ROOT/name).resolve()
    assert p.is_relative_to(ROOT)
    if p.exists():p.unlink();changes.append(name+' (archived in before.zip)')
(BACKUP/'manifest.json').write_text(json.dumps({'changes':changes,'preserved':['all database/document/vector data','applied Flyway V12','historical read-only IR/audit compatibility','cancellation commit guards','evaluation artifacts']},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'backup':str(BACKUP),'changed_files':len(changes)},ensure_ascii=False))
