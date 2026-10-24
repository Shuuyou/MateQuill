"""Archive scope differs from image-render scope; synthetic headers, no network."""
import hashlib
from types import SimpleNamespace

from scripts.sync_vmall_materials import Capture


def test_oversize_detail_original_is_archived_without_decoding_or_claiming_render_eligibility(tmp_path,monkeypatch):
    raw=b'explicit-synthetic-source-bytes'
    class Response:
        status_code=200;is_redirect=False;headers={'content-type':'image/jpeg'}
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_bytes(self,*a):yield raw
    class Header:
        width=1920;height=47246;format='JPEG'
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def verify(self):raise AssertionError('This case is source-byte archiving, not full image validation')
    import scripts.sync_vmall_materials as module
    monkeypatch.setattr(module.Image,'open',lambda *a:Header())
    capture=Capture(tmp_path,min_free_gb=0);capture.client.close()
    capture.client=SimpleNamespace(stream=lambda *a,**k:Response())
    result=capture.asset({'image_url':'https://res.vmallres.com/detail.jpg','media_type':'image'})
    assert result['download_status']=='downloaded' and result['render_eligible'] is False
    assert result['archive_validation']=='header_and_source_bytes'
    assert result['sha256']==hashlib.sha256(raw).hexdigest()
    assert (tmp_path/result['path']).read_bytes()==raw
