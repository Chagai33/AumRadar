import React, { useState, useEffect } from 'react';
import { useAuth } from '../contexts/AuthContext';
import axios from 'axios';
import { LogOut, Search, Calendar, Play, ListMusic, Filter, Clock, AlertTriangle, Settings, RefreshCw, Save, Layers, X, Check } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import clsx from 'clsx';

// Types updated to match new backend
interface AlbumGroup {
    key: string;
    artist: string;
    album: string;
    tracks: any[];
    selected: boolean;
}

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
    logs?: string[];
    partial_scan?: boolean;
}

export const Dashboard: React.FC = () => {
    const { user, logout } = useAuth();

    // State
    const [scanStatus, setScanStatus] = useState<ScanStatus>({
        is_running: false, status: 'idle', progress: 0, total: 0, current_artist: '', results_count: 0
    });

    const [results, setResults] = useState<Track[]>([]);
    const [dateOption, setDateOption] = useState<'last7' | 'last30' | 'custom' | 'sat_to_fri' | 'sun_to_sat'>('sun_to_sat');
    const [customStart, setCustomStart] = useState('');
    const [customEnd, setCustomEnd] = useState('');

    // Cache State
    const [cacheInfo, setCacheInfo] = useState<{ exists: boolean, count: number, last_updated: string | null } | null>(null);
    const [refreshArtists, setRefreshArtists] = useState(true);

    // Scan Settings State
    const [albumTypes, setAlbumTypes] = useState<string[]>(['single']);
    const [includeFollowed, setIncludeFollowed] = useState(true);
    const [includeLiked, setIncludeLiked] = useState(false);
    const [minLikedSongs, setMinLikedSongs] = useState(1);

    // Album Grouping State
    const [showAlbumModal, setShowAlbumModal] = useState(false);
    const [detectedAlbums, setDetectedAlbums] = useState<AlbumGroup[]>([]);
    const [detectedSingles, setDetectedSingles] = useState<Track[]>([]);

    // Scan date range (shown in results header)
    const [scanDateRange, setScanDateRange] = useState<{ start: string; end: string } | null>(null);

    // Search and Restore State
    const [searchTerm, setSearchTerm] = useState('');
    const [originalResults, setOriginalResults] = useState<Track[]>([]);

    // Selection State
    const [selectedUris, setSelectedUris] = useState<Set<string>>(new Set());

    // Auto-select all on load
    useEffect(() => {
        if (results.length > 0) {
            setSelectedUris(new Set(results.map(t => t.uri)));
        }
    }, [results]);

    const toggleSelection = (uri: string) => {
        const newSet = new Set(selectedUris);
        if (newSet.has(uri)) newSet.delete(uri);
        else newSet.add(uri);
        setSelectedUris(newSet);
    };

    // Advanced Filters State
    const [minDurationSec, setMinDurationSec] = useState(90);
    const [maxDurationSec, setMaxDurationSec] = useState(270); // Default 4:30
    const [forbiddenKeywords, setForbiddenKeywords] = useState(
        "live\nsession\nלייב\nקאבר\na capella\nacapella\nFSOE\ntechno\nextended\nsped up\nspeed up\nintro\nslow\nremaster\ninstrumental"
    );
    const [excludedArtists, setExcludedArtists] = useState('');
    const [showSettings, setShowSettings] = useState(false);

    // Artist Picker State
    const [showArtistPicker, setShowArtistPicker] = useState(false);
    const [artistList, setArtistList] = useState<{id: string, name: string}[]>([]);
    const [artistSearch, setArtistSearch] = useState('');
    const [selectedArtistIds, setSelectedArtistIds] = useState<Set<string> | null>(null); // null = all
    const [pickerDraft, setPickerDraft] = useState<Set<string>>(new Set());
    const [pickerAllMode, setPickerAllMode] = useState(true);

    // Automation State
    const [showAutoSettings, setShowAutoSettings] = useState(false);
    const [autoEnabled, setAutoEnabled] = useState(false);
    const [autoDay, setAutoDay] = useState('friday');
    const [autoTime, setAutoTime] = useState('10:00');
    const [autoDateMode, setAutoDateMode] = useState<'sun_to_sat' | 'last7'>('sun_to_sat');
    const [autoExcludeAlbums, setAutoExcludeAlbums] = useState(false);
    const [autoRefreshArtists, setAutoRefreshArtists] = useState(false);
    const [settingsLoaded, setSettingsLoaded] = useState(false);

    // Load defaults from LocalStorage on mount
    useEffect(() => {
        const saved = localStorage.getItem('aum_settings');
        if (saved) {
            try {
                const p = JSON.parse(saved);
                if (p.minDurationSec !== undefined) setMinDurationSec(p.minDurationSec);
                if (p.maxDurationSec !== undefined) setMaxDurationSec(p.maxDurationSec);
                if (p.forbiddenKeywords !== undefined) setForbiddenKeywords(p.forbiddenKeywords);
                if (p.excludedArtists !== undefined) setExcludedArtists(p.excludedArtists);
                if (p.albumTypes !== undefined) setAlbumTypes(p.albumTypes);
                if (p.includeFollowed !== undefined) setIncludeFollowed(p.includeFollowed);
                if (p.includeLiked !== undefined) setIncludeLiked(p.includeLiked);
                if (p.minLikedSongs !== undefined) setMinLikedSongs(p.minLikedSongs);
            } catch (e) { console.error("Error loading settings", e); }
        }
        setSettingsLoaded(true);
    }, []);

    // Autosave Settings to LocalStorage
    useEffect(() => {
        if (!settingsLoaded) return; // Don't save before loading

        const timeoutId = setTimeout(() => {
            const settings = {
                minDurationSec, maxDurationSec, forbiddenKeywords, excludedArtists,
                albumTypes, includeFollowed, includeLiked, minLikedSongs
            };
            localStorage.setItem('aum_settings', JSON.stringify(settings));
            // console.log("Settings autosaved");
        }, 1000); // 1-second debounce to avoid spamming storage while typing

        return () => clearTimeout(timeoutId);
    }, [minDurationSec, maxDurationSec, forbiddenKeywords, excludedArtists, albumTypes, includeFollowed, includeLiked, minLikedSongs, settingsLoaded]);

    // Load Automation Config
    useEffect(() => {
        axios.get('/api/automation/config').then(res => {
            const data = res.data;
            if (data) {
                if (data.enabled !== undefined) setAutoEnabled(data.enabled);
                if (data.run_day) setAutoDay(data.run_day);
                if (data.run_time) setAutoTime(data.run_time);

                // Pre-fill Advanced Filters if saved
                if (data.settings) {
                    if (data.settings.exclude_artists?.length > 0) {
                        setExcludedArtists(data.settings.exclude_artists.join('\n'));
                    }
                    if (data.settings.forbidden_keywords?.length > 0) {
                        setForbiddenKeywords(data.settings.forbidden_keywords.join('\n'));
                    }
                    if (data.settings.exclude_albums !== undefined) setAutoExcludeAlbums(data.settings.exclude_albums);
                    if (data.settings.refresh_artists !== undefined) setAutoRefreshArtists(data.settings.refresh_artists);
                    if (data.settings.start_date === 'LAST7') setAutoDateMode('last7');
                    else setAutoDateMode('sun_to_sat');
                }
            }
        }).catch(e => console.error("Auto config load error", e));
    }, []);

    // Initial load: cache info + one status check
    useEffect(() => {
        checkCacheInfo();
        const checkStatus = async () => {
            try {
                const { data } = await axios.get('/api/status');
                setScanStatus(data);
                if (data.results_count > 0) {
                    const res = await axios.get('/api/results');
                    setResults(res.data);
                    setOriginalResults(res.data);
                }
            } catch (e) {
                console.error("Status check failed", e);
            }
        };
        checkStatus();
    }, []);

    // Poll only while a scan is running
    useEffect(() => {
        if (!scanStatus.is_running) return;

        const interval = setInterval(async () => {
            try {
                const { data } = await axios.get('/api/status');
                setScanStatus(data);

                if (data.status === 'completed' || data.results_count > results.length) {
                    const res = await axios.get('/api/results');
                    setResults(res.data);
                    setOriginalResults(res.data);
                }
            } catch (e) {
                console.error("Status poll failed", e);
            }
        }, 2000);

        return () => clearInterval(interval);
    }, [scanStatus.is_running, results.length]);

    const openArtistPicker = async () => {
        if (artistList.length === 0) {
            try {
                const res = await axios.get('/api/artists');
                if (!res.data.length) { alert('No artist cache found. Run a scan first to load the artist list.'); return; }
                setArtistList(res.data);
            } catch { alert('Could not load artist list.'); return; }
        }
        if (selectedArtistIds === null) {
            setPickerAllMode(true);
            setPickerDraft(new Set());
        } else {
            setPickerAllMode(false);
            setPickerDraft(new Set(selectedArtistIds));
        }
        setArtistSearch('');
        setShowArtistPicker(true);
    };

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
            if (!dateParams.start_date || !dateParams.end_date) {
                alert('Please select a start and end date.');
                return;
            }
            setScanDateRange({ start: dateParams.start_date, end: dateParams.end_date });

            await axios.post('/api/start', {
                start_date: dateParams.start_date,
                end_date: dateParams.end_date,
                include_followed: includeFollowed,
                include_liked_songs: includeLiked,
                min_liked_songs: minLikedSongs,
                album_types: albumTypes,
                refresh_artists: refreshArtists,
                min_duration_sec: minDurationSec,
                max_duration_sec: maxDurationSec,
                forbidden_keywords: forbiddenKeywords.split('\n').map(k => k.trim()).filter(k => k.length > 0),
                exclude_artists: excludedArtists.split('\n').map(s => s.trim()).filter(s => s.length > 0),
                selected_artist_ids: selectedArtistIds ? Array.from(selectedArtistIds) : null,
            });

            setSelectedArtistIds(null); // one-time — reset after scan starts
            setScanStatus(prev => ({ ...prev, is_running: true, status: 'scanning' }));

        } catch (e: any) {
            alert('Failed to start scan: ' + (e.response?.data?.detail || e.message));
        }
    };

    const calculateDates = () => {
        const today = new Date();
        const start = new Date();
        const end = new Date();

        if (dateOption === 'last7') {
            start.setDate(today.getDate() - 7);
        } else if (dateOption === 'last30') {
            start.setDate(today.getDate() - 30);
        } else if (dateOption === 'sat_to_fri') {
            // Saturday to Friday: find most recent Friday, go back 6 days to Saturday
            const daysSinceFriday = (today.getDay() + 7 - 5) % 7;
            end.setDate(today.getDate() - daysSinceFriday);
            start.setTime(end.getTime());
            start.setDate(end.getDate() - 6);
        } else if (dateOption === 'sun_to_sat') {
            // Current calendar week: Sunday → Saturday
            // getDay(): 0=Sun, 1=Mon, …, 6=Sat
            const daysSinceSunday = today.getDay();            // days elapsed since Sunday
            start.setDate(today.getDate() - daysSinceSunday);  // rewind to Sunday
            end.setDate(start.getDate() + 6);                  // forward to Saturday
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

    const handleRestoreResults = () => {
        if (confirm("Restore original scan results? This will undo album organization.")) {
            setResults(originalResults);
        }
    };

    const handleExport = async () => {
        if (results.length === 0) return;

        // Calculate date range string
        let startD = new Date();
        let endD = new Date();

        if (dateOption === 'last7') {
            startD.setDate(endD.getDate() - 7);
        } else if (dateOption === 'last30') {
            startD.setDate(endD.getDate() - 30);
        } else if (dateOption === 'sat_to_fri') {
            const daysSinceFriday = (endD.getDay() + 7 - 5) % 7;
            endD.setDate(endD.getDate() - daysSinceFriday);
            startD.setTime(endD.getTime());
            startD.setDate(endD.getDate() - 6);
        } else if (dateOption === 'sun_to_sat') {
            const daysSinceSunday = endD.getDay();
            startD.setDate(endD.getDate() - daysSinceSunday);
            endD.setDate(startD.getDate() + 6);
        } else if (dateOption === 'custom' && customStart && customEnd) {
            startD = new Date(customStart);
            endD = new Date(customEnd);
        }

        const fmt = (d: Date) => {
            return `${d.getDate()}.${d.getMonth() + 1}.${d.getFullYear().toString().slice(-2)}`;
        };

        const dateStr = `${fmt(startD)} - ${fmt(endD)}`;
        const selSuffix = scanStatus.partial_scan ? ' [SEL]' : '';
        const defaultName = `NewReleases ${dateStr}${selSuffix}`;

        const name = prompt("Enter a name for your new playlist:", defaultName);
        if (!name) return;

        try {
            const uris = Array.from(selectedUris);
            if (uris.length === 0) {
                alert("Please select at least one track to export.");
                return;
            }

            const res = await axios.post('/api/export', { name, uris });

            if (res.data.status === 'success') {
                if (confirm("Playlist created successfully! Open in Spotify?")) {
                    window.open(res.data.playlist_url, '_blank');
                }
            } else {
                alert("Export failed: " + res.data.message);
            }
        } catch (e: any) {
            alert("Export error: " + (e.response?.data?.message || e.message));
        }
    };

    const handleAnalyzeAlbums = () => {
        const groups: { [key: string]: any[] } = {};

        // 1. Group by Artist + Album
        results.forEach(track => {
            const albumName = track.album?.name || 'Unknown';
            const artistName = track.artists && track.artists[0] ? track.artists[0].name : 'Unknown';
            const key = `${artistName}::${albumName}`;

            if (!groups[key]) groups[key] = [];
            groups[key].push(track);
        });

        const albums: AlbumGroup[] = [];
        const singles: Track[] = [];

        // 2. Filter Groups >= 4
        Object.entries(groups).forEach(([key, tracks]) => {
            if (tracks.length >= 4) {
                const [artist, album] = key.split('::');
                // Try to sort using track number if available, else original index in results (preserved by push order)
                // Note: The tracks pushed are from 'results', so they are somewhat ordered by artist scan time.
                // We should rely on spotify metadata track_number if possible, but our current Track interface might not show it.
                // Let's assume we trust the scan order or better yet, verify track_number exists? 
                // In engine.py we inject full item, so track_number exists in JSON even if not in TS interface.
                // Let's force cast to any to access track_number
                tracks.sort((a, b) => ((a as any).track_number || 0) - ((b as any).track_number || 0));

                albums.push({
                    key,
                    artist,
                    album,
                    tracks,
                    selected: true
                });
            } else {
                singles.push(...tracks);
            }
        });

        if (albums.length === 0) {
            alert("No albums (groups of 4+ tracks) detected.");
            return;
        }

        setDetectedAlbums(albums);
        setDetectedSingles(singles);
        setShowAlbumModal(true);
    };

    const handleApplyAlbumOrganization = () => {
        let newOrder = [...detectedSingles];
        const selectedAlbums = detectedAlbums.filter(a => a.selected);

        selectedAlbums.forEach(group => {
            newOrder = [...newOrder, ...group.tracks];
        });

        setResults(newOrder);
        setShowAlbumModal(false);
    };

    const toggleAlbumSelection = (key: string) => {
        setDetectedAlbums(prev => prev.map(a =>
            a.key === key ? { ...a, selected: !a.selected } : a
        ));
    };

    const filteredResults = results.filter(r =>
        r.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        r.artists.some(a => a.name.toLowerCase().includes(searchTerm.toLowerCase()))
    );

    const percent = scanStatus.total > 0 ? (scanStatus.progress / scanStatus.total) * 100 : 0;

    return (
        <div className="min-h-screen bg-[#121212] text-white font-sans">
            {/* Top Bar */}
            <header className="sticky top-0 z-50 bg-[#000]/90 backdrop-blur-md border-b border-[#333] px-6 py-4 flex justify-between items-center">
                <div className="flex items-center gap-3">
                    <img src="/logo.svg" alt="Aum Radar" className="h-9 w-auto" />
                    <h1 className="text-xl font-bold tracking-tight">
                        Aum <span className="text-[#1DB954]">Radar</span>
                    </h1>
                </div>

                <div className="flex items-center gap-4">
                    <div className="text-sm font-medium text-gray-300 hidden md:block">
                        {user?.display_name}
                    </div>
                    <button onClick={() => setShowAutoSettings(true)} className="p-2 hover:bg-[#333] rounded-full transition-colors text-gray-400 hover:text-[#1DB954] mr-2" title="Automation Settings">
                        <Calendar className="w-5 h-5" />
                    </button>
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
                                <span>
                                    {scanStatus.progress} / {scanStatus.total} Artists
                                    {scanStatus.partial_scan && <span className="text-yellow-500 ml-1">(selected only)</span>}
                                </span>
                                {scanStatus.results_count > 0 && (
                                    <span className="text-[#1DB954]">{scanStatus.results_count} tracks found</span>
                                )}
                                <span>{Math.round(percent)}%</span>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>

                {/* Completion Banner */}
                {scanStatus.status === 'completed' && !scanStatus.is_running && (
                    <motion.div
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="bg-green-900/20 border border-[#1DB954]/40 p-4 rounded-xl mb-8 flex items-center justify-between shadow-lg backdrop-blur-sm"
                    >
                        <div className="flex items-center gap-3">
                            <div className="bg-[#1DB954]/20 p-2 rounded-full">
                                <Check className="w-5 h-5 text-[#1DB954]" />
                            </div>
                            <div>
                                <h3 className="font-bold text-white text-lg">Scan Completed</h3>
                                <p className="text-gray-400 text-sm">
                                    {results.length === 0
                                        ? "No new releases found in the selected date range."
                                        : `Found ${results.length} new releases.`
                                    }
                                </p>
                            </div>
                        </div>
                        <button
                            onClick={() => setScanStatus(prev => ({ ...prev, status: 'idle' }))}
                            className="bg-[#282828] hover:bg-[#333] text-white px-3 py-1 rounded text-sm transition-colors border border-gray-700"
                        >
                            Dismiss
                        </button>
                    </motion.div>
                )}

                {/* Error Banner */}
                {scanStatus.status === 'error' && (
                    <motion.div
                        initial={{ opacity: 0, y: -20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="bg-red-900/10 border border-red-500/30 rounded-xl p-6 mb-8 flex flex-col md:flex-row items-start gap-4"
                    >
                        <AlertTriangle className="w-6 h-6 text-red-500 shrink-0 mt-1" />
                        <div className="flex-1 w-full">
                            <h3 className="text-lg font-bold text-red-400">Scan Failed</h3>
                            <p className="text-gray-300 mt-1 text-sm">{scanStatus.error || "Too many requests to Spotify. Please wait a while before scanning again."}</p>
                        </div>
                        <button
                            onClick={async () => {
                                setScanStatus(prev => ({ ...prev, status: 'idle', error: undefined }));
                                try { await axios.post('/api/dismiss-error'); } catch {}
                            }}
                            className="text-red-400 hover:text-white shrink-0"
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </motion.div>
                )}

                {/* Controls (Disabled while scanning) */}
                {!scanStatus.is_running && (
                    <section className="bg-[#181818] rounded-xl p-6 border border-[#282828] mb-8 shadow-xl transition-all duration-300">

                        {/* Compact Toolbar */}
                        <div className="flex flex-col xl:flex-row xl:items-end justify-between gap-6">

                            {/* Left: Date Range & Start Button */}
                            <div className="flex flex-col md:flex-row gap-4 md:items-end w-full xl:w-auto">
                                <div className="flex-1 md:flex-none">
                                    <label className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-2 block">
                                        Time Range
                                    </label>
                                    <div className="flex bg-[#282828] rounded-lg p-1">
                                        {(['sun_to_sat', 'sat_to_fri', 'last7', 'last30', 'custom'] as const).map(opt => (
                                            <button
                                                key={opt}
                                                onClick={() => setDateOption(opt)}
                                                className={clsx(
                                                    "px-3 py-2 rounded-md text-sm font-medium transition-all whitespace-nowrap flex-1 md:flex-none",
                                                    dateOption === opt ? "bg-[#333] text-white shadow-sm" : "text-gray-400 hover:text-gray-200"
                                                )}
                                            >
                                                {opt === 'sun_to_sat' ? 'Sun – Sat' : opt === 'sat_to_fri' ? 'Sat – Fri' : opt === 'last7' ? 'Last 7' : opt === 'last30' ? 'Last 30' : 'Custom'}
                                            </button>
                                        ))}
                                    </div>
                                    {dateOption !== 'custom' && (() => {
                                        const d = calculateDates();
                                        const fmt = (s: string) => {
                                            const [y, m, day] = s.split('-');
                                            return `${parseInt(day)}.${parseInt(m)}.${y.slice(-2)}`;
                                        };
                                        return (
                                            <div className="text-xs text-gray-500 mt-1.5 pl-1">
                                                {fmt(d.start_date)} – {fmt(d.end_date)}
                                            </div>
                                        );
                                    })()}
                                </div>

                                {dateOption === 'custom' && (
                                    <div className="flex gap-2 items-end flex-1 md:flex-none">
                                        <div>
                                            <label className="text-xs text-gray-500 block mb-1">Start</label>
                                            <input
                                                type="date"
                                                className="bg-[#282828] border border-[#333] rounded px-3 py-2 text-sm text-white focus:border-[#1DB954] outline-none w-full"
                                                value={customStart}
                                                onChange={e => setCustomStart(e.target.value)}
                                            />
                                        </div>
                                        <div>
                                            <label className="text-xs text-gray-500 block mb-1">End</label>
                                            <input
                                                type="date"
                                                className="bg-[#282828] border border-[#333] rounded px-3 py-2 text-sm text-white focus:border-[#1DB954] outline-none w-full"
                                                value={customEnd}
                                                onChange={e => setCustomEnd(e.target.value)}
                                            />
                                        </div>
                                    </div>
                                )}

                                <button
                                    onClick={handleStartScan}
                                    className="bg-[#1DB954] hover:bg-[#1ed760] text-black font-bold py-2.5 px-6 rounded-lg shadow-lg hover:shadow-[#1DB954]/20 transition-all transform hover:scale-105 flex items-center justify-center gap-2 whitespace-nowrap mt-2 md:mt-0"
                                >
                                    <Search className="w-5 h-5" />
                                    Start Scan
                                </button>
                            </div>

                            {/* Force Refresh — always visible */}
                            <div className="flex flex-col gap-1 self-end pb-1">
                                <label className="flex items-center gap-2 cursor-pointer text-sm font-medium text-gray-300 hover:text-white transition-colors">
                                    <input
                                        type="checkbox"
                                        checked={refreshArtists}
                                        onChange={(e) => setRefreshArtists(e.target.checked)}
                                        className="w-4 h-4 rounded text-[#1DB954] focus:ring-[#1DB954] bg-[#333] border-gray-600"
                                    />
                                    Force Refresh Artist List
                                    {cacheInfo?.exists && !refreshArtists && (
                                        <span className="text-xs text-gray-500 font-normal">(using cache)</span>
                                    )}
                                </label>
                                {cacheInfo?.exists && includeFollowed && (
                                    <span className="text-xs text-gray-500 ml-6 flex items-center gap-2">
                                        <button onClick={openArtistPicker} className="hover:text-[#1DB954] transition-colors hover:underline underline-offset-2">
                                            {cacheInfo.count} artists · Last updated: {new Date(cacheInfo.last_updated!).toLocaleString()}
                                        </button>
                                        {selectedArtistIds !== null && (
                                            <>
                                                <span className="text-[#1DB954] font-semibold">({selectedArtistIds.size} selected)</span>
                                                <button onClick={() => setSelectedArtistIds(null)} className="text-gray-600 hover:text-white transition-colors" title="Clear selection — scan all artists">
                                                    <X className="w-3 h-3" />
                                                </button>
                                            </>
                                        )}
                                    </span>
                                )}
                            </div>

                            {/* Right: Settings Toggle */}
                            <button
                                onClick={() => setShowSettings(!showSettings)}
                                className={`flex items-center justify-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors border border-transparent ${showSettings ? 'bg-[#333] text-white border-gray-600' : 'text-gray-400 hover:text-white hover:bg-[#282828]'}`}
                            >
                                <Settings className="w-4 h-4" />
                                {showSettings ? 'Hide Settings' : 'Configure Filters'}
                            </button>
                        </div>

                        <AnimatePresence>
                            {showSettings && (
                                <motion.div
                                    initial={{ height: 0, opacity: 0 }}
                                    animate={{ height: 'auto', opacity: 1 }}
                                    exit={{ height: 0, opacity: 0 }}
                                    className="overflow-hidden"
                                >
                                    <div className="mt-6 pt-6 border-t border-[#333]">
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

                                            {/* Advanced Filters */}
                                            <div className="md:col-span-2 border-t border-[#333] pt-6 mt-2">
                                                <label className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-4 block flex items-center gap-2">
                                                    <Filter className="w-4 h-4" /> Advanced Filters
                                                </label>

                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                                    <div>
                                                        <label className="text-xs text-gray-400 block mb-2">Track Duration (Seconds)</label>
                                                        <div className="flex items-center gap-4">
                                                            <div className="flex-1">
                                                                <span className="text-[10px] text-gray-500 uppercase block mb-1">Min</span>
                                                                <input
                                                                    type="number"
                                                                    value={minDurationSec}
                                                                    onChange={e => setMinDurationSec(Number(e.target.value))}
                                                                    className="w-full bg-[#282828] border border-[#333] rounded px-3 py-2 text-sm focus:border-[#1DB954] outline-none"
                                                                />
                                                            </div>
                                                            <div className="flex-1">
                                                                <span className="text-[10px] text-gray-500 uppercase block mb-1">Max</span>
                                                                <input
                                                                    type="number"
                                                                    value={maxDurationSec}
                                                                    onChange={e => setMaxDurationSec(Number(e.target.value))}
                                                                    className="w-full bg-[#282828] border border-[#333] rounded px-3 py-2 text-sm focus:border-[#1DB954] outline-none"
                                                                />
                                                            </div>
                                                        </div>
                                                    </div>

                                                    <div>
                                                        <label className="text-xs text-gray-400 block mb-2">Forbidden Keywords (One per line)</label>
                                                        <textarea
                                                            value={forbiddenKeywords}
                                                            onChange={e => setForbiddenKeywords(e.target.value)}
                                                            rows={4}
                                                            className="w-full bg-[#282828] border border-[#333] rounded px-3 py-2 text-xs font-mono text-gray-300 focus:border-[#1DB954] outline-none resize-none"
                                                            placeholder="live&#10;remix&#10;..."
                                                        />
                                                    </div>

                                                    <div className="md:col-span-2 mt-4 pt-4 border-t border-[#333]">
                                                        <label className="text-xs text-gray-400 block mb-2">Excluded Artists (One Name or ID per line)</label>
                                                        <textarea
                                                            value={excludedArtists}
                                                            onChange={e => setExcludedArtists(e.target.value)}
                                                            rows={3}
                                                            className="w-full bg-[#282828] border border-[#333] rounded px-3 py-2 text-xs font-mono text-gray-300 focus:border-[#1DB954] outline-none resize-none"
                                                            placeholder="Justin Bieber&#10;6eUKZXaKkcviH0Ku9w2n3V&#10;..."
                                                        />
                                                    </div>

                                                    <div className="md:col-span-2 mt-2 pt-2 flex justify-end">
                                                        <span className="text-[10px] text-gray-600 flex items-center gap-1.5 opacity-70">
                                                            <Save className="w-3 h-3" /> Settings auto-saved
                                                        </span>
                                                    </div>
                                                </div>
                                            </div>
                                        </div>


                                    </div>
                                </motion.div>
                            )}
                        </AnimatePresence>
                    </section>
                )}

                {/* Results Grid Header */}
                <div className="flex flex-col gap-4 mb-6">
                    <div className="flex items-center justify-between">
                        <h2 className="text-2xl font-bold flex items-center gap-2 flex-wrap">
                            Found Releases
                            <span className="text-sm font-normal bg-[#333] text-white px-2 py-0.5 rounded-full">
                                {filteredResults.length} {filteredResults.length !== results.length && <span className="text-gray-500">/ {results.length}</span>}
                            </span>
                            {scanDateRange && results.length > 0 && (
                                <span className="text-xs font-normal text-gray-500 bg-[#222] px-2 py-0.5 rounded-full">
                                    {scanDateRange.start} → {scanDateRange.end}
                                </span>
                            )}
                            {results.length > 0 && (
                                <button
                                    onClick={() => {
                                        const allVisible = filteredResults.map(r => r.uri);
                                        const newSet = new Set(selectedUris);
                                        const allSelected = allVisible.every(uri => newSet.has(uri));

                                        if (allSelected) {
                                            allVisible.forEach(uri => newSet.delete(uri));
                                        } else {
                                            allVisible.forEach(uri => newSet.add(uri));
                                        }
                                        setSelectedUris(newSet);
                                    }}
                                    className="text-xs ml-2 text-[#1DB954] hover:text-white font-medium transition-colors border border-[#1DB954]/30 px-2 py-1 rounded hover:bg-[#1DB954]/10"
                                >
                                    {filteredResults.length > 0 && filteredResults.every(r => selectedUris.has(r.uri)) ? 'Deselect All' : 'Select All'}
                                </button>
                            )}
                        </h2>

                        {results.length > 0 && !scanStatus.is_running && (
                            <div className="flex flex-wrap gap-2 justify-end">
                                {results.length !== originalResults.length && (
                                    <button
                                        onClick={handleRestoreResults}
                                        className="flex items-center gap-2 text-red-400 border border-red-900/50 hover:bg-red-900/20 px-3 py-1.5 rounded-full text-xs font-bold transition-all"
                                        title="Undo album grouping and restore original scan"
                                    >
                                        <RefreshCw className="w-3 h-3" />
                                        Undo
                                    </button>
                                )}
                                <button
                                    onClick={handleAnalyzeAlbums}
                                    className="flex items-center gap-2 bg-[#282828] hover:bg-[#333] border border-gray-600 text-white px-3 py-2 rounded-full text-sm font-bold transition-all"
                                >
                                    <Layers className="w-4 h-4 text-blue-400" />
                                    Albums
                                </button>
                                <button
                                    onClick={handleExport}
                                    className="flex items-center gap-2 bg-[#1DB954] hover:bg-[#1ed760] text-black px-4 py-2 rounded-full text-sm font-bold transition-all"
                                >
                                    <Save className="w-4 h-4" />
                                    Export
                                </button>
                            </div>
                        )}
                    </div>

                    {results.length > 0 && (
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                            <input
                                type="text"
                                placeholder="Filter by artist or track..."
                                value={searchTerm}
                                onChange={e => setSearchTerm(e.target.value)}
                                className="w-full bg-[#181818] border border-[#333] rounded-lg pl-10 pr-4 py-2 text-sm text-gray-200 focus:border-[#1DB954] outline-none"
                            />
                        </div>
                    )}
                </div>

                {
                    results.length === 0 && !scanStatus.is_running && (
                        <div className="text-center py-20 text-gray-500 border-2 border-dashed border-[#282828] rounded-xl">
                            <Search className="w-12 h-12 mx-auto mb-4 opacity-50" />
                            <p>Ready to scan. Select a date range above.</p>
                        </div>
                    )
                }

                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                    {filteredResults.length === 0 && results.length > 0 && (
                        <p className="col-span-full text-center text-gray-500 py-10">No matches found for "{searchTerm}"</p>
                    )}
                    {filteredResults.map((track, i) => (
                        <motion.div
                            key={track.id + i}
                            initial={{ opacity: 0, scale: 0.9 }}
                            animate={{ opacity: 1, scale: 1 }}
                            className="bg-[#181818] group hover:bg-[#282828] p-4 rounded-lg transition-all duration-300 relative"
                        >
                            <div className="relative aspect-square mb-4 shadow-lg overflow-hidden rounded-md">
                                <div className="absolute top-2 left-2 z-20" onClick={e => e.stopPropagation()}>
                                    <input
                                        type="checkbox"
                                        checked={selectedUris.has(track.uri)}
                                        onChange={() => toggleSelection(track.uri)}
                                        className="w-5 h-5 rounded border-gray-500 bg-black/60 text-[#1DB954] focus:ring-[#1DB954] cursor-pointer"
                                    />
                                </div>
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
                {/* Album Organization Modal */}
                <AnimatePresence>
                    {showAlbumModal && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                        >
                            <div className="bg-[#181818] border border-gray-800 rounded-xl w-full max-w-2xl max-h-[80vh] flex flex-col shadow-2xl">
                                <div className="p-6 border-b border-gray-800 flex justify-between items-center">
                                    <h3 className="text-xl font-bold text-white flex items-center gap-2">
                                        <Layers className="w-5 h-5 text-blue-400" />
                                        Organize Detected Albums
                                    </h3>
                                    <button onClick={() => setShowAlbumModal(false)} className="text-gray-400 hover:text-white">
                                        <X className="w-5 h-5" />
                                    </button>
                                </div>

                                <div className="p-6 overflow-y-auto flex-1 text-gray-300 space-y-4">
                                    <div className="bg-blue-900/20 border border-blue-900/50 p-4 rounded-lg text-sm mb-4 flex justify-between items-start">
                                        <div>
                                            <p>Found <strong>{detectedAlbums.length}</strong> albums with 4+ tracks.</p>
                                            <p className="mt-1">☑ <strong>Checked</strong> → moved to the <strong>end</strong> of the playlist.</p>
                                            <p>☐ <strong>Unchecked</strong> → <strong>removed</strong> from results entirely.</p>
                                        </div>
                                        <button
                                            onClick={() => {
                                                const allSelected = detectedAlbums.every(a => a.selected);
                                                setDetectedAlbums(prev => prev.map(a => ({ ...a, selected: !allSelected })));
                                            }}
                                            className="bg-blue-600 hover:bg-blue-500 text-white px-3 py-1.5 rounded text-xs font-bold transition-colors shadow-lg ml-4 whitespace-nowrap"
                                        >
                                            {detectedAlbums.every(a => a.selected) ? 'Deselect All' : 'Select All'}
                                        </button>
                                    </div>

                                    <div className="space-y-2">
                                        {detectedAlbums.map(group => (
                                            <div
                                                key={group.key}
                                                className={`flex items-center justify-between p-3 rounded-lg border cursor-pointer transition-all ${group.selected
                                                    ? 'bg-[#282828] border-gray-600'
                                                    : 'bg-red-900/10 border-red-900/30 opacity-60'
                                                    }`}
                                                onClick={() => toggleAlbumSelection(group.key)}
                                            >
                                                <div className="flex items-center gap-3">
                                                    <div className={`w-5 h-5 rounded border flex items-center justify-center ${group.selected ? 'bg-blue-500 border-blue-500' : 'border-gray-500'
                                                        }`}>
                                                        {group.selected && <Check className="w-3 h-3 text-white" />}
                                                    </div>
                                                    <div>
                                                        <div className="font-bold text-white">{group.album}</div>
                                                        <div className="text-xs text-gray-400">{group.artist} • {group.tracks.length} tracks</div>
                                                    </div>
                                                </div>
                                                <div className={`text-xs font-semibold ${group.selected ? 'text-blue-400' : 'text-red-400'}`}>
                                                    {group.selected ? '→ End' : '✕ Remove'}
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                <div className="p-6 border-t border-gray-800 flex justify-end gap-3">
                                    <button
                                        onClick={() => setShowAlbumModal(false)}
                                        className="px-4 py-2 rounded-lg font-bold text-gray-400 hover:text-white hover:bg-[#333] transition-all"
                                    >
                                        Cancel
                                    </button>
                                    <button
                                        onClick={handleApplyAlbumOrganization}
                                        className="px-6 py-2 rounded-lg font-bold bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/20 transition-all"
                                    >
                                        Apply Changes ({detectedAlbums.filter(a => a.selected).length} Albums)
                                    </button>
                                </div>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
                {/* Artist Picker Modal */}
                <AnimatePresence>
                    {showArtistPicker && (() => {
                        const filtered = artistSearch.length >= 1
                            ? artistList.filter(a => a.name.toLowerCase().includes(artistSearch.toLowerCase()))
                            : artistList.slice(0, 200);
                        const showingAll = artistSearch.length >= 1;
                        const isChecked = (id: string) => pickerAllMode || pickerDraft.has(id);
                        const selectedCount = pickerAllMode ? artistList.length : pickerDraft.size;

                        const toggleArtist = (id: string) => {
                            if (pickerAllMode) {
                                const next = new Set(artistList.map(a => a.id));
                                next.delete(id);
                                setPickerDraft(next);
                                setPickerAllMode(false);
                            } else {
                                const next = new Set(pickerDraft);
                                if (next.has(id)) next.delete(id); else next.add(id);
                                setPickerDraft(next);
                            }
                        };

                        const selectVisible = () => {
                            if (pickerAllMode) return;
                            const next = new Set(pickerDraft);
                            filtered.forEach(a => next.add(a.id));
                            setPickerDraft(next);
                        };

                        const deselectVisible = () => {
                            if (pickerAllMode) {
                                const next = new Set(artistList.map(a => a.id));
                                filtered.forEach(a => next.delete(a.id));
                                setPickerDraft(next);
                                setPickerAllMode(false);
                            } else {
                                const next = new Set(pickerDraft);
                                filtered.forEach(a => next.delete(a.id));
                                setPickerDraft(next);
                            }
                        };

                        const confirmSelection = () => {
                            if (pickerAllMode || pickerDraft.size === artistList.length) {
                                setSelectedArtistIds(null);
                            } else if (pickerDraft.size === 0) {
                                setSelectedArtistIds(null);
                            } else {
                                setSelectedArtistIds(new Set(pickerDraft));
                            }
                            setShowArtistPicker(false);
                        };

                        return (
                            <motion.div
                                initial={{ opacity: 0 }}
                                animate={{ opacity: 1 }}
                                exit={{ opacity: 0 }}
                                className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                                onClick={() => setShowArtistPicker(false)}
                            >
                                <div className="bg-[#181818] border border-gray-800 rounded-xl w-full max-w-lg shadow-2xl flex flex-col" style={{maxHeight: '85vh'}} onClick={e => e.stopPropagation()}>
                                    {/* Header */}
                                    <div className="p-5 border-b border-gray-800 flex items-center justify-between shrink-0">
                                        <div>
                                            <h3 className="text-lg font-bold text-white">Select Artists to Scan</h3>
                                            <p className="text-xs text-gray-500 mt-0.5">
                                                <span className={selectedCount === artistList.length ? 'text-gray-400' : 'text-[#1DB954] font-semibold'}>
                                                    {selectedCount}
                                                </span>
                                                <span className="text-gray-600"> / {artistList.length} selected</span>
                                            </p>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <button onClick={() => { setPickerAllMode(true); setPickerDraft(new Set()); }} className="text-xs text-gray-400 hover:text-white px-2 py-1 rounded hover:bg-[#333] transition-colors">All</button>
                                            <button onClick={() => setShowArtistPicker(false)} className="text-gray-500 hover:text-white ml-1"><X className="w-5 h-5" /></button>
                                        </div>
                                    </div>

                                    {/* Search */}
                                    <div className="px-4 py-3 border-b border-gray-800 shrink-0">
                                        <div className="relative">
                                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                                            <input
                                                autoFocus
                                                type="text"
                                                placeholder="Search artists..."
                                                value={artistSearch}
                                                onChange={e => setArtistSearch(e.target.value)}
                                                className="w-full bg-[#222] border border-[#333] rounded-lg pl-9 pr-4 py-2 text-sm text-white focus:border-[#1DB954] outline-none"
                                            />
                                        </div>
                                        <div className="flex gap-3 mt-2">
                                            <button onClick={selectVisible} className="text-xs text-gray-400 hover:text-white transition-colors">+ Select visible</button>
                                            <button onClick={deselectVisible} className="text-xs text-gray-400 hover:text-white transition-colors">− Deselect visible</button>
                                            {!showingAll && artistList.length > 200 && (
                                                <span className="text-xs text-gray-600 ml-auto">Showing 200 of {artistList.length} — search to find more</span>
                                            )}
                                        </div>
                                    </div>

                                    {/* List */}
                                    <div className="overflow-y-auto flex-1">
                                        {filtered.map(artist => (
                                            <label key={artist.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-[#222] cursor-pointer border-b border-[#1a1a1a] last:border-0">
                                                <input
                                                    type="checkbox"
                                                    checked={isChecked(artist.id)}
                                                    onChange={() => toggleArtist(artist.id)}
                                                    className="rounded text-[#1DB954] focus:ring-[#1DB954] bg-[#333] border-gray-600 shrink-0"
                                                />
                                                <span className="text-sm text-gray-200 truncate">{artist.name}</span>
                                            </label>
                                        ))}
                                        {filtered.length === 0 && (
                                            <p className="text-center text-gray-600 py-10 text-sm">No artists found</p>
                                        )}
                                    </div>

                                    {/* Footer */}
                                    <div className="p-4 border-t border-gray-800 flex justify-end gap-3 shrink-0">
                                        <button onClick={() => setShowArtistPicker(false)} className="px-4 py-2 rounded-lg text-gray-400 hover:text-white hover:bg-[#333] transition-colors text-sm font-medium">Cancel</button>
                                        <button onClick={confirmSelection} className="px-5 py-2 rounded-lg bg-[#1DB954] hover:bg-[#1ed760] text-black font-bold text-sm transition-colors">
                                            {selectedCount === artistList.length ? 'Scan All' : `Scan ${selectedCount} Artists`}
                                        </button>
                                    </div>
                                </div>
                            </motion.div>
                        );
                    })()}
                </AnimatePresence>

                {/* Automation Modal */}
                <AnimatePresence>
                    {showAutoSettings && (
                        <motion.div
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            exit={{ opacity: 0 }}
                            className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                        >
                            <div className="bg-[#181818] border border-[#1DB954]/30 rounded-xl w-full max-w-lg shadow-2xl p-6 max-h-[90vh] overflow-y-auto">
                                <h3 className="text-xl font-bold text-white mb-6 flex items-center gap-2">
                                    <Calendar className="w-6 h-6 text-[#1DB954]" /> Automate Weekly Scan
                                </h3>

                                <div className="space-y-5">
                                    {/* Enable Toggle */}
                                    <div className="flex items-center justify-between bg-[#282828] p-4 rounded-lg">
                                        <span className="text-gray-200 font-medium">Enable Automation</span>
                                        <div
                                            onClick={() => setAutoEnabled(!autoEnabled)}
                                            className={`w-12 h-6 rounded-full p-1 cursor-pointer transition-colors ${autoEnabled ? 'bg-[#1DB954]' : 'bg-gray-600'}`}
                                        >
                                            <div className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${autoEnabled ? 'translate-x-6' : ''}`} />
                                        </div>
                                    </div>

                                    {/* Schedule managed in GCP Cloud Scheduler — not editable here */}
                                    <div className="text-xs text-gray-600 bg-[#1a1a1a] border border-[#2a2a2a] rounded-lg px-4 py-2.5">
                                        ⏰ Schedule is managed directly in <span className="text-gray-400">GCP Cloud Scheduler</span>
                                    </div>

                                    {/* Date Range */}
                                    <div>
                                        <label className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-2 block">Date Range to Scan</label>
                                        <div className="flex gap-2">
                                            {(['sun_to_sat', 'last7'] as const).map(mode => (
                                                <button
                                                    key={mode}
                                                    onClick={() => setAutoDateMode(mode)}
                                                    className={`flex-1 py-2 px-3 rounded-lg text-sm font-medium transition-all border ${autoDateMode === mode ? 'bg-[#333] text-white border-gray-500' : 'text-gray-400 border-transparent hover:text-gray-200 hover:bg-[#282828]'}`}
                                                >
                                                    {mode === 'sun_to_sat' ? 'Current Week (Sun–Sat)' : 'Last 7 Days'}
                                                </button>
                                            ))}
                                        </div>
                                        <p className="text-xs text-gray-600 mt-1.5 pl-1">
                                            {autoDateMode === 'sun_to_sat'
                                                ? 'Dates are recalculated fresh each time the automation runs'
                                                : '7 days rolling back from the moment the automation runs'}
                                        </p>
                                    </div>

                                    {/* Refresh Artists */}
                                    <label className="flex items-start gap-3 bg-[#282828] p-4 rounded-lg cursor-pointer hover:bg-[#2a2a2a] transition-colors">
                                        <input
                                            type="checkbox"
                                            checked={autoRefreshArtists}
                                            onChange={e => setAutoRefreshArtists(e.target.checked)}
                                            className="w-4 h-4 mt-0.5 rounded text-[#1DB954] focus:ring-[#1DB954] bg-[#333] border-gray-600 cursor-pointer"
                                        />
                                        <div>
                                            <div className="text-gray-200 font-medium text-sm">Force Refresh Artist List</div>
                                            <div className="text-xs text-gray-500 mt-0.5">Fetch a fresh artist list from Spotify each run — slower but ensures no new follows are missed</div>
                                        </div>
                                    </label>

                                    {/* Album Exclusion */}
                                    <label className="flex items-start gap-3 bg-[#282828] p-4 rounded-lg cursor-pointer hover:bg-[#2a2a2a] transition-colors">
                                        <input
                                            type="checkbox"
                                            checked={autoExcludeAlbums}
                                            onChange={e => setAutoExcludeAlbums(e.target.checked)}
                                            className="w-4 h-4 mt-0.5 rounded text-[#1DB954] focus:ring-[#1DB954] bg-[#333] border-gray-600 cursor-pointer"
                                        />
                                        <div>
                                            <div className="text-gray-200 font-medium text-sm">Exclude Albums from Playlist</div>
                                            <div className="text-xs text-gray-500 mt-0.5">Artists with 4+ tracks from the same album will be excluded from the automated export</div>
                                        </div>
                                    </label>

                                    {/* Detailed Summary */}
                                    <div className="bg-[#0e0e0e] border border-[#2a2a2a] rounded-lg p-4 text-sm space-y-2">
                                        <p className="text-xs font-bold text-gray-500 uppercase tracking-widest mb-3">What will happen when it runs</p>
                                        <div className="space-y-2 text-sm">
                                            <div className="flex gap-2">
                                                <span className="w-5 shrink-0">📅</span>
                                                <span className="text-gray-500">Scan range:</span>
                                                <span className="text-gray-200 ml-1">{autoDateMode === 'sun_to_sat' ? 'Current Sun–Sat calendar week' : 'Last 7 days'}</span>
                                            </div>
                                            <div className="flex gap-2">
                                                <span className="w-5 shrink-0">🎵</span>
                                                <span className="text-gray-500">Release types:</span>
                                                <span className="text-gray-200 ml-1 capitalize">{albumTypes.length > 0 ? albumTypes.join(', ') : 'None selected'}</span>
                                            </div>
                                            <div className="flex gap-2">
                                                <span className="w-5 shrink-0">👥</span>
                                                <span className="text-gray-500">Source:</span>
                                                <span className="text-gray-200 ml-1">
                                                    {[includeFollowed && 'Followed artists', includeLiked && `Liked songs (≥${minLikedSongs})`].filter(Boolean).join(' + ') || 'None'}
                                                </span>
                                            </div>
                                            <div className="flex gap-2">
                                                <span className="w-5 shrink-0">⏱</span>
                                                <span className="text-gray-500">Duration filter:</span>
                                                <span className="text-gray-200 ml-1">{minDurationSec}s – {maxDurationSec}s</span>
                                            </div>
                                            <div className="flex gap-2">
                                                <span className="w-5 shrink-0">🚫</span>
                                                <span className="text-gray-500">Keywords blocked:</span>
                                                <span className="text-gray-200 ml-1">{forbiddenKeywords.split('\n').filter(k => k.trim()).length} keywords</span>
                                            </div>
                                            {excludedArtists.split('\n').filter(a => a.trim()).length > 0 && (
                                                <div className="flex gap-2">
                                                    <span className="w-5 shrink-0">🚷</span>
                                                    <span className="text-gray-500">Excluded artists:</span>
                                                    <span className="text-gray-200 ml-1">{excludedArtists.split('\n').filter(a => a.trim()).length} artists</span>
                                                </div>
                                            )}
                                            <div className="flex gap-2">
                                                <span className="w-5 shrink-0">🔄</span>
                                                <span className="text-gray-500">Artist list:</span>
                                                <span className={`ml-1 font-medium ${autoRefreshArtists ? 'text-yellow-400' : 'text-gray-300'}`}>
                                                    {autoRefreshArtists ? 'Refreshed from Spotify each run' : 'Using cached list'}
                                                </span>
                                            </div>
                                            <div className="flex gap-2">
                                                <span className="w-5 shrink-0">💿</span>
                                                <span className="text-gray-500">Albums (4+ tracks):</span>
                                                <span className={`ml-1 font-medium ${autoExcludeAlbums ? 'text-red-400' : 'text-green-400'}`}>
                                                    {autoExcludeAlbums ? 'Excluded from playlist' : 'Included in playlist'}
                                                </span>
                                            </div>
                                            <div className="flex gap-2">
                                                <span className="w-5 shrink-0">💾</span>
                                                <span className="text-gray-500">Playlist name:</span>
                                                <span className="text-gray-200 ml-1 font-mono text-xs">"Weekly Radar [date range]"</span>
                                            </div>
                                        </div>
                                    </div>

                                    {/* Actions */}
                                    <div className="flex gap-3 pt-1">
                                        <button onClick={() => setShowAutoSettings(false)} className="flex-1 bg-[#282828] hover:bg-[#333] text-white py-2.5 rounded-lg transition-colors font-medium">
                                            Cancel
                                        </button>
                                        <button
                                            onClick={async () => {
                                                try {
                                                    const dateToken = autoDateMode === 'sun_to_sat' ? 'DYNAMIC' : 'LAST7';
                                                    await axios.post('/api/automation/config', {
                                                        enabled: autoEnabled,
                                                        run_day: autoDay,
                                                        run_time: autoTime,
                                                        settings: {
                                                            start_date: dateToken,
                                                            end_date: dateToken,
                                                            include_followed: includeFollowed,
                                                            include_liked_songs: includeLiked,
                                                            min_liked_songs: minLikedSongs,
                                                            album_types: albumTypes,
                                                            refresh_artists: autoRefreshArtists,
                                                            min_duration_sec: minDurationSec,
                                                            max_duration_sec: maxDurationSec,
                                                            forbidden_keywords: forbiddenKeywords.split('\n').map(k => k.trim()).filter(k => k.length > 0),
                                                            exclude_artists: excludedArtists.split('\n').map(s => s.trim()).filter(s => s.length > 0),
                                                            exclude_albums: autoExcludeAlbums,
                                                        }
                                                    });
                                                    alert('Automation settings saved!');
                                                    setShowAutoSettings(false);
                                                } catch (e: any) { alert('Error saving settings: ' + e.message); }
                                            }}
                                            className="flex-1 bg-[#1DB954] hover:bg-[#1ed760] text-black font-bold py-2.5 rounded-lg transition-colors shadow-lg"
                                        >
                                            Save Settings
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
            </main >
        </div >
    );
};
