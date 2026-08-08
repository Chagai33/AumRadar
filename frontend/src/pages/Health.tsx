import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

// Artist Health Engine — Stage 3b: the tuning dashboard. Drag the knobs, watch the
// 🔴🟠🟡🟢 band counts move live (server recompute < 1s on the in-memory songs — no
// Spotify re-pull), open any artist to see WHY their RANK is what it is, then Apply to
// feed the existing /cleanup. Nothing is unfollowed here. The rich manual-override
// layer (protect / floor / notes / pins) arrives in Stage 3c.

interface Weights {
  half_life: number; weekly: number; outof: number; other: number; legacy: number;
  bands: number[]; candidate_bands: string[];
}
interface Preview {
  counts: Record<string, number>; candidates: number; followed: number;
  current_week: number; generated: string; bands: number[];
}
interface Row {
  artist_uri: string; artist_id: string; artist: string; image: string; genres: string;
  followers: number; rank: number; raw_rank: number; entered: number; band: string;
  spotify_url: string;
}
interface Ranked { total: number; offset: number; limit: number; count: number; rows: Row[]; }
interface Song {
  isrc: string; album_type: string; week_number: number | null; type: string;
  legacy: boolean; age: number; playlist_uri: string; playlist_name: string | null;
  contribution: number; placements: number;
}
interface Playlist { playlist_uri: string; name: string | null; type: string; week_number: number | null; spotify_url: string | null; }
interface Why {
  artist_uri: string; artist_id: string; artist: string; image: string; genres: string;
  followers: number; spotify_url: string; followed: boolean; rank: number;
  effective_rank: number; band: string; entered: number; songs: Song[]; playlists: Playlist[];
  manual: Record<string, any>;
}

const DEFAULTS: Weights = {
  half_life: 26, weekly: 1, outof: 0.05, other: 0.05, legacy: 0.5,
  bands: [0, 0.05, 0.5], candidate_bands: ['red'],
};

// Ordered 🔴🟠🟡🟢 (STAGE3 §7.1). `boundary` describes the band given the 3 sliders.
const BANDS = [
  { key: 'red', emoji: '🔴', label: 'Never entered', chip: 'bg-red-600', ring: 'ring-red-500', boundary: (b: number[]) => `≤ ${fmt(b[0])}` },
  { key: 'orange', emoji: '🟠', label: 'Low', chip: 'bg-orange-500', ring: 'ring-orange-400', boundary: (b: number[]) => `${fmt(b[0])} – ${fmt(b[1])}` },
  { key: 'yellow', emoji: '🟡', label: 'Mid', chip: 'bg-yellow-500', ring: 'ring-yellow-400', boundary: (b: number[]) => `${fmt(b[1])} – ${fmt(b[2])}` },
  { key: 'green', emoji: '🟢', label: 'Strong', chip: 'bg-emerald-600', ring: 'ring-emerald-500', boundary: (b: number[]) => `> ${fmt(b[2])}` },
];
const chipOf: Record<string, string> = { red: 'bg-red-600', orange: 'bg-orange-500', yellow: 'bg-yellow-500', green: 'bg-emerald-600' };

const KNOBS: { key: keyof Weights; label: string; min: number; max: number; step: number; hint?: string }[] = [
  { key: 'half_life', label: 'Half-life (weeks)', min: 1, max: 104, step: 1, hint: 'How fast old placements decay. Higher = older weeks still count.' },
  { key: 'weekly', label: 'Weekly weight', min: 0, max: 2, step: 0.05 },
  { key: 'outof', label: 'Outofplaylist weight', min: 0, max: 1, step: 0.01 },
  { key: 'other', label: 'Other weight (manually included)', min: 0, max: 1, step: 0.01 },
  { key: 'legacy', label: 'Legacy multiplier (season 1)', min: 0, max: 1, step: 0.05 },
];

function fmt(n: number): string {
  if (n === undefined || n === null) return '—';
  if (Number.isInteger(n)) return String(n);
  return n.toFixed(n < 1 ? 3 : 2).replace(/0+$/, '').replace(/\.$/, '');
}
function toId(s: string): string {
  s = (s || '').trim();
  if (s.includes('open.spotify.com/artist/')) return s.split('artist/')[1].split(/[?/]/)[0];
  if (s.includes(':')) return s.split(':').pop() as string;
  return s;
}
function whenStr(gen: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})/.exec(gen || '');
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : (gen || '');
}

export const Health: React.FC = () => {
  const [weights, setWeights] = useState<Weights>(DEFAULTS);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [cold, setCold] = useState(false);
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(true);

  const [openBand, setOpenBand] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [ranked, setRanked] = useState<Ranked | null>(null);
  const [rankedBusy, setRankedBusy] = useState(false);
  const LIMIT = 50;

  const [artistIndex, setArtistIndex] = useState<{ id: string; name: string }[] | null>(null);
  const [q, setQ] = useState('');
  const [why, setWhy] = useState<Why | null>(null);
  const [whyBusy, setWhyBusy] = useState(false);

  const [applying, setApplying] = useState(false);
  const [applyRes, setApplyRes] = useState<any>(null);
  const [manifestOpen, setManifestOpen] = useState(false);
  const firstLoad = useRef(true);

  // Load the last-applied knobs + a soft "active cleanup" flag, then the debounced
  // effect below fetches the first preview.
  useEffect(() => {
    (async () => {
      try { const w = await axios.get('/api/health/weights'); setWeights(prev => ({ ...prev, ...w.data })); } catch { /* defaults */ }
      try { const m = await axios.get('/api/cleanup/latest-manifest'); setManifestOpen(!!m.data?.exists); } catch { /* ignore */ }
    })();
  }, []);

  // The single live loop: any knob / band / page change → (debounced) recompute the
  // counts, and the open band's page. 300ms keeps a slider drag smooth.
  useEffect(() => {
    const t = setTimeout(async () => {
      try {
        const r = await axios.post('/api/health/preview', weights);
        setPreview(r.data); setCold(false); setErr('');
      } catch (e: any) {
        if (e.response?.status === 409) { setCold(true); setPreview(null); }
        else setErr(e.response?.data?.detail || e.message);
        setLoading(false); firstLoad.current = false; return;
      }
      if (openBand) {
        setRankedBusy(true);
        try {
          const r = await axios.post('/api/health/ranked', { ...weights, band: openBand, offset: page * LIMIT, limit: LIMIT });
          setRanked(r.data);
        } catch { /* keep previous page on a transient error */ }
        finally { setRankedBusy(false); }
      }
      setLoading(false); firstLoad.current = false;
    }, firstLoad.current ? 0 : 300);
    return () => clearTimeout(t);
  }, [weights, openBand, page]);

  const setKnob = (k: keyof Weights, v: number) => setWeights(w => ({ ...w, [k]: v }));
  const setBand = (i: number, v: number) => setWeights(w => { const b = [...w.bands]; b[i] = v; return { ...w, bands: b }; });
  const toggleCandidate = (key: string) => setWeights(w => ({
    ...w, candidate_bands: w.candidate_bands.includes(key) ? w.candidate_bands.filter(b => b !== key) : [...w.candidate_bands, key],
  }));
  const drill = (key: string) => { setPage(0); setOpenBand(prev => prev === key ? null : key); };
  const reset = () => { setWeights(DEFAULTS); setOpenBand(null); setPage(0); };

  const ensureIndex = async () => {
    if (artistIndex) return;
    try { const r = await axios.get('/api/artists'); setArtistIndex((r.data || []).map((a: any) => ({ id: a.id, name: a.name }))); }
    catch { setArtistIndex([]); }
  };
  const matches = useMemo(() => {
    const s = q.trim().toLowerCase();
    if (!s || !artistIndex) return [];
    return artistIndex.filter(a => (a.name || '').toLowerCase().includes(s) || a.id.toLowerCase() === s).slice(0, 8);
  }, [q, artistIndex]);

  const openWhy = async (key: string) => {
    setWhyBusy(true); setWhy(null); setQ('');
    try { const r = await axios.post(`/api/health/artist/${encodeURIComponent(toId(key))}`, weights); setWhy(r.data); }
    catch (e: any) { setErr(e.response?.data?.detail || e.message); }
    finally { setWhyBusy(false); }
  };

  const apply = async () => {
    setApplying(true); setApplyRes(null);
    try { const r = await axios.post('/api/health/apply', weights); setApplyRes(r.data); }
    catch (e: any) { setApplyRes({ error: e.response?.data?.detail || e.message }); }
    finally { setApplying(false); }
  };

  return (
    <div className="min-h-screen bg-[#121212] text-zinc-200 pb-28">
      {/* header */}
      <div className="sticky top-0 z-20 bg-[#181818] border-b border-zinc-800 px-5 py-3 flex flex-wrap items-center gap-3">
        <Link to="/dashboard" className="text-zinc-400 hover:text-white text-sm">← Dashboard</Link>
        <h1 className="text-lg font-bold">🎛 Health Tuning</h1>
        {preview && (
          <span className="text-xs text-zinc-500">
            <b className="text-emerald-400">{preview.followed.toLocaleString()}</b> followed · week {preview.current_week}
            {preview.generated ? ` · data ${whenStr(preview.generated)}` : ''}
          </span>
        )}
        <div className="ms-auto flex items-center gap-3">
          <Link to="/recon" className="text-xs text-zinc-400 hover:text-white">📋 Recon</Link>
          <Link to="/bootstrap" className="text-xs text-zinc-400 hover:text-white">⚖️ Bootstrap</Link>
          <Link to="/cleanup" className="text-xs text-zinc-400 hover:text-white">🧹 Cleanup</Link>
        </div>
      </div>

      {err && <div className="mx-5 mt-3 p-3 rounded bg-red-900/40 text-red-300 text-sm">{err}</div>}

      {/* cold start */}
      {cold ? (
        <div className="max-w-2xl mx-auto px-5 mt-10">
          <div className="p-5 rounded-lg border border-amber-500/40 bg-amber-900/15 text-center">
            <div className="text-2xl mb-2">🎛️</div>
            <div className="font-semibold text-amber-300 mb-1">No song data yet</div>
            <p className="text-amber-100/80 text-sm mb-4">
              Live tuning needs the raw playlist fold. Run <b>Bootstrap</b> once to build it, then come back here to play with the knobs.
            </p>
            <Link to="/bootstrap" className="inline-block px-4 py-2 text-sm rounded bg-emerald-700 hover:bg-emerald-600 font-semibold">Go to Bootstrap →</Link>
          </div>
        </div>
      ) : loading ? (
        <div className="text-center text-zinc-500 py-20">Loading engine…</div>
      ) : (
        <div className="max-w-5xl mx-auto px-5 mt-4 space-y-5">
          {/* knobs */}
          <div className="bg-[#181818] border border-zinc-800 rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-bold text-zinc-300 uppercase tracking-wide">Tuning knobs</h2>
              <button onClick={reset} className="text-xs px-2.5 py-1 rounded bg-zinc-800 hover:bg-zinc-700">Reset to defaults</button>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
              {KNOBS.map(k => (
                <Slider key={k.key} label={k.label} hint={k.hint} min={k.min} max={k.max} step={k.step}
                  value={weights[k.key] as number} onChange={v => setKnob(k.key, v)} />
              ))}
            </div>
            <div className="mt-5 pt-4 border-t border-zinc-800">
              <div className="text-xs font-bold text-zinc-500 uppercase tracking-wide mb-3">Band boundaries</div>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-x-8 gap-y-4">
                <Slider label="🔴 → 🟠 (b1)" min={0} max={0.2} step={0.005} value={weights.bands[0]} onChange={v => setBand(0, v)} />
                <Slider label="🟠 → 🟡 (b2)" min={0} max={1} step={0.01} value={weights.bands[1]} onChange={v => setBand(1, v)} />
                <Slider label="🟡 → 🟢 (b3)" min={0} max={3} step={0.05} value={weights.bands[2]} onChange={v => setBand(2, v)} />
              </div>
            </div>
          </div>

          {/* bands */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {BANDS.map(b => {
              const n = preview?.counts?.[b.key] ?? 0;
              const isCand = weights.candidate_bands.includes(b.key);
              const active = openBand === b.key;
              return (
                <div key={b.key}
                  className={`rounded-xl border p-4 cursor-pointer transition-all ${active ? `bg-zinc-800/70 border-zinc-600 ring-1 ${b.ring}` : 'bg-[#181818] border-zinc-800 hover:bg-zinc-800/40'}`}
                  onClick={() => drill(b.key)}>
                  <div className="flex items-center justify-between">
                    <span className="text-2xl">{b.emoji}</span>
                    <span className="text-2xl font-bold">{n.toLocaleString()}</span>
                  </div>
                  <div className="mt-1 text-sm font-medium text-zinc-300">{b.label}</div>
                  <div className="text-[11px] text-zinc-500">rank {preview ? b.boundary(preview.bands) : ''}</div>
                  <label className="mt-2 flex items-center gap-1.5 text-[11px] text-zinc-400 cursor-pointer" onClick={e => e.stopPropagation()}>
                    <input type="checkbox" checked={isCand} onChange={() => toggleCandidate(b.key)} className="w-3.5 h-3.5 accent-red-500" />
                    counts as removal candidate
                  </label>
                </div>
              );
            })}
          </div>

          {/* artist lookup */}
          <div className="bg-[#181818] border border-zinc-800 rounded-xl p-4 relative">
            <label className="text-xs font-bold text-zinc-500 uppercase tracking-wide">Look up an artist</label>
            <input value={q} onFocus={ensureIndex} onChange={e => setQ(e.target.value)}
              placeholder="Search by name, or paste a Spotify ID / URL…"
              className="mt-2 w-full px-3 py-2 text-sm bg-zinc-800 rounded outline-none focus:ring-1 focus:ring-emerald-600" />
            {q.trim() && (
              <div className="absolute left-4 right-4 mt-1 bg-[#202020] border border-zinc-700 rounded-lg shadow-xl z-10 overflow-hidden">
                {matches.map(m => (
                  <button key={m.id} onClick={() => openWhy(m.id)}
                    className="block w-full text-left px-3 py-2 text-sm hover:bg-zinc-700/60 truncate">{m.name}</button>
                ))}
                {(toId(q) !== q.trim() || matches.length === 0) && (
                  <button onClick={() => openWhy(q)} className="block w-full text-left px-3 py-2 text-sm text-emerald-400 hover:bg-zinc-700/60">
                    Look up “{toId(q)}” directly →
                  </button>
                )}
              </div>
            )}
          </div>

          {/* ranked list for the open band */}
          {openBand && (
            <div className="bg-[#181818] border border-zinc-800 rounded-xl overflow-hidden">
              <div className="flex items-center gap-2 px-4 py-3 border-b border-zinc-800">
                <span className="text-sm font-semibold">
                  {BANDS.find(b => b.key === openBand)?.emoji} {BANDS.find(b => b.key === openBand)?.label}
                </span>
                <span className="text-xs text-zinc-500">{ranked?.total?.toLocaleString() ?? 0} artists</span>
                {rankedBusy && <span className="text-xs text-zinc-600">updating…</span>}
                <button onClick={() => setOpenBand(null)} className="ms-auto text-xs text-zinc-500 hover:text-white">✕ close</button>
              </div>
              <div>
                {(ranked?.rows || []).map(r => (
                  <div key={r.artist_uri} onClick={() => openWhy(r.artist_id)}
                    className="flex items-center gap-3 px-4 py-2 border-b border-zinc-800/60 hover:bg-zinc-800/50 cursor-pointer">
                    {r.image ? <img src={r.image} className="w-9 h-9 rounded-full object-cover" alt="" />
                      : <div className="w-9 h-9 rounded-full bg-zinc-700" />}
                    <div className="min-w-0 flex-1">
                      <div className="font-medium truncate">{r.artist}</div>
                      <div className="text-xs text-zinc-500 truncate">{r.genres || '—'}</div>
                    </div>
                    <div className="text-xs text-zinc-500 hidden sm:block w-24 text-center">{(r.followers || 0).toLocaleString()} followers</div>
                    <div className="text-sm text-center w-16"><b>{r.entered}</b> <span className="text-zinc-500 text-xs">songs</span></div>
                    <span className={`text-xs text-white px-2 py-0.5 rounded ${chipOf[r.band]}`}>{fmt(r.rank)}</span>
                  </div>
                ))}
                {ranked && ranked.rows.length === 0 && <div className="text-center text-zinc-500 py-8">No artists in this band.</div>}
              </div>
              {ranked && ranked.total > LIMIT && (
                <div className="flex items-center justify-center gap-3 py-3 text-sm">
                  <button disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))}
                    className="px-3 py-1 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40">← Prev</button>
                  <span className="text-zinc-500">{page * LIMIT + 1}–{Math.min((page + 1) * LIMIT, ranked.total)} of {ranked.total.toLocaleString()}</span>
                  <button disabled={(page + 1) * LIMIT >= ranked.total} onClick={() => setPage(p => p + 1)}
                    className="px-3 py-1 rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40">Next →</button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* apply bar */}
      {!cold && (
        <div className="fixed bottom-0 inset-x-0 z-20 bg-[#181818] border-t border-zinc-800 px-5 py-3 flex flex-wrap items-center gap-3">
          <span className="text-sm">
            <b className="text-red-400">{preview?.candidates?.toLocaleString() ?? 0}</b> candidates
            <span className="text-zinc-500"> (bands: {weights.candidate_bands.join(', ') || 'none'})</span>
          </span>
          {manifestOpen && <span className="text-xs text-amber-400/90">⚠ an open cleanup run exists — Apply replaces its list</span>}
          {applyRes && !applyRes.error && (
            <span className="text-xs text-emerald-400">✓ wrote {applyRes.candidates} · <Link to="/cleanup" className="underline">Go to Cleanup →</Link></span>
          )}
          {applyRes?.error && <span className="text-xs text-red-400">{applyRes.error}</span>}
          <div className="ms-auto flex gap-2">
            <Link to="/cleanup" className="px-4 py-2 text-sm rounded bg-zinc-700 hover:bg-zinc-600">Open Cleanup</Link>
            <button onClick={apply} disabled={applying || !preview || (weights.candidate_bands.length === 0)}
              className="px-4 py-2 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-40 font-semibold">
              {applying ? 'Applying…' : `Apply → feed Cleanup`}
            </button>
          </div>
        </div>
      )}

      {/* why panel (modal) */}
      {(why || whyBusy) && (
        <div className="fixed inset-0 z-30 bg-black/70 flex items-start justify-center p-4 overflow-y-auto" onClick={() => { setWhy(null); }}>
          <div className="bg-[#1c1c1c] rounded-xl max-w-2xl w-full my-8 border border-zinc-700" onClick={e => e.stopPropagation()}>
            {whyBusy || !why ? (
              <div className="p-10 text-center text-zinc-500">Loading breakdown…</div>
            ) : (
              <>
                <div className="flex items-center gap-3 p-5 border-b border-zinc-800">
                  {why.image ? <img src={why.image} className="w-14 h-14 rounded-full object-cover" alt="" />
                    : <div className="w-14 h-14 rounded-full bg-zinc-700" />}
                  <div className="min-w-0 flex-1">
                    <div className="text-lg font-bold truncate">{why.artist || why.artist_id}</div>
                    <div className="text-xs text-zinc-500 truncate">{why.genres || '—'} · {(why.followers || 0).toLocaleString()} followers</div>
                    {!why.followed && <div className="text-[11px] text-amber-400 mt-0.5">not in your followed list</div>}
                  </div>
                  <div className="text-right">
                    <span className={`text-white text-sm px-2 py-1 rounded ${chipOf[why.band]}`}>RANK {fmt(why.effective_rank)}</span>
                    {why.effective_rank !== why.rank && <div className="text-[11px] text-zinc-500 mt-1">raw {fmt(why.rank)} → {fmt(why.effective_rank)} 🛡️</div>}
                    <div className="text-[11px] text-zinc-500 mt-1">{why.entered} songs counted</div>
                  </div>
                </div>

                <div className="p-5 max-h-[55vh] overflow-y-auto space-y-4">
                  <div>
                    <div className="text-xs font-bold text-zinc-500 uppercase tracking-wide mb-2">What makes the score (strongest placement per song)</div>
                    {why.songs.length === 0 && <div className="text-sm text-zinc-500">No appearances in any included playlist — this is why the score is 0.</div>}
                    <div className="space-y-1">
                      {why.songs.map(s => (
                        <div key={s.isrc} className="flex items-center gap-2 text-sm py-1 border-b border-zinc-800/50">
                          <span className={`text-[10px] text-white px-1.5 py-0.5 rounded ${s.type === 'weekly' ? 'bg-emerald-700' : s.type === 'outofplaylist' ? 'bg-zinc-600' : 'bg-indigo-700'}`}>{s.type}</span>
                          {s.legacy && <span className="text-[10px] text-amber-400">legacy</span>}
                          <span className="text-zinc-400 truncate flex-1">
                            {s.playlist_name || (s.week_number ? `Week#${s.week_number}` : '—')}
                            <span className="text-zinc-600"> · age {s.age}w{s.placements > 1 ? ` · ${s.placements} placements` : ''}</span>
                          </span>
                          <span className="text-emerald-400 font-mono">{fmt(s.contribution)}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {why.playlists.length > 0 && (
                    <div>
                      <div className="text-xs font-bold text-zinc-500 uppercase tracking-wide mb-2">Appears in {why.playlists.length} playlist{why.playlists.length === 1 ? '' : 's'}</div>
                      <div className="flex flex-wrap gap-1.5">
                        {why.playlists.map(p => (
                          p.spotify_url
                            ? <a key={p.playlist_uri} href={p.spotify_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()}
                              className="text-xs px-2 py-1 rounded bg-zinc-800 hover:bg-zinc-700 truncate max-w-[220px]" title={p.name || ''}>{p.name || p.playlist_uri}</a>
                            : <span key={p.playlist_uri} className="text-xs px-2 py-1 rounded bg-zinc-800 truncate max-w-[220px]">{p.name || p.playlist_uri}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-2 p-4 border-t border-zinc-800">
                  <a href={why.spotify_url} target="_blank" rel="noreferrer" className="text-sm text-emerald-400 hover:underline">Open in Spotify ↗</a>
                  <button onClick={() => setWhy(null)} className="ms-auto px-4 py-2 text-sm rounded bg-zinc-700 hover:bg-zinc-600">Close</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const Slider: React.FC<{ label: string; hint?: string; min: number; max: number; step: number; value: number; onChange: (v: number) => void }> =
  ({ label, hint, min, max, step, value, onChange }) => (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label className="text-sm text-zinc-300">{label}</label>
        <span className="text-sm font-mono text-emerald-400">{fmt(value)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="w-full accent-emerald-500 cursor-pointer" />
      {hint && <div className="text-[11px] text-zinc-600 mt-0.5">{hint}</div>}
    </div>
  );
