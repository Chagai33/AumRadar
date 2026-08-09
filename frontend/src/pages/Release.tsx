import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { NavBar } from '../components/NavBar';

// Artist Health Engine — Stage 4 phase 2c: the weekly-measurement coverage tracker.
// Each week, once the official 彡…Week#N playlist is published, we measure "what was
// released × what entered" → hit/miss. This page shows what's measured, and a BACKLOG
// of unmeasured weeks (if you didn't confirm for a while, they all show together) each
// with a proposed scan↔playlist link you confirm — one or many at once.

interface BacklogRow {
  week_number: number; playlist_uri: string; playlist_name: string | null;
  oop_playlist_uri: string | null; proposed_scan_id: string | null;
  proposed_scan_dates: string | null; proposed_scan_tracks: number | null;
}
interface ScanOpt { id: string; dates: string; tracks: number; }
interface Coverage {
  measured_count: number; measured: string[]; backlog: BacklogRow[];
  available_scans: number; scans: ScanOpt[]; latest_week: number | null;
}
interface WeekResult { week_number: number; ok: boolean; error?: string; releases?: number; hits?: number; shadow?: number; misses?: number; albums?: number; }

export const Release: React.FC = () => {
  const [data, setData] = useState<Coverage | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [chosenScan, setChosenScan] = useState<Record<number, string>>({});
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<WeekResult[]>([]);

  const load = async () => {
    try {
      const r = await axios.get('/api/release/coverage');
      const cov: Coverage = r.data;
      setData(cov); setErr('');
      const cs: Record<number, string> = {};
      (cov.backlog || []).forEach(b => { if (b.proposed_scan_id) cs[b.week_number] = b.proposed_scan_id; });
      setChosenScan(cs);
      setSelected(new Set((cov.backlog || []).filter(b => b.proposed_scan_id).map(b => b.week_number)));
    } catch (e: any) {
      setErr(e.response?.status === 401 ? 'Please log in.' : (e.response?.data?.detail || e.message));
    }
  };
  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  const toggle = (wk: number) => setSelected(prev => { const n = new Set(prev); n.has(wk) ? n.delete(wk) : n.add(wk); return n; });

  const measure = async () => {
    const rows = (data?.backlog || []).filter(b => selected.has(b.week_number) && chosenScan[b.week_number]);
    let links: any[] = rows.map(b => ({
      week_number: b.week_number, playlist_uri: b.playlist_uri,
      scan_id: chosenScan[b.week_number], oop_playlist_uri: b.oop_playlist_uri,
    }));
    if (!links.length) return;
    setBusy(true); setResults([]);
    const acc: WeekResult[] = [];
    try {
      let guard = 0;
      while (links.length && guard++ < 50) {
        const r = (await axios.post('/api/release/measure', { links })).data;
        acc.push(...(r.measured || []));
        setResults([...acc]);
        links = r.remaining || [];
        if (r.done) break;
      }
    } catch (e: any) {
      setErr(e.response?.data?.detail || e.message);
    } finally {
      setBusy(false);
      await load();
    }
  };

  const backlog = data?.backlog || [];

  if (loading) return <div className="min-h-screen bg-[#121212] text-zinc-300 flex items-center justify-center">Loading coverage…</div>;

  return (
    <div className="min-h-screen bg-[#121212] text-zinc-200 pb-16">
      <NavBar />
      <div className="sticky top-14 z-30 bg-[#181818] border-b border-zinc-800 px-5 py-2.5 flex flex-wrap items-center gap-3">
        <h1 className="text-base font-bold">📆 Weekly Measurement</h1>
        {data && (
          <span className="text-xs text-zinc-500">
            <b className="text-emerald-400">{data.measured_count}</b> weeks measured
            {data.latest_week ? ` · latest week #${data.latest_week}` : ''} · {data.available_scans} scans available
          </span>
        )}
        <button onClick={() => { setLoading(true); load().finally(() => setLoading(false)); }}
          className="ms-auto text-xs px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700">↻ Refresh</button>
      </div>

      {err && <div className="mx-5 mt-3 p-3 rounded bg-red-900/40 text-red-300 text-sm">{err}</div>}

      <div className="max-w-3xl mx-auto px-5 mt-4 space-y-4">
        <p className="text-sm text-zinc-400">
          After you publish a week's official <b>彡 Week#N</b> playlist, measure it here: the app checks
          which of that week's releases entered, building the forward "release quality" data. Nothing is
          changed on Spotify.
        </p>

        {/* results banner */}
        {results.length > 0 && (
          <div className="p-3 rounded bg-zinc-800 text-sm space-y-1">
            <div className="font-semibold text-emerald-300">Measured {results.filter(r => r.ok).length} week{results.length === 1 ? '' : 's'}:</div>
            {results.map(r => (
              <div key={r.week_number} className="text-xs">
                {r.ok
                  ? <>Week #{r.week_number}: <b className="text-emerald-400">{r.hits}</b> entered · {r.shadow} shadow · <b className="text-red-400">{r.misses}</b> missed{r.albums ? ` · ${r.albums} albums` : ''}</>
                  : <span className="text-red-400">Week #{r.week_number}: {r.error}</span>}
              </div>
            ))}
          </div>
        )}

        {/* backlog */}
        {backlog.length === 0 ? (
          <div className="p-5 rounded-lg border border-emerald-700/40 bg-emerald-900/15 text-center">
            <div className="text-2xl mb-1">✓</div>
            <div className="font-semibold text-emerald-300">All caught up</div>
            <p className="text-sm text-zinc-400 mt-1">
              No unmeasured weeks with an available scan. When you publish the next week's playlist (and its
              scan is done), it'll appear here to measure.
            </p>
          </div>
        ) : (
          <div className="bg-[#181818] border border-zinc-800 rounded-xl overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
              <span className="text-sm font-semibold">⚠️ {backlog.length} week{backlog.length === 1 ? '' : 's'} to measure</span>
              <span className="text-xs text-zinc-500">{selected.size} selected</span>
              <button onClick={measure} disabled={busy || selected.size === 0}
                className="ms-auto px-4 py-1.5 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 font-semibold">
                {busy ? 'Measuring…' : `Measure ${selected.size} →`}
              </button>
            </div>
            {backlog.map(b => {
              const linkable = !!b.proposed_scan_id;
              return (
                <div key={b.week_number} className="flex flex-wrap items-center gap-3 px-4 py-2.5 border-b border-zinc-800/60">
                  <input type="checkbox" disabled={!linkable} checked={selected.has(b.week_number)}
                    onChange={() => toggle(b.week_number)} className="w-4 h-4 accent-emerald-500" />
                  <div className="min-w-0">
                    <div className="font-medium text-sm">Week #{b.week_number}</div>
                    <div className="text-xs text-zinc-500 truncate max-w-[240px]">{b.playlist_name || b.playlist_uri}</div>
                  </div>
                  <div className="ms-auto flex items-center gap-2 text-xs">
                    <span className="text-zinc-500">from scan</span>
                    {linkable ? (
                      <select value={chosenScan[b.week_number] || ''} onChange={e => setChosenScan(p => ({ ...p, [b.week_number]: e.target.value }))}
                        className="bg-zinc-800 rounded px-2 py-1 outline-none max-w-[220px]">
                        {(data?.scans || []).map(s => (
                          <option key={s.id} value={s.id}>{s.dates} ({s.tracks})</option>
                        ))}
                      </select>
                    ) : <span className="text-amber-400">no scan available — run a scan for this week</span>}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* recently measured */}
        {data && data.measured.length > 0 && (
          <div>
            <div className="text-xs font-bold text-zinc-500 uppercase tracking-wide mb-2">Recently measured</div>
            <div className="flex flex-wrap gap-1.5">
              {data.measured.map(w => (
                <span key={w} className="text-xs px-2 py-1 rounded bg-emerald-900/30 text-emerald-300">✓ Week #{w}</span>
              ))}
            </div>
          </div>
        )}

        <p className="text-xs text-zinc-600">
          The measured data feeds Release Quality (coming next) → the "flooder" review list → <Link to="/cleanup" className="text-emerald-400 hover:underline">Cleanup</Link>.
        </p>
      </div>
    </div>
  );
};
