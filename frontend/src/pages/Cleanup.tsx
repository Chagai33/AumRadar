import React, { useEffect, useMemo, useRef, useState } from 'react';
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
  const [progress, setProgress] = useState<{ done: number; total: number; wait: number } | null>(null);
  const cancelRef = useRef(false);
  const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

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

  // Resumable: keeps calling the backend with whatever is left, absorbing Spotify's
  // rate-limit waits, until everything selected is removed (or the user cancels).
  const doUnfollow = async () => {
    setConfirmOpen(false);
    const all = Array.from(selected);
    const total = all.length;
    if (!total) return;
    cancelRef.current = false;
    setBusy('unfollow'); setResult(null);
    setProgress({ done: 0, total, wait: 0 });
    let remaining = all, manifestId: string | undefined, done = 0, stuck = 0;
    try {
      while (remaining.length && !cancelRef.current) {
        let res: any;
        try {
          res = (await axios.post('/api/cleanup/unfollow',
            { uris: remaining, manifest_id: manifestId })).data;
        } catch {
          // network / proxy timeout mid-batch — pause and retry the same remaining
          if (++stuck > 6) { setResult({ kind: 'error', msg: 'Removal stalled — please try again later.' }); break; }
          for (let w = 12; w > 0 && !cancelRef.current; w--) { setProgress({ done, total, wait: w }); await sleep(1000); }
          continue;
        }
        manifestId = res.manifest_id;
        done += res.unfollowed || 0;
        remaining = res.remaining_uris || [];
        if (!res.unfollowed && !res.retry_after) { if (++stuck >= 2) break; } else stuck = 0;
        if (remaining.length && res.retry_after) {
          for (let w = res.retry_after; w > 0 && !cancelRef.current; w--) { setProgress({ done, total, wait: w }); await sleep(1000); }
        }
        setProgress({ done, total, wait: 0 });
      }
      setResult({ kind: 'unfollow', unfollowed: done, failed: remaining.length,
        cancelled: cancelRef.current && remaining.length > 0 });
    } finally {
      setProgress(null); setBusy(''); setSelected(new Set());
      await loadCandidates();  // reflect reality: removed drop off, any leftover stay
      fetchCount();
    }
  };
  const cancelUnfollow = () => { cancelRef.current = true; };

  const doUndo = async () => { await post('undo', '/api/cleanup/undo'); await loadCandidates(); fetchCount(); };

  if (loading) return <div className="min-h-screen bg-[#121212] text-zinc-300 flex items-center justify-center">Loading candidates…</div>;
  if (err) return <div className="min-h-screen bg-[#121212] text-red-400 flex items-center justify-center p-6">Error: {err}</div>;

  return (
    <div className="min-h-screen bg-[#121212] text-zinc-200 pb-28">
      {/* header */}
      <div className="sticky top-0 z-20 bg-[#181818] border-b border-zinc-800 px-5 py-3 flex flex-wrap items-center gap-3">
        <Link to="/dashboard" className="text-zinc-400 hover:text-white text-sm">← Dashboard</Link>
        <h1 className="text-lg font-bold">🧹 Artist Cleanup</h1>
        <span className="text-xs text-zinc-500">
          {data?.count} candidates · {data?.threshold}+ releases · {data?.window}
        </span>
        <div className="ms-auto flex items-center gap-2">
          <span className="text-sm text-zinc-300 flex items-center gap-1.5 bg-zinc-800 rounded-full ps-3 pe-2 py-1" title="Artists you currently follow (live from Spotify)">
            👥 <b className="text-white">{followCount ?? '…'}</b> <span className="text-zinc-500">following</span>
            <button onClick={fetchCount} disabled={countBusy} title="Refresh"
              className="text-zinc-400 hover:text-white disabled:opacity-50 text-base leading-none">{countBusy ? '⏳' : '↻'}</button>
          </span>
          <button onClick={doUndo} disabled={!!busy}
            className="px-3 py-1.5 text-sm rounded bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50">
            {busy === 'undo' ? 'Restoring…' : '↩ Undo last removal'}
          </button>
        </div>
      </div>

      {/* toolbar */}
      <div className="px-5 py-3 flex flex-wrap items-center gap-2 border-b border-zinc-800">
        <button onClick={() => setTierFilter('all')}
          className={`px-2.5 py-1 text-xs rounded ${tierFilter === 'all' ? 'bg-white text-black' : 'bg-zinc-800'}`}>All</button>
        {TIERS.map(t => (
          <div key={t} className="flex items-center rounded overflow-hidden">
            <button onClick={() => setTierFilter(t)}
              className={`px-2.5 py-1 text-xs ${tierFilter === t ? 'bg-white text-black' : 'bg-zinc-800'}`}>
              {t} <span className="opacity-60">({tierCounts[t] || 0})</span>
            </button>
            <button onClick={() => selectTier(t)} title="Select entire tier"
              className="px-2 py-1 text-xs bg-zinc-700 hover:bg-emerald-700">✓</button>
          </div>
        ))}
        <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search name / genre…"
          className="ms-2 px-3 py-1 text-sm bg-zinc-800 rounded outline-none w-52" />
        <button onClick={selectShown} className="px-2.5 py-1 text-xs rounded bg-emerald-800 hover:bg-emerald-700">Select shown ({shown.length})</button>
        <button onClick={clearSel} className="px-2.5 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700">Clear selection</button>
      </div>

      {/* result banner */}
      {result && (
        <div className={`mx-5 mt-3 p-3 rounded text-sm ${result.kind === 'error' ? 'bg-red-900/40 text-red-300' : 'bg-zinc-800'}`}>
          {result.kind === 'error' && <>Error: {result.msg}</>}
          {result.kind === 'dry' && <>🔎 Of <b>{result.requested}</b> selected, <b>{result.currently_followed}</b> still followed ({result.not_followed} already unfollowed).</>}
          {result.kind === 'unfollow' && (result.cancelled
            ? <>⏹ Cancelled — removed <b>{result.unfollowed}</b>, {result.failed} remaining.</>
            : <>✅ Removed <b>{result.unfollowed}</b> artist{result.unfollowed === 1 ? '' : 's'}{result.failed ? ` · ${result.failed} not removed` : ' — all done 🎉'}.</>)}
          {result.kind === 'undo' && <>↩ Restored {result.refollowed} artist{result.refollowed === 1 ? '' : 's'}.</>}
        </div>
      )}

      {/* live progress while removing */}
      {progress && (
        <div className="mx-5 mt-3 p-3 rounded bg-zinc-800">
          <div className="flex items-center justify-between text-sm mb-2">
            <span>
              {progress.wait > 0
                ? <>⏳ Spotify rate limit — waiting <b>{progress.wait}s</b>, then continuing… <span className="text-zinc-500">({progress.done}/{progress.total} removed)</span></>
                : <>Removing… <b className="text-emerald-400">{progress.done}</b> of {progress.total}</>}
            </span>
            <button onClick={cancelUnfollow}
              className="px-3 py-1 text-xs rounded bg-zinc-700 hover:bg-zinc-600">Cancel</button>
          </div>
          <div className="h-2 rounded bg-zinc-700 overflow-hidden">
            <div className="h-full bg-emerald-500 transition-all duration-300"
              style={{ width: `${progress.total ? Math.round((progress.done / progress.total) * 100) : 0}%` }} />
          </div>
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
              <div className="text-xs text-zinc-500 hidden sm:block w-24 text-center">{(c.followers || 0).toLocaleString()} followers</div>
              <div className="text-sm text-center w-24"><b>{c.releases}</b> <span className="text-zinc-500">releases</span></div>
              <span className={`text-xs text-white px-2 py-0.5 rounded ${tierColor[c.tier] || 'bg-zinc-600'}`}>{c.tier}</span>
              <button onClick={e => { e.stopPropagation(); toggleProtect(c.artist_uri, !isProt); }}
                title={isProt ? 'Unprotect' : 'Protect from removal'}
                className={`text-lg w-8 text-center ${isProt ? 'text-amber-400' : 'text-zinc-500 hover:text-amber-400'}`}>
                {isProt ? '🔒' : '🔓'}
              </button>
              <a href={c.spotify_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                className="text-xs text-emerald-400 hover:underline w-16 text-center">Spotify ↗</a>
            </div>
          );
        })}
        {shown.length === 0 && <div className="text-center text-zinc-500 py-10">No matches for this filter.</div>}
      </div>

      {/* sticky action bar */}
      <div className="fixed bottom-0 inset-x-0 z-20 bg-[#181818] border-t border-zinc-800 px-5 py-3 flex items-center gap-3">
        <span className="text-sm"><b className="text-emerald-400">{selected.size}</b> selected for removal</span>
        <div className="ms-auto flex gap-2">
          <button onClick={() => post('dry', '/api/cleanup/dry-run', { uris: Array.from(selected) })}
            disabled={!!busy || selected.size === 0}
            className="px-4 py-2 text-sm rounded bg-zinc-700 hover:bg-zinc-600 disabled:opacity-40">
            {busy === 'dry' ? 'Checking…' : 'Dry run'}
          </button>
          <button onClick={() => setConfirmOpen(true)} disabled={!!busy || selected.size === 0}
            className="px-4 py-2 text-sm rounded bg-red-600 hover:bg-red-500 disabled:opacity-40 font-semibold">
            {busy === 'unfollow' ? 'Removing…' : `Remove ${selected.size}`}
          </button>
        </div>
      </div>

      {/* confirm modal */}
      {confirmOpen && (
        <div className="fixed inset-0 z-30 bg-black/70 flex items-center justify-center p-4" onClick={() => setConfirmOpen(false)}>
          <div className="bg-[#202020] rounded-lg p-6 max-w-md w-full" onClick={e => e.stopPropagation()}>
            <h2 className="text-lg font-bold mb-2">Remove {selected.size} artist{selected.size === 1 ? '' : 's'}?</h2>
            <p className="text-sm text-zinc-400 mb-4">
              This unfollows them on Spotify. You can restore them anytime with “Undo last removal” — it remembers exactly who was removed. Your liked songs and playlists are not affected.
            </p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setConfirmOpen(false)} className="px-4 py-2 text-sm rounded bg-zinc-700 hover:bg-zinc-600">Cancel</button>
              <button onClick={doUnfollow} className="px-4 py-2 text-sm rounded bg-red-600 hover:bg-red-500 font-semibold">Yes, remove</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
