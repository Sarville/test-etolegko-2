import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

async function api(path, options = {}) {
  const response = await fetch('/api' + path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Проверьте введённые данные');
  return data;
}

function distribute(items) {
  const result = items.map(x => ({ ...x }));
  const active = result.filter(x => !x.removed), unlocked = active.filter(x => !x.locked);
  const remaining = 100 - active.filter(x => x.locked).reduce((sum, x) => sum + x.share, 0);
  if (remaining < 0 || (active.length && !unlocked.length && remaining !== 0)) throw new Error('Закреплённые доли не позволяют распределить 100%. Снимите закрепление.');
  result.filter(x => x.removed).forEach(x => { x.share = 0; x.locked = false; });
  unlocked.forEach((x, i) => { x.share = Math.floor(remaining / unlocked.length) + (i < remaining % unlocked.length ? 1 : 0); });
  return result;
}

function StreamCard({ initial, campaignId, catalog, busy, run, onDirty }) {
  const [stream, setStream] = useState(initial);
  const [draft, setDraft] = useState(() => initial.membership_changed ? distribute(initial.offers) : initial.offers);
  const [search, setSearch] = useState('');
  const dirty = JSON.stringify(draft) !== JSON.stringify(stream.offers);
  useEffect(() => { onDirty(stream.id, dirty); return () => onDirty(stream.id, false); }, [dirty, stream.id]);
  function edit(fn) { try { setDraft(distribute(fn(draft.map(x => ({ ...x }))))); } catch (e) { run(async () => { throw e; }); } }
  async function refresh() {
    if (dirty && !window.confirm('Заменить несохранённые изменения данными Keitaro?')) return;
    await run(async () => {
      const flows = await api(`/campaigns/${campaignId}/streams`);
      const updated = flows.find(x => x.id === stream.id);
      if (!updated) throw new Error('Поток больше не найден в кампании. Обновите список потоков.');
      setStream(updated); setDraft(updated.membership_changed ? distribute(updated.offers) : updated.offers);
    });
  }
  async function push() { await run(async () => {
    const updated = await api(`/campaigns/${campaignId}/streams/${stream.id}`, { method: 'PUT', body: JSON.stringify({ revision: stream.revision, offers: draft }) });
    setStream(updated); setDraft(updated.offers);
  }); }
  const filtered = catalog.filter(o => `${o.id} ${o.name}`.toLowerCase().includes(search.toLowerCase()) && !draft.some(x => x.id === o.id));
  const filters = stream.filters?.length ? stream.filters.map(f => `${f.name} (${f.mode}): ${Array.isArray(f.payload) ? f.payload.join(', ') : f.payload ?? '—'}`).join(' · ') : 'Фильтры не заданы';
  return <div className={'stream ' + (dirty ? 'dirty' : '')}>
    <div className="stream-head"><div><p className="eyebrow">ПОТОК {stream.position ?? ''} · #{stream.id}</p><h3>{stream.name || 'Без названия'}</h3><p className="muted">{filters}</p>{typeof stream.action_payload === 'string' && stream.action_payload && stream.schema !== 'landings' && <p className="muted">{stream.action_payload}</p>}</div><button className="secondary" disabled={busy} onClick={refresh}>↻ &nbsp; Fetch from KT</button></div>
    {stream.schema === 'landings' ? <>{dirty && <div className="draft-bar"><span>● &nbsp;Есть несохранённые изменения</span><div><button className="text-button" onClick={() => setDraft(stream.offers)}>Отменить</button><button className="primary" disabled={busy} onClick={push}>Push to KT ↗</button></div></div>}
    <div className="table-head"><span>ОФФЕР</span><span>ДОЛЯ</span><span></span></div>{draft.map(o => <div className={'offer-row ' + (o.removed ? 'removed' : '')} key={o.id}><div><strong>{o.name}</strong><small>#{o.id} {o.removed && ' · удалён'}</small></div><div className="share">{o.share}% <button title={o.locked ? 'Открепить долю' : 'Закрепить долю'} aria-label={o.locked ? 'Открепить долю' : 'Закрепить долю'} disabled={o.removed} className={'pin ' + (o.locked ? 'pinned' : '')} onClick={() => edit(xs => xs.map(x => x.id === o.id ? {...x, locked: !x.locked} : x))}>●</button></div><button className="secondary small" onClick={() => edit(xs => xs.map(x => x.id === o.id ? {...x, removed: !x.removed, locked: false} : x))}>{o.removed ? 'Вернуть' : 'Удалить'}</button></div>)}
    <div className="add"><label>Добавить оффер<input value={search} onChange={e => setSearch(e.target.value)} placeholder="Поиск по названию или ID"/></label>{search && <div className="results">{filtered.slice(0, 20).map(o => <button key={o.id} onClick={() => { edit(xs => [...xs, {id: o.id, name: o.name, share: 0, locked: false, removed: false}]); setSearch(''); }}>{o.name} <small>#{o.id}</small></button>)}{!filtered.length && <p>Офферы не найдены</p>}</div>}</div></> : <div className="add muted">Информационный поток · схема: {stream.schema || 'не указана'}</div>}
  </div>;
}

function App() {
  const [campaigns, setCampaigns] = useState([]), [catalog, setCatalog] = useState([]);
  const [selected, setSelected] = useState(''), [streams, setStreams] = useState(null), [dirtyStreams, setDirtyStreams] = useState({}), [generation, setGeneration] = useState(0);
  const [name, setName] = useState(''), [geo, setGeo] = useState(''), [newOffer, setNewOffer] = useState(''), [createSearch, setCreateSearch] = useState('');
  const [error, setError] = useState(''), [message, setMessage] = useState(''), [busy, setBusy] = useState(false);
  const dirty = Object.values(dirtyStreams).some(Boolean);
  async function run(fn) { setError(''); setMessage(''); setBusy(true); try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); } }
  async function refreshLists() { const [c, o] = await Promise.all([api('/campaigns'), api('/offers')]); setCampaigns(c.filter(x => x.state !== 'deleted')); setCatalog(o.filter(x => x.state !== 'deleted')); }
  useEffect(() => { run(refreshLists); }, []);
  async function load(id) { setStreams(await api(`/campaigns/${id}/streams`)); setDirtyStreams({}); setGeneration(n => n + 1); }
  function onDirty(id, value) { setDirtyStreams(old => old[id] === value ? old : {...old, [id]: value}); }
  async function create(e) { e.preventDefault(); if (!newOffer) { setError('Выберите оффер из списка.'); return; } if (dirty && !window.confirm('Отменить несохранённые изменения при переходе к новой кампании?')) return; await run(async () => { const c = await api('/campaigns', { method: 'POST', body: JSON.stringify({ name, geo, offer_id: Number(newOffer) }) }); setName(''); setGeo(''); setNewOffer(''); setCreateSearch(''); setCampaigns(old => [...old.filter(item => item.id !== c.id), c]); setSelected(String(c.id)); setStreams(null); setDirtyStreams({}); await load(c.id); setMessage('Кампания создана.'); }); }
  async function choose(e) { const id = e.target.value; if (dirty && !window.confirm('Отменить несохранённые изменения?')) return; setSelected(id); setStreams(null); if (id) await run(() => load(id)); }
  return <><header><div><span className="logo">K</span><strong>Campaign Studio</strong><span className="tag">KEITARO</span></div><span className="header-note">Управление кампаниями</span></header><main><div className="intro"><div><p className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО</p><h1>Кампании</h1><p>Создавайте кампании и управляйте распределением трафика.</p></div><span className="status"><i/> Admin API</span></div>
    {error && <div className="alert error" role="alert">{error}</div>}{message && <div className="alert success" role="status">{message}</div>}
    <div className="layout"><section className="card create"><div className="card-head"><div><p className="eyebrow">01 / СОЗДАНИЕ</p><h2>Новая кампания</h2></div><span className="icon">＋</span></div><p className="muted">Настройте два потока за несколько шагов.</p><form onSubmit={create}><label>Название кампании<input required value={name} onChange={e => setName(e.target.value)} placeholder="Например, Campaign MX"/></label><label>Гео страны<input required value={geo} maxLength="2" onChange={e => setGeo(e.target.value.toUpperCase())} placeholder="MX" pattern="[A-Za-z]{2}"/><small>Двухбуквенный код страны, например MX.</small></label><label>Оффер<input required list="campaign-offers" value={createSearch} onChange={e => { const value = e.target.value; setCreateSearch(value); setNewOffer(String(catalog.find(o => `${o.id} · ${o.name}` === value)?.id || "")); }} placeholder="Название или ID" autoComplete="off"/><datalist id="campaign-offers">{catalog.map(o => <option key={o.id} value={`${o.id} · ${o.name}`}/>)}</datalist></label><div className="hint">Трафик выбранной страны → Google<br/>Остальной трафик → выбранный оффер<br/>Группа кампании → FORTESTS</div><button className="primary wide" disabled={busy}>Создать кампанию <span>→</span></button></form></section>
    <section className="card editor"><div className="card-head"><div><p className="eyebrow">02 / УПРАВЛЕНИЕ</p><h2>Редактор офферов</h2></div><span className="icon">↗</span></div><p className="muted">Выберите кампанию и настройте её потоки.</p><label>Кампания<select value={selected} onChange={choose}><option value="">Выберите кампанию</option>{campaigns.map(c => <option key={c.id} value={c.id}>{c.name} · #{c.id}</option>)}</select></label>{!streams ? <div className="empty"><div>⇄</div><strong>Выберите кампанию</strong><p>Здесь появятся её потоки.</p></div> : <><button className="secondary" disabled={busy} onClick={() => { if (!dirty || window.confirm('Отменить несохранённые изменения и обновить потоки?')) run(() => load(selected)); }}>↻ &nbsp; Обновить все потоки</button>{streams.length ? streams.map(flow => <StreamCard key={`${selected}-${generation}-${flow.id}`} initial={flow} campaignId={selected} catalog={catalog} busy={busy} run={run} onDirty={onDirty}/>) : <div className="empty">В кампании нет потоков.</div>}</>}</section></div><footer>KEITARO CAMPAIGN STUDIO <span>Управление потоками и офферами</span></footer></main></>;
}

createRoot(document.getElementById('root')).render(<App/>);
