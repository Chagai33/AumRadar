import React from 'react';
import { useAuth } from '../contexts/AuthContext';
import { motion } from 'framer-motion';
import { Music, ArrowRight } from 'lucide-react';

export const Login: React.FC = () => {
    const { login } = useAuth();

    return (
        <div className="min-h-screen flex items-center justify-center bg-background text-white relative overflow-hidden">
            {/* Background Gradient */}
            <div className="absolute inset-0 bg-gradient-to-br from-purple-900/20 via-background to-emerald-900/20 z-0" />

            <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                className="relative z-10 text-center space-y-8 p-8 max-w-md w-full"
            >
                <div className="flex justify-center">
                    <div className="p-4 bg-primary/10 rounded-full">
                        <Music className="w-12 h-12 text-primary" />
                    </div>
                </div>

                <div className="space-y-2">
                    <h1 className="text-4xl font-bold tracking-tight">Antigravity Music</h1>
                    <p className="text-muted-foreground">
                        Your personal Spotify Release Radar, supercharged with custom filters.
                    </p>
                </div>

                <button
                    onClick={login}
                    className="w-full py-4 bg-[#1DB954] hover:bg-[#1ed760] text-black font-bold rounded-full transition-all transform hover:scale-105 flex items-center justify-center gap-2"
                >
                    Connect with Spotify
                    <ArrowRight className="w-5 h-5" />
                </button>
            </motion.div>
        </div>
    );
};
