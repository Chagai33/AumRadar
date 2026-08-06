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
  protected?: string[];
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
  const [protectedSet, setProtectedSet] = useState<Set<string>>(new Set());
  const [followCount, setFollowCount] = useState<number | null>(null);
  const [countBusy, setCountBusy] = useState(false);
  const [tierFilter, setTierFilter] = useState<string>('all');
  const [search, setSearch] = useState('');
  const [busy, setBusy] = useState<string>('');
  const [result, setResult] = useState<any>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loadCandidates = async () => {
    try {
      const r = await axios.get('/api/cleanup/candidates');
      setData(r.data); setProtectedSet(new Set(r.data.protected || []));
    } catch (e: any) { setErr(e.response?.data?.detail || e.message); }
  };
  useEffect(() => { loadCandidates().finally(() => setLoading(false)); }, []);

  const fetchCount = async () => {
    setCountBusy(true);
    try { const r = await axios.get('/api/cleanup/follow-count'); setFollowCount(r.data.count); }
    catch { /* keep last known */ } finally { setCountBusy(false); }
  };
  useEffect(() => {
    fetchCount();  // on open
    const onVis = () => { if (document.visibilityState === 'visible') fetchCount(); }; // when returning to the tab
    document.addEventListener('visibilitychange', onVis);
    return () => document.removeEventListener('visibilitychange', onVis);
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

  const toggle = (uri: string) => {
    if (protectedSet.has(uri)) return;   // protected artists can't be selected
    setSelected(prev => { const n = new Set(prev); n.has(uri) ? n.delete(uri) : n.add(uri); return n; });
  };
  const selectShown = () => setSelected(prev => { const n = new Set(prev); shown.forEach(c => { if (!protectedSet.has(c.artist_uri)) n.add(c.artist_uri); }); return n; });
  const selectTier = (t: string) => setSelected(prev => {
    const n = new Set(prev); (data?.candidates || []).filter(c => c.tier === t && !protectedSet.has(c.artist_uri)).forEach(c => n.add(c.artist_uri)); return n;
  });
  const clearSel = () => setSelected(new Set());

  const toggleProtect = async (uri: string, makeProtected: boolean) => {
    setProtectedSet(prev => { const n = new Set(prev); makeProtected ? n.add(uri) : n.delete(uri); return n; });
    if (makeProtected) setSelected(prev => { const n = new Set(prev); n.delete(uri); return n; });
    try { await axios.post('/api/cleanup/protect', { uri, protected: makeProtected }); }
    catch { setProtectedSet(prev => { const n = new Set(prev); makeProtected ? n.delete(uri) : n.add(uri); return n; }); }
  };

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
    if (res) {  // post() returns the data on success, undefined on error
      setData(d => {
        if (!d) return d;
        const remaining = d.candidates.filter(c => !selected.has(c.artist_uri));
        return { ...d, candidates: remaining, count: remaining.length };
      });
      setSelected(new Set());
      fetchCount();  // reflect the new follow count
    }
  };

  const doUndo = async () => { await post('undo', '/api/cleanup/undo'); fetchCount(); };

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
        <div className="ms-auto flex items-center gap-2">
          <span className="text-sm text-zinc-300 flex items-center gap-1.5 bg-zinc-800 rounded-full ps-3 pe-2 py-1" title="מספר עוקבים חי מספוטיפיי">
            👥 <b className="text-white">{followCount ?? '…'}</b> <span className="text-zinc-500">עוקב</span>
            <button onClick={fetchCount} disabled={countBusy} title="רענן"
              className="text-zinc-400 hover:text-white disabled:opacity-50 text-base leading-none">{countBusy ? '⏳' : '↻'}</button>
          </span>
          <button onClick={doUndo} disabled={!!busy}
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
          {result.kind === 'dry' && <>🔎 מתוך <b>{result.requested}</b> שנבחרו, <b>{result.currently_followed}</b> במעקב עכשio ({result.not_followed} כבר לא).</>}
          {result.kind === 'unfollow' && <>✅ הוסרו <b>{result.unfollowed}</b> אמנים{result.failed ? ` · ${result.failed} נכשלו (הגבלת קצב — פשוט נסה שוב)` : ''}.</>}
          {result.kind === 'undo' && <>↩ שוחזרו {result.refollowed} אמנים.</>}
        </div>
      )}

      {/* list */}
      <div className="px-3 sm:px-5 mt-3">
        {shown.map(c => {
          const isProt = protectedSet.has(c.artist_uri);
          const sel = selected.has(c.artist_uri);
          return (
            <div key={c.artist_uri} onClick={() => toggle(c.artist_uri)}
              className={`flex items-center gap-3 p-2 rounded border ${isProt ? 'opacity-60 border-transparent cursor-default' : sel ? 'bg-emerald-950/50 border-emerald-700 cursor-pointer' : 'border-transparent hover:bg-zinc-800/60 cursor-pointer'}`}>
              <input type="checkbox" readOnly disabled={isProt} checked={sel} className="w-4 h-4 accent-emerald-500" />
              {c.image
                ? <img src={c.image} alt="" className="w-10 h-10 rounded-full object-cover" />
                : <div className="w-10 h-10 rounded-full bg-zinc-700" />}
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{c.artist}{isProt && <span className="ms-1 text-amber-400">🔒</span>}</div>
                <div className="text-xs text-zinc-500 truncate">{c.genres || '—'}</div>
              </div>
              <div className="text-xs text-zinc-500 hidden sm:block w-24 text-center">{(c.followers || 0).toLocaleString()} עוקבים</div>
              <div className="text-sm text-center w-24"><b>{c.releases}</b> <span className="text-zinc-500">שחרורים</span></div>
              <span className={`text-xs text-white px-2 py-0.5 rounded ${tierColor[c.tier] || 'bg-zinc-600'}`}>{c.tier}</span>
              <button onClick={e => { e.stopPropagation(); toggleProtect(c.artist_uri, !isProt); }}
                title={isProt ? 'בטל הגנה' : 'הגן מהסרה'}
                className={`text-lg w-8 text-center ${isProt ? 'text-amber-400' : 'text-zinc-500 hover:text-amber-400'}`}>
                {isProt ? '🔒' : '🔓'}
              </button>
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
              אפשר לשחזר בכל רגע עם "בטל הסרה אחרונה" — היא זוכרת בדיוק את מי שהוסר. ביטול מעקב לא מוחק שירים שאהבת או פלייליסטים.
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
