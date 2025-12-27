import React, { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';
import axios from 'axios';
import { LogOut, Search, Calendar, Play, ListMusic, Filter, Clock, AlertTriangle, Settings, RefreshCw } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import clsx from 'clsx';

// Types updated to match new backend
interface Track {
    id: string;
    name: string;
    uri: string;
    artists: { name: string, id: string }[];
    album: { id: string, name: string, images: { url: string }[], release_date: string };
    duration_ms: number;
    explicit: boolean;
}

interface ScanStatus {
    is_running: boolean;
    status: string; // idle, fetching_artists, scanning, completed, error
    progress: number;
    total: number;
    current_artist: string;
    results_count: number;
    error?: string;
    retry_after?: number;
}

export const Dashboard: React.FC = () => {
    const { user, logout } = useAuth();

    // State
    const [scanStatus, setScanStatus] = useState<ScanStatus>({
        is_running: false, status: 'idle', progress: 0, total: 0, current_artist: '', results_count: 0
    });

    const [results, setResults] = useState<Track[]>([]);
    const [dateOption, setDateOption] = useState<'last7' | 'last30' | 'custom'>('last7');
    const [customStart, setCustomStart] = useState('');
    const [customEnd, setCustomEnd] = useState('');

    // Cache State
    const [cacheInfo, setCacheInfo] = useState<{ exists: boolean, count: number, last_updated: string | null } | null>(null);
    const [refreshArtists, setRefreshArtists] = useState(false);

    // Scan Settings State
    const [albumTypes, setAlbumTypes] = useState<string[]>(['album', 'single']);
    const [includeFollowed, setIncludeFollowed] = useState(true);
    const [includeLiked, setIncludeLiked] = useState(false);
    const [minLikedSongs, setMinLikedSongs] = useState(1);

    // Poll for status
    useEffect(() => {
        // Initial cache info check
        checkCacheInfo();

        let interval: any;
        const checkStatus = async () => {
            try {
                const { data } = await axios.get('/api/status');
                setScanStatus(data);

                if (data.status === 'completed' || data.results_count > results.length) {
                    const res = await axios.get('/api/results');
                    setResults(res.data);
                }
            } catch (e) {
                console.error("Status poll failed", e);
            }
        };

        checkStatus();
        interval = setInterval(checkStatus, 2000);

        return () => clearInterval(interval);
    }, []);

    const checkCacheInfo = async () => {
        try {
            const res = await axios.get('/api/cache-info');
            console.log("Cache info response:", res.data); // Debug
            setCacheInfo(res.data);
            // Default: if cache exists, don't refresh. 
            setRefreshArtists(!res.data.exists);
        } catch (e) {
            console.error("Failed to check cache info", e);
        }
    };

    const handleStartScan = async () => {
        try {
            const dateParams = calculateDates();

            await axios.post('/api/start', {
                start_date: dateParams.start_date,
                end_date: dateParams.end_date,
                include_followed: includeFollowed,
                include_liked_songs: includeLiked,
                min_liked_songs: minLikedSongs,
                album_types: albumTypes,
                refresh_artists: refreshArtists
            });

        } catch (e: any) {
            alert('Failed to start scan: ' + (e.response?.data?.detail || e.message));
        }
    };

    const calculateDates = () => {
        const end = new Date();
        const start = new Date();

        if (dateOption === 'last7') {
            start.setDate(end.getDate() - 7);
        } else if (dateOption === 'last30') {
            start.setDate(end.getDate() - 30);
        } else {
            // custom
            return { start_date: customStart, end_date: customEnd };
        }

        return {
            start_date: start.toISOString().split('T')[0],
            end_date: end.toISOString().split('T')[0]
        };
    };

    const handleStopScan = async () => {
        await axios.post('/api/stop');
    };

    const percent = scanStatus.total > 0 ? (scanStatus.progress / scanStatus.total) * 100 : 0;

    return (
        <div className="min-h-screen bg-[#121212] text-white font-sans">
            {/* Top Bar */}
            <header className="sticky top-0 z-50 bg-[#000]/90 backdrop-blur-md border-b border-[#333] px-6 py-4 flex justify-between items-center">
                <div className="flex items-center gap-3">
                    <div className="w-8 h-8 bg-gradient-to-br from-[#1DB954] to-emerald-600 rounded-lg flex items-center justify-center">
                        <ListMusic className="text-white w-5 h-5" />
                    </div>
                    <h1 className="text-xl font-bold tracking-tight">
                        Antigravity <span className="text-[#1DB954]">Radar</span>
                    </h1>
                </div>

                <div className="flex items-center gap-4">
                    <div className="text-sm font-medium text-gray-300 hidden md:block">
                        {user?.display_name}
                    </div>
                    <button onClick={logout} className="p-2 hover:bg-[#333] rounded-full transition-colors text-gray-400 hover:text-white">
                        <LogOut className="w-5 h-5" />
                    </button>
                </div>
            </header>

            <main className="container mx-auto px-6 py-8">

                {/* Status / Progress Card */}
                <AnimatePresence>
                    {scanStatus.is_running && (
                        <motion.div
                            initial={{ opacity: 0, y: -20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.95 }}
                            className="bg-[#1e1e1e] border border-[#1DB954]/30 rounded-xl p-6 mb-8 shadow-2xl relative overflow-hidden"
                        >
                            <div className="absolute top-0 left-0 h-1 bg-[#1DB954] transition-all duration-500 ease-out" style={{ width: `${percent}%` }} />

                            <div className="flex justify-between items-start mb-4">
                                <div>
                                    {scanStatus.status === 'rate_limited' ? (
                                        <h2 className="text-lg font-bold text-orange-400 flex items-center gap-2 animate-pulse">
                                            <AlertTriangle className="w-5 h-5" />
                                            Spotify Rate Limit Hit
                                        </h2>
                                    ) : scanStatus.status === 'fetching_artists' ? (
                                        <h2 className="text-lg font-bold text-white flex items-center gap-2">
                                            <RefreshCw className="w-4 h-4 animate-spin text-blue-400" />
                                            Loading Artist List...
                                        </h2>
                                    ) : (
                                        <h2 className="text-lg font-bold text-white flex items-center gap-2">
                                            <RefreshCw className="w-4 h-4 animate-spin text-[#1DB954]" />
                                            Scanning Your Library...
                                        </h2>
                                    )}

                                    {scanStatus.status === 'rate_limited' ? (
                                        <p className="text-gray-300 text-sm mt-1">
                                            Pausing for <span className="font-bold text-orange-400">{scanStatus.retry_after || 'few'}s</span> to respect API limits.
                                            <br /><span className="text-xs opacity-70">Don't worry, we'll auto-resume.</span>
                                        </p>
                                    ) : (
                                        <p className="text-gray-400 text-sm mt-1">
                                            Checking artist: <span className="text-[#1DB954] font-medium">{scanStatus.current_artist || 'Initializing...'}</span>
                                        </p>
                                    )}
                                </div>
                                <button onClick={handleStopScan} className="text-xs bg-[#333] hover:bg-red-900/50 text-white px-3 py-1 rounded border border-transparent hover:border-red-500 transition-colors">
                                    Stop Scan
                                </button>
                            </div>

                            <div className="flex justify-between text-xs text-gray-500 font-mono mt-2">
                                <span>{scanStatus.progress} / {scanStatus.total} Artists</span>
                                <span>{Math.round(percent)}%</span>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Error Banner */}
                {scanStatus.error && (
                    <motion.div
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="bg-red-900/30 border border-red-500/50 p-4 rounded-xl mb-6 flex items-start gap-4"
                    >
                        <AlertTriangle className="w-6 h-6 text-red-500 shrink-0 mt-1" />
                        <div>
                            <h3 className="text-lg font-bold text-red-100">Scan Failed</h3>
                            <p className="text-red-300">{scanStatus.error}</p>
                        </div>
                        <button onClick={() => setScanStatus(prev => ({ ...prev, error: undefined }))} className="ml-auto text-red-300 hover:text-white">x</button>
                    </motion.div>
                )}

                {/* Controls (Disabled while scanning) */}
                {!scanStatus.is_running && (
                    <section className="bg-[#181818] rounded-xl p-6 border border-[#282828] mb-8 shadow-xl">

                        {/* Scan Settings Grid */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">

                            {/* Release Types */}
                            <div>
                                <label className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-2 block">
                                    Release Types
                                </label>
                                <div className="flex flex-wrap gap-3">
                                    {['album', 'single', 'compilation', 'appears_on'].map(type => (
                                        <label key={type} className="flex items-center gap-2 cursor-pointer bg-[#282828] px-3 py-2 rounded border border-transparent hover:border-gray-600 transition-colors">
                                            <input
                                                type="checkbox"
                                                checked={albumTypes.includes(type)}
                                                onChange={e => {
                                                    if (e.target.checked) setAlbumTypes([...albumTypes, type]);
                                                    else setAlbumTypes(albumTypes.filter(t => t !== type));
                                                }}
                                                className="rounded text-[#1DB954] focus:ring-[#1DB954] bg-[#333] border-gray-600"
                                            />
                                            <span className="capitalize text-sm text-gray-300">{type.replace('_', ' ')}</span>
                                        </label>
                                    ))}
                                </div>
                            </div>

                            {/* Scan Source */}
                            <div>
                                <label className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-2 block">
                                    Scan Source
                                </label>
                                <div className="space-y-3">
                                    <label className="flex items-center gap-3 cursor-pointer">
                                        <div className={`w-10 h-6 rounded-full p-1 transition-colors ${includeFollowed ? 'bg-[#1DB954]' : 'bg-gray-600'}`}>
                                            <div className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${includeFollowed ? 'translate-x-4' : ''}`} />
                                        </div>
                                        <input type="checkbox" className="hidden" checked={includeFollowed} onChange={e => setIncludeFollowed(e.target.checked)} />
                                        <span className="text-sm text-gray-300">Scan Followed Artists</span>
                                    </label>

                                    <div className="bg-[#282828] p-3 rounded border border-gray-700">
                                        <div className="flex items-center justify-between mb-2">
                                            <label className="flex items-center gap-3 cursor-pointer">
                                                <div className={`w-10 h-6 rounded-full p-1 transition-colors ${includeLiked ? 'bg-[#1DB954]' : 'bg-gray-600'}`}>
                                                    <div className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${includeLiked ? 'translate-x-4' : ''}`} />
                                                </div>
                                                <input type="checkbox" className="hidden" checked={includeLiked} onChange={e => setIncludeLiked(e.target.checked)} />
                                                <span className="text-sm text-gray-300">Scan Artists from Liked Songs</span>
                                            </label>
                                        </div>

                                        {includeLiked && (
                                            <div className="ml-12">
                                                <label className="text-xs text-gray-500 block mb-1">
                                                    Minimum Liked Songs per Artist
                                                </label>
                                                <input
                                                    type="number"
                                                    min="1"
                                                    value={minLikedSongs}
                                                    onChange={e => setMinLikedSongs(parseInt(e.target.value) || 1)}
                                                    className="w-full bg-[#333] border border-gray-600 rounded px-2 py-1 text-sm text-white focus:border-[#1DB954] outline-none"
                                                />
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>

                        {/* Cache Notification (repositioned if needed, keeping existing logic) */}
                        {cacheInfo?.exists && includeFollowed && (
                            <div className="mb-6 p-4 bg-[#282828] rounded-lg border border-gray-700 flex flex-col md:flex-row justify-between items-center gap-4">
                                <div>
                                    <p className="text-sm text-gray-300">
                                        <span className="font-bold text-[#1DB954]">{cacheInfo.count} Artists</span> found in cache.
                                    </p>
                                    <p className="text-xs text-gray-500">
                                        Last updated: {new Date(cacheInfo.last_updated!).toLocaleString()}
                                    </p>
                                </div>
                                <label className="flex items-center gap-2 cursor-pointer text-sm font-medium text-gray-300 hover:text-white transition-colors bg-[#1a1a1a] px-3 py-2 rounded">
                                    <input
                                        type="checkbox"
                                        checked={refreshArtists}
                                        onChange={(e) => setRefreshArtists(e.target.checked)}
                                        className="w-4 h-4 rounded text-[#1DB954] focus:ring-[#1DB954] bg-[#333] border-gray-600"
                                    />
                                    Force Refresh Artist List
                                </label>
                            </div>
                        )}

                        <div className="flex flex-col md:flex-row gap-6 items-end justify-between">

                            <div className="flex gap-4">
                                <div>
                                    <label className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-2 block">
                                        Time Range
                                    </label>
                                    <div className="flex bg-[#282828] rounded-lg p-1">
                                        {(['last7', 'last30', 'custom'] as const).map(opt => (
                                            <button
                                                key={opt}
                                                onClick={() => setDateOption(opt)}
                                                className={clsx(
                                                    "px-4 py-2 rounded-md text-sm font-medium transition-all",
                                                    dateOption === opt ? "bg-[#333] text-white shadow-sm" : "text-gray-400 hover:text-gray-200"
                                                )}
                                            >
                                                {opt === 'last7' ? 'Last 7 Days' : opt === 'last30' ? 'Last 30 Days' : 'Custom'}
                                            </button>
                                        ))}
                                    </div>
                                </div>

                                {dateOption === 'custom' && (
                                    <div className="flex gap-2 items-end">
                                        <div>
                                            <label className="text-xs text-gray-500 block mb-1">Start</label>
                                            <input
                                                type="date"
                                                className="bg-[#282828] border border-[#333] rounded px-3 py-2 text-sm text-white focus:border-[#1DB954] outline-none"
                                                value={customStart}
                                                onChange={e => setCustomStart(e.target.value)}
                                            />
                                        </div>
                                        <div>
                                            <label className="text-xs text-gray-500 block mb-1">End</label>
                                            <input
                                                type="date"
                                                className="bg-[#282828] border border-[#333] rounded px-3 py-2 text-sm text-white focus:border-[#1DB954] outline-none"
                                                value={customEnd}
                                                onChange={e => setCustomEnd(e.target.value)}
                                            />
                                        </div>
                                    </div>
                                )}
                            </div>

                            <button
                                onClick={handleStartScan}
                                className="bg-[#1DB954] hover:bg-[#1ed760] text-black font-bold py-3 px-8 rounded-full shadow-lg hover:shadow-[#1DB954]/20 transition-all transform hover:scale-105 flex items-center gap-2"
                            >
                                <Search className="w-5 h-5" />
                                Start New Scan
                            </button>
                        </div>
                    </section>
                )}

                {/* Results Grid */}
                <div className="flex items-center justify-between mb-6">
                    <h2 className="text-2xl font-bold flex items-center gap-2">
                        Found Releases
                        <span className="text-sm font-normal bg-[#333] text-white px-2 py-0.5 rounded-full ml-2">{results.length}</span>
                    </h2>
                </div>

                {results.length === 0 && !scanStatus.is_running && (
                    <div className="text-center py-20 text-gray-500 border-2 border-dashed border-[#282828] rounded-xl">
                        <Search className="w-12 h-12 mx-auto mb-4 opacity-50" />
                        <p>Ready to scan. Select a date range above.</p>
                    </div>
                )}

                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                    {results.map((track, i) => (
                        <motion.div
                            key={track.id + i}
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            className="bg-[#181818] group hover:bg-[#282828] p-4 rounded-lg transition-all duration-300 relative"
                        >
                            <div className="relative aspect-square mb-4 shadow-lg overflow-hidden rounded-md">
                                <img
                                    src={track.album?.images?.[0]?.url || '/placeholder.png'}
                                    alt={track.album?.name}
                                    className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                                />
                                <a
                                    href={track.uri}
                                    className="absolute bottom-3 right-3 w-12 h-12 bg-[#1DB954] rounded-full flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all translate-y-4 group-hover:translate-y-0 shadow-xl hover:scale-110"
                                >
                                    <Play className="w-6 h-6 text-black fill-current ml-1" />
                                </a>
                                {track.explicit && (
                                    <span className="absolute top-2 right-2 bg-black/60 backdrop-blur-sm text-white text-[10px] px-1.5 py-0.5 rounded border border-white/10 font-bold">E</span>
                                )}
                            </div>

                            <h3 className="font-bold text-white truncate mb-1" title={track.name}>{track.name}</h3>
                            <p className="text-sm text-gray-400 truncate hover:text-[#1DB954] cursor-pointer">
                                {track.artists.map(a => a.name).join(', ')}
                            </p>

                            <div className="flex justify-between items-center text-xs text-gray-600 mt-3 pt-3 border-t border-[#222]">
                                <span className="flex items-center gap-1">
                                    <Clock className="w-3 h-3" />
                                    {Math.floor(track.duration_ms / 60000)}:{String(Math.floor((track.duration_ms % 60000) / 1000)).padStart(2, '0')}
                                </span>
                                <span>{track.album?.release_date}</span>
                            </div>
                        </motion.div>
                    ))}
                </div>
            </main>
        </div>
    );
};
