"""Live broker-outage compensation check; always restore the named local broker."""
import hashlib,importlib.util,json,subprocess,time
from pathlib import Path
import fitz
spec=importlib.util.spec_from_file_location('e',Path(__file__).with_name('final-e2e.py'))
e=importlib.util.module_from_spec(spec);spec.loader.exec_module(e)
D=r'C:/Program Files/Docker/Docker/resources/bin/docker.exe';BROKER='dovideo-ai-main-rmqbroker-1'
def main():
    actor=json.loads((e.OUT/'session.private.json').read_text(encoding='utf-8'))['actors'][0];token=actor['token']
    pdf=fitz.open();page=pdf.new_page();page.insert_text((40,80),'Broker recovery test. Durable queue intent survives a failed message send.')
    data=pdf.tobytes();session=e.api('/documents/uploads/init',dict(filename='final-broker-recovery.pdf',sizeBytes=len(data),sha256=hashlib.sha256(data).hexdigest()),token)
    did=session['uploadId'];e.upload_part(did,0,data,token)
    start=time.monotonic();record=None
    try:
        subprocess.run([D,'stop','-t','5',BROKER],check=True,capture_output=True)
        accepted=e.complete(did,token);record=e.api('/documents/'+did,token=token)
        assert record['status']=='QUEUED',record
    finally:
        subprocess.run([D,'start',BROKER],check=True,capture_output=True)
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        record=e.api('/documents/'+did,token=token)
        if record['parseComplete']:break
        assert record['status']!='FAILED',record
        time.sleep(1)
    assert record['parseComplete'],record
    e.save('dispatch-recovery.json',dict(passed=True,documentId=did,acceptedWhileBrokerDown=True,
        recoveredWithoutClientResubmission=True,elapsedMs=(time.monotonic()-start)*1000,finalState=record['status'],chatLlmCalls=0))
    print('Broker outage and durable compensation passed',flush=True)
if __name__=='__main__':main()
