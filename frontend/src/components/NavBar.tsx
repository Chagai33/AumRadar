import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { LogOut } from 'lucide-react';

// One navigation bar shared by every page, so moving between the tools is consistent
// and obvious (active page highlighted). Pages pass their own actions via `right`.
// The pipeline reads left→right: Scan new releases → Recon playlists → Bootstrap
// scores → Health tuning → Cleanup.
const NAV = [
  { to: '/dashboard', label: 'Scan', icon: '🛰️' },
  { to: '/recon', label: 'Recon', icon: '📋' },
  { to: '/bootstrap', label: 'Bootstrap', icon: '⚖️' },
  { to: '/release', label: 'Weekly', icon: '📆' },
  { to: '/health', label: 'Health', icon: '🎛' },
  { to: '/cleanup', label: 'Cleanup', icon: '🧹' },
];

export const NavBar: React.FC<{ right?: React.ReactNode }> = ({ right }) => {
  const { pathname } = useLocation();
  const { user, logout } = useAuth();

  return (
    <header className="sticky top-0 z-40 bg-[#0d0d0d]/95 backdrop-blur border-b border-zinc-800">
      <div className="px-3 sm:px-5 h-14 flex items-center gap-2">
        <Link to="/dashboard" className="flex items-center gap-2 shrink-0 me-1" title="Aum Radar">
          <img src="/logo.svg" alt="Aum Radar" className="h-7 w-auto" />
          <span className="font-bold tracking-tight hidden lg:inline">Aum <span className="text-[#1DB954]">Radar</span></span>
        </Link>

        <nav className="flex items-center gap-1 overflow-x-auto">
          {NAV.map(n => {
            const active = pathname === n.to;
            return (
              <Link key={n.to} to={n.to} title={n.label}
                className={`flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 rounded-full text-sm whitespace-nowrap transition-colors ${
                  active ? 'bg-[#1DB954] text-black font-semibold' : 'text-zinc-400 hover:text-white hover:bg-zinc-800'}`}>
                <span className="text-base leading-none">{n.icon}</span>
                <span className="hidden sm:inline">{n.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="ms-auto flex items-center gap-2 shrink-0">
          {right}
          {user?.images?.[0]?.url
            ? <img src={user.images[0].url} alt="" className="w-7 h-7 rounded-full object-cover" />
            : null}
          {user?.display_name && <span className="text-sm text-zinc-400 hidden md:inline max-w-[140px] truncate">{user.display_name}</span>}
          <button onClick={logout} title="Log out"
            className="p-2 rounded-full text-zinc-400 hover:text-white hover:bg-zinc-800"><LogOut className="w-5 h-5" /></button>
        </div>
      </div>
    </header>
  );
};
