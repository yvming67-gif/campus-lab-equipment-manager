"""Verify bundled real-photo provenance and equal files across the two clients."""
from pathlib import Path
from urllib.parse import urlsplit
import hashlib,json

root=Path(__file__).resolve().parents[1]
web=root/'static/photos';mini=root/'miniprogram/assets/photos'
catalog=json.loads((web/'catalog.json').read_text('utf8'))
assert catalog==json.loads((mini/'catalog.json').read_text('utf8')),'Photo metadata differs across clients'
module=(mini/'catalog.js').read_text('utf8').split('module.exports = ',1)[1].strip().removesuffix(';')
assert catalog==json.loads(module),'Native JS photo module differs from the provenance manifest'
assert {p['id'] for p in catalog}=={'scene','robot-platforms','robot-controls'}
total=0
for p in catalog:
    name=p['file'];assert Path(name).name==name and name.endswith('.jpg')
    for key in ('source_page','source_image'):
        url=urlsplit(p[key]);assert url.scheme=='https' and url.hostname.endswith('.csu.edu.cn')
    raw=(web/name).read_bytes();assert raw==(mini/name).read_bytes()
    assert raw.startswith(b'\xff\xd8') and len(raw)==p['bytes']
    assert hashlib.sha256(raw).hexdigest()==p['sha256'],'Source image bytes changed'
    assert p['kind']=='真实公开照片' and p['caption']=='中南大学实景参考 · 非本资源实拍'
    total+=len(raw)
assert total<300000,'Keep this bundled photo set small'
print(f'PASS: {len(catalog)} original real photographs, {total} bytes per client, matching sources and SHA-256; no external image dependency')
