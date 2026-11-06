"""Closed, bundled reference photographs; never an inventory or availability fact."""
import json
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def catalog():
    path=Path(__file__).resolve().parent.parent/'static/photos/catalog.json'
    return {item['id']:dict(item,asset='photos/'+item['file']) for item in json.loads(path.read_text('utf8'))}


def photo(key='scene'):
    return catalog().get(key)


def resource_photo(resource):
    if resource.kind=='ROOM':
        return photo('scene')
    text=(resource.name+' '+resource.equipment.category).lower()
    return photo('robot-platforms') if any(x in text for x in ('机器人','robot')) else None
