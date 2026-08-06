import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

interface Playlist {
  playlist_uri: string;
  playlist_id: string;
  name: string;
  owner_id: string;
  owner_name: string;
  type: 'weekly' | 'outofplaylist' | 'other';
  week_number: number | null;
  track_count: number;
  image: string | null;
  spotify_url: string;
  included: boolean;
  auto_included: boolean;
}
interface ReconResp {
  generated: string;
  owner_id: string;
  total: number;
  counts: { weekly: number; outofplaylist: number; other: number };
  followed_count: number;
  included_count: number;
  playlists: Playlist[];
}

type TypeFilter = 'all' | 'weekly' | 'outofplaylist' | 'other';

const typeLabel: Record<string, string> = {
  weekly: 'Weekly', outofplaylist: 'Outofplaylist', other: 'Other',
};
const typeColor: Record<string, string> = {
  weekly: 'bg-emerald-700', outofplaylist: 'bg-sky-700', other: 'bg-zinc-600',
};

export const Recon: React.FC = () => {
  const [data, setData] = useState<ReconResp | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all');
  const [search, setSearch] = useState('');
  const [scanning, setScanning] = useState(false);
  const [scanMsg, setScanMsg] = useState('');

  const load = async () => {
    try {
      const r = await axios.get('/api/recon/playlists');
      setData(r.data); setErr('');
    } catch (e: any) {
      if (e.response?.status === 404) setData(null);   // no snapshot yet — show empty state
      else setErr(e.response?.data?.detail || e.message);
    }
  };
  useEffect(() => { load().finally(() => setLoading(false)); }, []);

  const rescan = async () => {
    setScanning(true); setScanMsg(''); setErr('');
    try {
      const r = await axios.post('/api/recon/scan');
      setScanMsg(`Scanned ${r.data.total} owned playlists · ${r.data.counts.weekly} weekly, ${r.data.counts.outofplaylist} outofplaylist, ${r.data.counts.other} other.`);
      await load();
    } catch (e: any) {
      setErr(e.response?.data?.detail || e.message);
    } finally { setScanning(false); }
  };

  const setInclude = async (pl: Playlist, included: boolean) => {
    if (pl.auto_included) return;
    // optimistic
    setData(prev => prev && ({
      ...prev,
      included_count: prev.included_count + (included ? 1 : -1),
      playlists: prev.playlists.map(p => p.playlist_uri === pl.playlist_uri ? { ...p, included } : p),
    }));
    try {
      await axios.post('/api/recon/include', { playlist_uri: pl.playlist_uri, included });
    } catch {
      // revert
      setData(prev => prev && ({
        ...prev,
        included_count: prev.included_count + (included ? -1 : 1),
        playlists: prev.playlists.map(p => p.playlist_uri === pl.playlist_uri ? { ...p, included: !included } : p),
      }));
    }
  };

  const shown = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    return data.playlists.filter(p =>
      (typeFilter === 'all' || p.type === typeFilter) &&
      (!q || (p.name || '').toLowerCase().includes(q))
    );
  }, [data, typeFilter, search]);

  if (loading) return <div className="min-h-screen bg-[#121212] text-zinc-300 flex items-center justify-center">Loading…</div>;
  if (err) return (
    <div className="min-h-screen bg-[#121212] text-red-400 flex flex-col items-center justify-center gap-4 p-6">
      <div>Error: {err}</div>
      <Link to="/dashboard" className="text-zinc-400 hover:text-white text-sm">← Dashboard</Link>
    </div>
  );

  const counts = data?.counts;
  const filters: { key: TypeFilter; label: string; n: number }[] = [
    { key: 'all', label: 'All', n: data?.total || 0 },
    { key: 'weekly', label: 'Weekly', n: counts?.weekly || 0 },
    { key: 'outofplaylist', label: 'Outofplaylist', n: counts?.outofplaylist || 0 },
    { key: 'other', label: 'Other', n: counts?.other || 0 },
  ];

  return (
    <div className="min-h-screen bg-[#121212] text-zinc-200 pb-16">
      {/* header */}
      <div className="sticky top-0 z-20 bg-[#181818] border-b border-zinc-800 px-5 py-3 flex flex-wrap items-center gap-3">
        <Link to="/dashboard" className="text-zinc-400 hover:text-white text-sm">← Dashboard</Link>
        <h1 className="text-lg font-bold">📋 Playlist Recon</h1>
        {data && (
          <span className="text-xs text-zinc-500">
            {data.total} owned · <b className="text-emerald-400">{data.included_count}</b> feeding the engine
            {data.followed_count ? <> · {data.followed_count} followed (not owned, excluded)</> : null}
            {data.generated ? <> · scanned {data.generated}</> : null}
          </span>
        )}
        <button onClick={rescan} disabled={scanning}
          className="ms-auto px-3 py-1.5 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 font-semibold">
          {scanning ? 'Scanning…' : data ? '↻ Rescan from Spotify' : 'Scan from Spotify'}
        </button>
      </div>

      {scanMsg && <div className="mx-5 mt-3 p-3 rounded bg-zinc-800 text-sm">✅ {scanMsg}</div>}

      {/* empty state */}
      {!data && (
        <div className="flex flex-col items-center justify-center text-center gap-3 py-24 px-6">
          <div className="text-4xl">📋</div>
          <h2 className="text-lg font-semibold">No snapshot yet</h2>
          <p className="text-sm text-zinc-500 max-w-md">
            Recon reads all the playlists you own from Spotify, sorts them into Weekly / Outofplaylist / Other,
            and lets you pick which “Other” ones feed the engine. Nothing is changed on Spotify.
          </p>
          <button onClick={rescan} disabled={scanning}
            className="mt-2 px-4 py-2 text-sm rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 font-semibold">
            {scanning ? 'Scanning…' : 'Scan from Spotify'}
          </button>
        </div>
      )}

      {data && (
        <>
          {/* toolbar */}
          <div className="px-5 py-3 flex flex-wrap items-center gap-2 border-b border-zinc-800">
            {filters.map(f => (
              <button key={f.key} onClick={() => setTypeFilter(f.key)}
                className={`px-2.5 py-1 text-xs rounded ${typeFilter === f.key ? 'bg-white text-black' : 'bg-zinc-800'}`}>
                {f.label} <span className="opacity-60">({f.n})</span>
              </button>
            ))}
            <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search name…"
              className="ms-2 px-3 py-1 text-sm bg-zinc-800 rounded outline-none w-52" />
          </div>

          {/* list */}
          <div className="px-3 sm:px-5 mt-3">
            {shown.map(p => (
              <div key={p.playlist_uri}
                className="flex items-center gap-3 p-2 rounded border border-transparent hover:bg-zinc-800/60">
                {p.image
                  ? <img src={p.image} alt="" className="w-10 h-10 rounded object-cover" />
                  : <div className="w-10 h-10 rounded bg-zinc-700" />}
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{p.name || '(untitled)'}</div>
                  <div className="text-xs text-zinc-500 truncate">
                    {p.week_number != null ? `Week #${p.week_number} · ` : ''}{p.track_count} tracks
                  </div>
                </div>
                <span className={`text-xs text-white px-2 py-0.5 rounded ${typeColor[p.type]}`}>{typeLabel[p.type]}</span>

                {/* inclusion control */}
                {p.auto_included ? (
                  <span className="text-xs text-emerald-400 w-32 text-center" title="Weekly & Outofplaylist always feed the engine">
                    🔒 auto-included
                  </span>
                ) : (
                  <label className="flex items-center gap-2 w-32 justify-center cursor-pointer text-xs select-none"
                    title="Include this playlist in the engine's scoring">
                    <input type="checkbox" checked={p.included}
                      onChange={e => setInclude(p, e.target.checked)}
                      className="w-4 h-4 accent-emerald-500" />
                    <span className={p.included ? 'text-emerald-400' : 'text-zinc-500'}>
                      {p.included ? 'included' : 'include'}
                    </span>
                  </label>
                )}

                <a href={p.spotify_url} target="_blank" rel="noreferrer"
                  className="text-xs text-emerald-400 hover:underline w-16 text-center">Spotify ↗</a>
              </div>
            ))}
            {shown.length === 0 && <div className="text-center text-zinc-500 py-10">No matches for this filter.</div>}
          </div>
        </>
      )}
    </div>
  );
};
