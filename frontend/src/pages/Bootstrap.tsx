import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import axios from 'axios';

// Artist Health Engine — Stage 2. Drives the Bootstrap Cloud Run Job: pull tracks
// for every included playlist, score each followed artist by decay-weighted RANK,
// and hand the RANK=0 artists to /cleanup. This page only starts/stops/polls the
// Job (all fast requests) — the heavy work runs server-side, survives a closed tab.

interface Summary { followed: number; with_rank: number; candidates: number; }
interface Status {
  is_running: boolean;
  status: string;            // starting|loading|pulling|scoring|rate_limited|done|stopped|interrupted|error|idle
  phase?: string;
  pulled?: number;
  total?: number;
  songs?: number;
  current?: string;
  current_week?: number;
  followed?: number;
  retry_after?: number;
  summary?: Summary;
  error?: string;
  logs?: string[];
  candidates_ready?: boolean;
  resumable?: boolean;
  included_count?: number;
  recon_ready?: boolean;
}

const RUNNING = new Set(['starting', 'loading', 'pulling', 'scoring', 'rate_limited']);

export const Bootstrap: React.FC = () => {
  const [st, setSt] = useState<Status | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  const load = async () => {
    try {
      const r = await axios.get('/api/bootstrap/status');
      setSt(r.data); setErr('');
      return r.data as Status;
    } catch (e: any) { setErr(e.response?.data?.detail || e.message); return null; }
  };

  // Initial load + poll only while a run is live.
  useEffect(() => { load(); }, []);
  useEffect(() => {
    const live = st?.is_running || (st && RUNNING.has(st.status));
    if (!live) { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } return; }
    if (pollRef.current) return;
    pollRef.current = window.setInterval(load, 2000);
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [st?.is_running, st?.status]);

  const act = async (url: string, optimistic: string) => {
    setBusy(true); setErr('');
    try {
      await axios.post(url);
      setSt(prev => ({ ...(prev || { status: 'starting' }), is_running: true, status: optimistic } as Status));
    } catch (e: any) { setErr(e.response?.data?.detail || e.message); }
    finally { setBusy(false); }
  };

  const start = () => act('/api/bootstrap/start', 'starting');
  const resume = () => act('/api/bootstrap/resume', 'starting');
  const stop = async () => {
    setSt(prev => (prev ? { ...prev, status: 'stopping' } : prev));
    try { await axios.post('/api/bootstrap/stop'); } catch { /* poll reflects reality */ }
  };

  const live = !!(st?.is_running || (st && RUNNING.has(st.status)));
  const pulled = st?.pulled || 0, total = st?.total || 0;
  const pct = total > 0 ? Math.round((pulled / total) * 100) : (st?.status === 'scoring' ? 100 : 0);

  const phaseLabel = () => {
    switch (st?.status) {
      case 'starting': return 'Starting the job…';
      case 'loading': return 'Loading playlists & followed artists…';
      case 'pulling': return 'Pulling playlist tracks…';
      case 'scoring': return 'Scoring artists…';
      case 'rate_limited': return `Spotify rate limit — waiting ${st?.retry_after ?? ''}s, then continuing…`;
      case 'stopping': return 'Stopping…';
      default: return 'Working…';
    }
  };

  return (
    <div className="min-h-screen bg-[#121212] text-zinc-200 pb-16">
      {/* header */}
      <div className="sticky top-0 z-20 bg-[#181818] border-b border-zinc-800 px-5 py-3 flex flex-wrap items-center gap-3">
        <Link to="/dashboard" className="text-zinc-400 hover:text-white text-sm">← Dashboard</Link>
        <h1 className="text-lg font-bold">⚖️ Artist Bootstrap</h1>
        {st && (
          <span className="text-xs text-zinc-500">
            <b className="text-emerald-400">{st.included_count ?? 0}</b> playlists feeding the engine
            {st.followed ? ` · ${st.followed} followed` : ''}
            {typeof st.current_week === 'number' && st.current_week > 0 ? ` · week ${st.current_week}` : ''}
          </span>
        )}
        <div className="ms-auto flex items-center gap-2">
          <Link to="/recon" className="text-xs text-zinc-400 hover:text-white">📋 Recon</Link>
          <Link to="/health" className="text-xs text-zinc-400 hover:text-white">🎛 Health</Link>
          <Link to="/cleanup" className="text-xs text-zinc-400 hover:text-white">🧹 Cleanup</Link>
        </div>
      </div>

      {err && <div className="mx-5 mt-3 p-3 rounded bg-red-900/40 text-red-300 text-sm">{err}</div>}

      <div className="max-w-2xl mx-auto px-5 mt-4 space-y-4">
        {/* explainer */}
        <p className="text-sm text-zinc-400">
          Bootstrap reads the tracks of every playlist you <b>included</b> in Recon, then scores each artist you follow by
          how much of their music lives in your curation (decay-weighted, half-life 26 weeks). An artist whose songs
          <b> never</b> entered any included playlist scores <b>RANK&nbsp;0</b> — a cleanup candidate. Nothing is unfollowed
          here; it only prepares the list for <Link to="/cleanup" className="text-emerald-400 hover:underline">Cleanup</Link>.
        </p>

        {/* needs Recon first */}
        {st && st.recon_ready === false && (
          <div className="p-4 rounded-lg border border-amber-500/40 bg-amber-900/15 text-sm">
            <div className="font-semibold text-amber-300 mb-1">Run Recon first</div>
            <p className="text-amber-100/80">Bootstrap needs a playlist snapshot. Go to{' '}
              <Link to="/recon" className="text-emerald-400 hover:underline">Playlist Recon</Link> and scan, then come back.</p>
          </div>
        )}

        {/* live progress */}
        {live && (
          <div className="p-4 rounded-lg border border-emerald-700/40 bg-[#181818]">
            <div className="flex items-center justify-between mb-2">
              <span className="font-medium text-sm">{phaseLabel()}</span>
              <button onClick={stop} disabled={st?.status === 'stopping'}
                className="px-3 py-1 text-xs rounded bg-zinc-700 hover:bg-red-900/60 disabled:opacity-50">Stop</button>
            </div>
            <div className="h-2 rounded bg-zinc-700 overflow-hidden">
              <div className={`h-full transition-all duration-500 ${st?.status === 'rate_limited' ? 'bg-orange-500' : 'bg-emerald-500'}`}
                style={{ width: `${pct}%` }} />
            </div>
            <div className="flex justify-between text-xs text-zinc-500 mt-2">
              <span>{total > 0 ? `${pulled} / ${total} playlists` : 'preparing…'}</span>
              {typeof st?.songs === 'number' && st.songs > 0 && <span className="text-emerald-400">{st.songs.toLocaleString()} songs</span>}
              <span>{pct}%</span>
            </div>
            {st?.current && st.status === 'pulling' && (
              <div className="text-xs text-zinc-500 mt-1 truncate">▸ {st.current}</div>
            )}
          </div>
        )}

        {/* done summary */}
        {!live && st?.status === 'done' && st.summary && (
          <div className="p-4 rounded-lg border border-emerald-600/40 bg-emerald-900/15">
            <div className="font-bold text-emerald-300 mb-2">✅ Bootstrap complete</div>
            <div className="grid grid-cols-3 gap-3 text-center mb-3">
              <Stat n={st.summary.candidates} label="candidates (RANK 0)" accent="text-red-300" />
              <Stat n={st.summary.with_rank} label="kept (RANK > 0)" accent="text-emerald-300" />
              <Stat n={st.summary.followed} label="followed total" accent="text-zinc-200" />
            </div>
            <div className="flex gap-2">
              <Link to="/cleanup" className="px-4 py-2 text-sm rounded bg-emerald-700 hover:bg-emerald-600 font-semibold">
                Review {st.summary.candidates} candidates in Cleanup →
              </Link>
              <button onClick={start} disabled={busy} className="px-4 py-2 text-sm rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50">
                Re-run
              </button>
            </div>
          </div>
        )}

        {/* resumable banner */}
        {!live && st?.resumable && st.status !== 'done' && (
          <div className="p-4 rounded-lg border border-amber-500/40 bg-amber-900/15">
            <div className="font-semibold text-amber-300 mb-1">Bootstrap paused — you can resume</div>
            <p className="text-amber-100/80 text-sm mb-3">
              Stopped at <b>{st.pulled ?? 0}/{st.total ?? 0}</b> playlists. Resume continues from the exact point — no re-pulling, no double-counting.
            </p>
            <div className="flex gap-2">
              <button onClick={resume} disabled={busy}
                className="px-4 py-2 text-sm rounded bg-emerald-700 hover:bg-emerald-600 font-semibold disabled:opacity-50">Resume</button>
              <button onClick={start} disabled={busy}
                className="px-4 py-2 text-sm rounded bg-zinc-800 hover:bg-zinc-700 disabled:opacity-50">Start over</button>
            </div>
          </div>
        )}

        {/* error */}
        {!live && st?.status === 'error' && (
          <div className="p-4 rounded-lg border border-red-500/40 bg-red-900/15 text-sm">
            <div className="font-semibold text-red-400 mb-1">Bootstrap failed</div>
            <p className="text-red-200/80">{st.error}</p>
          </div>
        )}

        {/* idle start button */}
        {!live && st && st.recon_ready !== false && !['done', 'error'].includes(st.status) && !st.resumable && (
          <button onClick={start} disabled={busy || (st.included_count ?? 0) === 0}
            className="w-full py-3 rounded-lg bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50 font-semibold">
            {busy ? 'Starting…' : st.candidates_ready ? '↻ Re-run bootstrap' : `Build scores from ${st.included_count ?? 0} playlists`}
          </button>
        )}

        {/* logs (small, collapsible-ish) */}
        {st?.logs && st.logs.length > 0 && (
          <details className="text-xs text-zinc-500">
            <summary className="cursor-pointer hover:text-zinc-300">Log</summary>
            <pre className="mt-2 p-2 bg-black/40 rounded overflow-x-auto whitespace-pre-wrap">{st.logs.join('\n')}</pre>
          </details>
        )}
      </div>
    </div>
  );
};

const Stat: React.FC<{ n: number; label: string; accent: string }> = ({ n, label, accent }) => (
  <div className="bg-black/30 rounded p-2">
    <div className={`text-2xl font-bold ${accent}`}>{n.toLocaleString()}</div>
    <div className="text-[11px] text-zinc-500 mt-0.5">{label}</div>
  </div>
);
