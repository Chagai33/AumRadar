import React, { useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { fmtGenerated } from './LibraryPanel';

// Drives the Library Job: one pull of Liked Songs + every playlist except Out Of
// Playlist, which builds the index every ♥/♪ badge and detail panel reads.
//
// It is deliberately NOT a per-artist button. Spotify has no artist-scoped view of
// your library — /me/tracks returns everything with no filter, and there is no
// "which playlists contain artist X" endpoint at all — so fetching for one artist
// costs exactly what fetching for all of them costs. One run therefore answers for
// every artist at once, and the per-artist lookups afterwards are instant.

interface Status {
  is_running: boolean;
  status: string;    // starting|loading|liked|pulling|building|rate_limited|done|stopped|interrupted|error|idle
  phase?: string;
  pulled?: number; total?: number; songs?: number; liked_seen?: number;
  current?: string; retry_after?: number; error?: string;
  index_ready?: boolean; resumable?: boolean; generated?: string;
  scan_scope?: number; recon_ready?: boolean;
  stats?: { liked_songs?: number; songs?: number; artists?: number; playlists_scanned?: number };
}

const RUNNING = new Set(['starting', 'loading', 'liked', 'pulling', 'building', 'rate_limited']);

export const LibraryControl: React.FC<{ onDone?: () => void }> = ({ onDone }) => {
  const [st, setSt] = useState<Status | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const pollRef = useRef<number | null>(null);
  const wasRunning = useRef(false);

  const load = async () => {
    try {
      const r = await axios.get('/api/library/status');
      setSt(r.data); setErr('');
      // Refresh the badges the moment a run finishes, so the list updates without
      // the user having to reload the page.
      const live = r.data.is_running || RUNNING.has(r.data.status);
      if (wasRunning.current && !live) onDone?.();
      wasRunning.current = live;
      return r.data as Status;
    } catch (e: any) { setErr(e.response?.data?.detail || e.message); return null; }
  };

  useEffect(() => { load(); }, []);
  useEffect(() => {
    const live = st?.is_running || (st && RUNNING.has(st.status));
    if (!live) { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } return; }
    if (pollRef.current) return;
    pollRef.current = window.setInterval(load, 2000);
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [st?.is_running, st?.status]);

  const act = async (url: string) => {
    setBusy(true); setErr('');
    try { await axios.post(url); await load(); }
    catch (e: any) { setErr(e.response?.data?.detail || e.message); }
    finally { setBusy(false); }
  };

  const live = !!(st?.is_running || (st && RUNNING.has(st.status)));
  const pulled = st?.pulled || 0, total = st?.total || 0;
  const pct = total ? Math.round((pulled / total) * 100) : 0;

  return (
    <div className="flex flex-col gap-1.5 w-full">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-zinc-300 flex items-center gap-1.5 bg-zinc-800 rounded-full ps-3 pe-3 py-1"
          title="Liked songs + playlist appearances, per artist. Built by one pull; Out Of Playlist playlists are not scanned.">
          📚 <span className="text-zinc-500">library</span>
          {st?.index_ready
            ? <><b className="text-white">{(st.stats?.liked_songs ?? 0).toLocaleString()}</b>
                <span className="text-zinc-500">liked ·</span>
                <span className="text-zinc-500">{fmtGenerated(st.generated)}</span></>
            : <span className="text-amber-400">not built yet</span>}
        </span>

        {!live && (
          <button onClick={() => act('/api/library/start')} disabled={busy || !st?.recon_ready}
            title={st?.recon_ready
              ? `Scan Liked Songs + ${st?.scan_scope || 0} playlists (all types except Out Of Playlist). Takes a few minutes; runs server-side.`
              : 'Run Playlist Recon first'}
            className="px-3 py-1.5 text-sm rounded bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50">
            {busy ? 'Starting…' : st?.index_ready ? '🔄 Refresh library data' : '📚 Build library data'}
          </button>
        )}
        {!live && st?.resumable && (
          <button onClick={() => act('/api/library/resume')} disabled={busy}
            className="px-3 py-1.5 text-sm rounded bg-amber-800 hover:bg-amber-700 disabled:opacity-50">
            ▶ Resume
          </button>
        )}
        {live && (
          <button onClick={() => act('/api/library/stop')} disabled={busy}
            className="px-3 py-1.5 text-sm rounded bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50">
            ⏹ Stop
          </button>
        )}
        {err && <span className="text-xs text-red-400">{err}</span>}
        {st?.status === 'error' && st.error && <span className="text-xs text-red-400">{st.error}</span>}
      </div>

      {live && (
        <div className="w-full">
          <div className="flex items-center justify-between text-xs text-zinc-400 mb-1">
            <span>
              {st?.status === 'rate_limited'
                ? <>⏳ Spotify rate limit — waiting <b>{st.retry_after}s</b>, then continuing…</>
                : st?.status === 'liked'
                  ? <>Scanning Liked Songs — <b className="text-pink-400">{(st.liked_seen || 0).toLocaleString()}</b> so far</>
                  : st?.status === 'building'
                    ? <>Building the index from <b>{(st.songs || 0).toLocaleString()}</b> songs…</>
                    : st?.status === 'starting'
                      ? <>Starting… (the job takes ~1 min to boot)</>
                      : <>Playlists <b className="text-emerald-400">{pulled}</b> / {total} · <span className="truncate">{st?.current}</span></>}
            </span>
            <span className="text-zinc-600">{(st?.songs || 0).toLocaleString()} songs</span>
          </div>
          <div className="h-1.5 rounded bg-zinc-800 overflow-hidden">
            <div className={`h-full transition-all duration-300 ${st?.status === 'liked' ? 'bg-pink-500 animate-pulse w-full' : 'bg-emerald-500'}`}
              style={st?.status === 'liked' ? undefined : { width: `${pct}%` }} />
          </div>
        </div>
      )}
    </div>
  );
};
