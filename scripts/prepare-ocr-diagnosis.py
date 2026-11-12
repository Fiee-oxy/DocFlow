"""Copy the specified user's source read-only to an isolated diagnostic folder."""
import json,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'evaluation/ocr-diagnosis-20261112'
D=r'C:/Program Files/Docker/Docker/resources/bin/docker.exe'
DOCUMENT='b4db1b3d-22d8-49fb-88d3-e92fad7767c3'
def main():
    OUT.mkdir(parents=True,exist_ok=True)
    shell='mc alias set docflow-ocr-diag http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc cp docflow-ocr-diag/media/documents/31/'+DOCUMENT+'/source.pdf /tmp/docflow-ocr-diagnostic.pdf >/dev/null'
    subprocess.run([D,'exec','dovideo-ai-main-minio-1','sh','-c',shell],check=True,capture_output=True)
    subprocess.run([D,'cp','dovideo-ai-main-minio-1:/tmp/docflow-ocr-diagnostic.pdf',str(OUT/'source.pdf')],check=True,capture_output=True)
    subprocess.run([D,'cp',str(OUT/'source.pdf'),'dovideo-docling:/tmp/docflow-ocr-diagnostic.pdf'],check=True,capture_output=True)
    import fitz
    with fitz.open(OUT/'source.pdf') as doc:
        page=doc[0];images=page.get_image_info(xrefs=True)
        metadata=dict(documentId=DOCUMENT,bytes=(OUT/'source.pdf').stat().st_size,pages=len(doc),
            pagePoints=list(page.rect),nativeCharacters=len(page.get_text().strip()),images=images)
        page.get_pixmap(matrix=fitz.Matrix(1.25,1.25)).save(OUT/'source-preview.png')
    (OUT/'source-metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2,default=lambda value:value.hex() if isinstance(value,bytes) else str(value)),encoding='utf-8')
    print(json.dumps({key:metadata[key] for key in ['bytes','pages','nativeCharacters']}),flush=True)
if __name__=='__main__':main()
