import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { NavBar } from '../components/NavBar';

interface PL {
  playlist_uri: string;
  name: string;
  type: 'weekly' | 'outofplaylist' | 'other';
  week_number: number | null;
  season: string | null;
  legacy: boolean;
  family: string | null;
  track_count: number;
  image: string | null;
  spotify_url: string;
  public: boolean | null;   // Spotify: public ≈ shown on profile; null when Spotify omits it
  collaborative?: boolean;
  included: boolean;
  auto_included: boolean;
}
interface Resp {
  generated: string;
  total: number;
  counts: { weekly: number; outofplaylist: number; other: number };
  followed_count: number;
  included_count: number;
  playlists: PL[];
}

const TYPE_CLS: Record<string, string> = {
  weekly: 'bg-emerald-800 text-emerald-200',
  outofplaylist: 'bg-sky-800 text-sky-200',
  other: 'bg-zinc-700 text-zinc-300',
};
const TYPE_LABEL: Record<string, string> = { weekly: 'weekly', outofplaylist: 'outof', other: 'other' };

const FAM_ORDER = ['favorite', 'bestof', 'genre', 'event', 'listen', 'mood', 'trip', 'personal', 'dj', 'working', 'aggregator', 'uncategorized'];
const FAM_LABEL: Record<string, string> = {
  favorite: '⭐ Favorites', bestof: '🏆 Best-of', genre: '🎧 Genre', event: '🎉 Events',
  listen: '💾 Listen / likes / Shazam', mood: '🧘 Mood / activity', trip: '✈️ Trips',
  personal: '👤 Personal', dj: '🎛 DJ / sets', working: '🛠 Working lists',
  aggregator: '🚫 Aggregators', uncategorized: '❓ Uncategorized',
};
const DANGER_FAM = new Set(['aggregator']);

interface Top { key: string; label: string; by: 'season' | 'family'; match: (p: PL) => boolean; }
const TOPS: Top[] = [
  { key: 'weekly', label: '📅 Weekly', by: 'season', match: p => p.type === 'weekly' },
  { key: 'outof', label: '🌓 Outofplaylist', by: 'season', match: p => p.type === 'outofplaylist' },
  { key: 'collections', label: '🗂 Season collections', by: 'season', match: p => p.type === 'other' && !!p.season },
  { key: 'other', label: '• Other / timeless', by: 'family', match: p => p.type === 'other' && !p.season },
];

const seasonOrder = (s: string) => (s === 'No season' ? 99 : parseInt(s.replace(/\D/g, '')) || 98);

export const Recon: React.FC = () => {
  const [data, setData] = useState<PL[] | null>(null);
  const [meta, setMeta] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [scanning, setScanning] = useState(false);
  const [q, setQ] = useState('');
  const [subQ, setSubQ] = useState<Record<string, string>>({});
  const [openTop, setOpenTop] = useState<Set<string>>(new Set(['weekly']));
  const [openSub, setOpenSub] = useState<Set<string>>(new Set());
  const [imgs, setImgs] = useState(false);
  const [dir, setDir] = useState(1);
  const [hideExc, setHideExc] = useState(false);

  const load = async () => {
    try {
      const r = await axios.get('/api/recon/playlists');
      setMeta(r.data); setData(r.data.playlists); setErr('');
    } catch (e: any) {
      if (e.response?.status === 404) setData(null);
      else setErr(e.response?.data?.detail || e.message);
    }
  };
  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  const rescan = async () => {
    setScanning(true); setErr('');
    try { await axios.post('/api/recon/scan'); await load(); }
    catch (e: any) { setErr(e.response?.data?.detail || e.message); }
    finally { setScanning(false); }
  };

  const patch = (uris: Set<string>, val: boolean) =>
    setData(d => (d ? d.map(x => (uris.has(x.playlist_uri) ? { ...x, included: val } : x)) : d));

  const setInc = async (p: PL, val: boolean) => {
    patch(new Set([p.playlist_uri]), val);
    try { await axios.post('/api/recon/include', { playlist_uri: p.playlist_uri, included: val }); }
    catch { patch(new Set([p.playlist_uri]), !val); }
  };
  const setBulk = async (pls: PL[], val: boolean) => {
    const uris = pls.map(p => p.playlist_uri);
    patch(new Set(uris), val);
    try { await axios.post('/api/recon/include-batch', { uris, included: val }); }
    catch { load(); }
  };

  const includedCount = useMemo(() => (data || []).filter(p => p.included).length, [data]);
  const pubCount = useMemo(() => (data || []).filter(p => p.public === true).length, [data]);
  const privCount = useMemo(() => (data || []).filter(p => p.public === false).length, [data]);
  const nullCount = (data?.length || 0) - pubCount - privCount;
  const gVisible = (p: PL) => (!q || p.name.toLowerCase().includes(q.toLowerCase())) && (!hideExc || p.included);
  const toggle = (set: Set<string>, key: string, upd: (s: Set<string>) => void) => {
    const n = new Set(set); n.has(key) ? n.delete(key) : n.add(key); upd(n);
  };

  if (loading) return <div className="min-h-screen bg-[#121212] text-zinc-300 flex items-center justify-center">Loading…</div>;

  return (
    <div className="min-h-screen bg-[#121212] text-zinc-200 pb-16">
      <NavBar />
      <div className="sticky top-14 z-30 bg-[#181818] border-b border-zinc-800 px-5 py-2.5 flex flex-wrap items-center gap-3">
        <h1 className="text-base font-bold">📋 Playlist Recon</h1>
        {data && (
          <span className="text-xs text-zinc-500">
            {data.length} owned · <b className="text-emerald-400">{includedCount}</b> feeding the engine
            {meta?.followed_count ? ` · ${meta.followed_count} followed (excluded)` : ''}
            {data.length > 0 && (
              <> · <span title="Public — shown on your profile">🌐 {pubCount}</span> · <span title="Private">🔒 {privCount}</span>
                {nullCount > 0 && <span title="Spotify returned no public/private value for these"> · ❔ {nullCount}</span>}</>
            )}
          </span>
        )}
        <button onClick={rescan} disabled={scanning}
          className="ms-auto px-3 py-1.5 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 font-semibold">
          {scanning ? 'Scanning…' : data ? '↻ Rescan' : 'Scan from Spotify'}
        </button>
      </div>

      {err && <div className="mx-5 mt-3 p-3 rounded bg-red-900/40 text-red-300 text-sm">{err}</div>}

      {!data && !err && (
        <div className="flex flex-col items-center gap-3 py-24 text-center px-6">
          <div className="text-4xl">📋</div>
          <h2 className="text-lg font-semibold">No snapshot yet</h2>
          <p className="text-sm text-zinc-500 max-w-md">Recon reads your owned playlists and sorts them by type and season. Nothing changes on Spotify.</p>
          <button onClick={rescan} disabled={scanning}
            className="mt-2 px-4 py-2 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 font-semibold">
            {scanning ? 'Scanning…' : 'Scan from Spotify'}
          </button>
        </div>
      )}

      {data && (
        <>
          <div className="px-5 py-3 border-b border-zinc-800 space-y-2">
            <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search all playlists…"
              className="w-full px-3 py-2 text-sm bg-zinc-800 rounded outline-none" />
            <div className="flex gap-2 flex-wrap">
              <button onClick={() => setImgs(v => !v)}
                className={`px-3 py-1 text-xs rounded border ${imgs ? 'bg-emerald-900/40 border-emerald-700 text-emerald-300' : 'bg-zinc-800 border-transparent text-zinc-400'}`}>🖼 Images {imgs ? 'on' : 'off'}</button>
              <button onClick={() => setDir(d => -d)} className="px-3 py-1 text-xs rounded bg-zinc-800 text-zinc-400">↕ Week {dir > 0 ? '↑' : '↓'}</button>
              <button onClick={() => setHideExc(v => !v)}
                className={`px-3 py-1 text-xs rounded border ${hideExc ? 'bg-emerald-900/40 border-emerald-700 text-emerald-300' : 'bg-zinc-800 border-transparent text-zinc-400'}`}>🙈 Hide excluded</button>
            </div>
          </div>

          <div className="px-3 sm:px-5 mt-3 space-y-2">
            {TOPS.map(top => {
              const all = data.filter(top.match);
              if (all.length === 0) return null;
              if ((q || hideExc) && !all.some(gVisible)) return null;
              const inc = all.filter(p => p.included).length;
              const topOpen = q ? true : openTop.has(top.key);

              const groupsMap: Record<string, PL[]> = {};
              all.forEach(p => {
                const k = top.by === 'season' ? (p.season || 'No season') : (p.family || 'uncategorized');
                if (!groupsMap[k]) groupsMap[k] = [];
                groupsMap[k].push(p);
              });
              const subKeys = Object.keys(groupsMap).sort((a, b) =>
                top.by === 'season' ? seasonOrder(a) - seasonOrder(b) : FAM_ORDER.indexOf(a) - FAM_ORDER.indexOf(b));

              return (
                <div key={top.key} className="rounded-lg border border-zinc-800 bg-[#181818] overflow-hidden">
                  <div onClick={() => toggle(openTop, top.key, setOpenTop)}
                    className="flex items-center gap-2 px-3 py-2.5 cursor-pointer hover:bg-zinc-800/60">
                    <span className="text-zinc-500 w-3">{topOpen ? '▾' : '▸'}</span>
                    <span className="font-medium">{top.label}</span>
                    <span className="text-xs text-zinc-500">{all.length}</span>
                    <span className={`ms-auto text-xs px-2 py-0.5 rounded ${inc > 0 ? 'bg-emerald-900/50 text-emerald-300' : 'bg-zinc-800 text-zinc-500'}`}>{inc} in</span>
                    <button onClick={e => { e.stopPropagation(); setBulk(all, true); }} className="text-xs px-2 py-0.5 rounded bg-zinc-800 hover:bg-emerald-800">Include all</button>
                    <button onClick={e => { e.stopPropagation(); setBulk(all, false); }} className="text-xs px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700">Exclude all</button>
                  </div>

                  {topOpen && subKeys.map(sk => {
                    const subKey = `${top.key}|${sk}`;
                    const full = groupsMap[sk];
                    const scoped = subQ[subKey] || '';
                    let leaves = full.filter(gVisible);
                    if (scoped) leaves = leaves.filter(p => p.name.toLowerCase().includes(scoped.toLowerCase()));
                    if ((q || hideExc || scoped) && leaves.length === 0) return null;
                    leaves = [...leaves].sort((a, b) =>
                      (top.by === 'season' && top.key !== 'collections')
                        ? ((a.week_number || 0) - (b.week_number || 0)) * dir
                        : b.track_count - a.track_count);
                    const subInc = full.filter(p => p.included).length;
                    const subOpen = q || scoped ? true : openSub.has(subKey);
                    const danger = top.by === 'family' && DANGER_FAM.has(sk);
                    const label = top.by === 'season' ? sk : (FAM_LABEL[sk] || sk);

                    return (
                      <div key={subKey} className="border-t border-zinc-800">
                        <div onClick={() => toggle(openSub, subKey, setOpenSub)}
                          className="flex items-center gap-2 pl-8 pr-3 py-2 cursor-pointer hover:bg-zinc-800/40">
                          <span className="text-zinc-600 text-xs w-3">{subOpen ? '▾' : '▸'}</span>
                          <span className={`text-sm ${danger ? 'text-red-300' : ''}`}>{label}</span>
                          <span className="text-xs text-zinc-500">{full.length}</span>
                          {danger && <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-900/50 text-red-300">exclude</span>}
                          <span className={`ms-auto text-xs px-2 py-0.5 rounded ${subInc > 0 ? 'bg-emerald-900/40 text-emerald-300' : 'text-zinc-600'}`}>{subInc} in</span>
                          <button onClick={e => { e.stopPropagation(); setBulk(full, true); }} className="text-xs px-2 py-0.5 rounded bg-zinc-800 hover:bg-emerald-800">In</button>
                          <button onClick={e => { e.stopPropagation(); setBulk(full, false); }} className="text-xs px-2 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700">Out</button>
                        </div>

                        {subOpen && (
                          <>
                            <div className="pl-14 pr-3 py-1.5">
                              <input value={scoped} onChange={e => setSubQ(s => ({ ...s, [subKey]: e.target.value }))}
                                placeholder={`Search in ${label}…`} className="w-56 px-2 py-1 text-xs bg-zinc-800 rounded outline-none" />
                            </div>
                            {leaves.map(p => (
                              <div key={p.playlist_uri} className="flex items-center gap-2.5 pl-14 pr-3 py-1.5 border-t border-zinc-900 hover:bg-zinc-800/30">
                                <input type="checkbox" checked={p.included} onChange={e => setInc(p, e.target.checked)} className="w-4 h-4 accent-emerald-500" />
                                {imgs && (p.image
                                  ? <img src={p.image} alt="" className="w-8 h-8 rounded object-cover" />
                                  : <div className="w-8 h-8 rounded bg-zinc-700" />)}
                                <span className="flex-1 min-w-0 truncate text-sm">
                                  {p.name}
                                  {p.legacy && <span className="ms-1 text-[10px] px-1 rounded bg-amber-900/50 text-amber-300">legacy</span>}
                                </span>
                                {p.public === true && <span title="Public — shown on your profile" className="text-[11px] leading-none">🌐</span>}
                                {p.public === false && <span title="Private" className="text-[11px] leading-none opacity-40">🔒</span>}
                                <span className={`text-[10px] px-1.5 py-0.5 rounded ${TYPE_CLS[p.type]}`}>
                                  {p.week_number != null ? `#${p.week_number}` : TYPE_LABEL[p.type]}
                                </span>
                                <span className="text-[11px] text-zinc-600 w-16 text-right">{p.track_count.toLocaleString()} tr</span>
                                <a href={p.spotify_url} target="_blank" rel="noreferrer" onClick={e => e.stopPropagation()} className="text-emerald-500 text-xs">↗</a>
                              </div>
                            ))}
                          </>
                        )}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
};
