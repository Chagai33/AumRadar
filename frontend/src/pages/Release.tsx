import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';
import { NavBar } from '../components/NavBar';

// Artist Health Engine — Stage 4 phase 2c: the weekly-measurement coverage tracker.
// Each week, once the official 彡…Week#N playlist is CLOSED, we measure "what was
// released × what entered" → hit/miss.
//
// There is no scan to pick here. A weekly playlist is built out of exactly one scan,
// so the backend looks up which one actually contains it and reports the match — the
// old "choose a scan" dropdown made the user do by hand what is a verifiable fact, and
// got weeks wrong when its date guess was off. Weeks still marked ❤ are mid-build and
// are not offered at all.

interface BacklogRow {
  week_number: number; playlist_uri: string; playlist_name: string | null;
  playlist_tracks: number | null; oop_playlist_uri: string | null;
}
interface ScanOpt { id: string; dates: string; tracks: number; }
interface InProgress { week_number: number; name: string | null; }
interface Coverage {
  measured_count: number; measured: string[]; backlog: BacklogRow[];
  available_scans: number; scans: ScanOpt[]; latest_week: number | null; min_tracks?: number;
  in_progress?: InProgress[];
}
interface WeekResult {
  week_number: number; ok: boolean; skipped?: boolean; error?: string;
  releases?: number; hits?: number; shadow?: number; misses?: number; albums?: number;
  matched_scan?: string; match_ratio?: number; playlist_tracks?: number;
}
interface Flooder { artist_uri: string; artist_id: string; artist: string; image: string; genres: string; followers: number; spotify_url: string; efficiency: number; releases: number; hits: number; primary_releases: number; }
interface Quality { flooders: Flooder[]; flooder_count: number; measured_weeks: number; scored_artists: number; current_week: number | null; note?: string; }
const QDEF = { half_life: 26, secondary_weight: 0.3, shadow_factor: 0.05, smoothing_k: 4, smoothing_prior: 0.3, min_primary: 4, threshold: 0.15 };

export const Release: React.FC = () => {
  const [data, setData] = useState<Coverage | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<WeekResult[]>([]);
  const [qp, setQp] = useState(QDEF);
  const [q, setQ] = useState<Quality | null>(null);
  const [qbusy, setQbusy] = useState(false);
  const [qsel, setQsel] = useState<Set<string>>(new Set());
  const [qres, setQres] = useState('');

  const load = async () => {
    try {
      const r = await axios.get('/api/release/coverage');
      const cov: Coverage = r.data;
      setData(cov); setErr('');
      // every offered week is measurable — the backend finds its scan itself
      setSelected(new Set((cov.backlog || []).map(b => b.week_number)));
    } catch (e: any) {
      setErr(e.response?.status === 401 ? 'Please log in.' : (e.response?.data?.detail || e.message));
    }
  };
  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  const toggle = (wk: number) => setSelected(prev => { const n = new Set(prev); n.has(wk) ? n.delete(wk) : n.add(wk); return n; });

  const measure = async () => {
    const rows = (data?.backlog || []).filter(b => selected.has(b.week_number));
    // no scan_id: the backend matches the playlist to the scan that contains it
    let links: any[] = rows.map(b => ({
      week_number: b.week_number, playlist_uri: b.playlist_uri,
      oop_playlist_uri: b.oop_playlist_uri,
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

  // Release Quality (flooders) — live recompute on any knob change (debounced).
  useEffect(() => {
    const t = setTimeout(async () => {
      try { const r = await axios.post('/api/release/quality', qp); setQ(r.data); } catch { /* keep last */ }
    }, 300);
    return () => clearTimeout(t);
  }, [qp]);
  const toggleFl = (uri: string) => setQsel(p => { const n = new Set(p); n.has(uri) ? n.delete(uri) : n.add(uri); return n; });
  const sendFlooders = async () => {
    setQbusy(true); setQres('');
    try {
      const r = await axios.post('/api/release/to-cleanup', { artist_uris: [...qsel] });
      setQres(`Added ${r.data.added} to Cleanup`); setQsel(new Set());
    } catch (e: any) { setQres(e.response?.data?.detail || e.message); }
    finally { setQbusy(false); }
  };

  const backlog = data?.backlog || [];
  const measurable = backlog.length;

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
                  ? <>
                      Week #{r.week_number}: <b className="text-emerald-400">{r.hits}</b> entered · {r.shadow} shadow · <b className="text-red-400">{r.misses}</b> missed{r.albums ? ` · ${r.albums} albums` : ''}
                      {r.matched_scan && (
                        <span className="text-zinc-500"> — matched scan {r.matched_scan}
                          {typeof r.match_ratio === 'number' ? ` (${Math.round(r.match_ratio * 100)}% of ${r.playlist_tracks} tracks)` : ''}
                        </span>
                      )}
                    </>
                  : <span className={r.skipped ? 'text-amber-400' : 'text-red-400'}>Week #{r.week_number}: {r.error}</span>}
              </div>
            ))}
          </div>
        )}

        {(data?.in_progress || []).length > 0 && (
          <div className="p-3 rounded-lg border border-zinc-700/60 bg-zinc-800/40 text-xs text-zinc-400">
            <b className="text-zinc-300">Not measured — still open:</b>{' '}
            {(data?.in_progress || []).map(w => `#${w.week_number}`).join(', ')}. A week marked ❤ is
            still being built; it is measured once you close it and drop the marker.
          </div>
        )}

        {/* backlog */}
        {backlog.length === 0 ? (
          <div className="p-5 rounded-lg border border-emerald-700/40 bg-emerald-900/15 text-center">
            <div className="text-2xl mb-1">{data && data.available_scans === 0 ? '🔍' : '✓'}</div>
            {data && data.available_scans === 0 ? (
              <>
                <div className="font-semibold text-amber-300">No full weekly scans found</div>
                <p className="text-sm text-zinc-400 mt-1">
                  A measurement needs a <b>complete</b> weekly scan — one-week window and ≥{data.min_tracks ?? 300} tracks.
                  1-day, multi-week, or tiny scans are ignored so the data stays correct. Run a proper weekly scan, then come back.
                </p>
              </>
            ) : (
              <>
                <div className="font-semibold text-emerald-300">All caught up</div>
                <p className="text-sm text-zinc-400 mt-1">Every week with a real scan is measured. The next week appears here once you publish its playlist.</p>
              </>
            )}
          </div>
        ) : (
          <div className="bg-[#181818] border border-zinc-800 rounded-xl overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
              <span className="text-sm font-semibold">⚠️ {measurable} week{measurable === 1 ? '' : 's'} ready to measure</span>
              <span className="text-xs text-zinc-500">{selected.size} selected</span>
              <button onClick={measure} disabled={busy || selected.size === 0}
                className="ms-auto px-4 py-1.5 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 font-semibold">
                {busy ? 'Measuring…' : `Measure ${selected.size} →`}
              </button>
            </div>
            {backlog.map(b => (
              <div key={b.week_number} className="flex flex-wrap items-center gap-3 px-4 py-2.5 border-b border-zinc-800/60">
                <input type="checkbox" checked={selected.has(b.week_number)}
                  onChange={() => toggle(b.week_number)} className="w-4 h-4 accent-emerald-500" />
                <div className="min-w-0">
                  <div className="font-medium text-sm">Week #{b.week_number}</div>
                  <div className="text-xs text-zinc-500 truncate max-w-[240px]">{b.playlist_name || b.playlist_uri}</div>
                </div>
                <div className="ms-auto flex items-center gap-2 text-xs text-zinc-500">
                  {b.playlist_tracks != null && <span>{b.playlist_tracks} tracks</span>}
                  <span className="text-zinc-600">· scan found automatically</span>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* release quality — flooders for review */}
        <div className="bg-[#181818] border border-zinc-800 rounded-xl p-4">
          <div className="text-xs font-bold text-zinc-500 uppercase tracking-wide">Release Quality — flooders for review</div>
          <p className="text-[11px] text-zinc-600 mb-3">Artists who release a lot but rarely enter. A separate axis from the RANK list — review here, then send to Cleanup.</p>
          {(!q || q.measured_weeks === 0) ? (
            <div className="text-sm text-zinc-500">Measure at least one week above to compute release quality.</div>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 mb-3">
                <Rng label="Half-life (wk)" min={4} max={104} step={1} value={qp.half_life} onChange={v => setQp({ ...qp, half_life: v })} />
                <Rng label="Flooder threshold" min={0} max={0.6} step={0.01} value={qp.threshold} onChange={v => setQp({ ...qp, threshold: v })} />
                <Rng label="Min releases" min={1} max={12} step={1} value={qp.min_primary} onChange={v => setQp({ ...qp, min_primary: v })} />
                <Rng label="Feature weight" min={0} max={1} step={0.05} value={qp.secondary_weight} onChange={v => setQp({ ...qp, secondary_weight: v })} />
                <Rng label="Shadow factor" min={0} max={0.5} step={0.01} value={qp.shadow_factor} onChange={v => setQp({ ...qp, shadow_factor: v })} />
                <Rng label="Smoothing" min={0} max={12} step={0.5} value={qp.smoothing_k} onChange={v => setQp({ ...qp, smoothing_k: v })} />
              </div>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-sm"><b className="text-red-400">{q.flooder_count}</b> flooders
                  <span className="text-zinc-500"> of {q.scored_artists} scored · {q.measured_weeks} weeks</span></span>
                <button onClick={sendFlooders} disabled={qbusy || qsel.size === 0}
                  className="ms-auto px-3 py-1.5 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 font-semibold">
                  {qbusy ? 'Sending…' : `Add ${qsel.size} to Cleanup`}
                </button>
              </div>
              {qres && <div className="text-xs text-emerald-400 mb-2">{qres} · <Link to="/cleanup" className="underline">Go to Cleanup →</Link></div>}
              <div className="max-h-96 overflow-y-auto">
                {q.flooders.map(f => (
                  <div key={f.artist_uri} onClick={() => toggleFl(f.artist_uri)}
                    className="flex items-center gap-3 px-2 py-1.5 rounded hover:bg-zinc-800/50 cursor-pointer">
                    <input type="checkbox" readOnly checked={qsel.has(f.artist_uri)} className="w-4 h-4 accent-red-500" />
                    {f.image ? <img src={f.image} className="w-8 h-8 rounded-full object-cover" alt="" /> : <div className="w-8 h-8 rounded-full bg-zinc-700" />}
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate">{f.artist}</div>
                      <div className="text-xs text-zinc-500 truncate">{f.genres || '—'}</div>
                    </div>
                    <div className="text-xs text-center w-20"><b>{f.hits}</b>/<b>{f.releases}</b> <span className="text-zinc-500">entered</span></div>
                    <span className="text-xs text-white px-2 py-0.5 rounded bg-red-700">{Math.round(f.efficiency * 100)}%</span>
                    <a href={f.spotify_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                      className="text-xs text-emerald-400 hover:underline w-14 text-center">Spotify ↗</a>
                  </div>
                ))}
                {q.flooders.length === 0 && <div className="text-sm text-zinc-500 py-4 text-center">No flooders at these settings 🎉</div>}
              </div>
            </>
          )}
        </div>

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

      </div>
    </div>
  );
};

const Rng: React.FC<{ label: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void }> =
  ({ label, min, max, step, value, onChange }) => (
    <div>
      <div className="flex justify-between text-[11px] mb-0.5">
        <span className="text-zinc-400">{label}</span><span className="font-mono text-emerald-400">{value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(Number(e.target.value))} className="w-full accent-emerald-500 cursor-pointer" />
    </div>
  );
