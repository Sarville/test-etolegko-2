import hashlib
import json
import os
import re
import secrets
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Integer, JSON, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from backend.shares import distribute

load_dotenv()
engine = create_engine('sqlite:///./keitaro.db')

class Base(DeclarativeBase):
    pass

class StreamState(Base):
    __tablename__ = 'stream_states'
    stream_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offers: Mapped[list] = mapped_column(JSON, default=list)

@asynccontextmanager
async def lifespan(app):
    Base.metadata.create_all(engine)
    yield

app = FastAPI(lifespan=lifespan)

async def kt(method, path, data=None):
    url, key = os.getenv('API_URL', '').rstrip('/'), os.getenv('API_KEY', '')
    if not url or not key:
        raise HTTPException(503, 'Укажите API_URL и API_KEY в .env')
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, trust_env=False) as client:
            response = await client.request(method, url + '/' + path.lstrip('/'), headers={'Api-Key': key}, json=data)
        response.raise_for_status()
        return response.json() if response.content else None
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f'Keitaro вернул HTTP {e.response.status_code}. Проверьте адрес API, ключ и права доступа.') from e
    except (httpx.RequestError, ValueError) as e:
        raise HTTPException(502, 'Не удалось получить корректный ответ Keitaro.') from e

def signature(stream):
    relevant = sorted((int(o['offer_id']), int(o.get('share', 0)), o.get('state', 'active')) for o in stream.get('offers', []))
    return hashlib.sha256(json.dumps(relevant).encode()).hexdigest()

async def campaign_streams(campaign_id):
    streams = await kt('GET', f'campaigns/{campaign_id}/streams')
    streams = sorted((s for s in streams if s.get('state') != 'deleted'), key=lambda s: s.get('position') or 0)
    return streams

async def second_stream(campaign_id):
    streams = await campaign_streams(campaign_id)
    if len(streams) < 2:
        raise HTTPException(400, 'В кампании меньше двух потоков.')
    return streams[1]

@app.get('/api/campaigns')
async def campaigns():
    return await kt('GET', 'campaigns')

@app.get('/api/offers')
async def offers():
    return await kt('GET', 'offers')

class NewCampaign(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    geo: str = Field(min_length=2, max_length=2)
    offer_id: int = Field(gt=0)

@app.post('/api/campaigns')
async def create_campaign(data: NewCampaign):
    if not data.name.strip() or not re.fullmatch('[A-Za-z]{2}', data.geo):
        raise HTTPException(422, 'Укажите название и двухбуквенный код страны.')
    groups = await kt('GET', 'groups?type=campaigns')
    group = next((x for x in groups if x.get('name', '').casefold() == 'fortests'), None)
    if not group:
        raise HTTPException(400, 'В Keitaro не найдена группа кампаний FORTESTS.')
    available = await kt('GET', 'offers')
    if not any(x.get('id') == data.offer_id and x.get('state') != 'deleted' for x in available):
        raise HTTPException(400, 'Оффер не найден в Keitaro.')
    sources = await kt('GET', 'traffic_sources')
    source = next((x for x in sources if x.get('name', '').casefold() == 'fortests'), None)
    campaign = {'name': data.name.strip(), 'alias': secrets.token_urlsafe(6), 'type': 'position', 'group_id': str(group['id'])}
    if source:
        campaign['traffic_source_id'] = source['id']
    domains = [x for x in await kt('GET', 'domains') if x.get('state') == 'active']
    if len(domains) == 1:
        campaign['domain_id'] = domains[0]['id']
    created = await kt('POST', 'campaigns', campaign)
    campaign_id = created['id']
    try:
        await kt('POST', 'streams', {'campaign_id': campaign_id, 'name': 'Google', 'type': 'regular', 'position': 1, 'schema': 'redirect', 'action_type': 'http', 'action_payload': 'https://google.com', 'filters': [{'name': 'country', 'mode': 'accept', 'payload': [data.geo.upper()]}]})
        await kt('POST', 'streams', {'campaign_id': campaign_id, 'name': 'Offers', 'type': 'default', 'position': 2, 'schema': 'landings', 'action_type': 'http', 'offers': [{'offer_id': data.offer_id, 'share': 100, 'state': 'active'}]})
    except HTTPException as exc:
        raise HTTPException(502, f'Кампания #{campaign_id} создана не полностью. Проверьте её в Keitaro. {exc.detail}') from exc
    return created

@app.get('/api/campaigns/{campaign_id}/stream')
async def fetch_stream(campaign_id: int):
    streams = await campaign_streams(campaign_id)
    if len(streams) < 2:
        raise HTTPException(400, 'В кампании меньше двух потоков.')
    first, stream = streams[:2]
    result = await prepare_stream(stream)
    result['first_stream'] = {'id': first['id'], 'name': first.get('name'), 'position': first.get('position'), 'filters': first.get('filters', [])}
    return result

async def prepare_stream(stream):
    catalog = await kt('GET', 'offers')
    names = {int(x['id']): x.get('name', str(x['id'])) for x in catalog}
    with Session(engine) as db:
        state = db.get(StreamState, stream['id'])
        old = {x['id']: x for x in state.offers} if state else {}
        previous_active = {x['id'] for x in old.values() if not x['removed']}
        result = []
        for item in stream.get('offers', []):
            oid = int(item['offer_id'])
            prior = old.pop(oid, {})
            result.append({'id': oid, 'name': names.get(oid, prior.get('name', str(oid))), 'share': int(item.get('share', 0)), 'locked': prior.get('locked', False), 'removed': item.get('state') in ('disabled', 'deleted')})
        for item in old.values():
            result.append({**item, 'name': names.get(item['id'], item['name']), 'share': 0, 'removed': True, 'locked': False})
        if not state:
            state = StreamState(stream_id=stream['id'])
            db.add(state)
        state.offers = result
        db.commit()
    current_active = {x['id'] for x in result if not x['removed']}
    changed = bool(previous_active) and previous_active != current_active
    return {'id': stream['id'], 'name': stream.get('name'), 'position': stream.get('position'), 'schema': stream.get('schema'), 'type': stream.get('type'), 'state': stream.get('state'), 'filters': stream.get('filters') or [], 'action_payload': stream.get('action_payload'), 'offers': result, 'revision': signature(stream), 'membership_changed': changed}

@app.get('/api/campaigns/{campaign_id}/streams')
async def list_streams(campaign_id: int):
    streams = await campaign_streams(campaign_id)
    return [await prepare_stream(stream) for stream in streams]

class OfferChange(BaseModel):
    id: int = Field(gt=0)
    name: str
    share: int = Field(ge=0, le=100)
    locked: bool
    removed: bool

class PushRequest(BaseModel):
    revision: str
    offers: list[OfferChange]

@app.put('/api/campaigns/{campaign_id}/stream')
async def push_stream(campaign_id: int, data: PushRequest):
    stream = await second_stream(campaign_id)
    await save_stream(stream, data)
    return await fetch_stream(campaign_id)

@app.put('/api/campaigns/{campaign_id}/streams/{stream_id}')
async def push_specific_stream(campaign_id: int, stream_id: int, data: PushRequest):
    stream = next((s for s in await campaign_streams(campaign_id) if s['id'] == stream_id), None)
    if not stream:
        raise HTTPException(404, 'Поток не найден в кампании.')
    if stream.get('schema') != 'landings':
        raise HTTPException(400, 'Этот поток не поддерживает редактирование офферов.')
    await save_stream(stream, data)
    updated = next((s for s in await campaign_streams(campaign_id) if s['id'] == stream_id), None)
    if not updated:
        raise HTTPException(404, 'Поток больше не найден в кампании.')
    return await prepare_stream(updated)

async def save_stream(stream, data):
    if signature(stream) != data.revision:
        raise HTTPException(409, 'Поток изменился в Keitaro. Загрузите данные заново.')
    items = [x.model_dump() for x in data.offers]
    if len({o['id'] for o in items}) != len(items):
        raise HTTPException(400, 'Оффер повторяется в списке.')
    active = [x for x in items if not x['removed']]
    if not active or sum(x['share'] for x in active) != 100:
        raise HTTPException(400, 'Нужен хотя бы один оффер с суммарной долей 100%.')
    catalog = await kt('GET', 'offers')
    allowed = {int(o['id']) for o in catalog if o.get('state') != 'deleted'}
    if any(x['id'] not in allowed for x in active):
        raise HTTPException(400, 'Один из офферов больше не доступен в Keitaro.')
    await kt('PUT', f"streams/{stream['id']}", {'offers': [{'offer_id': x['id'], 'share': x['share'], 'state': 'active'} for x in active]})
    with Session(engine) as db:
        state = db.get(StreamState, stream['id'])
        if not state:
            state = StreamState(stream_id=stream['id'])
            db.add(state)
        state.offers = items
        db.commit()
