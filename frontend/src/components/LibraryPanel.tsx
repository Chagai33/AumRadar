import React, { useEffect, useState } from 'react';
import axios from 'axios';

// The display-only "what do I actually have of this artist?" layer, shared by
// /cleanup and /health. It answers the question the RANK score cannot: how many of
// this artist's songs I marked with a LIKE, and which of my playlists their songs
// sit in — the two things worth knowing BEFORE unfollowing them.
//
// Nothing here feeds the score. The data comes from cache/library_*.json, built by
// the Library Job; when that index doesn't exist yet every badge is simply blank
// and the panel says so, so the pages behave exactly as before.

export interface LibCount { liked: number; playlists: number; songs: number; }
export interface LibCounts { [artistUri: string]: LibCount; }

export interface LibraryState {
  ready: boolean;
  generated?: string;
  counts: LibCounts;
  stats?: { liked_songs?: number; songs?: number; artists?: number; playlists_scanned?: number };
}

/** Fetch the small counts map once per page. A missing index is not an error —
 *  it just means the badges stay blank until the first build. */
export const useLibraryCounts = () => {
  const [lib, setLib] = useState<LibraryState>({ ready: false, counts: {} });
  const reload = async () => {
    try {
      const r = await axios.get('/api/library/counts');
      setLib(r.data);
    } catch { setLib({ ready: false, counts: {} }); }
  };
  useEffect(() => { reload(); }, []);
  return { lib, reloadLib: reload };
};

export const fmtGenerated = (g?: string) => {
  if (!g || g.length < 13) return '—';
  return `${g.slice(0, 4)}-${g.slice(4, 6)}-${g.slice(6, 8)} ${g.slice(9, 11)}:${g.slice(11, 13)}`;
};

/** The per-row cell: ❤️ liked · 🎵 distinct playlists. Zeroes are dimmed rather than
 *  hidden — "I checked and there is nothing" is exactly the signal that makes an
 *  unfollow safe, so it must be visibly different from "not indexed yet" (blank). */
export const LibraryBadge: React.FC<{ c?: LibCount; ready: boolean }> = ({ c, ready }) => {
  if (!ready) return <span className="w-20 text-center text-[11px] text-zinc-700">—</span>;
  const liked = c?.liked || 0, pl = c?.playlists || 0;
  return (
    <span className="w-20 text-center text-xs whitespace-nowrap"
      title={`${liked} liked song${liked === 1 ? '' : 's'} · in ${pl} playlist${pl === 1 ? '' : 's'}`}>
      <b className={liked ? 'text-pink-400' : 'text-zinc-600'}>♥{liked}</b>
      <span className="text-zinc-700 mx-1">·</span>
      <b className={pl ? 'text-sky-400' : 'text-zinc-600'}>♪{pl}</b>
    </span>
  );
};

export interface PanelArtist { uri: string; name?: string; image?: string; genres?: string; }

interface Detail {
  ready: boolean;
  generated?: string;
  liked: number; playlists: number; songs: number;
  rows: {
    track_id: string; name: string; liked: boolean; spotify_url?: string;
    playlists: { playlist_uri: string; name?: string; type?: string; week_number?: number; spotify_url?: string }[];
  }[];
}

/** The review panel. Given a LIST of artists it walks them one at a time (Prev/Next)
 *  — that is the "mark a batch, then check each one before removing" flow, which is
 *  the whole point of the feature. Given a single artist it is just a detail view. */
export const LibraryPanel: React.FC<{
  artists: PanelArtist[];
  startIndex?: number;
  onClose: () => void;
  selected?: Set<string>;
  onToggleSelect?: (uri: string) => void;
}> = ({ artists, startIndex = 0, onClose, selected, onToggleSelect }) => {
  const [i, setI] = useState(Math.min(startIndex, Math.max(0, artists.length - 1)));
  const [d, setD] = useState<Detail | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState('');
  const [onlyLiked, setOnlyLiked] = useState(false);
  const cur = artists[i];

  useEffect(() => {
    if (!cur) return;
    let cancelled = false;
    setBusy(true); setErr(''); setD(null);
    axios.get(`/api/library/artist/${encodeURIComponent(cur.uri)}`)
      .then(r => { if (!cancelled) setD(r.data); })
      .catch(e => { if (!cancelled) setErr(e.response?.data?.detail || e.message); })
      .finally(() => { if (!cancelled) setBusy(false); });
    return () => { cancelled = true; };
  }, [cur?.uri]);

  // Arrow keys walk the batch, Esc closes — reviewing 20 artists with the mouse is
  // the kind of thing that makes people stop reviewing.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      if (e.key === 'ArrowRight' && i < artists.length - 1) setI(i + 1);
      if (e.key === 'ArrowLeft' && i > 0) setI(i - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [i, artists.length, onClose]);

  if (!cur) return null;
  const isSel = selected?.has(cur.uri);
  const rows = (d?.rows || []).filter(r => !onlyLiked || r.liked);

  return (
    <div className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-[#1a1a1a] rounded-lg w-full max-w-3xl max-h-[88vh] flex flex-col overflow-hidden"
        onClick={e => e.stopPropagation()}>

        {/* header */}
        <div className="flex items-center gap-3 p-5 border-b border-zinc-800">
          {cur.image
            ? <img src={cur.image} className="w-14 h-14 rounded-full object-cover" alt="" />
            : <div className="w-14 h-14 rounded-full bg-zinc-700" />}
          <div className="min-w-0 flex-1">
            <div className="text-lg font-bold truncate">{cur.name || cur.uri}</div>
            <div className="text-xs text-zinc-500 truncate">{cur.genres || '—'}</div>
          </div>
          {d?.ready && (
            <div className="text-right shrink-0">
              <div className="text-sm">
                <span className="text-pink-400 font-bold">♥ {d.liked}</span>
                <span className="text-zinc-600 mx-2">·</span>
                <span className="text-sky-400 font-bold">♪ {d.playlists}</span>
              </div>
              <div className="text-[11px] text-zinc-500 mt-0.5">{d.songs} song{d.songs === 1 ? '' : 's'} in your library</div>
            </div>
          )}
          <button onClick={onClose} className="text-zinc-500 hover:text-white text-lg leading-none px-1">✕</button>
        </div>

        {/* batch navigation + keep/remove toggle */}
        <div className="flex items-center gap-2 px-5 py-2 border-b border-zinc-800 bg-[#151515]">
          {artists.length > 1 && (
            <>
              <button onClick={() => setI(i - 1)} disabled={i === 0}
                className="px-2.5 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40">← Prev</button>
              <span className="text-xs text-zinc-500">{i + 1} / {artists.length}</span>
              <button onClick={() => setI(i + 1)} disabled={i >= artists.length - 1}
                className="px-2.5 py-1 text-xs rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40">Next →</button>
            </>
          )}
          {onToggleSelect && (
            <button onClick={() => onToggleSelect(cur.uri)}
              className={`ms-auto px-3 py-1 text-xs rounded font-medium ${isSel
                ? 'bg-red-700 hover:bg-red-600 text-white'
                : 'bg-zinc-800 hover:bg-zinc-700 text-zinc-300'}`}>
              {isSel ? '✓ Marked for removal — click to keep' : 'Mark for removal'}
            </button>
          )}
          {d && d.songs > 0 && (
            <label className={`${onToggleSelect ? '' : 'ms-auto'} flex items-center gap-1.5 text-xs text-zinc-400 cursor-pointer`}>
              <input type="checkbox" checked={onlyLiked} onChange={e => setOnlyLiked(e.target.checked)}
                className="w-3.5 h-3.5 accent-pink-500" />
              liked only
            </label>
          )}
        </div>

        {/* body */}
        <div className="p-5 overflow-y-auto">
          {busy && <div className="text-sm text-zinc-500 py-6 text-center">Loading…</div>}
          {err && <div className="text-sm text-red-400 py-6 text-center">Error: {err}</div>}

          {!busy && !err && d && !d.ready && (
            <div className="text-sm text-zinc-400 py-6 text-center">
              The library index hasn’t been built yet.<br />
              <span className="text-zinc-500">Use “Refresh library data” to scan your Liked Songs and playlists once.</span>
            </div>
          )}

          {!busy && !err && d?.ready && d.songs === 0 && (
            <div className="text-sm text-zinc-400 py-6 text-center">
              Nothing of theirs in your library — <b>no liked songs</b> and <b>no playlist appearances</b>.
              <div className="text-zinc-600 text-xs mt-1">(Out Of Playlist playlists are not scanned.)</div>
            </div>
          )}

          {!busy && !err && d?.ready && d.songs > 0 && (
            <div className="space-y-1">
              {rows.map(r => (
                <div key={r.track_id || r.name} className="flex items-start gap-2 py-1.5 border-b border-zinc-800/50">
                  <span className={`text-sm w-5 text-center shrink-0 ${r.liked ? 'text-pink-400' : 'text-zinc-700'}`}
                    title={r.liked ? 'Liked' : 'Not liked'}>♥</span>
                  <div className="min-w-0 flex-1">
                    {r.spotify_url
                      ? <a href={r.spotify_url} target="_blank" rel="noreferrer"
                          className="text-sm hover:text-emerald-400 hover:underline">{r.name || '—'}</a>
                      : <span className="text-sm">{r.name || '—'}</span>}
                    <div className="flex flex-wrap gap-1 mt-0.5">
                      {r.playlists.length === 0 && <span className="text-[11px] text-zinc-600">not in any scanned playlist</span>}
                      {r.playlists.map(p => (
                        <a key={p.playlist_uri} href={p.spotify_url} target="_blank" rel="noreferrer"
                          className={`text-[11px] px-1.5 py-0.5 rounded hover:brightness-125 ${
                            p.type === 'weekly' ? 'bg-emerald-900 text-emerald-300' : 'bg-indigo-900 text-indigo-300'}`}>
                          {p.name || p.playlist_uri}
                        </a>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
              {rows.length === 0 && (
                <div className="text-sm text-zinc-500 py-6 text-center">No liked songs by this artist.</div>
              )}
            </div>
          )}
        </div>

        {d?.generated && (
          <div className="px-5 py-2 border-t border-zinc-800 text-[11px] text-zinc-600">
            Library index from {fmtGenerated(d.generated)} · Liked Songs + all playlists except Out Of Playlist
          </div>
        )}
      </div>
    </div>
  );
};
