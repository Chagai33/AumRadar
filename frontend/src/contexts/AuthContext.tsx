import React, { createContext, useContext, useEffect, useState } from 'react';
import axios from 'axios';

interface User {
    id: string;
    display_name: string;
    images: { url: string }[];
}

interface AuthContextType {
    user: User | null;
    loading: boolean;
    login: () => void;
    logout: () => void;
}

const AuthContext = createContext<AuthContextType>({} as AuthContextType);

// Configure Axios defaults
// In production, we use relative paths so Netlify proxies them (First-Party Cookies!)
// In development, we go direct to Python server.
axios.defaults.baseURL = import.meta.env.DEV ? 'http://127.0.0.1:8888' : '';
axios.defaults.withCredentials = true; // IMPORTANT for cookies

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const [user, setUser] = useState<User | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        checkAuth();
    }, []);

    const checkAuth = async () => {
        try {
            const { data } = await axios.get('/me');
            setUser(data.user);
        } catch (e) {
            setUser(null);
        } finally {
            setLoading(false);
        }
    };

    const login = async () => {
        try {
            const { data } = await axios.get('/login');
            window.location.href = data.url; // Redirect to Spotify
        } catch (e) {
            console.error("Login failed", e);
        }
    };

    const logout = async () => {
        await axios.get('/logout');
        setUser(null);
    };

    return (
        <AuthContext.Provider value={{ user, loading, login, logout }}>
            {children}
        </AuthContext.Provider>
    );
};

export const useAuth = () => useContext(AuthContext);
