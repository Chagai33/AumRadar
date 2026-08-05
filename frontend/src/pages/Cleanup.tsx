import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

interface Candidate {
  artist_uri: string;
  artist_id: string;
  artist: string;
  image: string;
  genres: string;
  followers: number;
  releases: number;
  entered: number;
  tier: string;
  spotify_url: string;
}
interface CandResp {
  generated: string;
  threshold: number;
  window: string;
  count: number;
  candidates: Candidate[];
}

const TIERS = ['50+', '21-50', '11-20', '6-10'];
const tierColor: Record<string, string> = {
  '50+': 'bg-red-600', '21-50': 'bg-orange-600', '11-20': 'bg-yellow-600', '6-10': 'bg-zinc-600',
};

export const Cleanup: React.FC = () => {
  const [data, setData] = useState<CandResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [tierFilter, setTierFilter] = useState<string>('all');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState<string>('');
  const [result, setResult] = useState<any>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  useEffect(() => {
    axios.get('/api/cleanup/candidates')
      .then(r => setData(r.data))
      .catch(e => setErr(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false));
  }, []);

  const shown = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.candidates.filter(c =>
      (tierFilter === 'all' || c.tier === tierFilter) &&
      (!q || (c.artist || '').toLowerCase().includes(q) || (c.genres || '').toLowerCase().includes(q))
    );
  }, [data, tierFilter, search]);

  const tierCounts = useMemo(() => {
    const m: Record<string, number> = {};
    (data?.candidates || []).forEach(c => { m[c.tier] = (m[c.tier] || 0) + 1; });
    return m;
  }, [data]);

  const toggle = (uri: string) => setSelected(prev => {
    const n = new Set(prev); n.has(uri) ? n.delete(uri) : n.add(uri); return n;
  });
  const selectShown = () => setSelected(prev => { const n = new Set(prev); shown.forEach(c => n.add(c.artist_uri)); return n; });
  const selectTier = (t: string) => setSelected(prev => {
    const n = new Set(prev); (data?.candidates || []).filter(c => c.tier === t).forEach(c => n.add(c.artist_uri)); return n;
  });
  const clearSel = () => setSelected(new Set());

  const post = async (kind: string, url: string, body?: any) => {
    setBusy(kind); setResult(null);
    try {
      const r = await axios.post(url, body || {});
      setResult({ kind, ...r.data });
      return r.data;
    } catch (e: any) {
      setResult({ kind: 'error', msg: e.response?.data?.detail || e.message });
    } finally { setBusy(''); }
  };

  const doUnfollow = async () => {
    setConfirmOpen(false);
    const res = await post('unfollow', '/api/cleanup/unfollow', { uris: Array.from(selected) });
    if (res && res.unfollowed >= 0) {
      setData(d => d ? { ...d, candidates: d.candidates.filter(c => !selected.has(c.artist_uri)) } : d);
      setSelected(new Set());
    }
  };

  if (loading) return <div className="min-h-screen bg-[#121212] text-zinc-300 flex items-center justify-center">טוען מועמדים…</div>;
  if (err) return <div className="min-h-screen bg-[#121212] text-red-400 flex items-center justify-center p-6">שגיאה: {err}</div>;

  return (
    <div dir="rtl" className="min-h-screen bg-[#121212] text-zinc-200 pb-28">
      {/* header */}
      <div className="sticky top-0 z-20 bg-[#181818] border-b border-zinc-800 px-5 py-3 flex flex-wrap items-center gap-3">
        <Link to="/dashboard" className="text-zinc-400 hover:text-white text-sm">← לוח הבקרה</Link>
        <h1 className="text-lg font-bold">🧹 ניקוי אמנים</h1>
        <span className="text-xs text-zinc-500">
          {data?.count} מועמדים · רף {data?.threshold}+ · {data?.window}
        </span>
        <div className="ms-auto flex gap-2">
          <button onClick={() => post('backup', '/api/cleanup/backup')} disabled={!!busy}
            className="px-3 py-1.5 text-sm rounded bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50">
            {busy === 'backup' ? 'מגבה…' : 'גבה עוקבים'}
          </button>
          <button onClick={() => post('undo', '/api/cleanup/undo')} disabled={!!busy}
            className="px-3 py-1.5 text-sm rounded bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50">
            {busy === 'undo' ? 'משחזר…' : '↩ בטל הסרה אחרונה'}
          </button>
        </div>
      </div>

      {/* toolbar */}
      <div className="px-5 py-3 flex flex-wrap items-center gap-2 border-b border-zinc-800">
        <button onClick={() => setTierFilter('all')}
          className={`px-2.5 py-1 text-xs rounded ${tierFilter === 'all' ? 'bg-white text-black' : 'bg-zinc-800'}`}>הכל</button>
        {TIERS.map(t => (
          <div key={t} className="flex items-center rounded overflow-hidden">
            <button onClick={() => setTierFilter(t)}
              className={`px-2.5 py-1 text-xs ${tierFilter === t ? 'bg-white text-black' : 'bg-zinc-800'}`}>
              {t} <span className="opacity-60">({tierCounts[t] || 0})</span>
            </button>
            <button onClick={() => selectTier(t)} title="בחר שכבה שלמה"
              className="px-2 py-1 text-xs bg-zinc-700 hover:bg-emerald-700">✓</button>
          </div>
        ))}
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="חיפוש שם / ז'אנר…"
          className="ms-2 px-3 py-1 text-sm bg-zinc-800 rounded outline-none w-52" />
        <button onClick={selectShown} className="px-2.5 py-1 text-xs rounded bg-emerald-800 hover:bg-emerald-700">בחר את המוצגים ({shown.length})</button>
        <button onClick={clearSel} className="px-2.5 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700">נקה בחירה</button>
      </div>

      {/* result banner */}
      {result && (
        <div className={`mx-5 mt-3 p-3 rounded text-sm ${result.kind === 'error' ? 'bg-red-900/40 text-red-300' : 'bg-zinc-800'}`}>
          {result.kind === 'error' && <>שגיאה: {result.msg}</>}
          {result.kind === 'backup' && <>✅ גובו {result.count} עוקבים (מזהה גיבוי {result.backup_id}).</>}
          {result.kind === 'dry' && <>🔎 הרצת יבש: יוסרו <b>{result.will_unfollow}</b> · כבר לא-עוקב {result.already_not_followed} · מ-{result.current_follow_count} → {result.after_count}.</>}
          {result.kind === 'unfollow' && <>✅ הוסרו <b>{result.unfollowed}</b> · דולגו {result.skipped_not_followed} · מ-{result.before_count} → {result.after_count}{result.verify_still_following ? ` · ⚠ ${result.verify_still_following} עדיין קיימים` : ''}{result.errors?.length ? ` · שגיאות: ${result.errors.length}` : ''}.</>}
          {result.kind === 'undo' && <>↩ שוחזרו {result.refollowed} אמנים.</>}
        </div>
      )}

      {/* list */}
      <div className="px-3 sm:px-5 mt-3">
        {shown.map(c => {
          const sel = selected.has(c.artist_uri);
          return (
            <div key={c.artist_uri} onClick={() => toggle(c.artist_uri)}
              className={`flex items-center gap-3 p-2 rounded cursor-pointer border ${sel ? 'bg-emerald-950/50 border-emerald-700' : 'border-transparent hover:bg-zinc-800/60'}`}>
              <input type="checkbox" readOnly checked={sel} className="w-4 h-4 accent-emerald-500" />
              {c.image
                ? <img src={c.image} alt="" className="w-10 h-10 rounded-full object-cover" />
                : <div className="w-10 h-10 rounded-full bg-zinc-700" />}
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{c.artist}</div>
                <div className="text-xs text-zinc-500 truncate">{c.genres || '—'}</div>
              </div>
              <div className="text-xs text-zinc-500 hidden sm:block w-24 text-center">{(c.followers || 0).toLocaleString()} עוקבים</div>
              <div className="text-sm text-center w-24"><b>{c.releases}</b> <span className="text-zinc-500">שחרורים</span></div>
              <span className={`text-xs text-white px-2 py-0.5 rounded ${tierColor[c.tier] || 'bg-zinc-600'}`}>{c.tier}</span>
              <a href={c.spotify_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                className="text-xs text-emerald-400 hover:underline w-16 text-center">Spotify ↗</a>
            </div>
          );
        })}
        {shown.length === 0 && <div className="text-center text-zinc-500 py-10">אין תוצאות לסינון הזה.</div>}
      </div>

      {/* sticky action bar */}
      <div className="fixed bottom-0 inset-x-0 z-20 bg-[#181818] border-t border-zinc-800 px-5 py-3 flex items-center gap-3">
        <span className="text-sm"><b className="text-emerald-400">{selected.size}</b> נבחרו להסרה</span>
        <div className="ms-auto flex gap-2">
          <button onClick={() => post('dry', '/api/cleanup/dry-run', { uris: Array.from(selected) })}
            disabled={!!busy || selected.size === 0}
            className="px-4 py-2 text-sm rounded bg-zinc-700 hover:bg-zinc-600 disabled:opacity-40">
            {busy === 'dry' ? 'בודק…' : 'הרצת יבש'}
          </button>
          <button onClick={() => setConfirmOpen(true)} disabled={!!busy || selected.size === 0}
            className="px-4 py-2 text-sm rounded bg-red-600 hover:bg-red-500 disabled:opacity-40 font-semibold">
            {busy === 'unfollow' ? 'מסיר…' : `הסר ${selected.size} אמנים`}
          </button>
        </div>
      </div>

      {/* confirm modal */}
      {confirmOpen && (
        <div className="fixed inset-0 z-30 bg-black/70 flex items-center justify-center p-4" onClick={() => setConfirmOpen(false)}>
          <div className="bg-[#202020] rounded-lg p-6 max-w-md w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold mb-2">לבטל מעקב אחרי {selected.size} אמנים?</h2>
            <p className="text-sm text-zinc-400 mb-4">
              המערכת קודם <b>מגבה</b> את כל העוקבים שלך, ורק אז מסירה. אפשר לשחזר בכל רגע עם "בטל הסרה אחרונה". ביטול מעקב לא מוחק שירים שאהבת או פלייליסטים.
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmOpen(false)} className="px-4 py-2 text-sm rounded bg-zinc-700 hover:bg-zinc-600">ביטול</button>
              <button onClick={doUnfollow} className="px-4 py-2 text-sm rounded bg-red-600 hover:bg-red-500 font-semibold">כן, הסר</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
